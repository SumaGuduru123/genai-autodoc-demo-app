"""
publishing/confluence.py

Pushes ALL generated documentation to exactly 2 Confluence pages:

  Page 1 — "Developer Reference Docs"
            All files from docs/generated/ as separate sections (h1 headings)

  Page 2 — "Support SOPs & Runbooks"
            All files from docs/sops/ as separate sections (h1 headings)

Each file becomes a clearly separated section within its page.
A change-history table is appended at the bottom of each page on every run.

Usage:
    python publishing/confluence.py --publish-all
    python publishing/confluence.py --publish-sops
    python publishing/confluence.py --publish-docs
    python publishing/confluence.py --dry-run --publish-all

Environment variables (set in .env or GitHub Actions secrets):
    CONFLUENCE_URL        e.g. https://yourorg.atlassian.net
    CONFLUENCE_EMAIL      your Atlassian account email
    CONFLUENCE_TOKEN      personal API token from id.atlassian.com
    CONFLUENCE_SPACE_KEY  the space key (e.g. SOP)

Page IDs are read from publishing/confluence_page_map.json.
Only 2 IDs needed: "developer_reference_page" and "support_sops_page".
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
PAGE_MAP_PATH = Path(__file__).parent / "confluence_page_map.json"

SOPS_DIR = REPO_ROOT / "docs" / "sops"
GENERATED_DIR = REPO_ROOT / "docs" / "generated"

# Order in which sections appear on each page
SOPS_ORDER = [
    "auth_incident_runbook.md",
    "user_management_runbook.md",
    "deployment_runbook.md",
]

DOCS_ORDER = [
    "auth_service_reference.md",
    "frontend_client_reference.md",
    "test_numbers_reference.md",
]

# Section divider inserted between files
SECTION_DIVIDER = '<hr/><p> </p>'


# ---------------------------------------------------------------------------
# Markdown to Confluence Storage Format converter
# ---------------------------------------------------------------------------

def markdown_to_storage(markdown: str) -> str:
    """
    Convert Markdown to Confluence Storage Format (XHTML-like).
    Handles headings, bold/italic, inline code, fenced code blocks,
    blockquotes, horizontal rules, numbered lists, bullet lists, tables.
    """
    s = markdown

    # Strip YAML-style front-matter comment lines (> lines at top)
    s = re.sub(r"^> \*\*.*?\*\*.*$", "", s, flags=re.MULTILINE)

    # Headings (process h4 before h3 before h2 before h1 to avoid partial matches)
    s = re.sub(r"^#### (.+)$", r"<h4>\1</h4>", s, flags=re.MULTILINE)
    s = re.sub(r"^### (.+)$",  r"<h3>\1</h3>", s, flags=re.MULTILINE)
    s = re.sub(r"^## (.+)$",   r"<h2>\1</h2>", s, flags=re.MULTILINE)
    s = re.sub(r"^# (.+)$",    r"<h1>\1</h1>", s, flags=re.MULTILINE)

    # Bold then italic (bold first to avoid consuming **)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*",     r"<em>\1</em>",         s)

    # Inline code (before fenced blocks)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)

    # Fenced code blocks
    s = re.sub(
        r"```(?:\w+)?\n(.*?)```",
        lambda m: (
            '<ac:structured-macro ac:name="code">'
            '<ac:plain-text-body><![CDATA[' + m.group(1) + ']]></ac:plain-text-body>'
            '</ac:structured-macro>'
        ),
        s,
        flags=re.DOTALL,
    )

    # Blockquotes
    s = re.sub(r"^> (.+)$", r"<blockquote><p>\1</p></blockquote>", s, flags=re.MULTILINE)

    # Horizontal rules
    s = re.sub(r"^---$", r"<hr/>", s, flags=re.MULTILINE)

    # Numbered lists — convert lines, then wrap consecutive <li> in <ol>
    s = re.sub(r"^\d+\. (.+)$", r"<li>\1</li>", s, flags=re.MULTILINE)

    # Bullet lists — convert lines (after numbered to avoid re-processing)
    s = re.sub(r"^[ \t]*[-*] (.+)$", r"<li>\1</li>", s, flags=re.MULTILINE)

    # Wrap consecutive <li> runs in <ol> (numbered was first, rest are <ul>)
    s = re.sub(
        r"(<li>.*?</li>\n?)+",
        lambda m: "<ol>" + m.group(0) + "</ol>",
        s,
        flags=re.DOTALL,
    )

    # Markdown tables
    def _convert_table(match: re.Match) -> str:
        lines = [ln.strip() for ln in match.group(0).strip().splitlines()]
        rows = [ln for ln in lines if not re.match(r"^\|[-| :]+\|$", ln)]
        html = "<table><tbody>"
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row.strip("|").split("|")]
            tag = "th" if i == 0 else "td"
            html += "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
        html += "</tbody></table>"
        return html

    s = re.sub(r"(\|.+\|\n)(\|[-| :]+\|\n)(\|.+\|\n?)+", _convert_table, s)

    # Wrap remaining bare text lines in <p>
    result = []
    for line in s.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<"):
            result.append(f"<p>{stripped}</p>")
        else:
            result.append(line)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Page builder — aggregates multiple files into one page body
# ---------------------------------------------------------------------------

def build_page_body(files: list[Path], page_title: str) -> str:
    """
    Aggregate all *files* into a single Confluence storage-format body.

    Each file becomes a section separated by a horizontal rule.
    A table of contents panel and change-history table are added.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Table of contents macro
    body = (
        f"<h1>{page_title}</h1>"
        f"<p><em>Auto-generated by the Gen AI Auto-Doc pipeline. "
        f"Last updated: {now}</em></p>"
        '<ac:structured-macro ac:name="toc">'
        '<ac:parameter ac:name="minLevel">2</ac:parameter>'
        '<ac:parameter ac:name="maxLevel">2</ac:parameter>'
        '</ac:structured-macro>'
        "<hr/>"
    )

    sections_added = []
    for file_path in files:
        if not file_path.exists():
            print(f"  WARN  File not found, skipping: {file_path.name}")
            continue

        markdown = file_path.read_text(encoding="utf-8")

        # Demote all headings by one level so file h1 becomes h2 (section heading)
        # This makes the TOC show each file as a top-level entry
        demoted = re.sub(r"^(#{1,5}) ", lambda m: "#" * (len(m.group(1)) + 1) + " ", markdown, flags=re.MULTILINE)

        storage = markdown_to_storage(demoted)
        body += f"\n{storage}\n{SECTION_DIVIDER}\n"
        sections_added.append(file_path.name)
        print(f"  + Section added: {file_path.name}")

    # Change history table at the bottom
    rows = "".join(
        f"<tr><td>{now}</td><td>{f}</td><td>Updated by Gen AI Auto-Doc pipeline</td></tr>"
        for f in sections_added
    )
    body += (
        "\n<h2>Change History</h2>"
        "<table><tbody>"
        "<tr><th>Updated At</th><th>Section</th><th>Action</th></tr>"
        f"{rows}"
        "</tbody></table>"
    )

    return body


# ---------------------------------------------------------------------------
# Confluence client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """Thin wrapper around the Confluence Cloud REST API."""

    def __init__(self, base_url: str, email: str, token: str,
                 space_key: str, dry_run: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.space_key = space_key
        self.dry_run = dry_run
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": "Basic " + base64.b64encode(
                f"{email}:{token}".encode()).decode(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{self.base_url}/wiki/rest/api{path}"

    def _get_page(self, page_id: str) -> dict:
        resp = self._session.get(
            self._url(f"/content/{page_id}"),
            params={"expand": "version"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def update_page(self, page_id: str, body: str, page_title: str) -> dict:
        """PUT the aggregated body to an existing Confluence page."""
        if self.dry_run:
            print(f"  DRY RUN: would update page {page_id} ({page_title}) "
                  f"with {len(body)} chars of storage format")
            return {"status": "dry_run", "page_id": page_id}

        page = self._get_page(page_id)
        current_version = page["version"]["number"]
        title = page["title"]

        payload = {
            "version": {"number": current_version + 1},
            "title": title,
            "type": "page",
            "body": {"storage": {"value": body, "representation": "storage"}},
        }

        resp = self._session.put(
            self._url(f"/content/{page_id}"),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        web_url = result.get("_links", {}).get("webui", "")
        print(f"  OK Updated '{title}' -> v{current_version + 1}  {self.base_url}/wiki{web_url}")
        return {"status": "updated", "page_id": page_id, "version": current_version + 1}

    def create_page(self, title: str, body: str) -> dict:
        """Create a brand-new page. Prints the page ID to add to page_map."""
        if self.dry_run:
            print(f"  DRY RUN: would create page '{title}'")
            return {"status": "dry_run", "title": title}

        payload = {
            "type": "page",
            "title": title,
            "space": {"key": self.space_key},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        resp = self._session.post(self._url("/content"), json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        page_id = result["id"]
        web_url = result.get("_links", {}).get("webui", "")
        print(f"  OK Created '{title}' -> page ID: {page_id}")
        print(f"     URL: {self.base_url}/wiki{web_url}")
        print(f"     Add to confluence_page_map.json:")
        print(f"       \"developer_reference_page\" or \"support_sops_page\": \"{page_id}\"")
        return {"status": "created", "page_id": page_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_page_map() -> dict:
    if not PAGE_MAP_PATH.exists():
        print(f"ERROR: {PAGE_MAP_PATH} not found.")
        sys.exit(1)
    with PAGE_MAP_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def build_client(dry_run: bool = False) -> ConfluenceClient:
    missing = [v for v in (
        "CONFLUENCE_URL", "CONFLUENCE_EMAIL",
        "CONFLUENCE_TOKEN", "CONFLUENCE_SPACE_KEY"
    ) if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    return ConfluenceClient(
        base_url=os.environ["CONFLUENCE_URL"],
        email=os.environ["CONFLUENCE_EMAIL"],
        token=os.environ["CONFLUENCE_TOKEN"],
        space_key=os.environ["CONFLUENCE_SPACE_KEY"],
        dry_run=dry_run,
    )


def _ordered_files(directory: Path, order: list[str]) -> list[Path]:
    """Return files in *order*, then any remaining files alphabetically."""
    ordered = [directory / f for f in order if (directory / f).exists()]
    extras = sorted(
        f for f in directory.glob("*.md")
        if f.name not in order and f.name != "README.md"
    )
    return ordered + extras


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push generated docs to 2 Confluence pages."
    )
    parser.add_argument("--publish-all",  action="store_true",
                        help="Publish both the Developer Docs page and the SOPs page")
    parser.add_argument("--publish-sops", action="store_true",
                        help="Publish only the Support SOPs & Runbooks page")
    parser.add_argument("--publish-docs", action="store_true",
                        help="Publish only the Developer Reference Docs page")
    parser.add_argument("--create-pages", action="store_true",
                        help="Create both pages from scratch (first-time setup)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Validate without making any API calls")
    args = parser.parse_args()

    if not any([args.publish_all, args.publish_sops,
                args.publish_docs, args.create_pages]):
        parser.print_help()
        sys.exit(0)

    client = build_client(dry_run=args.dry_run)
    page_map = load_page_map()
    results = []

    # --- Developer Reference Docs page ---
    if args.publish_all or args.publish_docs:
        print("\n[Developer Reference Docs page]")
        files = _ordered_files(GENERATED_DIR, DOCS_ORDER)
        body  = build_page_body(files, "Developer Reference Docs")
        page_id = page_map.get("developer_reference_page", "")
        if not page_id or page_id.startswith("REPLACE"):
            print("  ERROR: Set 'developer_reference_page' in confluence_page_map.json")
        else:
            results.append(client.update_page(page_id, body, "Developer Reference Docs"))

    # --- Support SOPs & Runbooks page ---
    if args.publish_all or args.publish_sops:
        print("\n[Support SOPs & Runbooks page]")
        files = _ordered_files(SOPS_DIR, SOPS_ORDER)
        body  = build_page_body(files, "Support SOPs & Runbooks")
        page_id = page_map.get("support_sops_page", "")
        if not page_id or page_id.startswith("REPLACE"):
            print("  ERROR: Set 'support_sops_page' in confluence_page_map.json")
        else:
            results.append(client.update_page(page_id, body, "Support SOPs & Runbooks"))

    # --- First-time page creation ---
    if args.create_pages:
        print("\n[Creating pages from scratch]")
        docs_files = _ordered_files(GENERATED_DIR, DOCS_ORDER)
        sops_files = _ordered_files(SOPS_DIR, SOPS_ORDER)
        results.append(client.create_page(
            "Developer Reference Docs",
            build_page_body(docs_files, "Developer Reference Docs")
        ))
        results.append(client.create_page(
            "Support SOPs & Runbooks",
            build_page_body(sops_files, "Support SOPs & Runbooks")
        ))

    # Summary
    updated = sum(1 for r in results if r.get("status") == "updated")
    created = sum(1 for r in results if r.get("status") == "created")
    dry     = sum(1 for r in results if r.get("status") == "dry_run")
    print(f"\n=== Confluence Publish Summary ===")
    print(f"Pages processed : {len(results)}")
    print(f"Updated         : {updated}")
    print(f"Created         : {created}")
    print(f"Dry run         : {dry}")
    print(f"==================================")


if __name__ == "__main__":
    main()

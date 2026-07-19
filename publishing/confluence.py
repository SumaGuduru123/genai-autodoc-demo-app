"""
publishing/confluence.py

Pushes generated Markdown documentation to Atlassian Confluence Cloud
via the REST API v2.

Usage (CLI):
    python publishing/confluence.py --file docs/sops/auth_incident_runbook.md
    python publishing/confluence.py --all-sops
    python publishing/confluence.py --all-docs
    python publishing/confluence.py --dry-run --all-sops

Environment variables required (set in .env or GitHub Actions secrets):
    CONFLUENCE_URL        e.g. https://yourorg.atlassian.net
    CONFLUENCE_EMAIL      your Atlassian account email
    CONFLUENCE_TOKEN      personal API token from id.atlassian.com
    CONFLUENCE_SPACE_KEY  the space key where pages live (e.g. GENAI)

Page IDs are read from publishing/confluence_page_map.json.
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

# ---------------------------------------------------------------------------
# Confluence client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """
    Thin wrapper around the Confluence Cloud REST API v2.

    Handles authentication, Markdown-to-Confluence-storage conversion,
    page create/update, and change-history appending.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        space_key: str,
        dry_run: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.space_key = space_key
        self.dry_run = dry_run
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": "Basic "
                + base64.b64encode(f"{email}:{token}".encode()).decode(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}/wiki/rest/api{path}"

    def _get_page(self, page_id: str) -> dict:
        """Fetch the current page metadata (title + version number)."""
        resp = self._session.get(
            self._url(f"/content/{page_id}"),
            params={"expand": "version,body.storage"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _markdown_to_storage(markdown: str) -> str:
        """
        Convert Markdown to Confluence Storage Format (XHTML-like).

        This is a lightweight converter sufficient for the doc pipeline.
        For full fidelity, replace with the Confluence /contentbody/convert
        endpoint or a dedicated library such as md2confluence.
        """
        # Headings
        storage = re.sub(r"^#### (.+)$", r"<h4>\1</h4>", markdown, flags=re.MULTILINE)
        storage = re.sub(r"^### (.+)$", r"<h3>\1</h3>", storage, flags=re.MULTILINE)
        storage = re.sub(r"^## (.+)$", r"<h2>\1</h2>", storage, flags=re.MULTILINE)
        storage = re.sub(r"^# (.+)$", r"<h1>\1</h1>", storage, flags=re.MULTILINE)

        # Bold and italic
        storage = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", storage)
        storage = re.sub(r"\*(.+?)\*", r"<em>\1</em>", storage)

        # Inline code
        storage = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", storage)

        # Fenced code blocks
        storage = re.sub(
            r"```(?:\w+)?\n(.*?)```",
            r'<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[\1]]></ac:plain-text-body></ac:structured-macro>',
            storage,
            flags=re.DOTALL,
        )

        # Blockquotes (> lines)
        storage = re.sub(
            r"^> (.+)$",
            r"<blockquote><p>\1</p></blockquote>",
            storage,
            flags=re.MULTILINE,
        )

        # Horizontal rules
        storage = re.sub(r"^---$", r"<hr/>", storage, flags=re.MULTILINE)

        # Numbered lists — wrap consecutive lines starting with digits
        storage = re.sub(r"^(\d+)\. (.+)$", r"<li>\2</li>", storage, flags=re.MULTILINE)
        storage = re.sub(
            r"(<li>.*?</li>(\n<li>.*?</li>)*)",
            r"<ol>\1</ol>",
            storage,
            flags=re.DOTALL,
        )

        # Bullet lists
        storage = re.sub(r"^[-*] (.+)$", r"<li>\1</li>", storage, flags=re.MULTILINE)

        # Markdown tables -> Confluence table markup
        def _convert_table(match: re.Match) -> str:
            lines = [l.strip() for l in match.group(0).strip().splitlines()]
            rows = [l for l in lines if not re.match(r"^\|[-| :]+\|$", l)]
            html = "<table><tbody>"
            for i, row in enumerate(rows):
                cells = [c.strip() for c in row.strip("|").split("|")]
                tag = "th" if i == 0 else "td"
                html += "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
            html += "</tbody></table>"
            return html

        storage = re.sub(
            r"(\|.+\|\n)((\|[-| :]+\|\n))(\|.+\|\n?)+",
            _convert_table,
            storage,
        )

        # Paragraphs — wrap bare lines
        lines = storage.splitlines()
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("<"):
                result.append(f"<p>{stripped}</p>")
            else:
                result.append(line)

        return "\n".join(result)

    @staticmethod
    def _change_history_entry(file_path: Path) -> str:
        """Return a single HTML change-history table row."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f'<tr><td>{now}</td>'
            f'<td>Updated by Gen AI Auto-Doc pipeline</td>'
            f'<td>{file_path.name}</td></tr>'
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, file_path: Path, page_id: str) -> dict:
        """
        Push *file_path* Markdown content to the Confluence page *page_id*.

        Appends a change-history row to the bottom of the page on every
        update. Returns the API response dict (or a dry-run summary dict).
        """
        print(f"\n>> Publishing '{file_path.name}' to page ID {page_id} ...", flush=True)

        if not file_path.exists():
            print(f"  FAIL File not found: {file_path}")
            return {"status": "skipped", "reason": "file_not_found"}

        markdown = file_path.read_text(encoding="utf-8")
        storage_body = self._markdown_to_storage(markdown)

        # Append change history table
        history_entry = self._change_history_entry(file_path)
        storage_body += (
            "\n<h2>Change History</h2>"
            '<table><tbody>'
            '<tr><th>Date</th><th>Action</th><th>Source File</th></tr>'
            f"{history_entry}"
            "</tbody></table>"
        )

        if self.dry_run:
            print(f"  OK DRY RUN — would update page {page_id} with {len(markdown)} chars")
            return {"status": "dry_run", "page_id": page_id, "file": str(file_path)}

        # Fetch current version number — required for update
        page = self._get_page(page_id)
        current_version = page["version"]["number"]
        title = page["title"]

        payload = {
            "version": {"number": current_version + 1},
            "title": title,
            "type": "page",
            "body": {
                "storage": {
                    "value": storage_body,
                    "representation": "storage",
                }
            },
        }

        resp = self._session.put(
            self._url(f"/content/{page_id}"),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"  OK Updated: {title} (v{current_version + 1}) -> {result.get('_links', {}).get('webui', '')}")
        return {"status": "updated", "page_id": page_id, "version": current_version + 1}

    def create_page(self, title: str, file_path: Path) -> dict:
        """
        Create a brand-new Confluence page from *file_path* under *space_key*.

        Use this once per doc to get the page ID, then add it to
        confluence_page_map.json for future updates.
        """
        print(f"\n>> Creating new page '{title}' in space {self.space_key} ...", flush=True)

        if self.dry_run:
            print(f"  OK DRY RUN — would create page '{title}'")
            return {"status": "dry_run", "title": title}

        markdown = file_path.read_text(encoding="utf-8")
        storage_body = self._markdown_to_storage(markdown)

        payload = {
            "type": "page",
            "title": title,
            "space": {"key": self.space_key},
            "body": {
                "storage": {
                    "value": storage_body,
                    "representation": "storage",
                }
            },
        }

        resp = self._session.post(self._url("/content"), json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        page_id = result["id"]
        print(f"  OK Created: '{title}' -> page ID {page_id}")
        print(f"  Add this to confluence_page_map.json: \"{file_path.name}\": \"{page_id}\"")
        return {"status": "created", "page_id": page_id, "title": title}


# ---------------------------------------------------------------------------
# Page map loader
# ---------------------------------------------------------------------------

def load_page_map() -> dict:
    """Load the filename -> page_id mapping from confluence_page_map.json."""
    if not PAGE_MAP_PATH.exists():
        print(f"ERROR: Page map not found at {PAGE_MAP_PATH}")
        print("Create publishing/confluence_page_map.json — see the file for instructions.")
        sys.exit(1)
    with PAGE_MAP_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_client(dry_run: bool = False) -> ConfluenceClient:
    """Build a ConfluenceClient from environment variables."""
    missing = [
        v for v in ("CONFLUENCE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN", "CONFLUENCE_SPACE_KEY")
        if not os.environ.get(v)
    ]
    if missing:
        print(f"ERROR: Missing environment variable(s): {', '.join(missing)}")
        print("Set them in your .env file or as GitHub Actions secrets.")
        sys.exit(1)

    return ConfluenceClient(
        base_url=os.environ["CONFLUENCE_URL"],
        email=os.environ["CONFLUENCE_EMAIL"],
        token=os.environ["CONFLUENCE_TOKEN"],
        space_key=os.environ["CONFLUENCE_SPACE_KEY"],
        dry_run=dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push generated docs to Confluence Cloud."
    )
    parser.add_argument("--file", help="Path to a single Markdown file to publish")
    parser.add_argument("--all-sops", action="store_true", help="Publish all files in docs/sops/")
    parser.add_argument("--all-docs", action="store_true", help="Publish all files in docs/generated/")
    parser.add_argument("--dry-run", action="store_true", help="Validate without making API calls")
    args = parser.parse_args()

    if not any([args.file, args.all_sops, args.all_docs]):
        parser.print_help()
        sys.exit(0)

    client = build_client(dry_run=args.dry_run)
    page_map = load_page_map()

    files: list[Path] = []
    if args.file:
        files.append(Path(args.file))
    if args.all_sops:
        files.extend(sorted(SOPS_DIR.glob("*.md")))
    if args.all_docs:
        files.extend(sorted(GENERATED_DIR.glob("*.md")))

    results = []
    for f in files:
        if f.name == "README.md":
            continue
        page_id = page_map.get(f.name)
        if not page_id:
            print(f"\nWARN  No page ID for '{f.name}' — skipping. Add it to confluence_page_map.json.")
            continue
        result = client.publish(f, page_id)
        results.append(result)

    # Summary
    updated = sum(1 for r in results if r.get("status") == "updated")
    dry = sum(1 for r in results if r.get("status") == "dry_run")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    print(f"\n=== Confluence Publish Summary ===")
    print(f"Files processed : {len(results)}")
    print(f"Updated         : {updated}")
    print(f"Dry run         : {dry}")
    print(f"Skipped         : {skipped}")
    print(f"==================================")


if __name__ == "__main__":
    main()

"""
scripts/doc_agent.py

Auto-documentation agent.
- Detects which .py and .ts files changed in the latest git commit
- Reads each changed file's content
- Calls the local Ollama REST API to generate technical documentation
- Overwrites docs/generated/TECHNICAL_DOCS.md with the result
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
OUTPUT_FILE = os.path.join("docs", "generated", "TECHNICAL_DOCS.md")
SUPPORTED_EXTENSIONS = {".py", ".ts"}


# ---------------------------------------------------------------------------
# Step 1 — Detect changed files
# ---------------------------------------------------------------------------

def get_changed_files() -> list[str]:
    """
    Return a list of source files changed in the latest commit.
    Uses `git diff HEAD~1 HEAD --name-only` to get the diff.
    Falls back to all tracked source files if there is only one commit.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except subprocess.CalledProcessError:
        # Only one commit in history — document all source files instead
        print("Warning: Could not diff HEAD~1. Falling back to all tracked source files.")
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [f.strip() for f in result.stdout.splitlines() if f.strip()]

    # Filter to supported source file extensions only
    filtered = [
        f for f in files
        if os.path.splitext(f)[1] in SUPPORTED_EXTENSIONS and os.path.isfile(f)
    ]

    return filtered


# ---------------------------------------------------------------------------
# Step 2 — Read file content
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    """Read and return the full content of a source file."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Step 3 — Build prompt
# ---------------------------------------------------------------------------

def build_prompt(filename: str, code: str) -> str:
    """
    Build the documentation generation prompt for a single source file.
    """
    return f"""You are a senior technical documentation writer.

Given the following source code file, generate clear and thorough technical documentation in Markdown format.

Your documentation must include:
- **File Purpose** — what this file does and its role in the system
- **Functions / Classes / Hooks** — for each one:
  - Description of what it does
  - Parameters (name, type, description)
  - Return value (type and description)
  - Errors / Exceptions raised (if any)
- **Usage Notes** — any important design decisions, security notes, or usage warnings

File: `{filename}`

```
{code}
```

Write the documentation now in clean Markdown. Do not repeat the source code.
"""


# ---------------------------------------------------------------------------
# Step 4 — Call Ollama API
# ---------------------------------------------------------------------------

def call_ollama(prompt: str) -> str:
    """
    Send the prompt to the local Ollama REST API and return the generated text.

    Raises
    ------
    RuntimeError
        If the Ollama API returns a non-200 status or an error field.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=300)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Could not connect to Ollama at {OLLAMA_URL}. "
            "Make sure Ollama is running: `ollama serve`"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama request timed out after 300 seconds.")

    data = response.json()

    if "error" in data:
        raise RuntimeError(f"Ollama returned an error: {data['error']}")

    return data.get("response", "").strip()


# ---------------------------------------------------------------------------
# Step 5 — Assemble and write the output file
# ---------------------------------------------------------------------------

def write_docs(file_docs: list[tuple[str, str]]) -> None:
    """
    Write the combined documentation for all changed files to TECHNICAL_DOCS.md.

    Parameters
    ----------
    file_docs:
        List of (filename, documentation_markdown) tuples.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    file_list = "\n".join(f"- `{f}`" for f, _ in file_docs)

    header = f"""# Technical Documentation

> **Auto-generated** by the doc agent on every push to `main`. Do not edit manually — changes will be overwritten.

**Last updated:** {now}

**Files documented in this run:**
{file_list}

---

"""

    body = "\n\n---\n\n".join(doc for _, doc in file_docs)
    content = header + body + "\n"

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        fh.write(content)

    print(f"Written: {OUTPUT_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Auto-Doc Agent starting ===")

    # 1. Detect changed files
    changed = get_changed_files()
    if not changed:
        print("No supported source files changed. Nothing to document.")
        sys.exit(0)

    print(f"Changed files to document ({len(changed)}):")
    for f in changed:
        print(f"  {f}")

    # 2. Generate documentation for each file
    file_docs: list[tuple[str, str]] = []
    for filepath in changed:
        print(f"\nGenerating docs for: {filepath}")
        try:
            code = read_file(filepath)
            prompt = build_prompt(filepath, code)
            doc = call_ollama(prompt)
            file_docs.append((filepath, doc))
            print(f"  Done ({len(doc)} chars)")
        except Exception as exc:
            print(f"  ERROR: {exc}")
            file_docs.append((filepath, f"## `{filepath}`\n\n> **Error generating documentation:** {exc}\n"))

    # 3. Write combined output
    write_docs(file_docs)
    print("\n=== Auto-Doc Agent complete ===")


if __name__ == "__main__":
    main()

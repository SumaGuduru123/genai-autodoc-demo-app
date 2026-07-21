"""
scripts/doc_agent.py

Auto-documentation agent.
- Detects which .py and .ts files changed in the latest git commit
- Reads each changed file's content
- Calls the local Ollama REST API to generate technical documentation
- Writes per-file reference docs to docs/generated/<module_name>_reference.md
- Never creates placeholder files; exits silently when no files changed
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
OUTPUT_DIR = os.path.join("docs", "generated")
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

def _module_name(filepath: str) -> str:
    """Derive a module name from a file path (stem of the basename)."""
    return os.path.splitext(os.path.basename(filepath))[0]


def write_doc(filepath: str, doc: str) -> str:
    """
    Write documentation for a single source file to its own reference doc.

    The output path follows the naming convention:
        docs/generated/<module_name>_reference.md

    Parameters
    ----------
    filepath:
        Path to the source file that was documented.
    doc:
        The generated Markdown documentation string.

    Returns
    -------
    str
        The output path that was written.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    module = _module_name(filepath)
    output_path = os.path.join(OUTPUT_DIR, f"{module}_reference.md")

    header = (
        f"> **Auto-generated** by the doc agent. "
        f"Do not edit manually — overwritten on the next pipeline run.  \n"
        f"> **Source:** `{filepath}` · **Last updated:** {now}\n\n---\n\n"
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(header + doc + "\n")

    print(f"Written: {output_path}")
    return output_path


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

    # 2. Generate and write documentation for each file individually
    written: list[str] = []
    for filepath in changed:
        print(f"\nGenerating docs for: {filepath}")
        try:
            code = read_file(filepath)
            prompt = build_prompt(filepath, code)
            doc = call_ollama(prompt)
            out = write_doc(filepath, doc)
            written.append(out)
            print(f"  Done ({len(doc)} chars)")
        except Exception as exc:
            print(f"  ERROR generating docs for {filepath}: {exc}")

    print(f"\n=== Auto-Doc Agent complete — {len(written)} file(s) written ===")


if __name__ == "__main__":
    main()

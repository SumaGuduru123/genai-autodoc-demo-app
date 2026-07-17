# Auto-Doc Agent Plan

## Top-Level Overview

**Goal:** Every time code is pushed to the `main` branch, a GitHub Actions workflow automatically:
1. Detects which source files changed in the commit
2. Reads those files
3. Sends them to a locally running **Ollama** LLM to generate technical documentation
4. Overwrites a single file `docs/generated/TECHNICAL_DOCS.md` with the fresh documentation
5. Commits and pushes that file back to the `main` branch automatically

**Scope:** Covers all source files across `auth_service/` (Python) and `frontend-client/` (TypeScript).

**Out of Scope:** Confluence publishing, SOPs, multi-file output.

**Trigger:** Push to `main` branch only.

**LLM:** Ollama running locally inside the GitHub Actions runner (no API key needed).

**Output:** Single overwritten file — `docs/generated/TECHNICAL_DOCS.md`

---

## Architecture

```
Developer pushes to main
        ↓
GitHub Actions Workflow (.github/workflows/autodoc.yml)
        ↓
Install Ollama + pull model (llama3)
        ↓
scripts/doc_agent.py
  - Detect changed .py and .ts files via git diff
  - Read each changed file's content
  - Build prompt and call Ollama REST API
  - Collect documentation output
        ↓
Overwrite docs/generated/TECHNICAL_DOCS.md
        ↓
git commit + git push back to main
```

---

## Sub-Tasks

---

### Sub-Task 1 — Create the Doc Agent Python Script

**Intent:**
Write `scripts/doc_agent.py` — the core script that detects changed files, reads them, calls Ollama, and writes the output markdown file.

**Expected Outcomes:**
- `scripts/doc_agent.py` exists and is runnable
- It detects changed `.py` and `.ts` files from the latest git commit
- It calls the Ollama REST API (`http://localhost:11434/api/generate`) with each file's content
- It writes the combined documentation output to `docs/generated/TECHNICAL_DOCS.md`
- If no relevant files changed, it exits gracefully with a message

**Todo List:**
1. Create `scripts/` directory
2. Write `scripts/doc_agent.py` with the following sections:
   - Use `git diff HEAD~1 HEAD --name-only` to get list of changed files
   - Filter to only `.py` and `.ts` files
   - For each changed file, read its full content
   - Build a documentation prompt (see prompt template below)
   - POST to Ollama API at `http://localhost:11434/api/generate`
   - Collect all responses and combine into one markdown string
   - Write final markdown to `docs/generated/TECHNICAL_DOCS.md`
   - Add header with timestamp and list of files documented

**Prompt Template:**
```
You are a technical documentation writer.
Given the following source code, generate clear and concise technical documentation in Markdown format.
Include: purpose of the file, list of functions/classes with descriptions, parameters, return values, and error cases.

File: {filename}

```{code}```

Generate the documentation now:
```

**Relevant Context:**
- Changed files are in `auth_service/` (Python) and `frontend-client/` (TypeScript)
- Ollama REST API: `POST http://localhost:11434/api/generate` with body `{"model": "llama3", "prompt": "...", "stream": false}`
- Output file: `docs/generated/TECHNICAL_DOCS.md`

**Status:** [ ] pending

---

### Sub-Task 2 — Create the GitHub Actions Workflow

**Intent:**
Write `.github/workflows/autodoc.yml` — the workflow that triggers on push to `main`, sets up Ollama, runs the doc agent, and commits the result back.

**Expected Outcomes:**
- `.github/workflows/autodoc.yml` exists
- Workflow triggers only on push to `main`
- Ollama is installed and `llama3` model is pulled inside the runner
- `scripts/doc_agent.py` is executed
- If `docs/generated/TECHNICAL_DOCS.md` was updated, it is committed and pushed back to `main` with the message `docs: auto-update TECHNICAL_DOCS.md [skip ci]`
- The `[skip ci]` tag prevents the commit from re-triggering the workflow infinitely

**Todo List:**
1. Create `.github/workflows/` directory
2. Write `autodoc.yml` with these steps:
   - Trigger: `on: push: branches: [main]`
   - Checkout repo with `actions/checkout@v4` (fetch-depth: 2 so git diff works)
   - Set up Python 3.11 with `actions/setup-python@v5`
   - Install agent dependencies: `pip install requests`
   - Install Ollama via shell: `curl -fsSL https://ollama.com/install.sh | sh`
   - Start Ollama server in background: `ollama serve &`
   - Wait for Ollama to be ready (sleep + health check loop)
   - Pull the model: `ollama pull llama3`
   - Run the agent: `python scripts/doc_agent.py`
   - Check if `docs/generated/TECHNICAL_DOCS.md` changed using `git diff --quiet`
   - If changed: configure git user, `git add`, `git commit`, `git push`

**Relevant Context:**
- CODEOWNERS maps `/.github/` to `@genai-autodoc-demo/platform-eng`
- Must use `[skip ci]` in commit message to avoid infinite loop
- `fetch-depth: 2` is required so that `git diff HEAD~1 HEAD` works correctly
- No secrets needed since Ollama is local

**Status:** [ ] pending

---

### Sub-Task 3 — Create the Agent Dependencies File

**Intent:**
Add `scripts/requirements.txt` listing the Python packages needed by `doc_agent.py` so the workflow can install them cleanly.

**Expected Outcomes:**
- `scripts/requirements.txt` exists with only the packages needed by the agent script
- `requests` library is listed (used to call Ollama REST API)

**Todo List:**
1. Create `scripts/requirements.txt` with:
   - `requests>=2.31.0`

**Relevant Context:**
- The main `requirements.txt` at the root is for the FastAPI app — do not modify it
- The agent only needs `requests` since Ollama has a simple REST API

**Status:** [ ] pending

---

### Sub-Task 4 — Update docs/generated/TECHNICAL_DOCS.md Placeholder

**Intent:**
Replace the existing `docs/generated/README.md` placeholder with a proper `TECHNICAL_DOCS.md` starter file so the directory is ready and the output location is clear.

**Expected Outcomes:**
- `docs/generated/TECHNICAL_DOCS.md` exists with a placeholder message
- The file explains it is auto-generated and will be overwritten on every push to main

**Todo List:**
1. Create `docs/generated/TECHNICAL_DOCS.md` with a placeholder header:
   - Title: `# Technical Documentation`
   - Note: `This file is auto-generated by the doc agent on every push to main. Do not edit manually.`
   - Last updated: `Not yet generated.`

**Relevant Context:**
- `docs/generated/README.md` already exists and describes the pipeline intent — leave it as-is
- The agent script in Sub-Task 1 targets `docs/generated/TECHNICAL_DOCS.md` specifically

**Status:** [ ] pending

---

### Sub-Task 5 — End-to-End Verification

**Intent:**
Verify the full pipeline works by making a small code change, pushing to main, and confirming the documentation file is updated.

**Expected Outcomes:**
- GitHub Actions workflow runs successfully on push to main
- `docs/generated/TECHNICAL_DOCS.md` is updated with new content
- A new commit appears in the repo history with message `docs: auto-update TECHNICAL_DOCS.md [skip ci]`
- The documentation content accurately reflects the changed source files

**Todo List:**
1. Make a trivial change to any source file (e.g. add a comment to `auth_service/auth.py`)
2. Commit and push to `main`
3. Go to GitHub → Actions tab → watch the workflow run
4. Once complete, check `docs/generated/TECHNICAL_DOCS.md` for updated content
5. Verify the commit history shows the auto-generated docs commit

**Relevant Context:**
- GitHub Actions tab: `https://github.com/SumaGuduru123/genai-autodoc-demo-app/actions`
- Output file: `docs/generated/TECHNICAL_DOCS.md`
- The workflow installs Ollama fresh each run — first run will be slow due to model download

**Status:** [ ] pending

---

## GitHub Secrets Required

None — Ollama runs locally inside the GitHub Actions runner. No API keys needed.

## Files to be Created

| File | Purpose |
|---|---|
| `.github/workflows/autodoc.yml` | GitHub Actions workflow |
| `scripts/doc_agent.py` | Core doc generation agent |
| `scripts/requirements.txt` | Agent Python dependencies |
| `docs/generated/TECHNICAL_DOCS.md` | Output documentation file (placeholder) |

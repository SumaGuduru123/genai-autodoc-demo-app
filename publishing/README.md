# publishing/

This module pushes generated documentation to Atlassian Confluence Cloud.

## Files

| File | Purpose |
|---|---|
| `confluence.py` | Publisher script — reads Markdown, converts to Confluence storage format, calls REST API |
| `confluence_page_map.json` | Maps each doc filename to its Confluence page ID |

## Setup (one-time)

### 1. Get a Confluence API Token
1. Go to [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token** → give it a name (e.g. `genai-autodoc`)
3. Copy the token — you won't see it again

### 2. Set environment variables
Copy `.env.example` to `.env` and fill in:
```
CONFLUENCE_URL=https://yourorg.atlassian.net
CONFLUENCE_EMAIL=your.email@yourorg.com
CONFLUENCE_TOKEN=<paste token here>
CONFLUENCE_SPACE_KEY=GENAI
```

### 3. Create Confluence pages (first time only)
For each doc, create a blank Confluence page manually in your space, then get its page ID:
1. Open the page in the browser
2. Go to **... menu → Page Information**
3. Copy the ID from the URL: `/pages/<ID>/info`
4. Add it to `confluence_page_map.json`

Or use the publisher to create pages automatically:
```bash
python publishing/confluence.py --create-page \
  --title "Auth Incident Runbook" \
  --file docs/sops/auth_incident_runbook.md
```

### 4. Run the publisher

**Dry run (no API calls — validate config):**
```bash
python publishing/confluence.py --dry-run --all-sops
```

**Publish all SOPs:**
```bash
python publishing/confluence.py --all-sops
```

**Publish all reference docs:**
```bash
python publishing/confluence.py --all-docs
```

**Publish a single file:**
```bash
python publishing/confluence.py --file docs/sops/auth_incident_runbook.md
```

## How it works

1. Reads the Markdown file from `docs/sops/` or `docs/generated/`
2. Converts Markdown → Confluence Storage Format (XHTML)
3. Fetches the current page version number from the API
4. PUTs the new content at version + 1
5. Appends a change-history row at the bottom of the page

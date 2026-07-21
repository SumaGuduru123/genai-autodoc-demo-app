# docs/sops/

This directory is managed by the **Gen AI Auto-Doc pipeline** (Track B — Support SOPs).

Plain-English runbooks and incident procedures are generated here from
code error handlers, deployment configs, and `CODEOWNERS` data.
Canonical copies are also pushed to the Confluence space
**`~GENAI_AUTODOC/SOPs`** after every pipeline run.

## Contents

| File | Description |
|---|---|
| `auth_incident_runbook.md` | Auth service — symptom/diagnosis/escalation guide |
| `user_management_runbook.md` | User CRUD errors — troubleshooting & rollback steps |
| `user_sop.md` | User management error handler — detailed decision-table SOP |
| `deployment_runbook.md` | Deployment procedures derived from `Dockerfile` changes |

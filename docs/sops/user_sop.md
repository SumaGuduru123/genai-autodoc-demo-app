> **Auto-generated** by the DocumentationAgent. Do not edit manually — overwritten on the next pipeline run.  
> **Source:** `auth_service/user.py` · **Last updated:** 2025-07-15

---

# Support SOP — `auth_service/user.py` Error Handling

## When to Use This SOP

Use this SOP when an incident, alert, or support ticket involves one of the following error codes raised by the user account service:

- `USER_NOT_FOUND` (HTTP 404)
- `DUPLICATE_EMAIL` (HTTP 409)
- `VALIDATION_ERROR` (HTTP 422)
- `PERMISSION_DENIED` (HTTP 403)
- `INTERNAL_USER_ERROR` (HTTP 500)

This SOP applies to on-call engineers, Tier-2 support agents, and security reviewers triaging issues in the `auth_service` module.

---

## Prerequisites

Before starting this procedure, confirm the following:

- You have read access to application logs (structured JSON logs from the `auth_service.user` logger).
- You have access to the in-memory user store or its database replacement in your environment.
- You know the `user_id` or email address associated with the failing request.
- You have confirmed the environment (dev / staging / production) where the error occurred.
- You have reviewed the HTTP response body for the `error_code` field to identify which exception class was raised.

---

## Step-by-Step Procedure

1. **Identify the error code** from the HTTP response body (`error_code` field) or from the structured log entry.
2. **Locate the log entry** — search for the `user_id` or email in the application log. Look for entries from the `auth_service.user` logger at `ERROR` or `INFO` level.
3. **Match the error code to the Decision Points section below** to determine the correct resolution path.
4. **Reproduce the failure** in a non-production environment using the same input payload (username, email, roles) if the error is a `VALIDATION_ERROR` or `DUPLICATE_EMAIL`.
5. **Apply the appropriate remediation** as described in the Decision Points section.
6. **Verify resolution** by re-submitting the request and confirming a `2xx` response or the expected record state.
7. **Document the incident** — record the `user_id`, error code, root cause, and remediation steps in the incident tracker.
8. **Escalate if unresolved** — follow the Escalation Path below if the error persists after remediation attempts.

---

## Decision Points

### `UserNotFoundError` — HTTP 404 · `USER_NOT_FOUND`
**Trigger:** A `get_user`, `update_user`, or `delete_user` call was made with a `user_id` that does not exist in the store.

**Resolution:**
- Verify the `user_id` is correct and was not truncated or misformatted by the caller.
- Check whether the user was previously soft-deleted (`is_active = False`). Soft-deleted records are retained in the store but may be excluded by `list_users(active_only=True)`.
- If the user genuinely does not exist, the caller should create a new account via `create_user`.
- If the record should exist but is missing, escalate to the data integrity runbook.

---

### `DuplicateEmailError` — HTTP 409 · `DUPLICATE_EMAIL`
**Trigger:** A `create_user` or `update_user` call attempted to register or assign an email address that is already mapped in `_email_index`.

**Resolution:**
- Confirm whether the existing registration was intentional by looking up the email in the index.
- If the user already has an account, direct the caller to log in or to use `get_user` to retrieve the existing record.
- If the existing record is stale (soft-deleted and abandoned), an admin may need to purge the `_email_index` entry. Escalate to an admin or follow the data-cleanup runbook.
- Never silently re-use an existing account without explicit user consent.

---

### `ValidationError` — HTTP 422 · `VALIDATION_ERROR`
**Trigger:** Raised by `_validate_email`, `_validate_username`, or `_validate_roles` when the supplied value fails the applicable constraint.

**Username rules:** 3–64 characters, only letters (`a–z`, `A–Z`), digits (`0–9`), underscores (`_`), and hyphens (`-`). Pattern: `^[a-zA-Z0-9_\-]{3,64}$`.

**Email rules:** Must match RFC-5322-style pattern `^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$`.

**Role rules:** Must be one of `viewer`, `developer`, `support`, `ops`, `admin`.

**Resolution:**
- Return the `ValidationError` message to the caller — it contains the specific constraint that failed.
- If the caller is a UI, surface the message directly to the end user for correction.
- If the caller is a service, update the request payload to conform to the constraints above and retry.

---

### `PermissionDeniedError` — HTTP 403 · `PERMISSION_DENIED`
**Trigger:** A `update_user` call attempted to change `roles`, or a `delete_user` call was made, by a caller whose `caller_roles` did not include `"admin"`.

**Resolution:**
- Confirm the authenticated user's current role list via `get_user`.
- If the caller should have admin rights, an existing admin must call `update_user` with `roles=["admin"]` and `caller_roles=["admin"]` to elevate the user.
- If the permission denial is unexpected, review the authentication token for correct role claims.
- Do not bypass the role check — role enforcement is intentional and must not be disabled.

---

### `InternalUserError` — HTTP 500 · `INTERNAL_USER_ERROR`
**Trigger:** An unexpected exception was caught by the generic `except Exception` handler inside `create_user` or `update_user`. This error wraps the original cause and logs it at `ERROR` level.

**Resolution:**
1. Locate the full stack trace in the application log — the `InternalUserError` is always raised with `from exc`, so the original exception is attached.
2. Inspect the root cause (`exc.__cause__`) from the log entry.
3. If the root cause is an infrastructure failure (e.g., DB connection, OOM), follow the infrastructure runbook.
4. If the root cause is a logic error or unexpected data state, escalate immediately to `@genai-autodoc-demo/security-identity` and `@alice`.
5. Do not retry automatically on `500` without understanding the root cause — retrying a broken state may corrupt records.

---

## Escalation Path

| Situation | Escalate To |
|---|---|
| `InternalUserError` with unknown root cause | `@genai-autodoc-demo/security-identity`, `@alice` |
| Data integrity issue (missing records, corrupt index) | `@genai-autodoc-demo/security-identity`, `@alice` |
| Repeated `PermissionDeniedError` for a legitimate admin | `@genai-autodoc-demo/security-identity` |
| Any error in production that cannot be resolved within 30 minutes | `@genai-autodoc-demo/security-identity`, `@alice` |

**Primary owner:** `@genai-autodoc-demo/security-identity`  
**Secondary owner / on-call contact:** `@alice`

---

## Rollback Procedure

The current implementation uses an **in-memory store** (`_users` dict and `_email_index` dict). There is no persistent transaction log. In the event of a bad write:

1. **If the service process is still running:** An admin must manually call `delete_user` (soft-delete) to deactivate any incorrectly created accounts, then update the record or create a corrected one.
2. **If the service was restarted:** All in-memory state is lost. Restore from the last known-good seed data or database backup if a persistent store has been configured.
3. **For a DB-backed deployment:** Follow the database rollback runbook for your environment. The `user_id` and `email` columns have unique constraints — a failed transaction will automatically roll back without data corruption.
4. **Revert a code change:** If a deployment introduced a regression, revert the commit via `git revert` and redeploy. The `[skip ci]` flag on doc commits ensures doc-only commits do not re-trigger the pipeline.

---

## Document Info

| Field | Value |
|---|---|
| **Owner** | `@genai-autodoc-demo/security-identity` |
| **On-call Contact** | `@alice` |
| **Source File** | `auth_service/user.py` |
| **Last Updated** | 2025-07-15 |
| **Generated By** | DocumentationAgent (auto-generated — do not edit manually) |

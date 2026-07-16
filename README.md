# genai-autodoc-demo-app

> **Demo repository** for the *Gen AI Auto-Doc & SOP Generator* — Watsonx Challenge proof-of-concept.

This repository contains a minimal but realistic Python + TypeScript application used as the **input target** for the AI-powered documentation and SOP generation pipeline.

---

## Repository Structure

```
genai-autodoc-demo-app/
├── auth_service/               # Python — Authentication & user management
│   ├── __init__.py
│   ├── auth.py                 # login · logout · token refresh · verify
│   └── user.py                 # CRUD operations with validation & RBAC
├── frontend-client/            # TypeScript — React frontend helpers
│   ├── apiClient.ts            # Base HTTP client with error mapping
│   └── authHooks.ts            # useAuth · useRequireRole React hooks
├── docs/
│   ├── generated/              # Auto-generated developer reference docs (via Draft PR)
│   └── sops/                   # Auto-generated support runbooks (also pushed to Confluence)
├── Dockerfile                  # Multi-stage build (Red Hat UBI 9, non-root)
├── CODEOWNERS                  # Maps paths → owners for SOP escalation fields
├── requirements.txt            # Python dependencies
├── package.json                # Node / TypeScript project config
└── tsconfig.json               # TypeScript compiler config
```

---

## Modules

### `auth_service/auth.py`

Handles the full authentication lifecycle.

| Function | Description |
|---|---|
| `login(username, password)` | Validates credentials, returns `TokenPair` |
| `logout(refresh_token)` | Revokes the refresh token, ends session |
| `refresh_access_token(refresh_token)` | Rotates tokens; old refresh token is revoked |
| `verify_access_token(token)` | Validates signature + TTL, returns `UserSession` |

**Error codes surfaced to SOP generator:**

| Code | HTTP | Trigger |
|---|---|---|
| `INVALID_CREDENTIALS` | 401 | Wrong username or password |
| `TOKEN_EXPIRED` | 401 | Access/refresh token TTL exceeded |
| `TOKEN_INVALID` | 401 | Bad signature or malformed structure |
| `ACCOUNT_LOCKED` | 403 | Too many failed login attempts |
| `INTERNAL_AUTH_ERROR` | 500 | Unexpected server-side failure |

---

### `auth_service/user.py`

CRUD operations for user accounts with role-based access control.

| Function | Description |
|---|---|
| `create_user(username, email, roles)` | Register a new account |
| `get_user(user_id)` | Fetch a single user by ID |
| `update_user(user_id, *, email, roles, caller_roles)` | Mutate email or roles |
| `delete_user(user_id, *, caller_roles)` | Soft-delete (admin only) |
| `list_users(*, active_only)` | List all (or active) users |

---

### `frontend-client/apiClient.ts`

Base HTTP client wrapping `fetch`.

| Export | Description |
|---|---|
| `request<T>(method, path, body, options)` | Core request with timeout + error mapping |
| `get / post / put / patch / del` | Convenience wrappers |
| `setAccessToken(token)` | Store token in memory |
| `clearAccessToken()` | Wipe stored token (called on logout) |
| `ApiError` | Typed error with `status`, `errorCode`, `detail` |
| `NetworkTimeoutError` | Thrown when request exceeds `timeoutMs` |
| `NetworkUnavailableError` | Thrown on network-level failures |

---

### `frontend-client/authHooks.ts`

React hooks for authentication state.

| Hook | Description |
|---|---|
| `useAuth()` | Full login/logout lifecycle + silent refresh on mount |
| `useRequireRole(role, user, navigate)` | Redirect if user lacks the required role |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET` | ✅ | — | HMAC-SHA256 signing key (min 32 chars) |
| `TOKEN_TTL` | ❌ | `3600` | Access token lifetime (seconds) |
| `REFRESH_TTL` | ❌ | `86400` | Refresh token lifetime (seconds) |
| `MAX_LOGIN_ATTEMPTS` | ❌ | `5` | Lockout threshold per username |
| `REACT_APP_API_BASE_URL` | ❌ | `http://127.0.0.1:8000` | Backend URL for the frontend client |

---

## Running Locally

```bash
# Backend (Python)
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
pip install -r requirements.txt
uvicorn auth_service.main:app --host 127.0.0.1 --port 8000

# Frontend (TypeScript)
npm install
npm run build
```

### Docker

```bash
docker build -t genai-autodoc-demo-app:latest .
docker run --rm \
  -e JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  -p 127.0.0.1:8000:8000 \
  genai-autodoc-demo-app:latest
```

---

## Contributing

See [CODEOWNERS](./CODEOWNERS) for team ownership and escalation paths.

All dependency changes require a security review from `@genai-autodoc-demo/security-identity` before merge.

---

## License

Apache 2.0 — see [LICENSE](./LICENSE).

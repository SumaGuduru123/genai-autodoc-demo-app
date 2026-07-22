> **Auto-generated** by the DocumentationAgent. Do not edit manually — overwritten on the next pipeline run.  
> **Source:** `auth_service/user.py` · **Last updated:** 2025-07-15

---

# `user` Module Reference

## Module Overview

`auth_service/user.py` provides the full CRUD lifecycle for user accounts in the auth service, covering creation, retrieval, update, soft-deletion, and listing. It validates all inputs against typed rules and raises a structured exception hierarchy whose `http_status` attributes map directly to HTTP response codes, making it suitable for use behind any REST framework without additional error-mapping logic.

## Functions

### Exception Classes

#### `UserError(message, *, detail=None)`
Base class for all user-management errors. Sets `http_status = 500` and `error_code = "USER_ERROR"`. All subclasses inherit the optional `detail` attribute for machine-readable context.

#### `UserNotFoundError`
Raised when a `user_id` does not exist in the store. `http_status = 404`, `error_code = "USER_NOT_FOUND"`.

#### `DuplicateEmailError`
Raised when attempting to register an email address that is already in use. `http_status = 409`, `error_code = "DUPLICATE_EMAIL"`.

#### `ValidationError`
Raised for malformed input (username, email, or roles). `http_status = 422`, `error_code = "VALIDATION_ERROR"`.

#### `PermissionDeniedError`
Raised when the caller lacks the required role. `http_status = 403`, `error_code = "PERMISSION_DENIED"`.

#### `InternalUserError`
Raised for unexpected server-side failures not covered by a more specific error class. `http_status = 500`, `error_code = "INTERNAL_USER_ERROR"`.

---

### Data Model

#### `UserRecord`
A `@dataclass` representing a persisted user account.

| Field | Type | Default | Description |
|---|---|---|---|
| `user_id` | `str` | — | UUID assigned at creation |
| `username` | `str` | — | Display name |
| `email` | `str` | — | Unique email address |
| `roles` | `list[str]` | `["viewer"]` | Assigned roles |
| `created_at` | `int` | `time.time()` | Unix timestamp of creation |
| `updated_at` | `int` | `time.time()` | Unix timestamp of last update |
| `is_active` | `bool` | `True` | Soft-delete flag; `False` means deactivated |

---

### Private Validation Helpers

#### `_validate_email(email: str) -> None`
Validates `email` against the compiled regex `_EMAIL_RE` (`^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$`). Raises `ValidationError` (HTTP 422) if the address is malformed.

#### `_validate_username(username: str) -> None`
Validates `username` against `_USERNAME_RE` (`^[a-zA-Z0-9_\-]{3,64}$`). Raises `ValidationError` (HTTP 422) if the username does not meet the 3–64 character alphanumeric/underscore/hyphen constraint.

#### `_validate_no_special_characters(value: str, field_name: str = "value") -> None`
Checks `value` against `_SPECIAL_CHAR_RE` (`[!@#$%^&*(),.?\":{}|<>]`). Raises `ValidationError` (HTTP 422) if any of the listed special characters are found.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `value` | `str` | — | The string to inspect |
| `field_name` | `str` | `"value"` | Human-readable field label used in the error message |

**Raises:**
- `ValidationError` (422) — `"{field_name} must not contain special characters: '{value}'"` when a match is found.

**Usage note:** This helper is generic — it can be applied to any string field (username, display name, etc.) by passing the appropriate `field_name` label.

#### `_validate_roles(roles: list[str]) -> None`
Checks every entry in `roles` against `_ALLOWED_ROLES` (`viewer`, `developer`, `support`, `ops`, `admin`). Raises `ValidationError` (HTTP 422) listing any unknown role strings.

---

### Public CRUD API

#### `create_user(username, email, roles=None) -> UserRecord`
Creates a new user account and stores it in the in-memory store.

| Parameter | Type | Description |
|---|---|---|
| `username` | `str` | Unique display name (3–64 chars: letters, digits, `_`, `-`) |
| `email` | `str` | Valid RFC-5322 email address; must be unique across all accounts |
| `roles` | `list[str] \| None` | Role list; defaults to `["viewer"]` |

**Returns:** `UserRecord` — the newly created record including the generated `user_id`.

**Raises:**
- `ValidationError` (422) — username, email, or roles failed validation
- `DuplicateEmailError` (409) — email is already registered
- `InternalUserError` (500) — unexpected failure during record creation

---

#### `get_user(user_id: str) -> UserRecord`
Retrieves a single user by UUID.

| Parameter | Type | Description |
|---|---|---|
| `user_id` | `str` | UUID string of the target user |

**Returns:** `UserRecord` — the matching record.

**Raises:**
- `UserNotFoundError` (404) — no user with that ID exists

---

#### `update_user(user_id, *, email=None, roles=None, caller_roles=None) -> UserRecord`
Updates mutable fields (`email`, `roles`) on an existing record. Role updates require the caller to hold the `admin` role.

| Parameter | Type | Description |
|---|---|---|
| `user_id` | `str` | UUID of the user to update |
| `email` | `str \| None` | New email address (optional); must pass uniqueness check |
| `roles` | `list[str] \| None` | New role list (optional); caller must have `admin` role |
| `caller_roles` | `list[str] \| None` | Roles of the authenticated caller |

**Returns:** `UserRecord` — the updated record.

**Raises:**
- `UserNotFoundError` (404) — user does not exist
- `ValidationError` (422) — new email or roles are invalid
- `DuplicateEmailError` (409) — new email is already in use by another account
- `PermissionDeniedError` (403) — caller lacks `admin` role for role updates
- `InternalUserError` (500) — unexpected failure during update

---

#### `delete_user(user_id, *, caller_roles=None) -> None`
Soft-deletes a user by setting `is_active = False`. Only callers with the `admin` role may invoke this function.

| Parameter | Type | Description |
|---|---|---|
| `user_id` | `str` | UUID of the user to deactivate |
| `caller_roles` | `list[str] \| None` | Roles of the authenticated caller |

**Returns:** `None`

**Raises:**
- `PermissionDeniedError` (403) — caller is not an admin
- `UserNotFoundError` (404) — user does not exist

---

#### `list_users(*, active_only=True) -> list[UserRecord]`
Returns all user records, optionally filtered to active accounts only.

| Parameter | Type | Description |
|---|---|---|
| `active_only` | `bool` | When `True` (default), excludes deactivated accounts |

**Returns:** `list[UserRecord]` — records sorted by `created_at` ascending.

---

## Dependencies

| Import | Purpose |
|---|---|
| `logging` | Structured event logging via module-level `logger` |
| `re` | Compiled regex patterns: `_EMAIL_RE`, `_USERNAME_RE`, `_SPECIAL_CHAR_RE` |
| `time` | Unix timestamps for `created_at` and `updated_at` fields |
| `dataclasses` | `@dataclass` and `field` for the `UserRecord` model |
| `typing.Optional` | Type annotations for optional parameters |
| `uuid.uuid4` | UUID generation for `user_id` at account creation |

## Usage Example

```python
from auth_service.user import (
    create_user, get_user, update_user, delete_user, list_users,
    ValidationError, DuplicateEmailError, PermissionDeniedError,
    _validate_no_special_characters,
)

# Validate a field before passing it to create_user
try:
    _validate_no_special_characters("alice<dev>", field_name="username")
except ValidationError as e:
    print(e)  # username must not contain special characters: 'alice<dev>'

# Create a new user (defaults to "viewer" role)
user = create_user("alice_dev", "alice@example.com", roles=["developer"])
print(user.user_id)  # e.g. "3f2a1b4c-..."

# Retrieve by ID
record = get_user(user.user_id)

# Update email
updated = update_user(user.user_id, email="alice2@example.com")

# Promote to admin (requires admin caller)
update_user(user.user_id, roles=["admin"], caller_roles=["admin"])

# Soft-delete (admin only)
delete_user(user.user_id, caller_roles=["admin"])

# List active users
active = list_users(active_only=True)
```

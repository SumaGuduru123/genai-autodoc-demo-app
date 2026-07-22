"""
auth_service/user.py

CRUD operations for user accounts.
Validates all inputs and raises typed errors that map to HTTP status codes.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------

class UserError(Exception):
    """Base class for user-management errors."""
    http_status: int = 500
    error_code: str = "USER_ERROR"

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


class UserNotFoundError(UserError):
    """Raised when a user_id does not exist in the store (404)."""
    http_status = 404
    error_code = "USER_NOT_FOUND"


class DuplicateEmailError(UserError):
    """Raised when attempting to register an already-used email (409)."""
    http_status = 409
    error_code = "DUPLICATE_EMAIL"


class ValidationError(UserError):
    """Raised for malformed input data (422)."""
    http_status = 422
    error_code = "VALIDATION_ERROR"


class PermissionDeniedError(UserError):
    """Raised when the caller lacks the required role (403)."""
    http_status = 403
    error_code = "PERMISSION_DENIED"


class InternalUserError(UserError):
    """Raised for unexpected server-side failures (500)."""
    http_status = 500
    error_code = "INTERNAL_USER_ERROR"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class UserRecord:
    user_id: str
    username: str
    email: str
    roles: list[str] = field(default_factory=lambda: ["viewer"])
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    is_active: bool = True


# ---------------------------------------------------------------------------
# In-memory store (replace with DB in production)
# ---------------------------------------------------------------------------

_users: dict[str, UserRecord] = {}
_email_index: dict[str, str] = {}  # email → user_id


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{3,64}$")
_ALLOWED_ROLES = {"viewer", "developer", "support", "ops", "admin"}


def _validate_email(email: str) -> None:
    if not _EMAIL_RE.match(email):
        raise ValidationError(f"Invalid email address: '{email}'")


def _validate_username(username: str) -> None:
    if not _USERNAME_RE.match(username):
        raise ValidationError(
            f"Username '{username}' must be 3–64 characters: letters, digits, _ or -"
        )


_SPECIAL_CHAR_RE = re.compile(r"[!@#$%^&*(),.?\":{}|<>]")


def _validate_no_special_characters(value: str, field_name: str = "value") -> None:
    """Raise ValidationError if *value* contains any special characters."""
    if _SPECIAL_CHAR_RE.search(value):
        raise ValidationError(
            f"{field_name} must not contain special characters: '{value}'"
        )


def _validate_roles(roles: list[str]) -> None:
    unknown = set(roles) - _ALLOWED_ROLES
    if unknown:
        raise ValidationError(f"Unknown role(s): {unknown}. Allowed: {_ALLOWED_ROLES}")


# ---------------------------------------------------------------------------
# Public CRUD API
# ---------------------------------------------------------------------------

def create_user(username: str, email: str, roles: Optional[list[str]] = None) -> UserRecord:
    """
    Create a new user account.

    Parameters
    ----------
    username:
        Unique display name (3–64 alphanumeric chars, underscores, hyphens).
    email:
        Valid RFC-5322 email address. Must be unique across all accounts.
    roles:
        Optional list of role strings. Defaults to ``["viewer"]``.
        Allowed values: ``viewer``, ``developer``, ``support``, ``ops``, ``admin``.

    Returns
    -------
    UserRecord
        The newly created user record, including the generated ``user_id``.

    Raises
    ------
    ValidationError
        HTTP 422 — username, email, or roles failed validation.
    DuplicateEmailError
        HTTP 409 — the email is already registered.
    InternalUserError
        HTTP 500 — unexpected failure during record creation.
    """
    roles = roles or ["viewer"]
    _validate_username(username)
    _validate_email(email)
    _validate_roles(roles)

    if email in _email_index:
        raise DuplicateEmailError(f"Email '{email}' is already registered")

    try:
        user_id = str(uuid4())
        record = UserRecord(user_id=user_id, username=username, email=email, roles=roles)
        _users[user_id] = record
        _email_index[email] = user_id
        logger.info("User created", extra={"user_id": user_id, "username": username})
        return record
    except (DuplicateEmailError, ValidationError):
        raise
    except Exception as exc:
        logger.error("Failed to create user", exc_info=True)
        raise InternalUserError("User creation failed due to an internal error") from exc


def get_user(user_id: str) -> UserRecord:
    """
    Retrieve a single user by *user_id*.

    Parameters
    ----------
    user_id:
        UUID string of the target user.

    Returns
    -------
    UserRecord
        The matching user record.

    Raises
    ------
    UserNotFoundError
        HTTP 404 — no user with the given ID exists.
    """
    record = _users.get(user_id)
    if record is None:
        raise UserNotFoundError(f"User '{user_id}' not found")
    return record


def update_user(
    user_id: str,
    *,
    email: Optional[str] = None,
    roles: Optional[list[str]] = None,
    caller_roles: Optional[list[str]] = None,
) -> UserRecord:
    """
    Update mutable fields on an existing user record.

    Parameters
    ----------
    user_id:
        UUID of the user to update.
    email:
        New email address (optional). Must pass uniqueness check.
    roles:
        New role list (optional). Only callers with ``admin`` role may change roles.
    caller_roles:
        Roles of the authenticated caller, used for permission enforcement.

    Returns
    -------
    UserRecord
        The updated user record.

    Raises
    ------
    UserNotFoundError
        HTTP 404 — user does not exist.
    ValidationError
        HTTP 422 — new email or roles are invalid.
    DuplicateEmailError
        HTTP 409 — new email is already in use by another account.
    PermissionDeniedError
        HTTP 403 — caller lacks the ``admin`` role required for role updates.
    InternalUserError
        HTTP 500 — unexpected failure during update.
    """
    record = get_user(user_id)

    if roles is not None:
        if not caller_roles or "admin" not in caller_roles:
            raise PermissionDeniedError("Only admins may update user roles")
        _validate_roles(roles)
        record.roles = roles

    if email is not None:
        _validate_email(email)
        if email != record.email:
            if email in _email_index:
                raise DuplicateEmailError(f"Email '{email}' is already in use")
            _email_index.pop(record.email, None)
            _email_index[email] = user_id
            record.email = email

    try:
        record.updated_at = int(time.time())
        logger.info("User updated", extra={"user_id": user_id})
        return record
    except Exception as exc:
        logger.error("Failed to update user", exc_info=True)
        raise InternalUserError("User update failed due to an internal error") from exc


def delete_user(user_id: str, *, caller_roles: Optional[list[str]] = None) -> None:
    """
    Soft-delete a user by setting ``is_active = False``.

    Parameters
    ----------
    user_id:
        UUID of the user to deactivate.
    caller_roles:
        Roles of the authenticated caller. Only ``admin`` may delete users.

    Raises
    ------
    PermissionDeniedError
        HTTP 403 — caller is not an admin.
    UserNotFoundError
        HTTP 404 — user does not exist.
    """
    if not caller_roles or "admin" not in caller_roles:
        raise PermissionDeniedError("Only admins may delete users")
    record = get_user(user_id)
    record.is_active = False
    record.updated_at = int(time.time())
    logger.info("User deactivated", extra={"user_id": user_id})


def list_users(*, active_only: bool = True) -> list[UserRecord]:
    """
    Return all user records, optionally filtered to active accounts only.

    Parameters
    ----------
    active_only:
        When ``True`` (default), exclude deactivated accounts.

    Returns
    -------
    list[UserRecord]
        Matching user records sorted by ``created_at`` ascending.
    """
    records = list(_users.values())
    if active_only:
        records = [r for r in records if r.is_active]
    return sorted(records, key=lambda r: r.created_at)

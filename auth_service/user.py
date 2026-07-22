"""
auth_service/user.py

CRUD operations for user accounts.
Validates all inputs and raises typed errors that map to HTTP status codes.

LDAP authentication
-------------------
Set the following environment variables to enable LDAP credential checks via
:func:`verify_user_credentials`:

    LDAP_URL              ldap(s):// URI  e.g. ``ldaps://ldap.example.com:636``
    LDAP_BASE_DN          Search base    e.g. ``dc=example,dc=com``
    LDAP_BIND_DN          Service-account DN for the initial search bind
    LDAP_BIND_PASSWORD    Service-account password
    LDAP_USER_ATTR        Attribute matched against *username* (default: ``sAMAccountName``)
    LDAP_ROLE_ATTR        Multi-valued group attribute       (default: ``memberOf``)
    LDAP_UID_ATTR         Stable user-id attribute           (default: ``entryUUID``)
    LDAP_CONN_TIMEOUT     TCP timeout in seconds             (default: ``5``)
    LDAP_USE_TLS          Set to ``1`` to STARTTLS a plain ldap:// connection

When LDAP_URL is **not** set the function falls back to the local in-memory
credential store so development/testing works without an LDAP server.
"""

from __future__ import annotations

import hmac
import logging
import os
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


class LDAPAuthError(UserError):
    """Raised when LDAP credential verification fails (401)."""
    http_status = 401
    error_code = "LDAP_AUTH_ERROR"


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
# LDAP configuration (read once at import time; all optional)
# ---------------------------------------------------------------------------

_LDAP_URL: str = os.environ.get("LDAP_URL", "")
_LDAP_BASE_DN: str = os.environ.get("LDAP_BASE_DN", "")
_LDAP_BIND_DN: str = os.environ.get("LDAP_BIND_DN", "")
_LDAP_BIND_PASSWORD: str = os.environ.get("LDAP_BIND_PASSWORD", "")
_LDAP_USER_ATTR: str = os.environ.get("LDAP_USER_ATTR", "sAMAccountName")
_LDAP_ROLE_ATTR: str = os.environ.get("LDAP_ROLE_ATTR", "memberOf")
_LDAP_UID_ATTR: str = os.environ.get("LDAP_UID_ATTR", "entryUUID")
_LDAP_CONN_TIMEOUT: int = int(os.environ.get("LDAP_CONN_TIMEOUT", "5"))
_LDAP_USE_TLS: bool = os.environ.get("LDAP_USE_TLS", "0") == "1"


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
# LDAP helpers
# ---------------------------------------------------------------------------

def _ldap_extract_roles(entry_attributes: dict) -> list[str]:
    """
    Parse role names from the multi-valued ``memberOf`` (or ``LDAP_ROLE_ATTR``)
    attribute.

    Each value is a full DN such as
    ``CN=developers,OU=groups,DC=example,DC=com``.
    Only the ``CN`` segment is kept and lower-cased so it can be compared
    against :data:`_ALLOWED_ROLES`.  Unknown role names are silently dropped;
    if nothing matches ``["viewer"]`` is returned as the safe default.
    """
    raw: list[str] = entry_attributes.get(_LDAP_ROLE_ATTR) or []
    roles: list[str] = []
    for dn_value in raw:
        for part in str(dn_value).split(","):
            part = part.strip()
            if part.lower().startswith("cn="):
                candidate = part[3:].lower()
                if candidate in _ALLOWED_ROLES:
                    roles.append(candidate)
                break
    return roles if roles else ["viewer"]


def ldap_authenticate(username: str, password: str) -> UserRecord:
    """
    Verify *username* / *password* against the configured LDAP directory and
    return a :class:`UserRecord` hydrated from the directory entry.

    Workflow
    --------
    1. Bind with the service account (``LDAP_BIND_DN``) to search for the
       user's DN using the filter ``(<LDAP_USER_ATTR>=<username>)``.
    2. Attempt a second bind with the found DN and the supplied *password* to
       verify the credential.
    3. Extract ``LDAP_UID_ATTR`` and ``LDAP_ROLE_ATTR`` from the entry and
       return a synthetic :class:`UserRecord`.

    Parameters
    ----------
    username:
        Login name matched against ``LDAP_USER_ATTR`` in the directory.
    password:
        Plain-text password (must be sent over TLS/LDAPS).

    Returns
    -------
    UserRecord
        Populated with ``user_id``, ``username``, ``email`` (empty string when
        not present in the directory), and ``roles`` extracted from
        ``LDAP_ROLE_ATTR``.

    Raises
    ------
    LDAPAuthError
        HTTP 401 — user not found in the directory or password is wrong.
    PermissionDeniedError
        HTTP 403 — the directory reports the account is disabled or locked.
    InternalUserError
        HTTP 500 — LDAP connectivity or unexpected protocol error.
    """
    try:
        import ldap3  # optional dependency — only required when LDAP is used
        from ldap3.core.exceptions import LDAPBindError, LDAPException
        from ldap3.utils.conv import escape_filter_chars
    except ImportError as exc:
        raise InternalUserError(
            "ldap3 package is not installed. "
            "Add it to your dependencies to enable LDAP authentication."
        ) from exc

    # --- build server object -----------------------------------------------
    use_ssl = _LDAP_URL.startswith("ldaps://")
    tls = ldap3.Tls(validate=ldap3.CERT_REQUIRED) if use_ssl else None
    server = ldap3.Server(
        _LDAP_URL,
        connect_timeout=_LDAP_CONN_TIMEOUT,
        use_ssl=use_ssl,
        tls=tls,
        get_info=ldap3.NONE,
    )

    auto_bind = (
        ldap3.AUTO_BIND_TLS_BEFORE_BIND if _LDAP_USE_TLS else ldap3.AUTO_BIND_NO_TLS
    )

    # --- step 1: service-account bind + user search ------------------------
    try:
        svc_conn = ldap3.Connection(
            server,
            user=_LDAP_BIND_DN,
            password=_LDAP_BIND_PASSWORD,
            auto_bind=auto_bind,
            raise_exceptions=True,
        )
    except LDAPBindError as exc:
        logger.error("LDAP service-account bind failed", exc_info=True)
        raise InternalUserError(
            "LDAP service bind failed — check LDAP_BIND_DN / LDAP_BIND_PASSWORD"
        ) from exc
    except LDAPException as exc:
        logger.error("LDAP connection error", exc_info=True)
        raise InternalUserError("LDAP connection error") from exc

    search_filter = f"({_LDAP_USER_ATTR}={escape_filter_chars(username)})"

    with svc_conn:
        svc_conn.search(
            search_base=_LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=[_LDAP_UID_ATTR, _LDAP_ROLE_ATTR, "mail", "userAccountControl"],
        )
        entries = svc_conn.entries
        if not entries:
            logger.warning("LDAP user not found", extra={"username": username})
            raise LDAPAuthError("Invalid username or password")

        entry = entries[0]
        user_dn: str = entry.entry_dn

        # Detect disabled / locked Active Directory accounts
        uac_raw = getattr(entry, "userAccountControl", None)
        if uac_raw is not None:
            uac = int(str(uac_raw))
            if uac & 0x2:    # ACCOUNTDISABLE
                logger.warning("LDAP account disabled", extra={"username": username})
                raise PermissionDeniedError(
                    f"Account '{username}' is disabled in the directory"
                )
            if uac & 0x10:   # LOCKOUT
                logger.warning("LDAP account locked out", extra={"username": username})
                raise PermissionDeniedError(
                    f"Account '{username}' is locked in the directory"
                )

        uid_raw = getattr(entry, _LDAP_UID_ATTR, None)
        user_id: str = str(uid_raw) if uid_raw else user_dn
        mail_raw = getattr(entry, "mail", None)
        email: str = str(mail_raw) if mail_raw else ""
        entry_attrs: dict = {
            _LDAP_ROLE_ATTR: [str(v) for v in (getattr(entry, _LDAP_ROLE_ATTR, None) or [])],
        }

    # --- step 2: user bind — password verification -------------------------
    try:
        user_conn = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=auto_bind,
            raise_exceptions=True,
        )
        user_conn.unbind()
    except LDAPBindError:
        logger.warning("LDAP password mismatch", extra={"username": username})
        raise LDAPAuthError("Invalid username or password")
    except LDAPException as exc:
        logger.error("LDAP error during user bind", exc_info=True)
        raise InternalUserError("LDAP connection error during authentication") from exc

    roles = _ldap_extract_roles(entry_attrs)
    logger.info(
        "LDAP authentication successful",
        extra={"username": username, "user_id": user_id},
    )
    return UserRecord(
        user_id=user_id,
        username=username,
        email=email,
        roles=roles,
    )


# ---------------------------------------------------------------------------
# Local (in-memory) credential store — used as fallback when LDAP is not set
# ---------------------------------------------------------------------------

#: username → (password, user_id, roles)  — replace with DB in production
_local_credentials: dict[str, tuple[str, str, list[str]]] = {}


def register_local_credentials(username: str, password: str, user_id: str) -> None:
    """
    Store a plain-text credential for *username* in the local fallback store.

    .. warning::
        This is a development/testing helper only.  Production deployments
        should rely on LDAP (``LDAP_URL`` set) or a proper password-hash store.
    """
    _local_credentials[username] = (password, user_id, [])


def verify_user_credentials(username: str, password: str) -> UserRecord:
    """
    Verify *username* / *password* and return the matching :class:`UserRecord`.

    When ``LDAP_URL`` is set the check is delegated to :func:`ldap_authenticate`.
    Otherwise the local in-memory credential store is consulted so that
    development and test environments work without an LDAP server.

    Parameters
    ----------
    username:
        Account login name.
    password:
        Plain-text password supplied by the client.

    Returns
    -------
    UserRecord
        The authenticated user record.

    Raises
    ------
    LDAPAuthError
        HTTP 401 — credentials are invalid (LDAP path).
    ValidationError
        HTTP 422 — username contains illegal characters.
    UserNotFoundError
        HTTP 404 — username not present in the local store (fallback path).
    PermissionDeniedError
        HTTP 403 — account is disabled or locked (LDAP path).
    InternalUserError
        HTTP 500 — LDAP connectivity error or ldap3 not installed.
    """
    _validate_username(username)
    _validate_no_special_characters(password, "password")

    if _LDAP_URL:
        return ldap_authenticate(username, password)

    # --- local fallback (dev / test only) ----------------------------------
    cred = _local_credentials.get(username)
    if cred is None:
        user_id_by_name = next(
            (r.user_id for r in _users.values() if r.username == username), None
        )
        if user_id_by_name is None:
            raise UserNotFoundError(f"User '{username}' not found")
        cred = (_local_credentials.get(username, ("", "", []))[0], user_id_by_name, [])

    stored_password, user_id, _ = cred
    if not hmac.compare_digest(stored_password, password):
        raise LDAPAuthError("Invalid username or password")

    record = _users.get(user_id)
    if record is None:
        raise UserNotFoundError(f"User '{username}' not found")
    if not record.is_active:
        raise PermissionDeniedError(f"Account '{username}' is inactive")

    logger.info("Local credential check passed", extra={"user_id": user_id})
    return record


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

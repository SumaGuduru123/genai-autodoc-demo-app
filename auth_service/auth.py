"""
auth_service/auth.py

Handles authentication lifecycle: login, logout, and token refresh.
Raises structured HTTP-style exceptions so callers (and the SOP generator)
can map error codes to incident-response procedures.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (read from environment — never hard-code secrets)
# ---------------------------------------------------------------------------
_JWT_SECRET: str = os.environ["JWT_SECRET"]          # HS256 signing key
_TOKEN_TTL: int = int(os.environ.get("TOKEN_TTL", "3600"))     # seconds
_REFRESH_TTL: int = int(os.environ.get("REFRESH_TTL", "86400"))  # seconds


# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Base class for all authentication errors."""
    http_status: int = 500
    error_code: str = "AUTH_ERROR"

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


class InvalidCredentialsError(AuthError):
    """Raised when username or password is incorrect (401)."""
    http_status = 401
    error_code = "INVALID_CREDENTIALS"


class TokenExpiredError(AuthError):
    """Raised when a JWT or refresh token has passed its TTL (401)."""
    http_status = 401
    error_code = "TOKEN_EXPIRED"


class TokenInvalidError(AuthError):
    """Raised when a token signature or structure is malformed (401)."""
    http_status = 401
    error_code = "TOKEN_INVALID"


class AccountLockedError(AuthError):
    """Raised after too many failed login attempts (403)."""
    http_status = 403
    error_code = "ACCOUNT_LOCKED"


class InternalAuthError(AuthError):
    """Raised when an unexpected server-side error occurs (500)."""
    http_status = 500
    error_code = "INTERNAL_AUTH_ERROR"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: int  # Unix timestamp


@dataclass
class UserSession:
    user_id: str
    username: str
    roles: list[str] = field(default_factory=list)
    issued_at: int = field(default_factory=lambda: int(time.time()))


# ---------------------------------------------------------------------------
# In-memory token store (replace with Redis in production)
# ---------------------------------------------------------------------------
_refresh_store: dict[str, UserSession] = {}
_failed_attempts: dict[str, int] = {}
_MAX_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    """Return an HMAC-SHA256 hex digest of *payload* using the JWT secret."""
    return hmac.new(_JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _build_token(user_id: str, username: str, ttl: int) -> str:
    """Create a minimal signed token: ``<header>.<payload>.<sig>``."""
    issued = int(time.time())
    expires = issued + ttl
    payload = f"{user_id}:{username}:{issued}:{expires}"
    sig = _sign(payload)
    return f"{payload}.{sig}"


def _verify_token(token: str) -> UserSession:
    """
    Parse and verify *token*, returning the embedded :class:`UserSession`.

    Raises
    ------
    TokenInvalidError
        If the token structure or HMAC is wrong.
    TokenExpiredError
        If the token's expiry timestamp has passed.
    """
    try:
        parts = token.rsplit(".", 1)
        if len(parts) != 2:
            raise TokenInvalidError("Malformed token structure")
        payload, sig = parts
        expected_sig = _sign(payload)
        if not hmac.compare_digest(sig, expected_sig):
            raise TokenInvalidError("Token signature mismatch")
        user_id, username, issued_str, expires_str = payload.split(":")
        if int(time.time()) > int(expires_str):
            raise TokenExpiredError("Access token has expired")
        return UserSession(user_id=user_id, username=username)
    except (TokenInvalidError, TokenExpiredError):
        raise
    except Exception as exc:
        logger.error("Unexpected error verifying token", exc_info=True)
        raise InternalAuthError("Token verification failed") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def login(username: str, password: str) -> TokenPair:
    """
    Authenticate *username* with *password* and return a :class:`TokenPair`.

    Parameters
    ----------
    username:
        The account's unique identifier string.
    password:
        Plain-text password supplied by the client.

    Returns
    -------
    TokenPair
        A fresh access/refresh token pair.

    Raises
    ------
    InvalidCredentialsError
        HTTP 401 — credentials do not match any known account.
    AccountLockedError
        HTTP 403 — account is temporarily locked after repeated failures.
    InternalAuthError
        HTTP 500 — unexpected server-side failure during credential lookup.
    """
    if _failed_attempts.get(username, 0) >= _MAX_ATTEMPTS:
        logger.warning("Login blocked — account locked", extra={"username": username})
        raise AccountLockedError(
            f"Account '{username}' is locked after {_MAX_ATTEMPTS} failed attempts.",
            detail="Contact support to unlock the account.",
        )

    try:
        user = _lookup_user(username, password)  # raises InvalidCredentialsError on miss
    except InvalidCredentialsError:
        _failed_attempts[username] = _failed_attempts.get(username, 0) + 1
        logger.warning("Failed login attempt", extra={"username": username,
                                                       "attempts": _failed_attempts[username]})
        raise
    except Exception as exc:
        logger.error("Unexpected error during login", exc_info=True)
        raise InternalAuthError("Login failed due to an internal error") from exc

    _failed_attempts.pop(username, None)  # reset on success
    access_token = _build_token(user.user_id, username, _TOKEN_TTL)
    refresh_token = secrets.token_urlsafe(48)
    _refresh_store[refresh_token] = user
    expires_at = int(time.time()) + _TOKEN_TTL
    logger.info("Login successful", extra={"user_id": user.user_id})
    return TokenPair(access_token=access_token, refresh_token=refresh_token, expires_at=expires_at)


def logout(refresh_token: str) -> None:
    """
    Invalidate *refresh_token*, ending the user's session.

    Parameters
    ----------
    refresh_token:
        The opaque refresh token issued at login.

    Raises
    ------
    TokenInvalidError
        HTTP 401 — the token was not found in the active-session store.
    """
    if refresh_token not in _refresh_store:
        raise TokenInvalidError("Refresh token not found or already revoked")
    session = _refresh_store.pop(refresh_token)
    logger.info("User logged out", extra={"user_id": session.user_id})


def refresh_access_token(refresh_token: str) -> TokenPair:
    """
    Exchange a valid *refresh_token* for a new :class:`TokenPair`.

    Parameters
    ----------
    refresh_token:
        The opaque refresh token issued at login or last refresh.

    Returns
    -------
    TokenPair
        A new access/refresh token pair; the old refresh token is revoked.

    Raises
    ------
    TokenInvalidError
        HTTP 401 — refresh token not recognised.
    TokenExpiredError
        HTTP 401 — session TTL has been exceeded.
    InternalAuthError
        HTTP 500 — unexpected failure during token rotation.
    """
    session = _refresh_store.pop(refresh_token, None)
    if session is None:
        raise TokenInvalidError("Refresh token not found or already used")

    elapsed = int(time.time()) - session.issued_at
    if elapsed > _REFRESH_TTL:
        raise TokenExpiredError("Refresh token session has expired — please log in again")

    try:
        new_access = _build_token(session.user_id, session.username, _TOKEN_TTL)
        new_refresh = secrets.token_urlsafe(48)
        session.issued_at = int(time.time())
        _refresh_store[new_refresh] = session
        expires_at = int(time.time()) + _TOKEN_TTL
        logger.info("Token refreshed", extra={"user_id": session.user_id})
        return TokenPair(access_token=new_access, refresh_token=new_refresh, expires_at=expires_at)
    except Exception as exc:
        logger.error("Token rotation failed", exc_info=True)
        raise InternalAuthError("Token refresh failed due to an internal error") from exc


def verify_access_token(token: str) -> UserSession:
    """
    Verify *token* and return the associated :class:`UserSession`.

    Parameters
    ----------
    token:
        The access token string to validate.

    Returns
    -------
    UserSession
        Decoded session data (user_id, username, roles).

    Raises
    ------
    TokenInvalidError
        HTTP 401 — bad signature or structure.
    TokenExpiredError
        HTTP 401 — token TTL exceeded.
    """
    return _verify_token(token)


# ---------------------------------------------------------------------------
# Stub — replace with real DB call
# ---------------------------------------------------------------------------

def _lookup_user(username: str, password: str) -> UserSession:
    """
    Validate credentials against the user store.

    .. note::
        This is a stub. Replace with a real database call that uses a
        constant-time password comparison against a bcrypt/scrypt hash.

    Raises
    ------
    InvalidCredentialsError
        When username does not exist or password does not match.
    """
    # STUB: in production, query DB and use bcrypt.checkpw()
    _DEMO_USERS = {
        "alice": ("hashed_pw_alice", "user-001", ["developer"]),
        "bob_ops": ("hashed_pw_bob", "user-002", ["support", "ops"]),
    }
    record = _DEMO_USERS.get(username)
    if record is None:
        raise InvalidCredentialsError("Invalid username or password")
    stored_hash, user_id, roles = record
    # Constant-time comparison placeholder
    if not hmac.compare_digest(stored_hash, f"hashed_pw_{username}"):
        raise InvalidCredentialsError("Invalid username or password")
    return UserSession(user_id=user_id, username=username, roles=roles)

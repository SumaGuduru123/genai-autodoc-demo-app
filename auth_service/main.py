"""
auth_service/main.py

FastAPI application entry-point.
Wires up the auth_service routes and provides the /health endpoint.
"""

from __future__ import annotations

import logging
import os

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth_service.auth import (
    AccountLockedError,
    AuthError,
    InternalAuthError,
    InvalidCredentialsError,
    TokenExpiredError,
    TokenInvalidError,
    login,
    logout,
    refresh_access_token,
    verify_access_token,
)
from auth_service.user import (
    DuplicateEmailError,
    InternalUserError,
    PermissionDeniedError,
    UserError,
    UserNotFoundError,
    ValidationError,
    create_user,
    delete_user,
    get_user,
    list_users,
    update_user,
)

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="GenAI AutoDoc Demo API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)


# ---------------------------------------------------------------------------
# Global exception handlers — return generic messages, log detail server-side
# ---------------------------------------------------------------------------

@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    logger.error("Auth error", error_code=exc.error_code, detail=exc.detail)
    return JSONResponse(
        status_code=exc.http_status,
        content={"error_code": exc.error_code, "message": str(exc)},
    )


@app.exception_handler(UserError)
async def user_error_handler(request: Request, exc: UserError) -> JSONResponse:
    logger.error("User error", error_code=exc.error_code, detail=exc.detail)
    return JSONResponse(
        status_code=exc.http_status,
        content={"error_code": exc.error_code, "message": str(exc)},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error_code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred"},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """
    Liveness probe.

    Returns ``{"status": "ok"}`` when the service is ready to accept traffic.
    Used by Docker ``HEALTHCHECK`` and Kubernetes liveness probes.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


@app.post("/auth/login", tags=["auth"])
async def auth_login(body: LoginRequest):
    """
    Authenticate and return a token pair.

    - **username**: account username
    - **password**: plain-text password (sent over TLS)
    """
    token_pair = login(body.username, body.password)
    return token_pair


@app.post("/auth/logout", tags=["auth"])
async def auth_logout(body: LogoutRequest):
    """Revoke the supplied refresh token and end the session."""
    logout(body.refresh_token)
    return {"message": "Logged out"}


@app.post("/auth/refresh", tags=["auth"])
async def auth_refresh(body: RefreshRequest):
    """Exchange a refresh token for a new token pair."""
    return refresh_access_token(body.refresh_token)


@app.get("/auth/me", tags=["auth"])
async def auth_me(request: Request):
    """Return the current user's session info from the Bearer token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth_header[len("Bearer "):]
    session = verify_access_token(token)
    return session


# ---------------------------------------------------------------------------
# User routes
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    username: str
    email: str
    roles: list[str] = ["viewer"]


class UpdateUserRequest(BaseModel):
    email: str | None = None
    roles: list[str] | None = None


@app.post("/users", status_code=status.HTTP_201_CREATED, tags=["users"])
async def user_create(body: CreateUserRequest):
    """Create a new user account."""
    return create_user(body.username, body.email, body.roles)


@app.get("/users", tags=["users"])
async def user_list(active_only: bool = True):
    """List all users (optionally filtered to active accounts)."""
    return list_users(active_only=active_only)


@app.get("/users/{user_id}", tags=["users"])
async def user_get(user_id: str):
    """Fetch a single user by ID."""
    return get_user(user_id)


@app.patch("/users/{user_id}", tags=["users"])
async def user_update(user_id: str, body: UpdateUserRequest, request: Request):
    """Update a user's email or roles (role changes require admin caller)."""
    # In production, extract caller_roles from the verified JWT
    caller_roles: list[str] = ["admin"]  # demo stub
    return update_user(user_id, email=body.email, roles=body.roles, caller_roles=caller_roles)


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
async def user_delete(user_id: str):
    """Soft-delete a user account (admin only)."""
    caller_roles: list[str] = ["admin"]  # demo stub
    delete_user(user_id, caller_roles=caller_roles)

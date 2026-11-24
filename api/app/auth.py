"""Accounts and sessions. Passwords are hashed with Argon2id. A session is a random 256-bit token kept by the
browser in an HttpOnly cookie; the database stores only its SHA-256 hash, so a leaked database cannot be used to
sign in. Every state-changing request must also carry the session's CSRF token in the X-CSRF-Token header.
The user is always taken from the session, never from the request body."""

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

from .config import settings
from .db import get_conn

COOKIE = "ba_session"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,24}$")
hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
_DUMMY_HASH = hasher.hash("not-a-real-password-0")  # equal time for unknown emails


@dataclass(frozen=True)
class User:
    id: int
    email: str
    full_name: str
    session_id: int
    csrf: str


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def check_password_rules(password: str) -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if len(password) > 200:
        return "Use at most 200 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use at least one letter and one digit."
    return None


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    try:
        return hasher.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, user_agent: str | None) -> tuple[str, str]:
    """A fresh session (new random token, so a session id can never be fixed in advance). Returns (token, csrf)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at, user_agent) "
            "VALUES (%s, %s, %s, now() + make_interval(hours => %s), %s)",
            (_digest(token), user_id, csrf, settings.session_hours, (user_agent or "")[:200]))
    return token, csrf


def revoke_session(session_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (session_id,))


def revoke_all_sessions(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))


def user_from_token(token: str | None) -> User | None:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """UPDATE sessions s SET last_seen_at = now()
               FROM users u
               WHERE s.token_hash = %s AND u.id = s.user_id AND s.revoked_at IS NULL AND s.expires_at > now()
                 AND u.status = 'active'
               RETURNING u.id, u.email, u.full_name, s.id, s.csrf_token""", (_digest(token),)).fetchone()
    return User(*row) if row else None


def current_user(request: Request) -> User:
    """FastAPI dependency for every protected route: a valid session, and a CSRF token on state changes."""
    user = user_from_token(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if request.method in UNSAFE and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), user.csrf):
        raise HTTPException(403, "Missing or wrong CSRF token. Reload the page and try again.")
    return user


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.session_hours * 3600, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")


class LoginLimiter:
    """At most `limit` failed sign-ins per email + address in `window` seconds (in memory: one api worker)."""

    def __init__(self, limit: int = 10, window: int = 600):
        self.limit, self.window, self.fails, self.lock = limit, window, {}, threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = time.monotonic()
        return [t for t in self.fails.get(key, []) if now - t < self.window]

    def blocked(self, key: str) -> bool:
        with self.lock:
            self.fails[key] = self._recent(key)
            return len(self.fails[key]) >= self.limit

    def failed(self, key: str) -> None:
        with self.lock:
            self.fails[key] = self._recent(key) + [time.monotonic()]

    def succeeded(self, key: str) -> None:
        with self.lock:
            self.fails.pop(key, None)


login_limiter = LoginLimiter()

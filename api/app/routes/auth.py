"""Accounts: register, sign in, sign out, and "who am I". Registration creates the person's private personal
workspace with a first project, so they can work without a company. Sign-in errors are generic ("wrong email or
password") and limited per email and address. A new session token is issued at every sign-in."""

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (COOKIE, EMAIL, User, check_password_rules, clear_session_cookie, create_session, current_user,
                    hash_password, login_limiter, normalize_email, revoke_all_sessions, revoke_session,
                    set_session_cookie, user_from_token, verify_password)
from ..db import get_conn

router = APIRouter(prefix="/auth")


class RegisterRequest(BaseModel):
    full_name: str
    email: str
    password: str
    confirm_password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


def slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "workspace"
    return f"{base}-{secrets.token_hex(3)}"


def workspaces(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT o.id, o.kind, o.name, o.slug, m.role, m.status
               FROM org_members m JOIN organizations o ON o.id = m.org_id
               WHERE m.user_id = %s ORDER BY o.kind = 'personal' DESC, o.name""", (user_id,)).fetchall()
    return [dict(zip(["id", "kind", "name", "slug", "role", "status"], r)) for r in rows]


def me_payload(user: User) -> dict:
    return {"user": {"id": user.id, "email": user.email, "full_name": user.full_name},
            "csrf_token": user.csrf, "workspaces": workspaces(user.id)}


@router.post("/register", status_code=201)
def register(req: RegisterRequest, request: Request, response: Response):
    name, email = req.full_name.strip(), normalize_email(req.email)
    if not 1 <= len(name) <= 100:
        raise HTTPException(422, "Enter your full name (up to 100 characters).")
    if not EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if req.password != req.confirm_password:
        raise HTTPException(422, "The two passwords do not match.")
    problem = check_password_rules(req.password)
    if problem:
        raise HTTPException(422, f"Choose a stronger password: {problem}")
    pw_hash = hash_password(req.password)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        try:
            uid = conn.execute(
                "INSERT INTO users (name, email, full_name, password_hash, status) VALUES (%s, %s, %s, %s, 'active') "
                "RETURNING id", (f"user:{email}", email, name, pw_hash)).fetchone()[0]
        except Exception as exc:  # the unique index catches two simultaneous registrations
            if "users_email_key" in str(exc) or "users_name_key" in str(exc):
                raise HTTPException(409, "An account with this email already exists. Sign in instead.") from None
            raise
        org = conn.execute(
            "INSERT INTO organizations (kind, name, slug, personal_owner_id, created_by) VALUES ('personal', %s, %s, %s, %s) "
            "RETURNING id", (f"{name.split()[0]}'s workspace"[:120], slugify(name), uid, uid)).fetchone()[0]
        conn.execute("INSERT INTO org_members (org_id, user_id, role) VALUES (%s, %s, 'owner')", (org, uid))
        conn.execute("INSERT INTO projects (org_id, name, description, created_by) VALUES (%s, 'My first project', "
                     "'Your private project. Add documents and ask questions.', %s)", (org, uid))
    token, _ = create_session(uid, request.headers.get("user-agent"))
    set_session_cookie(response, token)
    return me_payload(user_from_token(token))


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    email = normalize_email(req.email)
    key = f"{email}|{request.client.host if request.client else ''}"
    if login_limiter.blocked(key):
        raise HTTPException(429, "Too many failed sign-in attempts. Wait 10 minutes and try again.")
    with get_conn() as conn:
        row = conn.execute("SELECT id, password_hash, status FROM users WHERE lower(email) = %s", (email,)).fetchone()
    ok = verify_password(row[1] if row else None, req.password) and row is not None and row[2] == "active"
    if not ok:
        login_limiter.failed(key)
        raise HTTPException(401, "Wrong email or password.")
    login_limiter.succeeded(key)
    old = user_from_token(request.cookies.get(COOKIE))
    if old:  # signing in again replaces the old session instead of keeping both
        revoke_session(old.session_id)
    token, _ = create_session(row[0], request.headers.get("user-agent"))
    set_session_cookie(response, token)
    return me_payload(user_from_token(token))


@router.post("/logout")
def logout(response: Response, user: User = Depends(current_user)):
    revoke_session(user.session_id)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return me_payload(user)


@router.post("/password")
def change_password(req: PasswordRequest, request: Request, response: Response, user: User = Depends(current_user)):
    """Change the password; every other session of this account is signed out."""
    with get_conn() as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user.id,)).fetchone()[0]
    if not verify_password(stored, req.current_password):
        raise HTTPException(403, "The current password is wrong.")
    if req.new_password != req.confirm_password:
        raise HTTPException(422, "The two passwords do not match.")
    problem = check_password_rules(req.new_password)
    if problem:
        raise HTTPException(422, f"Choose a stronger password: {problem}")
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s",
                     (hash_password(req.new_password), user.id))
    revoke_all_sessions(user.id)
    token, _ = create_session(user.id, request.headers.get("user-agent"))
    set_session_cookie(response, token)
    return me_payload(user_from_token(token))

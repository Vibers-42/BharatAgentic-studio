"""Workspaces (organizations), their members and invitations.

Roles: owner, admin, member (docs/PHASE_8_SECURITY.md). Owners change settings and roles; owners and admins
invite people, confirm new members and remove ordinary members; nobody can remove or demote the last owner,
and admins cannot raise anyone (themselves included) to admin or owner.

Invitations: the token is random (256 bits), shown once inside a link, stored only as a SHA-256 hash, valid for
INVITATION_DAYS days and usable once. Only a signed-in account whose email is the invited email can accept it.
The app cannot send email, so accepting does not prove control of that mailbox: the membership is created as
"pending" and becomes active only when an owner or admin confirms it (administrator-confirmed enrolment)."""

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..access import accessible_project_ids, require_org
from ..auth import EMAIL, User, current_user, normalize_email
from ..config import settings
from ..db import get_conn
from .auth import slugify

router = APIRouter()


class OrgRequest(BaseModel):
    name: str
    description: str = ""


class RoleRequest(BaseModel):
    role: str


class InviteRequest(BaseModel):
    email: str
    role: str = "member"


class AcceptRequest(BaseModel):
    token: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _company(user: User, org_id: int, need: str) -> dict:
    org = require_org(user, org_id, need)
    if org["kind"] != "company":
        raise HTTPException(400, "A personal workspace has no other members. Create a company to invite people.")
    return org


def _owners(conn, org_id: int) -> int:
    return conn.execute("SELECT count(*) FROM org_members WHERE org_id = %s AND role = 'owner' AND status = 'active'",
                        (org_id,)).fetchone()[0]


@router.get("/orgs")
def list_orgs(user: User = Depends(current_user)):
    from .auth import workspaces
    return workspaces(user.id)


@router.post("/orgs", status_code=201)
def create_company(req: OrgRequest, user: User = Depends(current_user)):
    name = req.name.strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(422, "Enter a company name (up to 120 characters).")
    with get_conn() as conn:
        org = conn.execute("INSERT INTO organizations (kind, name, slug, description, created_by) "
                           "VALUES ('company', %s, %s, %s, %s) RETURNING id",
                           (name, slugify(name), req.description.strip()[:1000], user.id)).fetchone()[0]
        conn.execute("INSERT INTO org_members (org_id, user_id, role) VALUES (%s, %s, 'owner')", (org, user.id))
    return {"id": org, "kind": "company", "name": name, "role": "owner"}


@router.get("/orgs/{org_id}")
def get_org(org_id: int, user: User = Depends(current_user)):
    org = require_org(user, org_id)
    pids = accessible_project_ids(user.id, org_id)
    with get_conn() as conn:
        counts = conn.execute(
            """SELECT (SELECT count(*) FROM org_members WHERE org_id = %s AND status = 'active'),
                      (SELECT count(*) FROM org_members WHERE org_id = %s AND status = 'pending'),
                      (SELECT count(*) FROM sources WHERE project_id = ANY(%s)),
                      (SELECT count(*) FROM sources WHERE project_id = ANY(%s) AND status = 'ready'),
                      (SELECT coalesce(sum(chunk_count), 0) FROM sources WHERE project_id = ANY(%s)),
                      (SELECT count(*) FROM graphs WHERE project_id = ANY(%s))""",
            (org_id, org_id, pids, pids, pids, pids)).fetchone()
    return {**org, "projects": len(pids), "members": counts[0], "pending_members": counts[1] if org["role"] != "member" else None,
            "sources": counts[2], "ready_sources": counts[3], "chunks": int(counts[4]), "agents": counts[5]}


@router.patch("/orgs/{org_id}")
def update_org(org_id: int, req: OrgRequest, user: User = Depends(current_user)):
    org = require_org(user, org_id, "owner")
    if org["kind"] == "legacy":
        raise HTTPException(400, "The legacy workspace cannot be renamed.")
    name = req.name.strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(422, "Enter a name (up to 120 characters).")
    with get_conn() as conn:
        conn.execute("UPDATE organizations SET name = %s, description = %s, updated_at = now() WHERE id = %s",
                     (name, req.description.strip()[:1000], org_id))
    return {"id": org_id, "name": name, "description": req.description.strip()[:1000]}


@router.get("/orgs/{org_id}/members")
def list_members(org_id: int, user: User = Depends(current_user)):
    org = require_org(user, org_id)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.full_name, u.email, m.role, m.status, m.joined_at,
                      (SELECT array_agg(p.name || ':' || pm.role ORDER BY p.name) FROM project_members pm
                       JOIN projects p ON p.id = pm.project_id WHERE pm.user_id = u.id AND p.org_id = %s)
               FROM org_members m JOIN users u ON u.id = m.user_id WHERE m.org_id = %s
               ORDER BY m.status DESC, CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.full_name""",
            (org_id, org_id)).fetchall()
    show_all = org["role"] in ("owner", "admin")
    return [{"user_id": r[0], "full_name": r[1], "email": r[2] if show_all or r[0] == user.id else None, "role": r[3],
             "status": r[4], "joined_at": r[5].isoformat(), "projects": (r[6] or []) if show_all else None}
            for r in rows if show_all or r[4] == "active"]


@router.patch("/orgs/{org_id}/members/{member_id}")
def change_role(org_id: int, member_id: int, req: RoleRequest, user: User = Depends(current_user)):
    _company(user, org_id, "owner")  # only owners change roles
    if req.role not in ("owner", "admin", "member"):
        raise HTTPException(422, "Role must be owner, admin or member.")
    with get_conn() as conn:
        row = conn.execute("SELECT role, status FROM org_members WHERE org_id = %s AND user_id = %s FOR UPDATE",
                           (org_id, member_id)).fetchone()
        if row is None:
            raise HTTPException(404, "No such member.")
        if row[1] != "active":
            raise HTTPException(400, "Confirm this member first.")
        if row[0] == "owner" and req.role != "owner" and _owners(conn, org_id) <= 1:
            raise HTTPException(409, "This is the last owner. Make someone else an owner first.")
        conn.execute("UPDATE org_members SET role = %s WHERE org_id = %s AND user_id = %s", (req.role, org_id, member_id))
    return {"user_id": member_id, "role": req.role}


@router.post("/orgs/{org_id}/members/{member_id}/confirm")
def confirm_member(org_id: int, member_id: int, user: User = Depends(current_user)):
    _company(user, org_id, "admin")
    with get_conn() as conn:
        row = conn.execute("UPDATE org_members SET status = 'active', joined_at = now() WHERE org_id = %s AND user_id = %s "
                           "AND status = 'pending' RETURNING role", (org_id, member_id)).fetchone()
    if row is None:
        raise HTTPException(404, "No pending member with this id.")
    return {"user_id": member_id, "role": row[0], "status": "active"}


@router.delete("/orgs/{org_id}/members/{member_id}")
def remove_member(org_id: int, member_id: int, user: User = Depends(current_user)):
    """Owners remove anyone but the last owner; admins remove ordinary members; anyone may leave. Access ends
    at once: every later request checks membership again."""
    org = _company(user, org_id, "member")
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM org_members WHERE org_id = %s AND user_id = %s FOR UPDATE",
                           (org_id, member_id)).fetchone()
        if row is None:
            raise HTTPException(404, "No such member.")
        target = row[0]
        if member_id != user.id:
            if org["role"] == "member" or (org["role"] == "admin" and target != "member"):
                raise HTTPException(403, "You cannot remove this member.")
        if target == "owner" and _owners(conn, org_id) <= 1:
            raise HTTPException(409, "This is the last owner and cannot be removed.")
        conn.execute("DELETE FROM project_members WHERE user_id = %s AND project_id IN "
                     "(SELECT id FROM projects WHERE org_id = %s)", (member_id, org_id))
        conn.execute("DELETE FROM org_members WHERE org_id = %s AND user_id = %s", (org_id, member_id))
    return {"removed": member_id}


# ---------------- invitations ----------------
def _invite_row(r) -> dict:
    status = r[4]
    if status == "pending" and r[5] is not None and r[7]:
        status = "expired"
    return {"id": r[0], "email": r[1], "role": r[2], "invited_by": r[3], "status": status,
            "expires_at": r[5].isoformat(), "accepted_at": r[6].isoformat() if r[6] else None}


@router.get("/orgs/{org_id}/invitations")
def list_invitations(org_id: int, user: User = Depends(current_user)):
    _company(user, org_id, "admin")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.id, i.email, i.role, coalesce(u.full_name, u.email), i.status, i.expires_at, i.accepted_at,
                      i.expires_at < now()
               FROM invitations i LEFT JOIN users u ON u.id = i.invited_by WHERE i.org_id = %s ORDER BY i.id DESC""",
            (org_id,)).fetchall()
    return [_invite_row(r) for r in rows]


def _new_token(conn, invitation_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("UPDATE invitations SET token_hash = %s, status = 'pending', "
                 "expires_at = now() + make_interval(days => %s) WHERE id = %s",
                 (_hash(token), settings.invitation_days, invitation_id))
    return token


@router.post("/orgs/{org_id}/invitations", status_code=201)
def invite(org_id: int, req: InviteRequest, user: User = Depends(current_user)):
    org = _company(user, org_id, "admin")
    email = normalize_email(req.email)
    if not EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if req.role not in ("admin", "member"):
        raise HTTPException(422, "Role must be admin or member.")
    if req.role == "admin" and org["role"] != "owner":
        raise HTTPException(403, "Only owners can invite admins.")
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM org_members m JOIN users u ON u.id = m.user_id WHERE m.org_id = %s AND "
                        "lower(u.email) = %s", (org_id, email)).fetchone():
            raise HTTPException(409, "This person is already a member.")
        token = secrets.token_urlsafe(32)
        iid = conn.execute(
            "INSERT INTO invitations (org_id, email, role, invited_by, token_hash, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, now() + make_interval(days => %s)) RETURNING id, expires_at",
            (org_id, email, req.role, user.id, _hash(token), settings.invitation_days)).fetchone()
    return {"id": iid[0], "email": email, "role": req.role, "token": token, "expires_at": iid[1].isoformat(),
            "note": "Share this link with the person yourself; no email is sent. It works once."}


def _invitation_for_admin(user: User, invitation_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT org_id, status FROM invitations WHERE id = %s", (invitation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such invitation.")
    _company(user, row[0], "admin")
    return row


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: int, user: User = Depends(current_user)):
    _, status = _invitation_for_admin(user, invitation_id)
    if status != "pending":
        raise HTTPException(409, f"This invitation is already {status}.")
    with get_conn() as conn:
        conn.execute("UPDATE invitations SET status = 'revoked' WHERE id = %s AND status = 'pending'", (invitation_id,))
    return {"id": invitation_id, "status": "revoked"}


@router.post("/invitations/{invitation_id}/regenerate")
def regenerate_invitation(invitation_id: int, user: User = Depends(current_user)):
    """A new link (the old one stops working) with a fresh expiry; also re-opens a revoked invitation."""
    _, status = _invitation_for_admin(user, invitation_id)
    if status == "accepted":
        raise HTTPException(409, "This invitation was already accepted.")
    with get_conn() as conn:
        token = _new_token(conn, invitation_id)
        exp = conn.execute("SELECT expires_at FROM invitations WHERE id = %s", (invitation_id,)).fetchone()[0]
    return {"id": invitation_id, "token": token, "expires_at": exp.isoformat(), "status": "pending"}


@router.get("/invitations/preview")
def preview_invitation(token: str):
    """Public (no sign-in): what the link is for, so the page can show it. The email is partly hidden."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT o.name, i.role, i.email, i.status, i.expires_at < now() FROM invitations i
               JOIN organizations o ON o.id = i.org_id WHERE i.token_hash = %s""", (_hash(token),)).fetchone()
    if row is None:
        raise HTTPException(404, "This invitation link is not valid.")
    name, role, email, status, expired = row
    local, _, domain = email.partition("@")
    state = "expired" if status == "pending" and expired else status
    return {"organization": name, "role": role, "email_hint": f"{local[:1]}***@{domain}", "status": state}


@router.post("/invitations/accept")
def accept_invitation(req: AcceptRequest, user: User = Depends(current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT id, org_id, email, role, status, expires_at < now() FROM invitations "
                           "WHERE token_hash = %s FOR UPDATE", (_hash(req.token),)).fetchone()
        if row is None:
            raise HTTPException(404, "This invitation link is not valid.")
        iid, org_id, email, role, status, expired = row
        if status != "pending":
            raise HTTPException(409, f"This invitation was already {status}.")
        if expired:
            raise HTTPException(410, "This invitation has expired. Ask for a new link.")
        if normalize_email(user.email) != normalize_email(email):
            raise HTTPException(403, "This invitation is for a different email address. Sign in with the invited account.")
        existing = conn.execute("SELECT status FROM org_members WHERE org_id = %s AND user_id = %s",
                                (org_id, user.id)).fetchone()
        if existing is None:
            conn.execute("INSERT INTO org_members (org_id, user_id, role, status) VALUES (%s, %s, %s, 'pending')",
                         (org_id, user.id, role))
        conn.execute("UPDATE invitations SET status = 'accepted', accepted_by = %s, accepted_at = now() WHERE id = %s",
                     (user.id, iid))
        org_name = conn.execute("SELECT name FROM organizations WHERE id = %s", (org_id,)).fetchone()[0]
    return {"org_id": org_id, "organization": org_name, "role": role,
            "status": existing[0] if existing else "pending",
            "message": "An owner or admin of the company now confirms your membership; then the workspace opens."}

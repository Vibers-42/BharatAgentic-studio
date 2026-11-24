"""Projects: the boundary for shared knowledge. Each project has its own members, sources, agents, conversations
and usage. Owners and admins of the workspace create and manage projects and assign members (contributor or
viewer); members see only the projects they are assigned to."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import knowledge
from ..access import accessible_project_ids, require_org, require_project
from ..auth import User, current_user
from ..db import get_conn

router = APIRouter()


class ProjectRequest(BaseModel):
    org_id: int
    name: str
    description: str = ""
    local_only: bool = False


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    local_only: bool | None = None
    archived: bool | None = None


class MemberRequest(BaseModel):
    role: str = "viewer"


def _stats(conn, pid: int) -> dict:
    row = conn.execute(
        """SELECT (SELECT count(*) FROM sources WHERE project_id = %s),
                  (SELECT count(*) FROM sources WHERE project_id = %s AND status = 'ready'),
                  (SELECT coalesce(sum(chunk_count), 0) FROM sources WHERE project_id = %s),
                  (SELECT count(*) FROM graphs WHERE project_id = %s),
                  (SELECT count(*) FROM project_members WHERE project_id = %s)""", (pid, pid, pid, pid, pid)).fetchone()
    return {"sources": row[0], "ready_sources": row[1], "chunks": int(row[2]), "agents": row[3], "assigned_members": row[4]}


def _project_json(a, stats: dict) -> dict:
    return {"id": a.id, "org_id": a.org_id, "org_name": a.org_name, "org_kind": a.org_kind, "name": a.name,
            "description": a.description, "local_only": a.local_only, "archived": a.archived,
            "my_org_role": a.org_role, "my_project_role": a.project_role,
            "can_contribute": a.can_contribute, "can_manage": a.can_manage, **stats}


@router.get("/projects")
def list_projects(org_id: int, user: User = Depends(current_user)):
    require_org(user, org_id)
    from ..access import project_access
    out = []
    with get_conn() as conn:
        for pid in accessible_project_ids(user.id, org_id):
            a = project_access(user.id, pid)
            if a:
                out.append(_project_json(a, _stats(conn, pid)))
    return sorted(out, key=lambda p: (p["archived"], p["name"].lower()))


@router.post("/projects", status_code=201)
def create_project(req: ProjectRequest, user: User = Depends(current_user)):
    org = require_org(user, req.org_id, "admin")  # personal workspace owner, or company owner/admin
    name = req.name.strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(422, "Enter a project name (up to 120 characters).")
    with get_conn() as conn:
        pid = conn.execute("INSERT INTO projects (org_id, name, description, local_only, created_by) "
                           "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                           (org["id"], name, req.description.strip()[:2000], req.local_only, user.id)).fetchone()[0]
    return get_project(pid, user)


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(current_user)):
    a = require_project(user, project_id)
    with get_conn() as conn:
        stats = _stats(conn, project_id)
        recent = conn.execute(
            """(SELECT 'source' AS kind, s.title, coalesce(u.full_name, 'imported'), s.updated_at FROM sources s
                LEFT JOIN users u ON u.id = s.uploaded_by WHERE s.project_id = %s ORDER BY s.updated_at DESC LIMIT 5)
               UNION ALL
               (SELECT 'agent', g.name, coalesce(u.full_name, 'imported'), g.updated_at FROM graphs g
                LEFT JOIN users u ON u.id = g.created_by WHERE g.project_id = %s ORDER BY g.updated_at DESC LIMIT 5)
               ORDER BY 4 DESC LIMIT 8""", (project_id, project_id)).fetchall()
    return {**_project_json(a, stats),
            "recent": [{"kind": r[0], "title": r[1], "by": r[2], "at": r[3].isoformat()} for r in recent]}


@router.patch("/projects/{project_id}")
def update_project(project_id: int, req: ProjectUpdate, user: User = Depends(current_user)):
    require_project(user, project_id, "manage")
    fields, values = [], []
    if req.name is not None:
        if not 1 <= len(req.name.strip()) <= 120:
            raise HTTPException(422, "Enter a project name (up to 120 characters).")
        fields.append("name = %s"); values.append(req.name.strip())
    if req.description is not None:
        fields.append("description = %s"); values.append(req.description.strip()[:2000])
    if req.local_only is not None:
        fields.append("local_only = %s"); values.append(req.local_only)
    if req.archived is not None:
        fields.append("archived_at = " + ("now()" if req.archived else "NULL"))
    if fields:
        with get_conn() as conn:
            conn.execute(f"UPDATE projects SET {', '.join(fields)}, updated_at = now() WHERE id = %s", (*values, project_id))
    return get_project(project_id, user)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: User = Depends(current_user)):
    """Deletes the project with everything that belongs only to it: sources, chunks and vectors, stored files,
    agents and conversations (usage rows stay, without the project link)."""
    a = require_project(user, project_id, "manage")
    if a.org_kind == "legacy":
        raise HTTPException(400, "Imported legacy projects cannot be deleted from the app.")
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    knowledge.delete_project_files(project_id)
    return {"deleted": project_id}


@router.get("/projects/{project_id}/members")
def project_members(project_id: int, user: User = Depends(current_user)):
    a = require_project(user, project_id)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.full_name, m.role, pm.role FROM org_members m JOIN users u ON u.id = m.user_id
               LEFT JOIN project_members pm ON pm.project_id = %s AND pm.user_id = u.id
               WHERE m.org_id = %s AND m.status = 'active' AND (m.role IN ('owner', 'admin') OR pm.user_id IS NOT NULL)
               ORDER BY u.full_name""", (project_id, a.org_id)).fetchall()
    return [{"user_id": r[0], "full_name": r[1], "org_role": r[2],
             "project_role": r[3] or ("all projects" if r[2] in ("owner", "admin") else None)} for r in rows]


@router.put("/projects/{project_id}/members/{member_id}")
def assign_member(project_id: int, member_id: int, req: MemberRequest, user: User = Depends(current_user)):
    a = require_project(user, project_id, "manage")
    if req.role not in ("contributor", "viewer"):
        raise HTTPException(422, "Project role must be contributor or viewer.")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM org_members WHERE org_id = %s AND user_id = %s AND status = 'active'",
                            (a.org_id, member_id)).fetchone():
            raise HTTPException(400, "Only active members of this workspace can be added to its projects.")
        conn.execute("INSERT INTO project_members (project_id, user_id, role, assigned_by) VALUES (%s, %s, %s, %s) "
                     "ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role, assigned_by = EXCLUDED.assigned_by",
                     (project_id, member_id, req.role, user.id))
    return {"project_id": project_id, "user_id": member_id, "role": req.role}


@router.delete("/projects/{project_id}/members/{member_id}")
def unassign_member(project_id: int, member_id: int, user: User = Depends(current_user)):
    require_project(user, project_id, "manage")
    with get_conn() as conn:
        n = conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                         (project_id, member_id)).rowcount
    if not n:
        raise HTTPException(404, "This person is not assigned to the project.")
    return {"removed": member_id}

"""Central authorization. Every protected route resolves the resource's real owner from the database and asks
these functions whether the signed-in user may act on it. Nothing here trusts ids, roles or ownership sent by
the browser. A resource the user may not see is reported as "not found" (404), so ids cannot be probed.

Policy (docs/PHASE_8_SECURITY.md):
- Workspace (organization) roles: owner, admin, member. A personal workspace has one owner and no one else.
- Owners and admins can see and manage every project of their workspace.
- Members see only projects they are assigned to: "contributor" may add knowledge and create agents,
  "viewer" may read knowledge, use agents and ask questions.
- Only active memberships count (an accepted invitation waits as "pending" until an owner/admin confirms it).
- The legacy workspace has no members until an operator assigns an owner."""

from dataclasses import dataclass

from fastapi import HTTPException

from .auth import User
from .db import get_conn

LEVEL = {"view": 1, "contribute": 2, "manage": 3}


def not_found(what: str = "Not found."):
    return HTTPException(404, what)


def org_role(user_id: int, org_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM org_members WHERE org_id = %s AND user_id = %s AND status = 'active'",
                           (org_id, user_id)).fetchone()
    return row[0] if row else None


def require_org(user: User, org_id: int, need: str = "member") -> dict:
    """need: member | admin | owner."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT o.id, o.kind, o.name, o.slug, o.description, m.role
               FROM organizations o JOIN org_members m ON m.org_id = o.id
               WHERE o.id = %s AND m.user_id = %s AND m.status = 'active'""", (org_id, user.id)).fetchone()
    if row is None:
        raise not_found("No such workspace.")
    org = dict(zip(["id", "kind", "name", "slug", "description", "role"], row))
    rank = {"member": 1, "admin": 2, "owner": 3}
    if rank[org["role"]] < rank[need]:
        raise HTTPException(403, f"This needs the {need} role in {org['name']}.")
    return org


@dataclass
class ProjectAccess:
    id: int
    org_id: int
    org_kind: str
    org_name: str
    name: str
    description: str
    local_only: bool
    archived: bool
    org_role: str
    project_role: str | None   # contributor / viewer, or None for owners and admins
    level: int                 # 1 view, 2 contribute, 3 manage

    @property
    def can_contribute(self) -> bool:
        return self.level >= 2

    @property
    def can_manage(self) -> bool:
        return self.level >= 3


def project_access(user_id: int, project_id: int) -> ProjectAccess | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT p.id, p.org_id, o.kind, o.name, p.name, p.description, p.local_only, p.archived_at IS NOT NULL,
                      m.role, pm.role
               FROM projects p
               JOIN organizations o ON o.id = p.org_id
               JOIN org_members m ON m.org_id = p.org_id AND m.user_id = %s AND m.status = 'active'
               LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = %s
               WHERE p.id = %s""", (user_id, user_id, project_id)).fetchone()
    if row is None:
        return None
    pid, org_id, kind, org_name, name, desc, local_only, archived, orole, prole = row
    if orole in ("owner", "admin"):
        level = 3
    elif prole == "contributor":
        level = 2
    elif prole == "viewer":
        level = 1
    else:
        return None  # a company member who is not assigned to this project
    if archived and level < 3:
        return None
    return ProjectAccess(pid, org_id, kind, org_name, name, desc, local_only, archived, orole, prole, level)


def require_project(user: User, project_id: int, need: str = "view") -> ProjectAccess:
    access = project_access(user.id, project_id)
    if access is None:
        raise not_found("No such project.")
    if access.level < LEVEL[need]:
        raise HTTPException(403, "You can view this project but not change it." if access.level == 1
                            else "Only the workspace owners and admins can do this.")
    return access


def require_graph(user: User, graph_id: int, need: str = "view") -> tuple[dict, ProjectAccess]:
    """need: view (open and run) | edit (owners/admins, or the contributor who created it)."""
    with get_conn() as conn:
        row = conn.execute("SELECT id, project_id, name, description, graph, created_by, updated_at, created_at "
                           "FROM graphs WHERE id = %s", (graph_id,)).fetchone()
    if row is None or row[1] is None:
        raise not_found("No such agent.")
    graph = dict(zip(["id", "project_id", "name", "description", "graph", "created_by", "updated_at", "created_at"], row))
    access = project_access(user.id, graph["project_id"])
    if access is None:
        raise not_found("No such agent.")
    if need == "edit" and not (access.can_manage or (access.can_contribute and graph["created_by"] == user.id)):
        raise HTTPException(403, "Only the agent's creator and the workspace owners and admins can change it.")
    return graph, access


def require_source(user: User, source_id: int, need: str = "view") -> tuple[dict, ProjectAccess]:
    """need: view | manage (owners/admins, or the contributor who added it)."""
    with get_conn() as conn:
        row = conn.execute("SELECT id, project_id, uploaded_by FROM sources WHERE id = %s", (source_id,)).fetchone()
    if row is None:
        raise not_found("No such source.")
    access = project_access(user.id, row[1])
    if access is None:
        raise not_found("No such source.")
    if need == "manage" and not (access.can_manage or (access.can_contribute and row[2] == user.id)):
        raise HTTPException(403, "Only the person who added this source and the workspace owners and admins can change it.")
    return {"id": row[0], "project_id": row[1], "uploaded_by": row[2]}, access


def require_conversation(user: User, conversation_id: int) -> tuple[dict, ProjectAccess]:
    """A conversation belongs to one user; nobody else (not even owners) can open it. The user must also still
    have access to its project."""
    with get_conn() as conn:
        row = conn.execute("SELECT id, user_id, project_id, graph_id, title FROM conversations WHERE id = %s",
                           (conversation_id,)).fetchone()
    if row is None or row[1] != user.id:
        raise not_found("No such conversation.")
    access = project_access(user.id, row[2])
    if access is None:
        raise not_found("No such conversation.")
    return dict(zip(["id", "user_id", "project_id", "graph_id", "title"], row)), access


def accessible_project_ids(user_id: int, org_id: int | None = None) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.id FROM projects p
               JOIN org_members m ON m.org_id = p.org_id AND m.user_id = %s AND m.status = 'active'
               LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = %s
               WHERE (m.role IN ('owner', 'admin') OR (pm.user_id IS NOT NULL AND p.archived_at IS NULL))
                 AND (%s::int IS NULL OR p.org_id = %s)""", (user_id, user_id, org_id, org_id)).fetchall()
    return [r[0] for r in rows]

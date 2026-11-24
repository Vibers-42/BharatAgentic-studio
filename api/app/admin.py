"""Operator commands, run inside the api container (never reachable over HTTP):

  docker compose exec api python -m app.admin claim-legacy owner@example.com
      Make an existing, active account the owner of "Legacy workspace (imported)", the quarantined workspace that
      holds every agent, document and usage row created before Phase 8. Until this is run, nobody can see them.
  docker compose exec api python -m app.admin status
      Show the applied migrations and how much data the legacy workspace holds.
"""

import sys

from .auth import normalize_email
from .db import close_db, get_conn, init_db


def claim_legacy(email: str) -> str:
    with get_conn() as conn:
        user = conn.execute("SELECT id, status FROM users WHERE lower(email) = %s", (normalize_email(email),)).fetchone()
        if user is None or user[1] != "active":
            return f"No active account with the email {email}. The person must register first."
        orgs = conn.execute("SELECT id, name FROM organizations WHERE kind = 'legacy'").fetchall()
        for org_id, _ in orgs:
            conn.execute("INSERT INTO org_members (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active') "
                         "ON CONFLICT (org_id, user_id) DO UPDATE SET role = 'owner', status = 'active'", (org_id, user[0]))
    return f"{email} now owns: {', '.join(n for _, n in orgs) or 'nothing (no legacy workspace)'}"


def status() -> str:
    with get_conn() as conn:
        migs = conn.execute("SELECT version, name, applied_at FROM schema_migrations ORDER BY version").fetchall()
        legacy = conn.execute(
            """SELECT o.name, (SELECT count(*) FROM org_members m WHERE m.org_id = o.id),
                      (SELECT count(*) FROM projects p WHERE p.org_id = o.id),
                      (SELECT count(*) FROM graphs g JOIN projects p ON p.id = g.project_id WHERE p.org_id = o.id),
                      (SELECT count(*) FROM sources s JOIN projects p ON p.id = s.project_id WHERE p.org_id = o.id)
               FROM organizations o WHERE o.kind = 'legacy'""").fetchall()
    lines = [f"migration {v} {n} applied {a:%Y-%m-%d %H:%M}" for v, n, a in migs]
    lines += [f"{n}: {m} members, {p} projects, {g} agents, {s} sources" for n, m, p, g, s in legacy]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    init_db()
    try:
        if len(argv) == 2 and argv[0] == "claim-legacy":
            print(claim_legacy(argv[1]))
        elif argv == ["status"]:
            print(status())
        else:
            print(__doc__)
            return 2
    finally:
        close_db()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

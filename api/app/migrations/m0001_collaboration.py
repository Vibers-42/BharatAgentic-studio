"""Phase 8: accounts, workspaces (personal and company), projects, memberships, invitations, sessions, a source
registry, agent-source links, conversations, messages, and ownership on usage.

Existing data is kept and quarantined: every existing agent, chunk (with its vector, unchanged) and stored file
moves into a workspace "Legacy workspace (imported)" that has NO members, so no account can see it until an
operator assigns it (`python -m app.admin claim-legacy <email>`). Existing user names were never verified
identities, so those users get status 'legacy' and cannot sign in."""

from psycopg.types.json import Jsonb
import os
import re

SCHEMA = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('active', 'disabled', 'legacy'));
ALTER TABLE users ADD CONSTRAINT users_active_has_login CHECK (status <> 'active' OR (email IS NOT NULL AND password_hash IS NOT NULL));
CREATE UNIQUE INDEX users_email_key ON users (lower(email)) WHERE email IS NOT NULL;

CREATE TABLE organizations (
    id                 SERIAL PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('personal', 'company', 'legacy')),
    name               TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    slug               TEXT NOT NULL UNIQUE,
    description        TEXT NOT NULL DEFAULT '',
    personal_owner_id  INT UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    created_by         INT REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'personal') = (personal_owner_id IS NOT NULL))
);

CREATE TABLE org_members (
    id         SERIAL PRIMARY KEY,
    org_id     INT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('pending', 'active')),
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id)
);
CREATE INDEX org_members_user_idx ON org_members (user_id);

CREATE TABLE projects (
    id           SERIAL PRIMARY KEY,
    org_id       INT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    description  TEXT NOT NULL DEFAULT '',
    local_only   BOOLEAN NOT NULL DEFAULT false,
    created_by   INT REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at  TIMESTAMPTZ
);
CREATE INDEX projects_org_idx ON projects (org_id);

CREATE TABLE project_members (
    project_id   INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('contributor', 'viewer')),
    assigned_by  INT REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX project_members_user_idx ON project_members (user_id);

CREATE TABLE invitations (
    id           SERIAL PRIMARY KEY,
    org_id       INT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    invited_by   INT REFERENCES users(id) ON DELETE SET NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked')),
    expires_at   TIMESTAMPTZ NOT NULL,
    accepted_by  INT REFERENCES users(id) ON DELETE SET NULL,
    accepted_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX invitations_org_idx ON invitations (org_id);

CREATE TABLE sessions (
    id            BIGSERIAL PRIMARY KEY,
    token_hash    TEXT NOT NULL UNIQUE,
    user_id       INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent    TEXT
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

CREATE TABLE sources (
    id             SERIAL PRIMARY KEY,
    project_id     INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uploaded_by    INT REFERENCES users(id) ON DELETE SET NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('pdf', 'url', 'repo', 'text', 'markdown', 'docx', 'csv', 'xlsx')),
    title          TEXT NOT NULL,
    original_name  TEXT,
    url            TEXT,
    storage_path   TEXT,
    size_bytes     BIGINT,
    content_hash   TEXT,
    revision       TEXT,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    chunk_count    INT NOT NULL DEFAULT 0,
    error          TEXT,
    legacy_key     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sources_project_idx ON sources (project_id);

ALTER TABLE chunks ADD COLUMN source_ref INT REFERENCES sources(id) ON DELETE CASCADE;
CREATE INDEX chunks_source_ref_idx ON chunks (source_ref);

ALTER TABLE graphs ADD COLUMN project_id INT REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE graphs ADD COLUMN created_by INT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE graphs ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE graphs ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX graphs_project_idx ON graphs (project_id);

CREATE TABLE agent_sources (
    graph_id   INT NOT NULL REFERENCES graphs(id) ON DELETE CASCADE,
    node_id    TEXT NOT NULL,
    source_id  INT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    PRIMARY KEY (graph_id, node_id)
);
CREATE INDEX agent_sources_source_idx ON agent_sources (source_id);

CREATE TABLE conversations (
    id          SERIAL PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    graph_id    INT REFERENCES graphs(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner_idx ON conversations (user_id, project_id, updated_at DESC);

CREATE TABLE messages (
    id               BIGSERIAL PRIMARY KEY,
    conversation_id  INT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error')),
    model            TEXT,
    model_requested  TEXT,
    fallback         JSONB,
    citations        JSONB NOT NULL DEFAULT '[]',
    tool_calls       JSONB NOT NULL DEFAULT '[]',
    input_tokens     INT,
    output_tokens    INT,
    cost_usd         NUMERIC(16, 8),
    cost_inr         NUMERIC(16, 6),
    request_id       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, request_id, role)
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, id);

ALTER TABLE usage ADD COLUMN org_id INT REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE usage ADD COLUMN project_id INT REFERENCES projects(id) ON DELETE SET NULL;
ALTER TABLE usage ADD COLUMN graph_id INT REFERENCES graphs(id) ON DELETE SET NULL;
ALTER TABLE usage ADD COLUMN conversation_id INT REFERENCES conversations(id) ON DELETE SET NULL;
CREATE INDEX usage_org_idx ON usage (org_id);
CREATE INDEX usage_project_idx ON usage (project_id);
"""

GITHUB_BLOB = re.compile(r"^(https://github\.com/[^/]+/[^/]+)/blob/([0-9a-f]{7,40})/")


def _kind(source_type: str, origins: list[str]) -> str:
    if source_type == "source_repo":
        return "repo"
    return "url" if any(origins) else "pdf"


def upgrade(conn, upload_dir: str) -> None:
    conn.execute(SCHEMA)
    legacy_org = conn.execute(
        "INSERT INTO organizations (kind, name, slug, description) VALUES ('legacy', 'Legacy workspace (imported)', "
        "'legacy-imported', 'Agents and documents created before accounts existed. No one has access until an "
        "operator assigns an owner.') RETURNING id").fetchone()[0]
    agents_project = conn.execute(
        "INSERT INTO projects (org_id, name, description) VALUES (%s, 'Imported agents', "
        "'Saved agents and their documents from before Phase 8') RETURNING id", (legacy_org,)).fetchone()[0]
    quick_project = conn.execute(
        "INSERT INTO projects (org_id, name, description) VALUES (%s, 'Imported quick-chat sources', "
        "'Quick-chat documents from before Phase 8') RETURNING id", (legacy_org,)).fetchone()[0]
    conn.execute("UPDATE graphs SET project_id = %s, created_at = updated_at WHERE project_id IS NULL", (agents_project,))
    conn.execute("UPDATE usage SET org_id = %s WHERE org_id IS NULL", (legacy_org,))

    graphs = {gid: g for gid, g in conn.execute("SELECT id, graph FROM graphs").fetchall()}
    changed_graphs = set()
    groups = conn.execute(
        """SELECT scope, source_id, max(source_type), count(*), min(file_name),
                  array_remove(array_agg(DISTINCT origin), NULL), max(created_at)
           FROM chunks WHERE source_ref IS NULL GROUP BY scope, source_id ORDER BY scope, source_id""").fetchall()
    for scope, node_id, source_type, count, first_file, origins, created in groups:
        project = agents_project if scope.startswith("graph:") else quick_project
        kind = _kind(source_type, origins)
        title, url, revision, storage, size = first_file, None, None, None, None
        if kind == "repo":
            m = GITHUB_BLOB.match(origins[0]) if origins else None
            if m:
                url, revision = m.group(1), m.group(2)
                title = url.replace("https://github.com/", "") + f"@{revision[:7]}"
        elif kind == "url":
            url = origins[0]
        else:  # a PDF: keep the original file if it is still in the uploads volume
            rel = os.path.join(scope.replace(":", "_"), node_id, first_file)
            path = os.path.join(upload_dir, rel)
            if os.path.isfile(path):
                storage, size = rel.replace("\\", "/"), os.path.getsize(path)
        sid = conn.execute(
            """INSERT INTO sources (project_id, kind, title, original_name, url, storage_path, size_bytes, revision,
                                    status, chunk_count, legacy_key, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', %s, %s, %s, %s) RETURNING id""",
            (project, kind, title, first_file if kind == "pdf" else None, url, storage, size, revision, count,
             f"{scope}/{node_id}", created, created)).fetchone()[0]
        conn.execute("UPDATE chunks SET source_ref = %s, scope = %s, source_id = %s WHERE scope = %s AND source_id = %s",
                     (sid, f"project:{project}", f"src{sid}", scope, node_id))
        if scope.startswith("graph:"):
            gid = int(scope.split(":", 1)[1])
            graph = graphs.get(gid)
            if graph:
                for node in graph.get("nodes", []):
                    if node.get("id") == node_id:
                        node.setdefault("config", {})["source_ref"] = sid
                        changed_graphs.add(gid)
                        conn.execute("INSERT INTO agent_sources (graph_id, node_id, source_id) VALUES (%s, %s, %s) "
                                     "ON CONFLICT DO NOTHING", (gid, node_id, sid))
    for gid in changed_graphs:
        conn.execute("UPDATE graphs SET graph = %s WHERE id = %s", (Jsonb(graphs[gid]), gid))
    leftover = conn.execute("SELECT count(*) FROM chunks WHERE source_ref IS NULL").fetchone()[0]
    if leftover:
        raise RuntimeError(f"{leftover} chunks were not assigned to a source")

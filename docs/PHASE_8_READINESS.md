# Phase 8 readiness (proposal only, nothing here is implemented)

Phase 8 will add accounts, companies (organizations), projects and shared project knowledge. This document
describes what the current code base (Phase 7.1) already gives Phase 8, what is missing, and a proposed
database extension and migration order. It is based on the code and on the Phase 7.1 checks
([PHASE_7_1_AUDIT.md](PHASE_7_1_AUDIT.md)), not on what the frameworks could do in principle.

## 1. What Phase 8 can reuse as it is

| Part | Why it is ready | Evidence |
|---|---|---|
| Knowledge storage | Text, chunks and bge-m3 vectors are stored permanently in Postgres; original PDFs in the `uploads` volume | survived api, web and full-stack restarts (`verify_phase7_1.ps1`) |
| Re-use without re-upload | A saved agent's knowledge is read by any later request; nothing is tied to the browser or the uploader | same answers and citations after restarts, from two different callers |
| Per-agent isolation | Every chunk carries a scope (`graph:<id>`); search filters by scope and by connected source ids *before* ranking | two agents with the same source id, 8 concurrent mixed questions, no mixing |
| Concurrency safety of ingestion | One writer per source at a time (advisory lock); replace is one transaction | 6 concurrent re-ingestions leave one copy; interrupted ingestion leaves none or all |
| AI engine | One `generate()` for four models, fallback, local-only, tools, citations, claim check, usage accounting | model matrix in [results/model_matrix.json](results/model_matrix.json) |
| Usage records | One row per question with model, tokens, cost, time, sources | `usage` table; per-user and per-model endpoints |

The database and the AI engine can be **extended**; neither has to be rebuilt.

## 2. Gaps that block a multi-user product

| Gap | Today | Risk |
|---|---|---|
| No authentication | The caller names a user in the request body (`"user": "demo"`); the api trusts it | anyone can act and spend as anyone |
| No authorization | Every endpoint can read and change every agent, source and usage row | any caller can read any company's knowledge |
| `POST /users` is open | anyone can create users | |
| Quick-chat ("inline") scope is global | one shared bucket for everyone | must become per project or per user |
| No source table | a source's state is derived from its chunks plus the node config in the graph JSON | a failed or running ingestion has no stored status; nothing to own or permission |
| No delete for agents | `graphs` rows cannot be deleted through the api | when added, chunks and files must be deleted with it |
| No conversations | chat history lives only in the browser tab | needed for "my conversations" and audit |
| Schema migrations are `CREATE/ALTER ... IF NOT EXISTS` on start-up | fine for additive changes | not enough for backfills and NOT NULL constraints |

So the current isolation is **between agents (graphs)**, not between users or companies. It must not be
described as tenant isolation.

## 3. Proposed tables

```
organizations        id, name, slug UNIQUE, created_at
users (extend)       + email UNIQUE, password_hash (argon2) or external_subject, is_active, last_login_at
org_memberships      org_id FK, user_id FK, role (owner|admin|member), PRIMARY KEY (org_id, user_id)
projects             id, org_id FK, name, created_by FK users, created_at
project_members      project_id FK, user_id FK, role (manager|editor|viewer), PRIMARY KEY (project_id, user_id)
invitations          id, org_id FK, project_id FK NULL, email, role, token_hash, expires_at, accepted_at, invited_by
sources (new)        id UUID, project_id FK, kind (pdf|url|repo), title, uri, file_path, content_hash,
                     status (pending|indexing|ready|failed), error, chunk_count, created_by, created_at, indexed_at
agent_sources        graph_id FK, node_id, source_id FK sources   (which project sources an agent's node uses)
conversations        id, project_id FK, graph_id FK, user_id FK, title, created_at
messages             id, conversation_id FK, role, content, citations JSONB, usage_id FK NULL, created_at
graphs (extend)      + project_id FK, owner_id FK users, created_by, deleted_at
chunks (extend)      + source_uuid FK sources ON DELETE CASCADE, + project_id (denormalised for the filter)
usage (extend)       + org_id, project_id, conversation_id; user_id becomes the authenticated user
```

Roles: org owner/admin manage members and billing settings; project manager manages members and sources;
editor uploads sources and edits agents; viewer asks questions only.

## 4. Shared project knowledge

- Knowledge belongs to a **project**, not to an agent: an editor uploads a PDF/repo/docs page once into the
  project's source library (`sources`), and any agent in the project can connect to it (`agent_sources`).
- Retrieval keeps today's "filter first, then rank" query, with the filter becoming
  `project_id = :p AND source_uuid = ANY(:connected)`; the api checks membership before running it.
- Re-indexing writes a new version under the same source row (today's replace-in-one-transaction stays).
- The `uploads` volume layout becomes `project_<id>/<source uuid>/<file>`.

## 5. Migration that keeps today's data private

1. Take a backup (`pg_dump -Fc`, procedure in the audit) and adopt a migration tool (Alembic) with the
   current schema as the baseline.
2. Add the new tables and nullable columns.
3. Create one organization "Legacy workspace" and one project "Imported agents", owned by an administrator
   account created during the migration (no default password; a one-time setup link).
4. Backfill: every graph, every `graph:<id>` chunk set (as `sources` rows) and every usage row goes to that
   project; inline-scope chunks become sources of a "Quick chat (imported)" project.
5. Make `project_id` NOT NULL. Every query filters by the caller's projects, so a row without access is
   invisible by default (deny by default); nothing legacy becomes public.
6. Old user names stay as display names; they cannot log in until an admin invites them.

## 6. Recommended order for Phase 8

1. Migration tooling and backups (above).
2. Authentication: users with email + argon2 password (or an OIDC provider), short-lived sessions or JWT,
   rate-limited login, CSRF-safe cookies.
3. Organizations, projects, memberships, and one authorization dependency used by **every** route.
4. `sources` table with status, project source library, `agent_sources`; move uploads to project folders.
5. Ownership on graphs and usage; usage per organization and project.
6. Conversations and messages.
7. Invitations.
8. UI: sign-in, workspace/project switcher, members, source library.

Can wait until after Phase 8: background ingestion workers, Redis, horizontal scaling, SSO/SCIM, billing.

## 7. Blockers and open questions

- **Blockers (must be done in Phase 8 before any shared or public deployment):** authentication,
  authorization on every route, removal of the request-body user, per-project quick chat.
- Decide password login versus an external identity provider.
- Decide whether one source can be shared across projects (proposal: no, copy it; simpler permissions).
- Ingestion is synchronous inside the request (a large repo holds the request for its whole duration);
  acceptable for the MVP, a background job queue only if measured upload sizes require it.

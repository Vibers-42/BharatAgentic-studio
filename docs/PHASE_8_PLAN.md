# Phase 8 plan: accounts, workspaces, projects and shared knowledge

Baseline `e69e3f3`, 2026-11-24. Starting point: [PHASE_8_READINESS.md](PHASE_8_READINESS.md).
Before any change: database backup `backups/phase8_baseline_20261124.dump` (git-ignored), restored into a
scratch database with identical counts (20 users, 71 agents, 206 chunks, 1,106 usage rows, 82 sources,
73 stored files).

## What the baseline trusts today (to be closed)

- Every route is open; `POST /pipeline/run` and the usage endpoints take the user **name** from the request.
- `GET/POST /users` list and create users without any check.
- Sources are addressed by `(scope, node id)`; any caller can upload into or read any agent's scope.
- `POST /pipeline/run` accepts an inline graph plus any `graph_id` (whose documents it then searches).
- `/status`, `/models`, `/tools` are open (only `/health` should be).

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Migrations | Versioned Python migrations in `api/app/migrations/`, a `schema_migrations` table, one transaction per migration, an advisory lock so two api processes never migrate at once | no tool exists; `schema.sql` stays the baseline |
| Workspaces | One `organizations` table with `kind` = `personal` or `company`. Registration creates the personal workspace (the user is its owner) and a first project | one code path for authorization; a project always belongs to exactly one workspace |
| Roles | Workspace: owner, admin, member. Project: contributor (upload knowledge, create agents), viewer (read and ask). Owners and admins manage every project of their workspace; members only see projects they are assigned to | the minimum the scenario needs |
| Passwords | Argon2id (`argon2-cffi`, library defaults), min 10 characters with a letter and a digit | |
| Sessions | Server-side `sessions` table; the browser holds a random 256-bit token in an HttpOnly, SameSite=Lax cookie (`Secure` when `COOKIE_SECURE=true` for HTTPS); only its SHA-256 hash is stored; 24 h lifetime; logout and membership changes take effect on the next request | revocable, no tokens in localStorage |
| CSRF | Per-session token returned by `/auth/me`, required in `X-CSRF-Token` on every POST/PUT/PATCH/DELETE; plus SameSite cookies | cookie auth needs it |
| Login abuse | In-memory limiter: 10 failed attempts per email+IP per 10 minutes → 429 (one api worker) | |
| Invitations | Random token (shown once as a link, only its hash stored), 7-day expiry, single use (row lock). Accepting needs a signed-in account **with the invited email**; because the app cannot send email, the membership then waits for an owner/admin to **confirm** it (administrator-confirmed enrolment) | an email string is not proof of identity |
| Knowledge | New `sources` registry per project (status pending → processing → ready/failed, uploader, size, hash, revision, error). Chunks keep their vectors and get `source_ref` (FK, cascade); their scope becomes `project:<id>` and source id `src<id>` | one copy per project, reusable by every agent |
| Agents | `graphs` gets `project_id`, `created_by`, `description`, `created_at`. A source node points at a registry source with `config.source_ref`; `agent_sources` mirrors these links | many agents, one source |
| Execution | Every run resolves the project from the database (saved agent) or the request, checks access, and accepts only sources of that project that are ready. An unsaved graph may run only for contributors of the project. Project chat builds its graph on the server from the project's ready sources | the inline-graph route cannot become a bypass |
| Privacy | Effective local-only = request OR agent OR project setting OR server setting | most restrictive wins |
| Conversations | `conversations` + `messages`, private to their user; last 4 messages (bounded) give follow-up context; failed answers are stored as errors, never as answers | |
| Usage | Rows get `org_id`, `project_id`, `graph_id`, `conversation_id`; the user comes from the session. Members see their own usage, owners/admins their workspace's | |
| Legacy data | Moved into a workspace "Legacy workspace (imported)" with **no members**: invisible to everyone until an operator runs `python -m app.admin claim-legacy <email>` inside the api container | names like `demo` were never verified identities |
| Files | Stored under `uploads/project_<id>/source_<id>/`, served only through an authorized download route; replaced, refused and deleted files are removed | Phase 7.1 finding |

## Order

1. Migration (tested on a copy of the main database first), auth, sessions, CSRF, authorization helpers.
2. Workspaces, invitations, projects, memberships; source registry and ingestion; agents; secured pipeline.
3. Conversations, usage ownership, the frontend (landing, auth, shell, workspaces, projects, knowledge,
   agents, chat, members, usage).
4. Tests (isolation matrix), `verify_phase8.ps1`, updated phase scripts, browser journeys, docs.
5. Extra document formats (TXT, Markdown, DOCX, CSV, XLSX) if time allows.

Rollback: stop the api, restore the baseline dump (`pg_restore --clean`), check out `e69e3f3`, restart.

# Phase 8 security: authentication, authorization and isolation

Tested by `tests/test_tenancy_db.py` (real Postgres, separately registered accounts) and
`scripts/verify_phase8.ps1` (the running stack, real models). The app is **not** described as fully secure:
see "Remaining limitations".

## Authentication and sessions

| Control | Implementation | Test |
|---|---|---|
| Passwords | Argon2id (`argon2-cffi` defaults); at least 10 characters with a letter and a digit; hashes never returned | `test_registration_hashes_the_password_*` |
| Unique accounts | emails normalised to lower case; unique index on `lower(email)`; simultaneous registrations produce one account | `test_simultaneous_registrations_*`, verify_phase8 concurrency |
| Sign-in errors | the same message for a wrong password and an unknown email; a dummy hash is checked for unknown emails | `test_login_errors_are_generic_and_rate_limited` |
| Rate limit | 10 failed sign-ins per email + client address per 10 minutes → 429 (in memory, one api worker) | same |
| Sessions | random 256-bit token in an **HttpOnly, SameSite=Lax** cookie (`Secure` with `COOKIE_SECURE=true` behind HTTPS); only its SHA-256 hash is stored; 24 h lifetime; checked on every request | `test_csrf_logout_revocation_and_expiry` |
| Session fixation | every sign-in issues a new token and revokes the previous session of that browser | `test_signing_in_again_replaces_the_old_session` |
| Logout / revocation | logout revokes the session server-side; changing the password revokes all sessions | same, verify_phase8 |
| CSRF | a per-session token from `/auth/me`, required in `X-CSRF-Token` on every POST/PUT/PATCH/DELETE (constant-time compare); plus SameSite cookies; CORS allows no credentials | `test_csrf_*` |
| Legacy identities | users that existed before Phase 8 were names, not verified identities: status `legacy`, they cannot sign in | `test_legacy_users_cannot_sign_in_*` |
| Secrets in logs | passwords and tokens are never logged; uvicorn logs paths only; invitation tokens appear once in the response that creates them | review + Phase 7.1 log scan |

Local HTTP (`http://localhost:3000`): `COOKIE_SECURE=false` (browsers refuse Secure cookies on plain HTTP).
Production: serve over HTTPS and set `COOKIE_SECURE=true`; nothing else changes.

## Authorization architecture

`api/app/access.py` is the single place that decides. Every protected route calls `current_user` (session +
CSRF) and then one of `require_org`, `require_project`, `require_graph`, `require_source`, `require_conversation`,
which load the resource's real owner from the database, check an **active** membership, then the action level.
A resource the caller may not see answers **404** (ids cannot be probed). Ids, roles, user names and ownership
in request bodies are never trusted; a `user` field sent by an old client is ignored.

### Role and permission matrix

| Action | Workspace owner | Workspace admin | Project contributor | Project viewer | Not on the project |
|---|---|---|---|---|---|
| See the project, its sources, agents | yes (all projects) | yes (all projects) | yes | yes | no (404) |
| Ask (project chat, shared agents) | yes | yes | yes | yes | no |
| Add / re-index sources | yes | yes | yes | no (403) | no |
| Change or delete a source | yes | yes | only sources they added | no | no |
| Create agents; run unsaved graphs | yes | yes | yes | no | no |
| Edit / delete an agent | yes | yes | only agents they created | no | no |
| Create, edit, archive, delete projects; assign members | yes | yes | no | no | no |
| Invite members / confirm new members | yes (admins and members) | yes (members only) | no | no | no |
| Change roles, rename the company | yes | no | no | no | no |
| Read someone else's conversation | **no** | **no** | no | no | no |
| Workspace usage (aggregates, no message text) | yes | yes | own rows only | own rows only | no |

The last owner can be neither demoted nor removed. Admins cannot raise anyone (themselves included) to admin
or owner. A personal workspace has exactly one member, its owner.

## Tenant and project isolation

- Every retrieval runs with the project's scope (`project:<id>`) **and** the authorized, ready source ids of
  that project, filtered in SQL *before* ranking (nothing is retrieved globally and filtered afterwards).
- `POST /pipeline/run` resolves the project from the database: a saved agent (view access), an unsaved graph
  (contributors of the project only; with a `graph_id`, edit rights on that agent), project chat (the api builds
  the graph from the project's ready sources), or a conversation (only its owner). Every source node must point
  at a ready source **of that project** (`source_not_in_project`, `source_not_ready`, `source_not_selected`);
  saving an agent applies the same check. So a modified payload cannot reach another project's knowledge.
- Unauthorized content cannot reach prompts, tool plans, citations, SSE events or error messages, because the
  request is refused before retrieval.
- While an answer streams, access is re-checked every 2 seconds and before the answer is stored: removing someone
  from a project stops their stream with `access_revoked`.
- Tested: an outsider and a same-company member of another project are refused on 26 routes and payload
  variants (direct project, agent, source, file, conversation and usage ids; foreign sources in their own graph;
  an agent saved with a foreign source).

## Files

Stored under the `uploads` volume as `project_<id>/source_<id>/<file>`; never in a public folder (nginx serves
only the built web app). `GET /sources/{id}/file` checks project access and sends `Cache-Control: private,
no-store` and `X-Content-Type-Options: nosniff`. File names are reduced to a safe base name (no `../`, no
drive letters); type is checked by extension **and** content (PDF signature, zip container for DOCX/XLSX,
no NUL bytes in text); size limit `MAX_UPLOAD_MB`; zip containers over 50 MB unpacked or 2,000 entries are
refused; XML with DTD/entity declarations is refused; spreadsheets are read values-only (no formulas, macros or
external links). Refused, replaced and deleted files are removed from disk.

## Invitations

A random 256-bit token, shown once inside a link (`#/invite/<token>`), stored as a SHA-256 hash, valid
`INVITATION_DAYS` days, single use (the row is locked while accepting; concurrent acceptance creates one
membership). Only a signed-in account whose email equals the invited email can accept. Because the app sends no
email, an email string is not proof of identity: the membership is **pending** until an owner or admin confirms
it (administrator-confirmed enrolment; they check out of band that the person is who they say). Revoke and
regenerate (the old link stops working) are available. Tested: wrong account, invalid, reused, revoked, expired,
concurrent acceptance, unauthorized creation, admin inviting an admin.

## Local-only enforcement

Effective local-only = the request's switch OR the agent's setting OR the **project's** setting OR the server's
`LOCAL_ONLY`. With it, a cloud model is refused (400) before anything runs, tool planning and answers stay on the
local models, fallback stays local, and if both local models fail the answer is a clear `all_providers_failed`
error. Verified with Gemini and Groq pointed at a counting fake server: zero requests from local-only questions
(RAG, Calculator, both local models missing), while a normal-project request in the same run did reach it.
Adding a GitHub repo or docs link still downloads from that site: that is separate from LLM inference.

## Legacy data

Moved into "Legacy workspace (imported)" with **no members**: no account can see it. An operator assigns it with
`docker compose exec api python -m app.admin claim-legacy <email>` (inside the container only; no HTTP route).

## Remaining limitations

- No email delivery: no password reset by email, no email verification (hence administrator-confirmed enrolment).
- The login rate limiter is in memory (one api process); several api processes would need a shared store.
- No multi-factor authentication, no account lockout notifications, no audit log of administrative actions.
- An owner/admin who is also the database operator can read anything directly in Postgres; isolation is enforced
  by the application, not by row-level security in the database.
- The prompt-injection check covers one hostile document on one model; it is not a guarantee.
- The api must not be exposed to the internet without HTTPS (`COOKIE_SECURE=true`) and a reverse proxy.
- `/health` is public by design (it returns `{"ok": true}` only).

# Phase 8 final MVP acceptance evidence (2026-11-24)

Every row names the test that produced the result. pytest tests ran against a real Postgres with pgvector
(models and embeddings faked where the test says so); the scripts ran against the real Docker stack with real
models. Raw outputs: `docs/results/phase8_sources.json` (eight-source run), `docs/results/verify-*/` (script
logs, not committed). Status words: PASS = checked and correct; FAIL = checked and wrong; BLOCKED = could not
run because of a provider quota; NOT TESTED / NOT IMPLEMENTED say so.

Test files: `tests/test_tenancy_db.py` (T), `tests/test_sources_db.py` (S), `scripts/verify_phase8.ps1` (V8),
`scripts/verify_phase8_sources.py` (V8S), `scripts/ui_journeys.py` (UI), `scripts/verify_phase7_1.ps1` (V71).
Latest results: pytest 314 passed; V8 37/37; V8S 71/71; UI 36/36; V71 43/43.

## 1. Authentication and authorization

| Requirement | Test name or evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Unauthenticated users cannot use protected APIs | T `test_legacy_users_cannot_sign_in_and_unauthenticated_calls_are_refused`; V8 "only /health is public" | 401 on orgs, projects, graphs, sources, run, conversations, usage, status, models, tools | 401 (404/405 for removed routes); only /health answers | PASS |
| Logout invalidates the session | T `test_csrf_logout_revocation_and_expiry`; V8 "old sessions of signed-out people stay invalid" | the same cookie gives 401 after logout, also after a restart | 401 | PASS |
| Session expiration | T `test_csrf_logout_revocation_and_expiry` (session `expires_at` set in the past) | 401 | 401 | PASS (simulated in the database; not waited 24 h) |
| CSRF protection | T `test_csrf_logout_revocation_and_expiry` | a valid cookie without `X-CSRF-Token` cannot change anything | 403 | PASS |
| Members cannot escalate their roles | T `test_role_escalation_is_refused` | member cannot make self admin, remove an admin or rename the company | 403 each | PASS |
| Admins cannot remove or demote the owner | T `test_role_escalation_is_refused` | admin: remove owner 403, demote owner 403, make self owner 403, invite admins 403 | 403 each; last owner cannot leave (409) | PASS |
| Admins cannot impersonate the owner | Code inspection: no route creates a session for another account; `/auth/password` changes only the caller's password | no way to act as another account | no such route exists | PASS by inspection (no runtime test, nothing to call) |
| Invitation tokens expire and cannot be reused | T `test_invitation_rules`, `test_concurrent_acceptance_creates_one_membership` | reuse 409, revoked 409, expired 410, wrong account 403, 5 parallel accepts make one membership | as expected | PASS |
| Admin confirmation gates access | T `test_invitation_rules`; V8 invitations; UI "members: the owner confirms both" | accepted but unconfirmed person gets 404 for the company | 404 until confirmed, then member | PASS |

## 2. Company and project isolation

| Requirement | Test name or evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Outsider: another company's project | T `test_outsider_is_refused_on_every_route`; V8 outsider D; V8S outsider | 404 | 404 | PASS |
| Outsider: graph (agent) | same | 404 (read, delete, run) | 404 | PASS |
| Outsider: source | same; V8S 8 sources | 404 | 404 for all 8 sources | PASS |
| Outsider: stored files | same; V8S | 404 | 404 for all 8 `/sources/{id}/file` | PASS |
| Outsider: citations | citations exist only inside run results and stored messages: T outsider runs and `/conversations/{id}` | 404 / 422, nothing returned | 404 / 422 | PASS |
| Outsider: usage records | T `/usage/totals?org_id=`, `/usage/recent?project_id=` | 404 | 404 | PASS |
| Same-company employee, unassigned project | T `test_same_company_other_project_is_invisible`; V8 employee E | 404 on project, sources, agents, files, conversations, usage, runs | 404; E sees the company and member list without emails or project names | PASS |
| Modified graph referencing another project's source | T outsider (unsaved graph and save), S `test_an_agent_cannot_point_at_another_projects_source`, verify_phase5 cross-project agent | refused | 404 / 422 | PASS |
| Unauthorized pipeline execution | T outsider (graph_id, project_id, conversation_id, mixed payloads), T `test_project_roles` (viewer cannot run unsaved graphs) | 403 / 404 / 422 | as expected | PASS |
| Unauthorized conversation access | T `test_conversations_are_private_and_keep_context`; V8 "B and C share knowledge but not conversations" | colleague and owner get 404; continuing someone else's conversation 404 | 404 | PASS |

## 3. Membership revocation (employee stays signed in)

| Requirement | Test name or evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Project access | T `test_removing_access_takes_effect_immediately`; V8 "removing C"; V8S | 404 on the next request | 404 | PASS |
| Knowledge listing | same | 404 | 404 | PASS |
| Agent execution | same (graph_id run), V8S (agent and project chat) | 404 | 404 | PASS |
| Conversation continuation | T (conversation_id run); V8 | 404 | 404 | PASS |
| File downloads | T; V8S | 404 | 404 | PASS |
| Citation retrieval | via the stored conversation and new runs | 404 | 404 | PASS |
| Active SSE after revocation | T `test_access_removed_while_an_answer_streams_stops_it`, `test_final_answer_is_withheld_if_access_was_removed_during_the_run` (new); V8S live: removed after 7 tokens | stream ends with `access_revoked`, no result, no stored answer | as expected | PASS |

Limits: access is re-checked every 2 s while tokens stream and always right before the final result; tokens
already sent before the removal are not recalled. Fixed during this verification: for runs outside a
conversation the final result was not re-checked when the 2 s timer had not fired; it now always is.

## 4. Shared knowledge

| Requirement | Test name or evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Employee A adds PDF, GitHub repo, docs URL | V8 (B uploads), V8S (A adds all 8 types), UI (contributor in the browser) | ready, A as uploader | ready, uploader recorded | PASS |
| Employee B uses them without uploading again | V8S: B lists the same 8 ids, cannot upload (viewer), 32 cited answers | same ids, no new sources | 8 sources before and after | PASS |
| Agents reuse sources without new embeddings | T `test_agents_reuse_project_chunks_without_embedding_again` (new); V8S chunk ids before = after 32 questions and the agent | identical chunk rows | identical | PASS |
| Source deletion prevents retrieval | S `test_deleting_an_agent_keeps_its_sources_and_deleting_a_source_unlinks_agents`; V71 "after deleting, that agent can no longer use the removed document" | chunks removed, run refused | 0 chunks; 422 `source_not_in_project` | PASS |
| Agent deletion keeps knowledge used by another agent | S same test | source stays ready for agent two | ready | PASS |
| All eight types have ingestion and retrieval tests | before this check: PDF, repo, URL only; now T `test_every_file_type_is_extracted_chunked_embedded_stored_and_shared` (TXT, MD, DOCX, CSV, XLSX) + V8S all eight with real embeddings and models | each type ingested, stored, answered | yes | PASS |

## 5. Persistent conversations

| Requirement | Test name or evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Separate users, separate histories | T `test_conversations_are_private_and_keep_context`; V8 | each lists only their own | as expected | PASS |
| Cannot read each other's | same | 404 | 404 | PASS |
| Follow-up questions | V8 "follow-up ... 30 minutes" (real Qwen 3 14B); UI consumer chat | the follow-up resolves "it" | "30 minutes", cited | PASS |
| Survive logout and login | UI "history: after signing out and in again" | conversation and 3 questions there | there | PASS |
| Survive a full Docker restart | V8 (`docker compose down` + `up`, volumes kept), V8S (`docker compose restart`) | messages kept, conversation continues | kept; next answer cited | PASS |
| Failed or interrupted answers not stored as completed | T `test_failed_answer_is_stored_as_an_error_not_an_answer`; T revocation test; V8S abandoned stream | error stored as `error`; abandoned or revoked: no `ok` answer | as expected; abandoned stream stored only the question, 0 usage rows (generation stopped) | PASS |

## 6. Project-level local-only privacy

V8 points Gemini and Groq at a counting fake server (`tests/fake_gemini.py`) and checks its counter.

| Requirement | Test name or evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Cannot be overridden (request, agent setting, model, `local_only: false`, unsaved graph) | V8 "a local-only project refuses cloud models" (5 variants); T `test_local_only_project_never_reaches_a_cloud_model` | 400 before anything runs | 400 x 5 | PASS |
| RAG answers | V8 "RAG, Calculator and MCP answers use only local models" | local only | local | PASS |
| Tool planning | same (every generation step) | local only | local | PASS |
| Calculator execution | same (the calculator itself is local code) | local plan and answer | local | PASS |
| MCP execution | same (get_order) | local plan and answer | local | PASS |
| Primary local model missing | V8 "main local model missing: the 3B backup answers" | Qwen 2.5 3B answers, no cloud | as expected | PASS |
| Both local models missing | V8 "both local models missing" | clear error, no cloud | `all_providers_failed`, tried local and local_small | PASS |
| Zero cloud requests | V8 "the fake cloud received zero requests"; control request does reach it | 0 | 0 | PASS |

## 7. Database migration and persistence

| Requirement | Evidence | Expected | Actual | Status |
|---|---|---|---|---|
| Pre- and post-migration counts | Migration run 2026-11-24 (docs/PHASE_8_IMPLEMENTATION.md), rechecked in the live database today | nothing lost | users 20 -> 20, agents 71 -> 71, chunks 206 -> 206, vectors 206 -> 206, usage 1,106 -> 1,106; 82 sources created (53 PDF, 11 repo, 18 page); 0 chunks without a source | PASS |
| Original PDF files preserved | live check of every legacy PDF `storage_path` in the uploads volume | 53 present | 53 present, 0 missing | PASS |
| Embeddings preserved | live: legacy chunks 206, vectors 206 (re-pointed, never recomputed) | equal | equal | PASS |
| Ordinary accounts cannot reach the legacy workspace | T `test_legacy_data_is_quarantined_until_an_operator_assigns_it`; V8 migration check; live: legacy org has 0 members | invisible until `claim-legacy` | as expected | PASS |
| Backup and restoration | V71 "a database backup restores completely into a scratch database" (latest run PASS); the pre-migration dump was restored and migrated twice before the real migration | restored counts equal live counts | equal | PASS |
| New authenticated data survives a full restart | V8 persistence checks; V8S after `docker compose restart` | sources, chunks, memberships, sessions rules, conversations kept | kept | PASS |

## 9. All eight knowledge source types, two employees (V8S, run 2026-11-24 14:5x)

Owner created company #134 and project #169; employee A (contributor) added every source; employee B (viewer)
asked each source in project chat and through one shared agent, on Groq `openai/gpt-oss-120b` and Qwen 3 14B
(32 answers), then again on Groq after a full `docker compose restart` (8 answers). Every chunk has a 1024-dim
bge-m3 vector; the fact was found in the extracted chunk text; chunk ids were unchanged by all of it.

| Type | Source id | Chunks / vectors | Question | Expected | Generated (B, project chat, Groq) | Citation | 4 modes x models | After restart |
|---|---|---|---|---|---|---|---|---|
| PDF | 196 | 2 / 2 | Above what order value is delivery free? | 499 | "Delivery is free for orders above Rs. 499 [1]." | ecommerce-architecture.pdf | 4/4 | PASS |
| GitHub repo | 197 | 5 / 5 | What is the Spoon-Knife repository meant for? | fork / pull request | "...a practice project for users to fork, make changes, and submit a Pull Request..." | README.md | 4/4 | PASS |
| Docs URL | 198 | 1 / 1 | What is the example.com domain for? | documentation examples | "...intended for use in documentation examples without needing permission [1]." | Example Domain | 4/4 | PASS |
| TXT | 199 | 1 / 1 | Until what time does the Hyderabad warehouse ship orders on weekdays? | 6:30 | "...ships orders until 6:30 PM on weekdays [1]." | warehouse-notes.txt | 4/4 | PASS |
| Markdown | 200 | 1 / 1 | Within how many minutes must a priority-1 ticket get a first response? | 15 | "...within 15 minutes [1]." | support-runbook.md | 4/4 | PASS |
| DOCX | 201 | 1 / 1 | How many days of paid leave do employees get each year? | 18 | "Employees get 18 days of paid leave each year [1]." | leave-policy.docx | 4/4 | PASS |
| CSV | 202 | 1 / 1 | What is the maximum parcel weight SwiftShip carries? | 32 | "The maximum parcel weight that SwiftShip carries is 32 kg [1]." | courier-partners.csv | 4/4 | PASS |
| XLSX | 203 | 1 / 1 | For how many months is the Festive gift card valid? | 9 | "The Festive gift card is valid for 9 months [1]." | gift-cards.xlsx | 4/4 | PASS |

Unauthorized access: the outsider got 404 for all 8 sources and files, the listing, the agent and both kinds of
run. Download: employee B downloaded all 6 uploaded files with the same SHA-256 as the originals
(`Content-Disposition: attachment`). **In-browser preview is NOT IMPLEMENTED**: files download; the Knowledge
page shows each source's details (title, type, uploader, chunks, hash, link) but not the document contents.

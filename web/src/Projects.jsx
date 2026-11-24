// Projects of the active workspace: create (owners/admins), open, edit, local-only setting, members (assign
// company members as contributor or viewer), archive and delete (with confirmation).
import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Modal, useConfirm } from "./App.jsx";
import Icon from "./Icon.jsx";

export default function Projects({ org, isAdmin, projects, project, setProjectId, reloadProjects }) {
  const [creating, setCreating] = useState(false);
  const open = project;
  return (
    <section className="page-pad">
      <header className="page-head">
        <div><h1>Projects</h1><p className="muted">Each project has its own members, knowledge, agents and conversations.</p></div>
        {isAdmin && <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" size={15} /> New project</button>}
      </header>
      <div className="split">
        <div className="card list-card">
          {projects === null ? <div className="skeleton-line" /> : projects.length === 0 ? <p className="muted small">No projects yet.</p> : (
            <ul className="list">
              {projects.map((p) => (
                <li key={p.id}><button className={`list-btn${open?.id === p.id ? " on" : ""}`} onClick={() => setProjectId(p.id)}>
                  <span><strong>{p.name}</strong>{p.archived && <span className="pill idle sm-pill">archived</span>}
                    <span className="muted small block">{p.ready_sources} sources · {p.chunks} chunks · {p.agents} agents</span></span>
                </button></li>
              ))}
            </ul>
          )}
        </div>
        {open ? <ProjectDetail key={open.id} id={open.id} org={org} reloadProjects={reloadProjects} /> : <div className="card empty"><p className="muted">Select a project.</p></div>}
      </div>
      {creating && <CreateProject org={org} onClose={() => setCreating(false)} onCreated={async (p) => {
        setCreating(false); await reloadProjects(); setProjectId(p.id);
      }} />}
    </section>
  );
}

function CreateProject({ org, onClose, onCreated }) {
  const [form, setForm] = useState({ name: "", description: "", local_only: false });
  const [error, setError] = useState("");
  async function submit(e) {
    e.preventDefault();
    try { onCreated(await api("/projects", { method: "POST", body: { ...form, org_id: org.id } })); }
    catch (err) { setError(err.message); }
  }
  return (
    <Modal title="New project" onClose={onClose}>
      <form className="stack form" onSubmit={submit}>
        <label className="field"><span className="field-label">Name</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} autoFocus /></label>
        <label className="field"><span className="field-label">Description</span>
          <textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
        <label className="switch-row"><span><span className="switch-label">Local only</span>
          <span className="field-hint">Confidential: answers only from models on this computer, never a cloud AI.</span></span>
          <input type="checkbox" role="switch" className="switch" checked={form.local_only} onChange={(e) => setForm({ ...form, local_only: e.target.checked })} /></label>
        {error && <p className="inline-msg err"><Icon name="alert" size={14} /> {error}</p>}
        <button className="btn primary" disabled={!form.name.trim()}>Create project</button>
      </form>
    </Modal>
  );
}

function ProjectDetail({ id, org, reloadProjects }) {
  const [p, setP] = useState(null);
  const [members, setMembers] = useState([]);
  const [orgMembers, setOrgMembers] = useState([]);
  const [edit, setEdit] = useState(null);
  const [error, setError] = useState("");
  const [ask, dialog] = useConfirm();
  const load = () => {
    api(`/projects/${id}`).then(setP).catch((err) => setError(err.message));
    api(`/projects/${id}/members`).then(setMembers).catch(() => setMembers([]));
  };
  useEffect(load, [id]);
  useEffect(() => {
    if (org.kind === "company" && ["owner", "admin"].includes(org.role)) api(`/orgs/${org.id}/members`).then(setOrgMembers).catch(() => {});
  }, [org.id, org.kind, org.role]);

  async function act(fn) {
    setError("");
    try { await fn(); load(); reloadProjects(); } catch (err) { setError(err.message); }
  }
  if (!p) return <div className="card"><div className="skeleton-line" />{error && <p className="inline-msg err">{error}</p>}</div>;
  const assignable = orgMembers.filter((m) => m.status === "active" && m.role === "member" && !members.some((x) => x.user_id === m.user_id));

  return (
    <div className="stack">
      <div className="card">
        <div className="card-head">
          <div><h2>{p.name}</h2><p className="muted small">{p.org_name} · you: {p.can_manage ? "manage" : p.my_project_role}</p></div>
          {p.local_only && <span className="pill ok push"><Icon name="lock" size={12} /> Local only</span>}
        </div>
        {p.description && <p className="muted">{p.description}</p>}
        <div className="kpi-row compact">
          <div className="kpi"><span className="kpi-label">Sources</span><strong>{p.ready_sources}<small> / {p.sources}</small></strong></div>
          <div className="kpi"><span className="kpi-label">Chunks</span><strong>{p.chunks.toLocaleString()}</strong></div>
          <div className="kpi"><span className="kpi-label">Agents</span><strong>{p.agents}</strong></div>
          <div className="kpi"><span className="kpi-label">People</span><strong>{members.length}</strong></div>
        </div>
        <div className="row-wrap">
          <a className="btn" href="#/app/knowledge"><Icon name="upload" size={15} /> Knowledge</a>
          <a className="btn" href="#/app/agents"><Icon name="builder" size={15} /> Agents</a>
          <a className="btn primary" href="#/app/chat"><Icon name="chat" size={15} /> Open chat</a>
        </div>
        {p.recent?.length > 0 && (
          <>
            <div className="section-label">Recent activity</div>
            <ul className="activity">{p.recent.map((r, i) => <li key={i}><Icon name={r.kind === "agent" ? "builder" : "knowledge"} size={13} /> {r.kind === "agent" ? "Agent" : "Source"} <b>{r.title}</b> by {r.by} · {new Date(r.at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</li>)}</ul>
          </>
        )}
        {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
      </div>

      <div className="card">
        <div className="card-head"><h3>People on this project</h3></div>
        <ul className="list">
          {members.map((m) => (
            <li key={m.user_id} className="member-row">
              <span className="avatar sm">{m.full_name.slice(0, 1)}</span>
              <span className="grow"><strong>{m.full_name}</strong><span className="muted small block">{m.org_role === "member" ? m.project_role : `${m.org_role} (all projects)`}</span></span>
              {p.can_manage && m.org_role === "member" && (
                <>
                  <select value={m.project_role} aria-label={`Role of ${m.full_name}`} onChange={(e) => act(() => api(`/projects/${id}/members/${m.user_id}`, { method: "PUT", body: { role: e.target.value } }))}>
                    <option value="contributor">contributor</option><option value="viewer">viewer</option>
                  </select>
                  <button className="btn ghost sm subtle-danger" onClick={() => ask(`Remove ${m.full_name} from ${p.name}? They lose access at once.`, () => act(() => api(`/projects/${id}/members/${m.user_id}`, { method: "DELETE" })), "Remove")}>Remove</button>
                </>
              )}
            </li>
          ))}
        </ul>
        {p.can_manage && org.kind === "company" && (
          assignable.length ? (
            <AddMember people={assignable} onAdd={(uid, role) => act(() => api(`/projects/${id}/members/${uid}`, { method: "PUT", body: { role } }))} />
          ) : <p className="muted small">Every company member is on this project, or invite people in Members.</p>
        )}
      </div>

      {p.can_manage && (
        <div className="card">
          <div className="card-head"><h3>Settings</h3></div>
          {edit ? (
            <form className="stack form" onSubmit={(e) => { e.preventDefault(); act(async () => { await api(`/projects/${id}`, { method: "PATCH", body: edit }); setEdit(null); }); }}>
              <label className="field"><span className="field-label">Name</span><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
              <label className="field"><span className="field-label">Description</span><textarea rows={2} value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></label>
              <div className="row-end"><button type="button" className="btn ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn primary">Save</button></div>
            </form>
          ) : (
            <div className="stack">
              <label className="switch-row"><span><span className="switch-label">Local only</span><span className="field-hint">No cloud AI for this project, for anyone, not even as fallback.</span></span>
                <input type="checkbox" role="switch" className="switch" checked={p.local_only} onChange={(e) => act(() => api(`/projects/${id}`, { method: "PATCH", body: { local_only: e.target.checked } }))} /></label>
              <div className="row-wrap">
                <button className="btn" onClick={() => setEdit({ name: p.name, description: p.description })}>Edit name and description</button>
                <button className="btn" onClick={() => act(() => api(`/projects/${id}`, { method: "PATCH", body: { archived: !p.archived } }))}>{p.archived ? "Restore" : "Archive"}</button>
                {p.org_kind !== "legacy" && <button className="btn ghost subtle-danger" onClick={() => ask(`Delete "${p.name}" with all its sources, stored files, agents and conversations? This cannot be undone.`, () => act(() => api(`/projects/${id}`, { method: "DELETE" })))}><Icon name="trash" size={14} /> Delete project</button>}
              </div>
            </div>
          )}
        </div>
      )}
      {dialog}
    </div>
  );
}

function AddMember({ people, onAdd }) {
  const [uid, setUid] = useState(people[0]?.user_id || "");
  const [role, setRole] = useState("viewer");
  return (
    <form className="row-wrap add-member" onSubmit={(e) => { e.preventDefault(); onAdd(Number(uid), role); }}>
      <select value={uid} onChange={(e) => setUid(e.target.value)} aria-label="Company member">{people.map((m) => <option key={m.user_id} value={m.user_id}>{m.full_name}</option>)}</select>
      <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Project role"><option value="viewer">viewer</option><option value="contributor">contributor</option></select>
      <button className="btn">Add to project</button>
    </form>
  );
}

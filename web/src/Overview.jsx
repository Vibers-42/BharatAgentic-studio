// Workspace overview: real counts from the api for the active workspace (personal or company), its projects,
// and quick actions. Nothing here is sample data.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import Icon from "./Icon.jsx";

export default function Overview({ org, projects, setProjectId, session }) {
  const [info, setInfo] = useState(null);
  const [recent, setRecent] = useState([]);
  useEffect(() => {
    if (!org) return;
    setInfo(null);
    api(`/orgs/${org.id}`).then(setInfo).catch(() => setInfo(false));
  }, [org?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!projects?.length) { setRecent([]); return; }
    Promise.all(projects.slice(0, 6).map((p) => api(`/conversations?project_id=${p.id}`).then((cs) => cs.slice(0, 3).map((c) => ({ ...c, project: p }))).catch(() => [])))
      .then((all) => setRecent(all.flat().sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 6)));
  }, [projects]);

  if (!org) return <div className="page-pad"><p className="muted">No workspace.</p></div>;
  const company = org.kind === "company";
  const kpis = info ? [
    ["folder", "Projects", info.projects],
    ["knowledge", "Ready sources", `${info.ready_sources}`, `${info.chunks.toLocaleString()} chunks indexed`],
    ["builder", "Agents", info.agents],
    ...(company ? [["user", "Members", info.members, info.pending_members ? `${info.pending_members} waiting for confirmation` : null]] : []),
  ] : null;

  return (
    <section className="page-pad">
      <header className="page-head">
        <div>
          <p className="eyebrow">{company ? `Company · you are ${org.role}` : "Personal workspace · private to you"}</p>
          <h1>{company ? org.name : `Welcome, ${session.user.full_name.split(" ")[0]}`}</h1>
          {info?.description && <p className="muted">{info.description}</p>}
        </div>
        <div className="head-actions">
          <a className="btn" href="#/app/knowledge"><Icon name="upload" size={15} /> Add knowledge</a>
          <a className="btn primary" href="#/app/chat"><Icon name="chat" size={15} /> Ask a question</a>
        </div>
      </header>
      <div className="kpi-row">
        {kpis ? kpis.map(([icon, label, value, sub]) => (
          <div key={label} className="kpi"><span className="kpi-label"><Icon name={icon} size={14} /> {label}</span><strong>{value}</strong>{sub && <small className="muted">{sub}</small>}</div>
        )) : [0, 1, 2, 3].map((i) => <div key={i} className="kpi skeleton" />)}
      </div>
      <div className="grid2">
        <div className="card">
          <div className="card-head"><h3>Projects</h3><a className="btn ghost sm push" href="#/app/projects">Manage</a></div>
          {projects === null ? <div className="skeleton-line" /> : projects.length === 0 ? (
            <p className="muted small">No projects you can see yet{company && org.role === "member" ? ": ask an owner or admin to add you to one." : "."}</p>
          ) : (
            <ul className="list">
              {projects.map((p) => (
                <li key={p.id}>
                  <button className="list-btn" onClick={() => { setProjectId(p.id); window.location.hash = "#/app/chat"; }}>
                    <span><strong>{p.name}</strong>{p.local_only && <span className="pill ok sm-pill"><Icon name="lock" size={11} /> local only</span>}
                      <span className="muted small block">{p.ready_sources} sources · {p.agents} agents · {p.can_manage ? "you manage it" : p.my_project_role}</span></span>
                    <Icon name="connect" size={15} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <div className="card-head"><h3>Your recent conversations</h3></div>
          {recent.length === 0 ? <p className="muted small">No conversations yet. Open Chat and ask about a project.</p> : (
            <ul className="list">
              {recent.map((c) => (
                <li key={c.id}><button className="list-btn" onClick={() => { setProjectId(c.project.id); window.location.hash = `#/app/chat`; }}>
                  <span><strong>{c.title}</strong><span className="muted small block">{c.project.name} · {new Date(c.updated_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</span></span>
                  <Icon name="chat" size={15} /></button></li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

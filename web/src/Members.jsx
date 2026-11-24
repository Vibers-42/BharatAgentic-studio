// Company members and invitations. Owners change roles; owners and admins invite (owners also invite admins),
// confirm people who accepted an invitation, and remove ordinary members. No email is sent: the invitation link
// is shown once, to copy and share yourself.
import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { useConfirm } from "./App.jsx";
import Icon from "./Icon.jsx";

export default function Members({ org, session }) {
  const [members, setMembers] = useState(null);
  const [invites, setInvites] = useState([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [link, setLink] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [ask, dialog] = useConfirm();
  const admin = org && ["owner", "admin"].includes(org.role);

  const load = useCallback(() => {
    if (!org || org.kind !== "company") return;
    api(`/orgs/${org.id}/members`).then(setMembers).catch((err) => setError(err.message));
    if (admin) api(`/orgs/${org.id}/invitations`).then(setInvites).catch(() => {});
  }, [org?.id, admin]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(load, [load]);

  if (!org || org.kind !== "company") {
    return <section className="page-pad"><div className="empty"><Icon name="user" size={26} /><h3>Your personal workspace is private</h3>
      <p className="muted">Create a company from the workspace menu to invite colleagues.</p></div></section>;
  }
  async function act(fn) { setError(""); try { await fn(); load(); } catch (err) { setError(err.message); } }
  const showLink = (r) => { setLink({ email: r.email, url: `${window.location.origin}/#/invite/${r.token}`, expires: r.expires_at }); setCopied(false); };

  return (
    <section className="page-pad">
      <header className="page-head"><div><h1>Members</h1><p className="muted">{org.name} · you are {org.role}</p></div></header>
      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
      {admin && (
        <div className="card">
          <div className="card-head"><h3>Invite a colleague</h3></div>
          <form className="row-wrap" onSubmit={(e) => { e.preventDefault(); act(async () => { showLink(await api(`/orgs/${org.id}/invitations`, { method: "POST", body: { email, role } })); setEmail(""); }); }}>
            <input type="email" placeholder="colleague@company.com" value={email} onChange={(e) => setEmail(e.target.value)} required aria-label="Email to invite" className="grow" />
            <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
              <option value="member">member</option>{org.role === "owner" && <option value="admin">admin</option>}
            </select>
            <button className="btn primary"><Icon name="plus" size={14} /> Create invitation</button>
          </form>
          {link && (
            <div className="invite-link" role="status">
              <p className="small">Send this link to <b>{link.email}</b> yourself (no email is sent). It works once, until {new Date(link.expires).toLocaleDateString("en-IN")}. When they accept, confirm them below.</p>
              <div className="row-wrap"><input readOnly value={link.url} aria-label="Invitation link" className="grow mono" onFocus={(e) => e.target.select()} />
                <button className="btn" onClick={() => navigator.clipboard?.writeText(link.url).then(() => setCopied(true))}>{copied ? "Copied" : "Copy link"}</button></div>
            </div>
          )}
        </div>
      )}
      <div className="card">
        <div className="card-head"><h3>People</h3></div>
        {members === null ? <div className="skeleton-line" /> : (
          <ul className="list">
            {members.map((m) => (
              <li key={m.user_id} className="member-row">
                <span className="avatar sm">{m.full_name.slice(0, 1)}</span>
                <span className="grow"><strong>{m.full_name}{m.user_id === session.user.id ? " (you)" : ""}</strong>
                  <span className="muted small block">{m.email || ""}{m.projects?.length ? ` · ${m.projects.join(", ")}` : ""}</span></span>
                {m.status === "pending" ? (
                  <>
                    <span className="pill warn">waiting for confirmation</span>
                    {admin && <button className="btn primary sm" onClick={() => act(() => api(`/orgs/${org.id}/members/${m.user_id}/confirm`, { method: "POST" }))}>Confirm</button>}
                  </>
                ) : org.role === "owner" && m.user_id !== session.user.id ? (
                  <select value={m.role} aria-label={`Role of ${m.full_name}`} onChange={(e) => act(() => api(`/orgs/${org.id}/members/${m.user_id}`, { method: "PATCH", body: { role: e.target.value } }))}>
                    <option value="owner">owner</option><option value="admin">admin</option><option value="member">member</option>
                  </select>
                ) : <span className="pill">{m.role}</span>}
                {admin && m.user_id !== session.user.id && (org.role === "owner" || m.role === "member") && (
                  <button className="btn ghost sm subtle-danger" onClick={() => ask(`Remove ${m.full_name} from ${org.name}? They lose access to every project at once.`, () => act(() => api(`/orgs/${org.id}/members/${m.user_id}`, { method: "DELETE" })), "Remove")}>Remove</button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {admin && invites.length > 0 && (
        <div className="card">
          <div className="card-head"><h3>Invitations</h3></div>
          <div className="table-wrap"><table className="data-table">
            <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Expires</th><th>By</th><th /></tr></thead>
            <tbody>{invites.map((i) => (
              <tr key={i.id}><td>{i.email}</td><td>{i.role}</td><td><span className={`pill ${i.status === "accepted" ? "ok" : i.status === "pending" ? "running" : "idle"}`}>{i.status}</span></td>
                <td>{new Date(i.expires_at).toLocaleDateString("en-IN")}</td><td>{i.invited_by}</td>
                <td className="row-end">{i.status !== "accepted" && <button className="btn ghost sm" onClick={() => act(async () => showLink({ ...(await api(`/invitations/${i.id}/regenerate`, { method: "POST" })), email: i.email }))}>New link</button>}
                  {i.status === "pending" && <button className="btn ghost sm subtle-danger" onClick={() => act(() => api(`/invitations/${i.id}/revoke`, { method: "POST" }))}>Revoke</button>}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      {dialog}
    </section>
  );
}

// Settings for the selected node. Agent: model (all four), language, local-only. Guardrails: which checks.
// Sources: upload a PDF, fetch a docs link or download a GitHub repo into THIS saved graph's documents
// (?graph_id=...), with the stored chunks shown from the api. Tools: what they do, the MCP server's
// read-only tools (listed live from the mcp-runner), and a keyboard-friendly "connect to Agent" switch.
import Icon from "../Icon.jsx";
import SourceCard from "../SourceCard.jsx";
import { kindOf, NODE_KINDS } from "./graphModel.js";

const CHECKS = [
  ["empty", "Empty questions"],
  ["abuse", "Abusive language"],
  ["off_topic", "Off-topic questions"],
];

const ABOUT = {
  input: "Receives the question and detects its language (English, Telugu or Hindi).",
  router: "Chooses the path: search the sources, call a tool, or answer directly. The rules are shown in the run log.",
  output: "Packs the answer with citations, tokens, cost and the model that answered.",
  tool_calculator: "Exact arithmetic for amounts in your documents: + − × ÷, percentages (18% of 4500), round, min, max, sqrt. The agent's model decides the expression; the calculator computes it safely (no code runs).",
};

function Field({ label, children, hint }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

function Switch({ checked, onChange, label, hint, disabled }) {
  return (
    <label className={`switch-row${disabled ? " disabled" : ""}`}>
      <span>
        <span className="switch-label">{label}</span>
        {hint && <span className="field-hint">{hint}</span>}
      </span>
      <input type="checkbox" role="switch" className="switch" checked={checked} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} aria-label={label} />
    </label>
  );
}

function McpTools({ tools }) {
  const server = tools?.mcp_servers?.[0];
  if (!tools) return <div className="skeleton-line" />;
  if (!server?.ok) return <p className="inline-msg err"><Icon name="alert" size={14} /> {server?.error || "MCP runner unavailable"}</p>;
  return (
    <ul className="mcp-tools">
      {server.tools.map((t) => (
        <li key={t.name}>
          <span className="mono">{t.name}</span>
          <span className={`pill ${t.read_only ? "ok" : "warn"}`}>{t.read_only ? "read-only" : "writes · blocked"}</span>
          <span className="muted small">{t.description}</span>
        </li>
      ))}
    </ul>
  );
}

export default function ConfigPanel({ node, models, graphId, sourceInfo, tools, connected, onConnect, onConfig, onDelete, onSave, errors, onChangedSources }) {
  if (!node) {
    return (
      <div className="empty small-empty">
        <Icon name="builder" size={22} />
        <p>Select a block to configure it.</p>
        <p className="muted small">Drag a source or tool from the palette, then draw a line from its handle to the Agent.</p>
      </div>
    );
  }
  const { type, config } = node.data;
  const kind = kindOf(type, config);
  const spec = NODE_KINDS[kind];
  const set = (key, value) => onConfig({ ...config, [key]: value });
  const isExtra = spec.group !== "main";

  return (
    <div className="inspector">
      <div className="inspector-head">
        <span className={`chip-icon group-${spec.group}`}><Icon name={kind} size={16} /></span>
        <div>
          <strong>{spec.label}</strong>
          <div className="muted mono small">{node.id} · {type}</div>
        </div>
      </div>

      {errors?.length > 0 && (
        <div className="inline-msg err" role="alert">
          <Icon name="alert" size={14} />
          <span>{errors.join(" ")}</span>
        </div>
      )}

      {ABOUT[type] && <p className="muted">{ABOUT[type]}</p>}

      {type === "agent" && (
        <>
          <Field label="Model" hint={models.find((m) => m.key === (config.model || "gemini"))?.why_not_ready || "The chat's model choice overrides this for one question."}>
            <select value={config.model || "gemini"} onChange={(e) => set("model", e.target.value)} aria-label="Agent model">
              {models.filter((m) => !config.local_only || m.local).map((m) => (
                <option key={m.key} value={m.key}>{m.label}{m.local ? "" : " · cloud"}{m.ready ? "" : " (not set up)"}</option>
              ))}
            </select>
          </Field>
          <Field label="Answer language">
            <select value={config.language || "en"} onChange={(e) => set("language", e.target.value)} aria-label="Agent language">
              <option value="en">English</option>
              <option value="te">తెలుగు · Telugu</option>
              <option value="hi">हिन्दी · Hindi</option>
            </select>
          </Field>
          <Switch label="Local only" checked={config.local_only === true}
            hint="No cloud AI at all, not even as fallback. Answers come from Qwen on this computer."
            onChange={(on) => onConfig({ ...config, local_only: on,
              model: on && !models.find((m) => m.key === config.model)?.local ? "local" : config.model })} />
        </>
      )}

      {type === "guardrails" && (
        <div className="stack">
          <span className="field-label">Block</span>
          {CHECKS.map(([c, label]) => (
            <label key={c} className="check-row">
              <input type="checkbox" checked={(config.block || []).includes(c)}
                onChange={(e) => set("block", e.target.checked ? [...(config.block || []), c] : (config.block || []).filter((x) => x !== c))} />
              {label}
            </label>
          ))}
        </div>
      )}

      {(type === "source_doc" || type === "source_repo") && (
        graphId ? (
          <SourceCard key={`${graphId}-${node.id}`} sourceId={node.id} kind={kind} graphId={graphId}
            info={sourceInfo} initialUrl={config.url || ""} onChanged={onChangedSources}
            onConfig={(patch) => onConfig({ ...config, ...patch })} />
        ) : (
          <div className="callout">
            <Icon name="info" size={16} />
            <div>
              <p>Documents belong to a saved agent, so other agents never see them.</p>
              <button className="btn primary sm" onClick={onSave}><Icon name="save" size={14} /> Save agent</button>
            </div>
          </div>
        )
      )}

      {type === "tool_mcp" && (
        <>
          <Field label="Server" hint="Only servers the api knows can be used.">
            <select value={config.server || ""} onChange={(e) => set("server", e.target.value)} aria-label="MCP server">
              {!config.server && <option value="">Choose…</option>}
              {(tools?.mcp_servers || [{ name: "orders-db" }]).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Mode">
            <select value={config.mode || ""} onChange={(e) => set("mode", e.target.value)} aria-label="MCP mode">
              <option value="read_only">Read-only</option>
              <option value="read_write">Read and write (needs allow_write)</option>
            </select>
          </Field>
          <div className="stack">
            <span className="field-label">Tools on {config.server || "the server"}</span>
            <McpTools tools={tools} />
          </div>
        </>
      )}

      {isExtra && (
        <Switch label="Connected to Agent" checked={connected} onChange={onConnect}
          hint="Same as drawing a line to the Agent. Only connected sources and tools are ever used." />
      )}

      {isExtra && (
        <button className="btn ghost subtle-danger" onClick={onDelete}><Icon name="trash" size={14} /> Remove block</button>
      )}
      {!isExtra && <p className="muted small">Part of the fixed main line: Input → Guardrails → Router → Agent → Output.</p>}
    </div>
  );
}

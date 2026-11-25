// One canvas node, for all nine types. It shows the node's kind, id, its settings, what it holds (sources:
// chunks and file from the api's source listing) or last did (tools: the latest call and result), and its
// live status from the run's SSE events (running / done / blocked / skipped / error; sources: searched /
// cited; tools: called / cited). Handles: the main line flows top to bottom (Input only sends, Output only
// receives); sources send from their right side into the Agent's left handle, tools from their left side
// into its right handle.
import { Handle, Position } from "@xyflow/react";
import Icon from "../Icon.jsx";
import { NODE_KINDS, summary } from "./graphModel.js";

const STATUS_TEXT = {
  running: "Running", done: "Done", blocked: "Blocked", skipped: "Skipped", error: "Failed",
  searched: "Searched", cited: "Cited", called: "Called",
};

const KIND_ICON = {
  input: "input", guardrails: "guardrails", router: "router", agent: "agent", output: "output",
  pdf: "pdf", url: "url", repo: "repo", calculator: "calculator", mcp: "mcp",
};

function SourceLine({ data }) {
  const info = data.source;
  if (!info) {
    const wanted = data.config.title;  // set by a generated agent: what this block should hold
    return <span className="node-line warn" title={wanted}><Icon name="alert" size={13} /> {wanted ? `Add: ${wanted}` : "Choose project knowledge"}</span>;
  }
  if (info.status !== "ready") return <span className="node-line warn"><Icon name="alert" size={13} /> {info.file}: {info.status}</span>;
  return (
    <span className="node-line ok" title={info.file_names?.join("\n")}>
      <Icon name="check" size={13} /> {info.chunks} chunks · {info.file}
    </span>
  );
}

function ToolLine({ data }) {
  const t = data.tool;
  if (!t) return null;
  if (t.pending) return <span className="node-line running">Calling {t.tool}…</span>;
  return t.ok ? (
    <span className="node-line ok mono" title={t.output}>{t.tool === "calculator" ? t.output : `${t.tool} ✓`}</span>
  ) : (
    <span className="node-line err" title={t.error}><Icon name="alert" size={13} /> {t.error}</span>
  );
}

function agentSummary(data) {
  const c = data.config;
  const lang = { en: "English", te: "Telugu", hi: "Hindi" }[c.language || "en"];
  return `${data.modelLabel || c.model || "groq"} · ${lang}`;
}

export default function PipelineNode({ id, data, selected }) {
  const spec = NODE_KINDS[data.kind];
  const group = spec?.group || "main";
  const status = data.status;
  const classes = ["pnode", `pnode-${group}`, `kind-${data.kind}`, status ? `is-${status}` : "",
    data.invalid ? "is-invalid" : "", selected ? "is-selected" : ""];
  const text = data.type === "agent" ? agentSummary(data) : data.kind === "pdf" ? "" : summary(data.type, data.config);
  return (
    <div className={classes.join(" ")} data-node-id={id} data-status={status || "idle"}
      title={data.invalid?.join("\n")} aria-label={`${spec?.label || data.type} ${id}${status ? `, ${STATUS_TEXT[status] || status}` : ""}`}>
      {data.type !== "input" && group === "main" && <Handle type="target" id="top" position={Position.Top} />}
      {data.type === "agent" && <Handle type="target" id="left" position={Position.Left} className="side-handle" />}
      {data.type === "agent" && <Handle type="target" id="right" position={Position.Right} className="side-handle" />}
      <div className="pnode-head">
        <span className="pnode-icon"><Icon name={KIND_ICON[data.kind] || "info"} size={16} /></span>
        <span className="pnode-titles">
          <strong>{spec?.label || data.type}</strong>
          <span className="pnode-id">{id}</span>
        </span>
        {status && <span className={`pnode-status s-${status}`}>{STATUS_TEXT[status] || status}</span>}
      </div>
      {text && <div className="pnode-summary" title={text}>{text}</div>}
      {data.type === "agent" && data.config.local_only && (
        <span className="node-line ok"><Icon name="lock" size={13} /> Local only</span>
      )}
      {(data.type === "source_doc" || data.type === "source_repo") && <SourceLine data={data} />}
      {group === "tool" && <ToolLine data={data} />}
      {data.invalid && <div className="pnode-error"><Icon name="alert" size={13} /> {data.invalid[0]}</div>}
      {group === "main" && data.type !== "output" && <Handle type="source" position={Position.Bottom} />}
      {group === "source" && <Handle type="source" position={Position.Right} />}
      {group === "tool" && <Handle type="source" position={Position.Left} />}
    </div>
  );
}

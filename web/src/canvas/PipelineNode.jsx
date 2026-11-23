// One canvas node, for all nine types. It shows the node's kind, id, a summary of its settings, and its
// live status from the run (running / done / blocked / skipped / error, or searched / cited for sources).
// Handles: sources and tools only send (right); Input only sends; Output only receives.
import { Handle, Position } from "@xyflow/react";
import { NODE_KINDS, summary } from "./graphModel.js";

const STATUS_TEXT = {
  running: "running…", done: "done", blocked: "blocked", skipped: "skipped", error: "error",
  searched: "searched", cited: "cited",
};

export default function PipelineNode({ id, data, selected }) {
  const spec = NODE_KINDS[data.kind];
  const group = spec?.group || "main";
  const status = data.status;
  const classes = ["pnode", `pnode-${group}`, status ? `is-${status}` : "", data.invalid ? "is-invalid" : "", selected ? "is-selected" : ""];
  return (
    <div className={classes.join(" ")} data-node-id={id} data-status={status || "idle"} title={data.invalid?.join("\n")}>
      {data.type !== "input" && group === "main" && <Handle type="target" position={Position.Left} />}
      <div className="pnode-head">
        <strong>{spec?.label || data.type}</strong>
        <span className="pnode-id">{id}</span>
      </div>
      <div className="pnode-summary">{summary(data.type, data.config)}</div>
      {status && <div className={`pnode-status s-${status}`}>{STATUS_TEXT[status] || status}</div>}
      {data.invalid && <div className="pnode-error">{data.invalid[0]}</div>}
      {data.type !== "output" && <Handle type="source" position={Position.Right} />}
    </div>
  );
}

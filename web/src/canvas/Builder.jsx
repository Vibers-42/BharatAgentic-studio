// The visual agent builder. Draw the pipeline (the five fixed blocks plus sources and tools), connect a
// source to the Agent by drawing a line, configure nodes, save/load graphs, and run a question. Everything
// is checked and executed by the api (POST /pipeline/validate, /pipeline/run); nodes light up from the
// api's own Server-Sent Events as they arrive. Nothing here simulates a run.
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  addEdge, Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useEdgesState, useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import AnswerCard from "../AnswerCard.jsx";
import { runPipeline } from "../sse.js";
import ConfigPanel from "./ConfigPanel.jsx";
import {
  applyEvent, defaultCanvas, edgesWithActivity, errorsByNode, fromGraph, newNode, NODE_KINDS, PALETTE, toGraph,
} from "./graphModel.js";
import PipelineNode from "./PipelineNode.jsx";

const nodeTypes = { pipeline: PipelineNode };

async function postJson(url, body, method = "POST") {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function BuilderInner({ user }) {
  const start = useMemo(() => defaultCanvas(), []);
  const [nodes, setNodes, onNodesChange] = useNodesState(start.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(start.edges);
  const [selectedId, setSelectedId] = useState(null);
  const [graphId, setGraphId] = useState(null);
  const [name, setName] = useState("My agent");
  const [saved, setSaved] = useState([]);
  const [models, setModels] = useState([]);
  const [errors, setErrors] = useState({ byNode: {}, general: [] });
  const [message, setMessage] = useState("");
  const [statuses, setStatuses] = useState({});
  const [question, setQuestion] = useState("");
  const [streamed, setStreamed] = useState("");
  const [result, setResult] = useState(null);
  const [notices, setNotices] = useState([]);
  const [runError, setRunError] = useState("");
  const [busy, setBusy] = useState(false);

  const refreshSaved = useCallback(() => {
    fetch("/api/graphs").then((r) => r.json()).then(setSaved).catch(() => {});
  }, []);
  useEffect(() => {
    refreshSaved();
    fetch("/api/models").then((r) => r.json()).then((d) => setModels(d.models)).catch(() => {});
  }, [refreshSaved]);

  const graph = () => toGraph(nodes, edges);

  // Status and validation marks are layered onto the nodes for display only.
  const shownNodes = nodes.map((n) => ({
    ...n, data: { ...n.data, status: statuses[n.id], invalid: errors.byNode[n.id] },
  }));
  const shownEdges = edgesWithActivity(edges, statuses);
  const selected = nodes.find((n) => n.id === selectedId) || null;

  const onConnect = useCallback((c) => setEdges((es) => addEdge({ ...c, id: `${c.source}->${c.target}` }, es)), [setEdges]);

  function addNode(kind) {
    const node = newNode(kind, nodes, { x: 60 + nodes.length * 25, y: 40 + (nodes.length % 4) * 30 });
    setNodes((ns) => [...ns, node]);
    setSelectedId(node.id);
  }

  function updateConfig(config) {
    setNodes((ns) => ns.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, config } } : n)));
  }

  function removeSelected() {
    setNodes((ns) => ns.filter((n) => n.id !== selectedId));
    setEdges((es) => es.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }

  async function validate() {
    const { data } = await postJson("/api/pipeline/validate", graph());
    setErrors(errorsByNode(data.errors || []));
    return data.valid === true;
  }

  async function save() {
    setMessage("");
    const { ok, status, data } = graphId
      ? await postJson(`/api/graphs/${graphId}`, { name, graph: graph() }, "PUT")
      : await postJson("/api/graphs", { name, graph: graph() });
    if (!ok) {
      setErrors(errorsByNode(data.detail?.errors || []));
      setMessage(status === 422 ? "Not saved: fix the errors marked on the canvas." : `Not saved (HTTP ${status}).`);
      return;
    }
    setErrors({ byNode: {}, general: [] });
    setGraphId(data.id);
    setMessage(`Saved as graph ${data.id}.`);
    refreshSaved();
  }

  async function load(id) {
    if (!id) return;
    const res = await fetch(`/api/graphs/${id}`);
    if (!res.ok) return setMessage(`Could not load graph ${id}.`);
    const g = fromGraph(await res.json());
    setNodes(g.nodes);
    setEdges(g.edges);
    setGraphId(Number(id));
    setName(saved.find((s) => s.id === Number(id))?.name || `Graph ${id}`);
    setStatuses({});
    setErrors({ byNode: {}, general: [] });
    setResult(null);
    setStreamed("");
    setSelectedId(null);
    setMessage(`Loaded graph ${id}.`);
  }

  function startNew() {
    const g = defaultCanvas();
    setNodes(g.nodes);
    setEdges(g.edges);
    setGraphId(null);
    setName("My agent");
    setStatuses({});
    setErrors({ byNode: {}, general: [] });
    setResult(null);
    setMessage("New graph (not saved yet).");
  }

  async function run(e) {
    e.preventDefault();
    setRunError("");
    setResult(null);
    setStreamed("");
    setNotices([]);
    setStatuses({});
    if (!(await validate())) {
      setRunError("The graph is not valid: fix the errors marked on the canvas, then run again.");
      return;
    }
    setBusy(true);
    try {
      // The canvas graph runs as drawn; graph_id (if saved) says whose documents may be searched.
      const body = { question, graph: graph(), user, ...(graphId ? { graph_id: graphId } : {}) };
      await runPipeline(body, (ev) => {
        setStatuses((s) => applyEvent(s, ev));
        if (ev.type === "token") setStreamed((t) => t + ev.text);
        if (ev.type === "token_reset") {
          setStreamed("");
          setNotices((n) => [...n, `${ev.model} stopped mid-answer (${ev.code}); its partial text was discarded.`]);
        }
        if (ev.type === "fallback") setNotices((n) => [...n, `${ev.from} failed (${ev.code}); ${ev.to} is answering instead.`]);
        if (ev.type === "result") setResult(ev);
        if (ev.type === "error") setRunError(`${ev.stage}: ${ev.message}`);
      });
    } catch (err) {
      setRunError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="builder">
      <div className="toolbar card">
        <input className="graph-name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Graph name" />
        <button onClick={save}>{graphId ? "Save" : "Save as new"}</button>
        <button onClick={validate}>Validate</button>
        <button onClick={startNew}>New</button>
        <select value="" onChange={(e) => load(e.target.value)} aria-label="Load a saved graph">
          <option value="">Load saved graph…</option>
          {saved.map((s) => <option key={s.id} value={s.id}>{s.id}: {s.name}</option>)}
        </select>
        <span className="muted">{graphId ? `graph ${graphId}` : "not saved"}</span>
        {message && <span className="muted">{message}</span>}
      </div>

      <div className="palette card">
        <strong>Add</strong>
        {PALETTE.map((k) => (
          <button key={k} className={`pal pal-${NODE_KINDS[k].group}`} onClick={() => addNode(k)}>+ {NODE_KINDS[k].label}</button>
        ))}
        <span className="muted">Draw a line from a source or tool to the Agent to connect it.</span>
      </div>

      {(errors.general.length > 0 || Object.keys(errors.byNode).length > 0) && (
        <div className="card validation" role="alert">
          <strong>Graph problems</strong>
          <ul>
            {errors.general.map((m) => <li key={m}>{m}</li>)}
            {Object.entries(errors.byNode).flatMap(([id, ms]) => ms.map((m) => <li key={id + m}><b>{id}</b>: {m}</li>))}
          </ul>
        </div>
      )}

      <div className="canvas-row">
        <div className="canvas" data-testid="canvas">
          <ReactFlow
            nodes={shownNodes}
            edges={shownEdges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, n) => setSelectedId(n.id)}
            onPaneClick={() => setSelectedId(null)}
            deleteKeyCode={["Backspace", "Delete"]}
            fitView
          >
            <Background />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
        </div>
        <ConfigPanel node={selected} models={models} graphId={graphId} onConfig={updateConfig} onDelete={removeSelected} />
      </div>

      <form className="ask card" onSubmit={run}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label="Question"
          placeholder="Ask in English, తెలుగు or हिन्दी, then watch the blocks light up…" />
        <button disabled={busy || !question.trim()}>{busy ? "Running…" : "Run"}</button>
      </form>
      {notices.map((n) => <p key={n} className="notice">{n}</p>)}
      {runError && <p className="error">{runError}</p>}
      <AnswerCard streamed={streamed} result={result} busy={busy} />
    </section>
  );
}

export default function Builder(props) {
  return (
    <ReactFlowProvider>
      <BuilderInner {...props} />
    </ReactFlowProvider>
  );
}

// The visual agent builder. Drag sources and tools onto the canvas (or click to add), connect them to the
// Agent by drawing a line, configure blocks, save/load agents, and run a question. Everything is checked
// and executed by the api (POST /pipeline/validate, /pipeline/run); blocks light up from the api's own
// Server-Sent Events as they arrive. Nothing here simulates a run.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge, Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "../api.js";
import Icon from "../Icon.jsx";
import { runPipeline } from "../sse.js";
import ConfigPanel from "./ConfigPanel.jsx";
import DesignDialog, { DesignNotes } from "./DesignDialog.jsx";
import {
  applyEvent, autoLayout, defaultCanvas, edgesWithActivity, errorsByNode, fromGraph, newNode, NODE_KINDS, PALETTE, targetHandle, toGraph,
} from "./graphModel.js";
import PipelineNode from "./PipelineNode.jsx";
import RunConsole from "./RunConsole.jsx";

const nodeTypes = { pipeline: PipelineNode };
const DRAG_TYPE = "application/x-bharat-node";
const NO_ERRORS = { byNode: {}, general: [] };
const PALETTE_HINT = {
  pdf: "Upload a PDF", url: "Fetch a docs page", repo: "Index a GitHub repository",
  calculator: "Exact arithmetic", mcp: "Orders lookup; actions need approval",
};

function BuilderInner({ project }) {
  const start = useMemo(() => defaultCanvas(), []);
  const flow = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState(start.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(start.edges);
  const [selectedId, setSelectedId] = useState(null);
  const [graphId, setGraphId] = useState(null);
  const [name, setName] = useState("My agent");
  const [savedJson, setSavedJson] = useState(null);
  const [saved, setSaved] = useState([]);
  const [models, setModels] = useState([]);
  const [tools, setTools] = useState(null);
  const [sources, setSources] = useState([]);   // the project's knowledge registry
  const [meta, setMeta] = useState(null);       // the opened agent: creator, can_edit
  const [errors, setErrors] = useState(NO_ERRORS);
  const [toast, setToast] = useState(null);
  const [statuses, setStatuses] = useState({});
  const [toolActivity, setToolActivity] = useState({});
  const [question, setQuestion] = useState("");
  const [streamed, setStreamed] = useState("");
  const [result, setResult] = useState(null);
  const [log, setLog] = useState([]);
  const [runTools, setRunTools] = useState([]);
  const [approvals, setApprovals] = useState([]);  // risky tool calls waiting for (or given) the user's decision
  const [runError, setRunError] = useState("");
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState("inspect");
  const [designing, setDesigning] = useState(false);   // the "generate from a description" dialog is open
  const [design, setDesign] = useState(null);          // notes about the last generated agent
  const [designSeed, setDesignSeed] = useState("");     // an example picked on the start card, prefilled in the dialog
  const [heroHidden, setHeroHidden] = useState(false);  // the start card was dismissed
  const questionRef = useRef(null);
  const runAbort = useRef(null);  // Stop: cancels the streaming run (the api stops when the stream closes)
  const canvasRef = useRef(null);

  // Fit the drawing to the canvas whenever the canvas gets a new size: when the builder is first shown
  // (it starts hidden if the app opens on another screen), when the window is resized, or on rotation.
  useEffect(() => {
    if (!canvasRef.current) return undefined;
    let last = { w: 0, h: 0 };
    const ro = new ResizeObserver(([e]) => {
      const { width: w, height: h } = e.contentRect;
      if (w > 0 && h > 0 && (Math.abs(w - last.w) > 40 || Math.abs(h - last.h) > 40)) {
        last = { w, h };
        requestAnimationFrame(() => flow.fitView({ padding: 0.15, maxZoom: 1 }));
      }
    });
    ro.observe(canvasRef.current);
    return () => ro.disconnect();
  }, [flow]);

  const say = useCallback((text, tone = "ok") => setToast({ text, tone, id: Date.now() }), []);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const refreshSaved = useCallback(() => {
    api(`/graphs?project_id=${project.id}`).then(setSaved).catch(() => {});
  }, [project.id]);
  const refreshSources = useCallback(() => api(`/projects/${project.id}/sources`).then(setSources).catch(() => setSources([])),
    [project.id]);
  useEffect(() => {
    refreshSaved();
    api("/models").then((d) => setModels(d.models)).catch(() => {});
    api("/tools").then(setTools).catch(() => setTools({ mcp_servers: [{ name: "orders-db", ok: false, error: "Cannot reach the api.", tools: [] }] }));
  }, [refreshSaved]);
  useEffect(() => { refreshSources(); }, [refreshSources]);
  // Contributors and managers edit; viewers (and contributors on someone else's agent) open and run saved agents.
  const canEdit = graphId ? !!meta?.can_edit : project.can_contribute;

  const graph = () => toGraph(nodes, edges);
  const currentJson = JSON.stringify(toGraph(nodes, edges)) + name;
  const dirty = savedJson !== currentJson;
  const modelLabel = Object.fromEntries(models.map((m) => [m.key, m.label]));

  // Status, validation marks, source contents and tool results are layered onto the nodes for display only.
  const byId = Object.fromEntries(sources.map((s) => [s.id, s]));
  const shownNodes = nodes.map((n) => {
    const src = byId[n.data.config.source_ref];
    return {
      ...n,
      data: {
        ...n.data, status: statuses[n.id], invalid: errors.byNode[n.id],
        source: src ? { chunks: src.chunk_count, files: 1, file: src.title, file_names: [src.title], status: src.status } : null,
        tool: toolActivity[n.id], modelLabel: modelLabel[n.data.config.model],
      },
    };
  });
  const shownEdges = edgesWithActivity(edges, statuses);
  const selected = nodes.find((n) => n.id === selectedId) || null;
  const agentId = nodes.find((n) => n.data.type === "agent")?.id;
  const agent = nodes.find((n) => n.id === agentId);

  // A line into the Agent always enters the side that matches the block (sources left, tools right).
  const onConnect = useCallback((c) => {
    const typeOf = (id) => nodes.find((n) => n.id === id)?.data.type;
    const into = typeOf(c.target) === "agent" ? { targetHandle: targetHandle(typeOf(c.source)) } : {};
    setEdges((es) => addEdge({ ...c, ...into, id: `${c.source}->${c.target}` }, es));
  }, [setEdges, nodes]);

  function tidy() {
    setNodes((ns) => autoLayout(ns));
    setTimeout(() => flow.fitView({ padding: 0.15, duration: 300 }), 50);
  }

  // Dropped blocks go where they were dropped; clicked blocks go to the next free place in their column
  // (sources left of the Agent, tools right of it), and the view is fitted so the new block is visible.
  function addNode(kind, position) {
    const node = newNode(kind, nodes, position ? { x: position.x - 100, y: position.y - 30 } : null);
    setNodes((ns) => [...ns, node]);
    setSelectedId(node.id);
    setPanel("inspect");
    if (!position) setTimeout(() => flow.fitView({ padding: 0.15, maxZoom: 1, duration: 250 }), 50);
  }

  function onDrop(e) {
    e.preventDefault();
    const kind = e.dataTransfer.getData(DRAG_TYPE);
    if (!NODE_KINDS[kind]) return;
    addNode(kind, flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
  }

  function updateConfig(config) {
    setNodes((ns) => ns.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, config } } : n)));
  }

  function setConnected(on) {
    if (!selectedId || !agentId) return;
    setEdges((es) => (on
      ? addEdge({ id: `${selectedId}->${agentId}`, source: selectedId, target: agentId, targetHandle: targetHandle(selected?.data.type) }, es)
      : es.filter((e) => !(e.source === selectedId && e.target === agentId))));
  }

  function removeSelected() {
    setNodes((ns) => ns.filter((n) => n.id !== selectedId));
    setEdges((es) => es.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }

  async function validate(quiet) {
    try {
      const data = await api(`/pipeline/validate?project_id=${project.id}`, { method: "POST", body: graph() });
      setErrors(errorsByNode(data.errors || []));
      if (!quiet) say(data.valid ? "The agent is valid and ready to run." : `${data.errors.length} problem${data.errors.length > 1 ? "s" : ""} found: see the marked blocks.`, data.valid ? "ok" : "err");
      return data.valid === true;
    } catch (err) {
      say(err.message, "err");
      return false;
    }
  }

  async function save() {
    try {
      const data = graphId
        ? await api(`/graphs/${graphId}`, { method: "PUT", body: { name, graph: graph() } })
        : await api("/graphs", { method: "POST", body: { project_id: project.id, name, graph: graph() } });
      setErrors(NO_ERRORS);
      if (!graphId) setMeta({ can_edit: true });
      setGraphId(data.id);
      setSavedJson(currentJson);
      say(graphId ? `Saved “${name}”.` : `Saved as agent #${data.id} in ${project.name}. Everyone on the project can use it.`);
      refreshSaved();
    } catch (err) {
      if (err.status === 422) {
        setErrors(errorsByNode(err.data?.detail?.errors || []));
        say("Not saved: fix the problems marked on the canvas.", "err");
      } else say(`Not saved: ${err.message}`, "err");
    }
  }

  function resetRun() {
    setStatuses({});
    setToolActivity({});
    setResult(null);
    setStreamed("");
    setLog([]);
    setRunTools([]);
    setApprovals([]);
    setRunError("");
  }

  async function load(id) {
    if (!id) return;
    try {
      const raw = await api(`/graphs/${id}`);
      const g = fromGraph(raw);
      const label = raw.meta?.name || `Agent ${id}`;
      setMeta(raw.meta || null);
      setNodes(g.nodes);
      setEdges(g.edges);
      setGraphId(Number(id));
      setName(label);
      setSavedJson(JSON.stringify(toGraph(g.nodes, g.edges)) + label);
      setErrors(NO_ERRORS);
      setSelectedId(null);
      setDesign(null);
      resetRun();
      setTimeout(() => flow.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 50);
      say(`Opened “${label}”.`);
    } catch (err) {
      say(`Could not open agent ${id}: ${err.message}`, "err");
    }
  }

  function startNew() {
    const g = defaultCanvas();
    setNodes(g.nodes);
    setEdges(g.edges);
    setGraphId(null);
    setMeta(null);
    setName("My agent");
    setSavedJson(null);
    setErrors(NO_ERRORS);
    setSelectedId(null);
    setDesign(null);
    resetRun();
    setTimeout(() => flow.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 50);
  }

  // A generated agent replaces the canvas as a new, unsaved agent; its notes show beside the canvas.
  function applyDesign(data) {
    const g = fromGraph(data.graph);
    refreshSources();  // the design may reuse sources added since the builder opened (another tab, the API)
    setNodes(g.nodes);
    setEdges(g.edges);
    setGraphId(null);
    setMeta(null);
    setName(data.name || "My agent");
    setSavedJson(null);
    setErrors(NO_ERRORS);
    setSelectedId(null);
    resetRun();
    setDesign(data);
    setDesigning(false);
    setPanel("inspect");
    // Fit once the new blocks are drawn, and again after React Flow has measured them (sizes arrive a moment later).
    setTimeout(() => flow.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 50);
    setTimeout(() => flow.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 400);
    say(`Designed “${data.name}” with ${data.generation.label.split(":")[0]} in ${(data.generation.ms / 1000).toFixed(1)} s. Save it to use it in Chat.`);
  }

  function askSuggested(q) {
    setQuestion(q);
    openRun();
  }

  async function run(e) {
    e?.preventDefault();
    if (busy || !question.trim()) return;
    resetRun();
    setPanel("run");
    if (canEdit && !(await validate(true))) {
      setRunError("The agent is not valid: fix the problems marked on the canvas, then run again.");
      return;
    }
    setBusy(true);
    const t0 = performance.now();
    try {
      // Editors run the canvas as drawn (unsaved edits included) inside this project; everyone else runs the
      // saved agent. The api decides from its own records which sources each may search.
      const body = canEdit
        ? { question, graph: graph(), ...(graphId ? { graph_id: graphId } : { project_id: project.id }) }
        : { question, graph_id: graphId };
      runAbort.current = new AbortController();
      await runPipeline(body, (ev) => {
        if (ev.type !== "token") setLog((l) => [...l, { t: performance.now() - t0, ev }]);
        setStatuses((s) => applyEvent(s, ev));
        if (ev.type === "token") setStreamed((t) => t + ev.text);
        if (ev.type === "token_reset") setStreamed("");
        if (ev.type === "tool_call") {
          setToolActivity((a) => ({ ...a, [ev.node_id]: { tool: ev.tool, pending: true } }));
          setRunTools((t) => [...t, { node_id: ev.node_id, tool: ev.tool, args: ev.args, pending: true }]);
        }
        if (ev.type === "tool_result") {
          setToolActivity((a) => ({ ...a, [ev.node_id]: ev }));
          setRunTools((t) => [...t.filter((x) => !(x.pending && x.node_id === ev.node_id)), ev]);
        }
        if (ev.type === "approval_required") setApprovals((a) => [...a, { ask: ev, decision: null }]);
        if (ev.type === "approval_result") setApprovals((a) => a.map((x) => (x.ask.approval_id === ev.approval_id ? { ...x, decision: ev.decision } : x)));
        if (ev.type === "result") setResult(ev);
        if (ev.type === "error") setRunError(ev.code === "access_revoked" ? "Your access to this project was removed." : `${ev.stage}: ${ev.message}`);
      }, runAbort.current.signal);
    } catch (err) {
      setRunError(err.name === "AbortError" ? "Stopped. The run was cancelled." : err.message);
    } finally {
      setBusy(false);
    }
  }

  function openRun() {
    setPanel("run");
    setTimeout(() => questionRef.current?.focus(), 30);
  }

  // A fresh, untouched canvas invites the headline feature: describe the assistant and let it be built.
  const showHero = !heroHidden && !graphId && !design && canEdit && project.can_contribute && nodes.length === 5;

  const problemCount = errors.general.length + Object.values(errors.byNode).reduce((n, m) => n + m.length, 0);
  const agentModel = agent ? models.find((m) => m.key === (agent.data.config.model || "groq")) : null;
  const agentInfo = agent ? `Runs on ${agentModel?.label || agent.data.config.model}${agent.data.config.local_only ? " · local only" : ""}` : "";

  return (
    <section className="builder" aria-label="Agent builder">
      <header className="builder-bar">
        <div className="builder-title">
          <input className="title-input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Agent name" maxLength={80} />
          <span className={`pill ${graphId ? (dirty ? "warn" : "ok") : "idle"}`}>
            {graphId ? (dirty && canEdit ? "Unsaved changes" : `Saved · #${graphId}`) : "Not saved"}
          </span>
          {!canEdit && <span className="pill idle" title="You can run this agent; only its creator and the project managers change it.">Read only</span>}
        </div>
        <div className="builder-actions">
          <select className="select-sm" value="" onChange={(e) => load(e.target.value)} aria-label="Open a saved agent">
            <option value="">Open agent…</option>
            {saved.map((s) => <option key={s.id} value={s.id}>#{s.id} · {s.name} ({s.created_by_name})</option>)}
          </select>
          <button className="btn magic" onClick={() => { setDesignSeed(""); setDesigning(true); }} disabled={!project.can_contribute}
            title={project.can_contribute ? "Describe the use case and get a ready agent" : "Only contributors can create agents"}>
            <Icon name="sparkle" size={15} /> <span className="hide-sm">Generate</span>
          </button>
          <button className="btn ghost" onClick={startNew} title="Start a new agent"><Icon name="newfile" size={15} /> <span className="hide-sm">New</span></button>
          <button className="btn ghost" onClick={tidy} title="Tidy the layout"><Icon name="builder" size={15} /> <span className="hide-sm">Tidy</span></button>
          <button className="btn ghost" onClick={() => validate(false)} title="Check the agent for problems"><Icon name="validate" size={15} /> <span className="hide-sm">Validate</span></button>
          <button className="btn" onClick={save} disabled={!canEdit || (graphId && !dirty)} title={canEdit ? "" : "Only the creator and project managers can save this agent"}><Icon name="save" size={15} /> {graphId ? "Save" : "Save agent"}</button>
          <button className="btn primary" onClick={openRun}><Icon name="play" size={14} /> Run</button>
        </div>
      </header>

      <div className="builder-body">
        <aside className="palette" aria-label="Blocks palette">
          <div className="section-label">Sources</div>
          {PALETTE.filter((k) => NODE_KINDS[k].group === "source").map((k) => <PaletteItem key={k} kind={k} onAdd={addNode} />)}
          <div className="section-label">Tools</div>
          {PALETTE.filter((k) => NODE_KINDS[k].group === "tool").map((k) => <PaletteItem key={k} kind={k} onAdd={addNode} />)}
          <p className="palette-tip"><Icon name="info" size={14} /><span>Drag a block onto the canvas, then draw a line from its handle to the <b>Agent</b>. Unconnected blocks are never used.</span></p>
        </aside>

        <div className="canvas-wrap">
          {problemCount > 0 && (
            <div className="canvas-banner" role="alert">
              <Icon name="alert" size={15} />
              <span><b>{problemCount} problem{problemCount > 1 ? "s" : ""}</b> · {[...errors.general, ...Object.entries(errors.byNode).map(([id, m]) => `${id}: ${m[0]}`)][0]}</span>
              <button className="btn ghost sm" onClick={() => setErrors(NO_ERRORS)} aria-label="Dismiss problems"><Icon name="x" size={14} /></button>
            </div>
          )}
          {showHero && (
            <StartCard onGenerate={(seed) => { setDesignSeed(seed || ""); setDesigning(true); }} onDismiss={() => setHeroHidden(true)} />
          )}
          <div className="canvas" data-testid="canvas" ref={canvasRef} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }} onDrop={onDrop}>
            <ReactFlow
              nodes={shownNodes}
              edges={shownEdges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, n) => { setSelectedId(n.id); setPanel("inspect"); }}
              onPaneClick={() => setSelectedId(null)}
              deleteKeyCode={["Backspace", "Delete"]}
              defaultEdgeOptions={{ type: "smoothstep" }}
              fitView
              fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
              minZoom={0.3}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={22} size={1.2} />
              <MiniMap position="top-right" pannable zoomable className="minimap" style={{ width: 150, height: 100 }} nodeColor={(n) => `var(--g-${NODE_KINDS[n.data.kind]?.group || "main"})`} maskColor="var(--minimap-mask)" />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
          <div className="legend" aria-label="Status legend">
            {[["running", "Running"], ["done", "Done"], ["searched", "Searched"], ["cited", "Cited"], ["error", "Failed"]].map(([s, l]) => (
              <span key={s}><i className={`dot s-${s}`} /> {l}</span>
            ))}
          </div>
        </div>

        <aside className="side-panel" aria-label="Inspector and run">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={panel === "inspect"} className={panel === "inspect" ? "on" : ""} onClick={() => setPanel("inspect")}>
              <Icon name="builder" size={14} /> Inspector
            </button>
            <button role="tab" aria-selected={panel === "run"} className={panel === "run" ? "on" : ""} onClick={openRun}>
              <Icon name="play" size={13} /> Run {busy && <span className="spinner" />}
            </button>
          </div>
          <div className="side-panel-body">
            {panel === "inspect" && !selected && design ? (
              <DesignNotes design={design} onSelectNode={setSelectedId} onAsk={askSuggested} onDismiss={() => setDesign(null)} />
            ) : panel === "inspect" ? (
              <ConfigPanel node={selected} models={models} project={project} tools={tools} sources={sources} canEdit={canEdit}
                connected={!!selected && edges.some((e) => e.source === selected.id && e.target === agentId)}
                onConnect={setConnected} onConfig={updateConfig} onDelete={removeSelected}
                errors={selected ? errors.byNode[selected.id] : null} onChangedSources={refreshSources} />
            ) : (
              <RunConsole question={question} setQuestion={setQuestion} onRun={run} busy={busy} log={log}
                streamed={streamed} result={result} toolCalls={runTools} runError={runError} approvals={approvals}
                onStop={() => runAbort.current?.abort()} agentInfo={agentInfo} inputRef={questionRef} />
            )}
          </div>
        </aside>
      </div>

      {designing && <DesignDialog project={project} initial={designSeed} onClose={() => setDesigning(false)} onDesigned={applyDesign} />}

      {toast && (
        <div key={toast.id} className={`toast ${toast.tone}`} role="status">
          <Icon name={toast.tone === "err" ? "alert" : "check"} size={15} /> {toast.text}
        </div>
      )}
    </section>
  );
}

const START_EXAMPLES = [
  ["Clinic · Telugu", "Answer patient questions about our clinic's refund and cancellation policy in Telugu, and work out refund amounts."],
  ["College · Hindi", "Help students of our college find admission dates, fees and hostel rules from the prospectus, in Hindi."],
  ["Pharmacy desk", "Front desk agent for our clinic pharmacy: look up order status and cancel orders when patients ask, in English."],
];

function StartCard({ onGenerate, onDismiss }) {
  return (
    <div className="start-card" role="region" aria-label="Start with a description">
      <button className="btn ghost icon-btn sm start-close" onClick={onDismiss} aria-label="Hide and build by hand"><Icon name="x" size={14} /></button>
      <span className="start-icon"><Icon name="sparkle" size={22} /></span>
      <h3>Describe your assistant</h3>
      <p className="muted">One sentence is enough. BharatAgent picks the sources, tools, language and guardrails, and draws the agent here.</p>
      <div className="start-examples">
        {START_EXAMPLES.map(([label, text]) => (
          <button key={label} type="button" className="suggestion" onClick={() => onGenerate(text)} title={text}>{label}</button>
        ))}
      </div>
      <button className="btn magic lg" onClick={() => onGenerate("")}><Icon name="sparkle" size={16} /> Generate an agent</button>
      <span className="muted small">or drag blocks from the left to build it by hand</span>
    </div>
  );
}

function PaletteItem({ kind, onAdd }) {
  const spec = NODE_KINDS[kind];
  return (
    <button type="button" className={`palette-item group-${spec.group}`} draggable
      onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, kind); e.dataTransfer.effectAllowed = "copy"; }}
      onClick={() => onAdd(kind)} title={`Add ${spec.label} (click, or drag onto the canvas)`}>
      <span className={`chip-icon group-${spec.group}`}><Icon name={kind} size={16} /></span>
      <span className="palette-text">
        <strong>{spec.label}</strong>
        <span className="muted small">{PALETTE_HINT[kind]}</span>
      </span>
      <Icon name="plus" size={14} className="palette-plus" />
    </button>
  );
}

export default function Builder(props) {
  // The canvas works inside one project (its agents and its knowledge).
  return (
    <ReactFlowProvider>
      <BuilderInner {...props} />
    </ReactFlowProvider>
  );
}

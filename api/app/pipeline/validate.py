"""Checks a graph against the pipeline rules and returns every problem at once, each with a clear
message, so the user can fix the canvas in one go. An empty list means the graph can run."""

from .graph import LANGUAGES, MAIN_LINE, NODE_TYPES, SOURCE_TYPES, TOOL_TYPES, Graph


def _error(code: str, message: str, node_id: str | None = None) -> dict:
    return {"code": code, "message": message, "node_id": node_id}


def validate_graph(graph: Graph) -> list[dict]:
    errors: list[dict] = []
    ids = [n.id for n in graph.nodes]

    # 1. Node ids are unique and every type is known.
    for node_id in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(_error("duplicate_id", f"Two or more nodes share the id '{node_id}'.", node_id))
    for n in graph.nodes:
        if n.type not in NODE_TYPES:
            errors.append(_error("unknown_type", f"Node '{n.id}' has unknown type '{n.type}'. "
                                 f"Allowed: {', '.join(sorted(NODE_TYPES))}.", n.id))

    # 2. Exactly one of each main-line block.
    for t in MAIN_LINE:
        count = len(graph.of_type(t))
        if count != 1:
            errors.append(_error(f"{t}_count", f"The pipeline needs exactly one '{t}' node, found {count}."))

    # 3. Every edge points at nodes that exist and is not a loop.
    known = set(ids)
    for e in graph.edges:
        for end in (e.from_, e.to):
            if end not in known:
                errors.append(_error("unknown_edge_node", f"Edge {e.from_} -> {e.to} refers to missing node '{end}'."))
        if e.from_ == e.to:
            errors.append(_error("self_loop", f"Node '{e.from_}' has an edge to itself.", e.from_))

    # Rules 4-7 need one node of each main type; skip them if rule 2 failed.
    main = {t: graph.of_type(t) for t in MAIN_LINE}
    if any(len(nodes) != 1 for nodes in main.values()):
        return errors
    main_id = {t: nodes[0].id for t, nodes in main.items()}
    agent_id = main_id["agent"]

    # 4. The main line runs input -> guardrails -> router -> agent -> output with no gaps.
    edge_set = {(e.from_, e.to) for e in graph.edges}
    for a, b in zip(MAIN_LINE, MAIN_LINE[1:]):
        if (main_id[a], main_id[b]) not in edge_set:
            errors.append(_error("main_line_gap", f"Missing the link {a} -> {b} "
                                 f"({main_id[a]} -> {main_id[b]}). The main line must have no gaps.",
                                 main_id[a]))

    # 5. Main-line blocks connect only to their neighbour: no shortcuts or backward links.
    allowed = {(main_id[a], main_id[b]) for a, b in zip(MAIN_LINE, MAIN_LINE[1:])}
    main_ids = set(main_id.values())
    for e in graph.edges:
        if e.from_ in main_ids and e.to in main_ids and (e.from_, e.to) not in allowed:
            errors.append(_error("main_line_shortcut", f"Edge {e.from_} -> {e.to} skips or reverses the "
                                 "main line. Blocks connect only to the next block.", e.from_))

    # 6. Every source and tool has an edge into the agent, and only into the agent.
    for n in graph.nodes:
        if n.type not in SOURCE_TYPES | TOOL_TYPES:
            continue
        kind = "Source" if n.type in SOURCE_TYPES else "Tool"
        outgoing = [e.to for e in graph.edges if e.from_ == n.id]
        incoming = [e.from_ for e in graph.edges if e.to == n.id]
        if agent_id not in outgoing:
            errors.append(_error("not_connected", f"{kind} '{n.id}' ({n.type}) is not connected to the "
                                 f"agent. Draw a line from it to '{agent_id}', or remove it.", n.id))
        for target in outgoing:
            if target != agent_id:
                errors.append(_error("wrong_target", f"{kind} '{n.id}' connects to '{target}'. Sources and "
                                     "tools can only connect to the agent.", n.id))
        if incoming:
            errors.append(_error("wrong_target", f"{kind} '{n.id}' has an incoming edge from "
                                 f"'{incoming[0]}'. Sources and tools only send into the agent.", n.id))

    # 7. Node settings: MCP tools are read-only unless writing is explicitly enabled; known languages.
    for n in graph.of_type("tool_mcp"):
        mode = n.config.get("mode")
        if mode != "read_only" and n.config.get("allow_write") is not True:
            errors.append(_error("mcp_not_read_only", f"MCP tool '{n.id}' has mode '{mode}'. Set mode to "
                                 "'read_only', or set allow_write: true to enable writes on purpose.", n.id))
    lang = main["agent"][0].config.get("language")
    if lang is not None and lang not in LANGUAGES:
        errors.append(_error("bad_language", f"Agent language '{lang}' is not supported. "
                             f"Use one of: {', '.join(sorted(LANGUAGES))}.", agent_id))

    return errors

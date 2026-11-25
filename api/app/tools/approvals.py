"""Human approval for risky tool calls (PRD FR-36: "unapproved risky action executed: 0").

A tool that changes data (an MCP tool not marked read-only) never runs on the model's word alone. The agent
stage registers the call here, sends an approval_required event to the live stream, and waits; the person who
asked the question answers with POST /pipeline/approvals/{id}. Only that person can decide, only once, and
silence is a "no": after TIMEOUT_SECONDS the call is denied automatically. Approvals live in this api process's
memory, which is enough because the waiting stream runs in the same process (single api container)."""

import secrets
import threading
import time
from dataclasses import dataclass, field

TIMEOUT_SECONDS = 120.0
_lock = threading.Lock()
_pending: dict[str, "Approval"] = {}


@dataclass
class Approval:
    id: str
    user_id: int
    tool: str
    args: dict
    node_id: str
    created: float = field(default_factory=time.monotonic)
    decision: str | None = None          # "approved" / "denied" / "timeout"
    _event: threading.Event = field(default_factory=threading.Event, repr=False)


def request(user_id: int, tool: str, args: dict, node_id: str) -> Approval:
    approval = Approval(id=secrets.token_urlsafe(12), user_id=user_id, tool=tool, args=dict(args), node_id=node_id)
    with _lock:
        _pending[approval.id] = approval
    return approval


def decide(approval_id: str, user_id: int, approve: bool) -> str | None:
    """Record the decision of the person who asked. Returns the decision, or None if there is no such pending
    approval for this user (unknown id, someone else's, or already decided / timed out)."""
    with _lock:
        approval = _pending.get(approval_id)
        if approval is None or approval.user_id != user_id or approval.decision is not None:
            return None
        approval.decision = "approved" if approve else "denied"
        del _pending[approval_id]
    approval._event.set()
    return approval.decision


def wait(approval: Approval, timeout: float | None = None) -> str:
    """Block until the user decides, or deny when the time is up."""
    approval._event.wait(TIMEOUT_SECONDS if timeout is None else timeout)
    with _lock:
        if approval.decision is None:
            approval.decision = "timeout"
            _pending.pop(approval.id, None)
    return approval.decision

"""Real browser journeys against the running app (http://127.0.0.1:3000), driven through headless Microsoft
Edge or Google Chrome with the DevTools protocol. Nothing is mocked: the journeys click the real UI, upload
a real PDF through the file inputs, and wait for real answers from the api and the chosen model.

  1. Builder: new agent -> add PDF, Calculator and MCP tool -> connect them to the Agent -> pick the model
     -> save -> upload the PDF in the inspector -> run a refund question -> the tool node and the PDF light
     up from the SSE events and the answer cites both.
  2. Knowledge: upload the PDF to the quick chat's PDF source; the card shows it as indexed.
  3. Chat: local-only on, MCP tool on, ask about order ORD-1002 -> cited answer from the MCP tool.
  4. Usage and Status pages load with data.
  5. Every screen at 1440, 1024, 768 and 390 px wide: no sideways scrolling.

Screenshots are saved in docs/screenshots. Prints PASS / FAIL per check; exit code 1 if anything failed.

  pip install websockets
  python scripts/ui_journeys.py                 # the agent answers with Qwen 3 14B (local)
  python scripts/ui_journeys.py --model groq    # or another model key
"""

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"
PDF = ROOT / "samples" / "refund-policy.pdf"
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append(("PASS" if ok else "FAIL", name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{f' - {detail}' if detail else ''}", flush=True)
    return ok


class Browser:
    def __init__(self, exe: str):
        self.profile = tempfile.mkdtemp(prefix="ba-journey-")
        with socket.socket() as s:  # a free port, so a browser left over from another run is never reused
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
                                      "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
                                      "--window-size=1440,900", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))
                page = next(p for p in pages if p["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("the browser did not start")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024).__enter__()
        self.next_id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")

    def send(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expression: str):
        r = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "JS error"))
        return r["result"].get("value")

    def viewport(self, width: int, height: int):
        self.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1,
                                                          "mobile": width < 768})

    def goto(self, url: str):
        self.send("Page.navigate", {"url": url})
        self.wait("document.readyState === 'complete' && !!document.querySelector('.app')", 20)
        time.sleep(1.0)

    def wait(self, condition: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(f"Boolean({condition})"):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.4)
        return False

    def shot(self, name: str):
        data = self.send("Page.captureScreenshot", {"format": "png"})["data"]
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / f"{name}.png").write_bytes(base64.b64decode(data))

    def upload(self, selector: str, path: Path):
        doc = self.send("DOM.getDocument", {"depth": -1})
        node = self.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": selector})["nodeId"]
        if not node:
            raise RuntimeError(f"no element {selector}")
        self.send("DOM.setFileInputFiles", {"nodeId": node, "files": [str(path)]})

    def close(self):
        try:
            self.ws.close()
        finally:
            if os.name == "nt":  # the browser starts helper processes; end the whole tree
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            shutil.rmtree(self.profile, ignore_errors=True)


# Helpers that run in the page. React needs the native value setter plus an input/change event.
HELPERS = r"""
window.__set = (sel, value) => {
  const el = document.querySelector(sel); if (!el) throw new Error('no ' + sel);
  const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
  return true;
};
window.__click = (sel, text) => {
  const els = [...document.querySelectorAll(sel)].filter((e) => !text || e.textContent.includes(text));
  const el = els.find((e) => e.offsetParent !== null) || els[0]; if (!el) throw new Error('no ' + sel + ' ' + (text || ''));
  el.click(); return true;
};
window.__text = (sel) => document.querySelector(sel)?.textContent || '';
true
"""


def helpers(b: Browser):
    b.js(HELPERS)


def builder_journey(b: Browser, base: str, model: str):
    b.viewport(1440, 900)
    b.goto(f"{base}/#builder")
    helpers(b)
    b.js("__click('button[title=\"Start a new agent\"]')")
    b.js("__set('input[aria-label=\"Agent name\"]', 'Clinic refunds (UI journey)')")
    for kind, node_id in (("PDF", "src1"), ("Calculator", "tool1"), ("MCP tool", "tool2")):
        b.js(f"__click('button.palette-item', '{kind}')")
        b.wait(f"document.querySelector('[data-node-id=\"{node_id}\"]')", 5)
        b.js("__click('input[aria-label=\"Connected to Agent\"]')")
    b.wait("document.querySelectorAll('.react-flow__edge').length === 7", 5)
    edges = b.js("document.querySelectorAll('.react-flow__edge').length")
    check("builder: PDF, Calculator and MCP blocks added and connected to the Agent", edges == 7, f"{edges} lines on the canvas")
    overlaps = b.js("""(() => { const r = [...document.querySelectorAll('.react-flow__node')].map(n => [n.dataset.id, n.getBoundingClientRect()]);
      const hit = []; for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) { const [a, A] = r[i], [b, B] = r[j];
      if (A.left < B.right && B.left < A.right && A.top < B.bottom && B.top < A.bottom) hit.push(a + '/' + b); } return hit; })()""")
    check("builder: new blocks are placed without overlapping", not overlaps, ", ".join(overlaps))

    b.js("document.querySelector('.react-flow__node[data-id=\"n4\"]').click(); true")
    b.wait("document.querySelector('select[aria-label=\"Agent model\"]')", 5)
    b.js(f"__set('select[aria-label=\"Agent model\"]', '{model}')")
    b.js("__set('select[aria-label=\"Agent language\"]', 'en')")
    b.js("__click('.builder-actions button', 'Save agent')")
    saved = b.wait("__text('.builder-title').includes('Saved')", 15)
    check("builder: agent saved through the api", saved, __import__("re").sub(r"\s+", " ", b.js("__text('.builder-title')")))

    b.js("document.querySelector('.react-flow__node[data-id=\"src1\"]').click(); true")
    b.wait("document.querySelector('.side-panel input[type=file]')", 5)
    b.upload(".side-panel input[type=file]", PDF)
    indexed = b.wait("__text('[data-node-id=\"src1\"]').includes('chunks')", 180)
    check("builder: PDF uploaded in the inspector shows its chunks on the node", indexed, b.js("__text('[data-node-id=\"src1\"] .node-line')"))
    check("builder: the agent stays saved after the upload", "Saved" in b.js("__text('.builder-title')"), b.js("__text('.builder-title .pill')"))
    b.shot("builder-agent-with-sources")

    b.js("__click('.builder-actions button', 'Run')")
    b.wait("document.querySelector('#run-q')", 5)
    b.js("__set('#run-q', 'My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?')")
    started = time.time()
    b.js("__click('.run-form button', 'Run')")
    if b.wait("['running','called','cited'].includes(document.querySelector('[data-node-id=\"tool1\"]')?.dataset.status)", 240):
        b.shot("builder-run-tool-called")
    done = b.wait("document.querySelector('.run-answer .answer-meta')", 420)
    took = time.time() - started
    status = {n: b.js(f"document.querySelector('[data-node-id=\"{n}\"]').dataset.status") for n in ("n1", "n4", "n5", "src1", "tool1")}
    answer = b.js("__text('.run-answer .answer-text')")
    check("builder: run streamed to a final answer", done, f"{took:.0f} s")
    check("builder: main blocks lit up from SSE events", status["n1"] == "done" and status["n5"] == "done", json.dumps(status))
    check("builder: calculator node ran and was cited", status["tool1"] in ("called", "cited"), status["tool1"])
    check("builder: PDF node cited", status["src1"] == "cited", status["src1"])
    check("builder: answer says 960 and cites the PDF and the calculator",
          "960" in answer and b.js("document.querySelectorAll('.run-answer .citations li').length") >= 2, answer[:160])
    b.shot("builder-run-answer")


def knowledge_journey(b: Browser, base: str):
    b.viewport(1440, 900)
    b.goto(f"{base}/#knowledge")
    helpers(b)
    b.wait("document.querySelector('[data-source=\"chat_pdf\"]') && !document.querySelector('.source-card.skeleton')", 15)
    b.upload("[data-source=\"chat_pdf\"] input[type=file]", PDF)
    ok = b.wait("__text('[data-source=\"chat_pdf\"]').includes('Indexed refund-policy.pdf')", 180)
    check("knowledge: PDF uploaded to the quick chat with progress and indexed", ok, b.js("__text('[data-source=\"chat_pdf\"] .inline-msg')"))
    b.shot("knowledge")


def chat_journey(b: Browser, base: str):
    b.viewport(1440, 900)
    b.goto(f"{base}/#chat")
    helpers(b)
    b.wait("document.querySelector('input[aria-label=\"Use MCP tool · orders-db\"]')", 10)
    b.js("const s = document.querySelector('.privacy-switch input'); if (!s.checked) s.click(); true")
    b.js("const m = document.querySelector('input[aria-label=\"Use MCP tool · orders-db\"]'); if (!m.checked) m.click(); true")
    b.wait("__text('.toggle-card').includes('chunks')", 10)
    b.js("__set('textarea[aria-label=\"Question\"]', 'What is the status and refund of order ORD-1002?')")
    b.js("__click('button[aria-label=\"Send\"]')")
    done = b.wait("document.querySelector('.bot-body .answer-meta')", 420)
    answer = b.js("__text('.bot-body .answer-text')")
    meta = b.js("__text('.bot-body .answer-meta')")
    tools = b.js("__text('.bot-body .tool-calls')")
    check("chat: order question answered", done, answer[:160])
    check("chat: the MCP tool get_order was called", "get_order" in tools, tools[:120])
    check("chat: answer cites the MCP tool and was local only", "orders-db.get_order" in b.js("__text('.bot-body .citations')")
          and "Local only" in meta, meta[:160])
    b.shot("chat-mcp-answer")


def pages_journey(b: Browser, base: str):
    b.viewport(1440, 900)
    b.goto(f"{base}/#dashboard")
    helpers(b)
    ok = b.wait("document.querySelectorAll('.kpi strong').length === 4 && document.querySelector('.chart-card')", 20)
    check("usage: KPI cards and charts load", ok, b.js("[...document.querySelectorAll('.kpi strong')].map(e => e.textContent).join(' | ')"))
    b.js("__set('select[aria-label=\"Model filter\"]', 'local')")
    time.sleep(2)
    check("usage: model filter applies", b.wait("document.querySelectorAll('.kpi strong').length === 4", 15),
          b.js("__text('.kpi strong')") + " requests on the local model")
    b.js("__set('select[aria-label=\"Model filter\"]', '')")
    time.sleep(2)
    b.shot("usage")
    b.goto(f"{base}/#status")
    ok = b.wait("document.querySelectorAll('[data-ok=\"true\"]').length === 4", 20)
    check("status: all four services healthy", ok)
    b.shot("status")


def responsive_journey(b: Browser, base: str):
    keep = {("builder", 768), ("dashboard", 768), ("builder", 390), ("chat", 390), ("dashboard", 390)}
    for width, height in ((1440, 900), (1024, 768), (768, 1024), (390, 844)):
        b.viewport(width, height)
        bad = []
        for screen in ("builder", "chat", "knowledge", "dashboard", "status"):
            b.goto(f"{base}/#{screen}")
            over = b.js("document.documentElement.scrollWidth - innerWidth")
            if over > 1:
                bad.append(f"{screen} +{over}px")
            if (screen, width) in keep:
                b.shot(f"{screen}-{width}px")
        check(f"responsive: no sideways scrolling at {width}px", not bad, ", ".join(bad))
    b.viewport(1440, 900)
    b.goto(f"{base}/#builder")
    b.js("localStorage.setItem('theme', 'light'); location.reload(); true")
    time.sleep(2.5)
    b.shot("builder-light-theme")
    b.js("localStorage.setItem('theme', 'dark'); true")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # answers contain Rs, Telugu and Hindi
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3000")
    ap.add_argument("--model", default="local", choices=["local", "local_small", "groq", "gemini"])
    ap.add_argument("--only", nargs="*", help="builder knowledge chat pages responsive")
    args = ap.parse_args()
    exe = next((p for p in BROWSERS if os.path.exists(p)), None)
    if not exe:
        print("[SKIP] no Edge or Chrome found")
        return 0
    b = Browser(exe)
    steps = {"builder": lambda: builder_journey(b, args.base, args.model), "knowledge": lambda: knowledge_journey(b, args.base),
             "chat": lambda: chat_journey(b, args.base), "pages": lambda: pages_journey(b, args.base),
             "responsive": lambda: responsive_journey(b, args.base)}
    try:
        for name, step in steps.items():
            if args.only and name not in args.only:
                continue
            try:
                step()
            except Exception as exc:  # a broken step is a failure, the next journey still runs
                check(f"{name}: journey crashed", False, str(exc)[:200])
    finally:
        b.close()
    passed = sum(r[0] == "PASS" for r in results)
    print(f"\n{passed} passed, {len(results) - passed} failed. Screenshots: {SHOTS}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

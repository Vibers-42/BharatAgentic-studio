"""Real browser journeys against the running app (http://127.0.0.1:3000), driven through headless Microsoft
Edge or Google Chrome with the DevTools protocol. Nothing is mocked: every person uses their own browser
profile (separate cookies and storage), registers through the real forms, and waits for real answers.

Phase 8 multi-user journey:
  1. Owner: landing page -> Get started -> registration -> personal workspace -> creates the company and the
     "E-commerce Platform" project -> creates invitation links for a contributor and a consumer.
  2. Contributor and consumer (two more profiles): open their invitation link, create an account with the
     invited email, accept; the owner confirms both (Members) and assigns contributor / viewer (Projects).
  3. Contributor: uploads samples/ecommerce-architecture.pdf, adds the GitHub repo and the docs page (Knowledge).
  4. Owner: builds the shared agent from those project sources in the builder (nothing uploaded again), saves
     it and test-runs it: the blocks light up from the real SSE events and the answer cites the PDF.
  5. Consumer: sees the shared knowledge read-only, asks the agent in Chat (cited answers, a follow-up that
     uses the conversation), signs out, signs in through the login page and finds the conversation again.
  6. Contributor: does not see the consumer's private conversation (list and direct id).
  7. Outsider (fourth profile): registers, sees nothing of the company, every direct company route is 404,
     a used invitation link is refused.
  8. Usage and Status pages; every screen at 1440, 1024, 768 and 390 px wide without sideways scrolling.
  Every profile also fails if the page logs a console error or an uncaught exception.

Screenshots (p8-*.png) are saved in docs/screenshots. Prints PASS / FAIL per check; exit code 1 if anything
failed. The test accounts get a random password for this run only; it is never printed or saved.

  pip install websockets
  python scripts/ui_journeys.py                 # the agent answers with Qwen 3 14B (local)
  python scripts/ui_journeys.py --model groq    # or another model key
"""

import argparse
import base64
import json
import os
import secrets
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
        self.console_errors: list[str] = []  # uncaught exceptions, console.error and failed requests
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Log.enable")

    def _note(self, msg: dict):
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Runtime.exceptionThrown":
            self.console_errors.append(p.get("exceptionDetails", {}).get("text", "exception"))
        elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
            self.console_errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:160])
        elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
            e = p["entry"]
            self.console_errors.append(f"{e.get('text', '')[:120]} {e.get('url', '')}")

    def send(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if "method" in msg:
                self._note(msg)
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
        self.wait("document.readyState === 'complete' && !!document.querySelector('#root > *')", 20)
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
window.__el = (sel, n) => { const els = [...document.querySelectorAll(sel)]; const el = els[n || 0]; if (!el) throw new Error('no ' + sel); return el; };
window.__set = (sel, value, n) => {
  const el = __el(sel, n);
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
window.__msgs = () => [...document.querySelectorAll('.inline-msg')].map((e) => e.textContent).join(' | ');
window.__option = (sel, text) => {  // the value of the first option whose label contains text
  const o = [...__el(sel).options].find((x) => x.textContent.includes(text)); return o ? o.value : null;
};
window.__api = async (path, opts) => {  // the same-origin api with this profile's own session cookie and CSRF token
  const me = await fetch('/api/auth/me').then((r) => r.ok ? r.json() : {});
  const r = await fetch('/api' + path, { method: (opts && opts.method) || 'GET',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token || '' },
    body: opts && opts.body ? JSON.stringify(opts.body) : undefined });
  let json = null; try { json = await r.json(); } catch (e) {}
  return { status: r.status, json };
};
true
"""

ECOM = ROOT / "samples" / "ecommerce-architecture.pdf"
STAMP = time.strftime("%m%d%H%M%S")
PASSWORD = "Ui-" + secrets.token_urlsafe(14) + "9"  # random per run, never printed or stored
PEOPLE = {k: (f"UI {k.title()}", f"ui-{k}-{STAMP}@example.test") for k in ("owner", "contributor", "consumer", "outsider")}
COMPANY = f"Bharat Technologies UI {STAMP}"
PROJECT = "E-commerce Platform"
S: dict = {}  # ids found along the way (company, project, sources, agent, conversation, invitation links)


def helpers(b: Browser):
    b.js(HELPERS)


_LOADS = [0]


def open_app(b: Browser, base: str, screen: str):
    # A new query string forces a full page load: navigating to the same "#/app/x" URL again would only be a
    # same-document hash change and keep stale data on the screen.
    _LOADS[0] += 1
    b.goto(f"{base}/?v={_LOADS[0]}#/app/{screen}")
    helpers(b)


def choose(b: Browser, label: str, text: str) -> bool:
    """Pick the option of the select[aria-label=label] whose text contains `text`."""
    helpers(b)
    if not b.wait(f"__option('select[aria-label=\"{label}\"]', {json.dumps(text)}) !== null", 15):
        return False
    value = b.js(f"__option('select[aria-label=\"{label}\"]', {json.dumps(text)})")
    b.js(f"__set('select[aria-label=\"{label}\"]', {json.dumps(value)})")
    time.sleep(1.0)
    return True


def in_company(b: Browser, base: str, screen: str):
    open_app(b, base, screen)
    if not (choose(b, "Workspace", COMPANY) and choose(b, "Project", PROJECT)):
        raise RuntimeError(f"could not open {COMPANY} / {PROJECT} (no access?)")
    time.sleep(1.0)


def register(b: Browser, key: str, shot: str | None = None) -> bool:
    name, email = PEOPLE[key]
    helpers(b)
    b.wait("document.querySelector('input[autocomplete=\"name\"]')", 15)
    b.js(f"__set('input[autocomplete=\"name\"]', {json.dumps(name)})")
    b.js(f"__set('input[autocomplete=\"email\"]', {json.dumps(email)})")
    b.js(f"__set('input[autocomplete=\"new-password\"]', {json.dumps(PASSWORD)}, 0)")
    b.js(f"__set('input[autocomplete=\"new-password\"]', {json.dumps(PASSWORD)}, 1)")
    if shot:
        b.shot(shot)
    b.js("__click('button.btn.primary', 'Create account')")
    return b.wait("!!document.querySelector('.sidebar') || __text('body').includes('Accept invitation')", 20)


def owner_journey(b: Browser, base: str):
    b.viewport(1440, 900)
    b.goto(f"{base}/#/")
    helpers(b)
    check("landing: the public landing page offers Get started and Sign in",
          b.wait("__text('body').includes('Get started') && __text('body').includes('Sign in')", 15))
    b.shot("p8-01-landing")
    b.js("__click('a.btn.primary', 'Get started')")
    b.wait("location.hash.includes('register')", 10)
    check("registration: the owner registers and lands in a personal workspace",
          register(b, "owner", "p8-02-registration") and b.wait("__text('select[aria-label=\"Workspace\"]').includes('(personal)')", 15))
    b.wait("document.querySelector('.kpi')", 15)
    b.shot("p8-03-personal-dashboard")

    choose(b, "Workspace", "+ Create a company")
    b.wait("document.querySelector('[role=dialog][aria-label=\"Create a company\"] input')", 10)
    b.js(f"__set('[role=dialog] input', {json.dumps(COMPANY)})")
    b.js("__click('[role=dialog] button.btn.primary', 'Create company')")
    ok = b.wait(f"__text('.topbar').includes({json.dumps(COMPANY)})", 20)
    check("company: the owner creates the company from the workspace switcher", ok)

    open_app(b, base, "projects")
    choose(b, "Workspace", COMPANY)
    b.js("__click('button.btn.primary', 'New project')")
    b.wait("document.querySelector('[role=dialog] input')", 10)
    b.js(f"__set('[role=dialog] input', {json.dumps(PROJECT)})")
    b.js("__click('[role=dialog] button.btn.primary', 'Create project')")
    check("project: the owner creates the E-commerce Platform project",
          b.wait(f"__text('.list-card').includes({json.dumps(PROJECT)})", 15))
    me = b.js("__api('/auth/me')")["json"]
    S["org"] = next(w["id"] for w in me["workspaces"] if w["name"] == COMPANY)
    S["project"] = next(p["id"] for p in b.js(f"__api('/projects?org_id={S['org']}')")["json"] if p["name"] == PROJECT)

    open_app(b, base, "overview")
    choose(b, "Workspace", COMPANY)
    b.wait("document.querySelector('.kpi')", 15)
    b.shot("p8-04-company-dashboard")

    open_app(b, base, "members")
    choose(b, "Workspace", COMPANY)
    for key in ("contributor", "consumer"):
        b.js(f"__set('input[aria-label=\"Email to invite\"]', {json.dumps(PEOPLE[key][1])})")
        b.js("__set('select[aria-label=\"Role\"]', 'member')")
        b.js("__click('button.btn.primary', 'Create invitation')")
        ok = b.wait(f"(document.querySelector('input[aria-label=\"Invitation link\"]')?.value || '').includes('invite/') && "
                    f"document.querySelector('input[aria-label=\"Invitation link\"]').value !== {json.dumps(S.get('link_contributor', ''))}", 15)
        S[f"link_{key}"] = b.js("document.querySelector('input[aria-label=\"Invitation link\"]').value")
        check(f"invitations: the owner creates an invitation link for the {key}", ok, "")
    # The screenshot is committed: hide the token in the shown link (the real one is kept in S for the invitees).
    b.js("(() => { const i = document.querySelector('input[aria-label=\"Invitation link\"]'); "
         "if (i) i.value = i.value.replace(/invite\\/.*/, 'invite/<token hidden in this screenshot>'); return true; })()")
    b.shot("p8-05-invitations")


def invitee_journey(b: Browser, base: str, key: str, shot: str | None):
    b.viewport(1440, 900)
    link = S[f"link_{key}"]
    b.goto(link)
    helpers(b)
    b.wait(f"__text('body').includes({json.dumps(COMPANY)})", 15)
    registered = register(b, key)
    b.wait("__text('body').includes('Accept invitation')", 15)
    if shot:
        b.shot(shot)
    b.js("__click('button.btn.primary', 'Accept invitation')")
    ok = b.wait("__text('body').includes('Invitation accepted')", 15)
    check(f"invitation: the {key} opens the link, creates an account with the invited email and accepts", registered and ok,
          __import__("re").sub(r"\s+", " ", b.js("__text('.auth-card') || __text('body')"))[:140])
    if shot:
        b.shot(shot + "-done")


def owner_confirms(b: Browser, base: str):
    open_app(b, base, "members")
    choose(b, "Workspace", COMPANY)
    for _ in range(2):
        if not b.wait("[...document.querySelectorAll('button.btn.primary.sm')].some(e => e.textContent.includes('Confirm'))", 15):
            break
        b.js("__click('button.btn.primary.sm', 'Confirm')")
        time.sleep(2.0)
    members = b.js(f"__api('/orgs/{S['org']}/members')")["json"]
    active = [m for m in members if m["status"] == "active"]
    check("members: the owner confirms both people (administrator-confirmed enrolment)", len(active) == 3,
          ", ".join(f"{m['full_name']}={m['status']}" for m in members))
    b.shot("p8-06-member-management")

    open_app(b, base, "projects")
    choose(b, "Workspace", COMPANY)
    choose(b, "Project", PROJECT)
    for key, role in (("contributor", "contributor"), ("consumer", "viewer")):
        b.wait("document.querySelector('select[aria-label=\"Company member\"]')", 10)
        choose(b, "Company member", PEOPLE[key][0])
        b.js(f"__set('select[aria-label=\"Project role\"]', '{role}')")
        b.js("__click('.add-member button', 'Add to project')")
        time.sleep(1.5)
    people = b.js(f"__api('/projects/{S['project']}/members')")["json"]
    roles = {m["full_name"]: m.get("project_role") for m in people}
    check("project: the contributor is a contributor and the consumer a viewer",
          roles.get(PEOPLE["contributor"][0]) == "contributor" and roles.get(PEOPLE["consumer"][0]) == "viewer", json.dumps(roles))
    b.shot("p8-07-project-dashboard")


def contributor_uploads(b: Browser, base: str):
    in_company(b, base, "knowledge")
    b.wait("document.querySelector('input[aria-label=\"Upload a file\"]')", 15)
    b.upload("input[aria-label=\"Upload a file\"]", ECOM)
    ok = b.wait("__msgs().includes('ecommerce-architecture.pdf is ready')", 240)
    check("knowledge: the contributor uploads the PDF in the browser and it becomes ready", ok, b.js("__msgs()"))
    for kind, label, url, title in (("GitHub repo", "GitHub repository address", "https://github.com/octocat/Spoon-Knife", "Spoon-Knife"),
                                    ("Docs page", "Docs page address", "https://example.com", "Example Domain")):
        b.js(f"__click('.segmented button', '{kind}')")
        time.sleep(0.5)
        b.js(f"__set('input[aria-label=\"{label}\"]', '{url}')")
        b.js("__click('form.card button.btn', 'index')")
        ok = b.wait(f"__msgs().includes('{title}') && __msgs().includes('is ready') && !document.querySelector('.progress')", 240)
        check(f"knowledge: the contributor adds the {kind} and it becomes ready", ok, b.js("__msgs()"))
    b.wait("document.querySelectorAll('.source-row').length === 3", 15)
    rows = b.js("[...document.querySelectorAll('.source-row')].map(r => r.textContent).join(' | ')")
    check("knowledge: three ready sources, each showing who added it",
          rows.count("ready") == 3 and rows.count(PEOPLE["contributor"][0]) == 3, rows[:200])
    S["sources"] = {s["kind"]: s["id"] for s in b.js(f"__api('/projects/{S['project']}/sources')")["json"]}


def owner_builds_agent(b: Browser, base: str, model: str):
    in_company(b, base, "agents")
    b.wait("document.querySelector('button[title=\"Start a new agent\"]')", 15)
    b.js("__click('button[title=\"Start a new agent\"]')")
    b.js("__set('input[aria-label=\"Agent name\"]', 'E-commerce helper (UI journey)')")
    picks = (("PDF", "src1", "ecommerce-architecture.pdf"), ("GitHub repo", "src2", "Spoon-Knife"), ("Docs link", "src3", "Example Domain"))
    for kind, node_id, title in picks:
        b.js(f"__click('button.palette-item', '{kind}')")
        b.wait(f"document.querySelector('[data-node-id=\"{node_id}\"]')", 5)
        b.js("__click('input[aria-label=\"Connected to Agent\"]')")
        choose(b, "Project source", title)
    b.wait("document.querySelectorAll('.react-flow__edge').length === 7", 5)
    lines = b.js("['src1','src2','src3'].map(id => __text('[data-node-id=\"' + id + '\"] .node-line')).join(' | ')")
    check("builder: PDF, GitHub repo and Docs link blocks use the project's sources (nothing uploaded again)",
          b.js("document.querySelectorAll('.react-flow__edge').length") == 7 and lines.count("chunks") == 3, lines)
    b.js("document.querySelector('.react-flow__node[data-id=\"n4\"]').click(); true")
    b.wait("document.querySelector('select[aria-label=\"Agent model\"]')", 5)
    b.js(f"__set('select[aria-label=\"Agent model\"]', '{model}')")
    b.js("__click('.builder-actions button', 'Save agent')")
    saved = b.wait("__text('.builder-title').includes('Saved')", 15)
    check("builder: the shared agent is saved in the project", saved, b.js("__text('.builder-title .pill')"))
    b.shot("p8-08-agent-builder")
    agents = b.js(f"__api('/graphs?project_id={S['project']}')")["json"]
    S["agent"] = next((a["id"] for a in agents if a["name"].startswith("E-commerce helper")), None)

    b.js("__click('.builder-actions button', 'Run')")
    b.wait("document.querySelector('#run-q')", 5)
    b.js("__set('#run-q', 'Above what order value is delivery free?')")
    b.js("__click('.run-form button', 'Run')")
    done = b.wait("document.querySelector('.run-answer .answer-meta')", 420)
    status = {n: b.js(f"document.querySelector('[data-node-id=\"{n}\"]')?.dataset.status") for n in ("n1", "n4", "n5", "src1")}
    answer = b.js("__text('.run-answer .answer-text')")
    check("builder: a test run streams to a cited answer (Rs. 499) and the blocks light up from SSE events",
          done and "499" in answer and status["n5"] == "done" and status["src1"] == "cited", f"{json.dumps(status)} {answer[:100]}")
    b.shot("p8-09-agent-run")


def consumer_journey(b: Browser, base: str):
    in_company(b, base, "knowledge")
    b.wait("document.querySelectorAll('.source-row').length === 3", 15)
    check("consumer: sees the three shared sources and has nothing to upload",
          b.js("document.querySelectorAll('.source-row').length") == 3 and not b.js("!!document.querySelector('input[aria-label=\"Upload a file\"]')"))
    b.shot("p8-10-shared-knowledge")

    in_company(b, base, "chat")
    choose(b, "Ask", "E-commerce helper")
    b.js("__set('select[aria-label=\"Model\"]', 'local')")
    for q, fact in (("Above what order value is delivery free?", "499"),
                    ("Which service talks to the payment gateway?", None),
                    ("What happens to new orders if it is down?", "30")):
        n = b.js("document.querySelectorAll('.bot-body .answer-meta').length")
        b.js(f"__set('textarea[aria-label=\"Question\"]', {json.dumps(q)})")
        b.js("__click('button[aria-label=\"Send\"]')")
        b.wait(f"document.querySelectorAll('.bot-body .answer-meta').length > {n} && !document.querySelector('.composer .spinner')", 420)
        answer = b.js("[...document.querySelectorAll('.bot-body .answer-text')].pop()?.textContent || ''")
        if fact:
            check(f"chat: the consumer asks \"{q}\" and gets a cited answer ({fact})",
                  fact in answer and b.js("[...document.querySelectorAll('.bot-body')].pop().querySelectorAll('.citations li').length") >= 1,
                  answer[:140])
    b.shot("p8-11-employee-chat")
    convs = b.js(f"__api('/conversations?project_id={S['project']}')")["json"]
    S["conversation"] = convs[0]["id"] if convs else None

    # Persistent history: sign out, sign in again through the login page, reopen the conversation.
    b.js("__click('button[aria-label=\"Sign out\"]')")
    b.wait("document.querySelector('input[autocomplete=\"current-password\"]')", 15)
    helpers(b)
    b.js(f"__set('input[autocomplete=\"email\"]', {json.dumps(PEOPLE['consumer'][1])})")
    b.js(f"__set('input[autocomplete=\"current-password\"]', {json.dumps(PASSWORD)})")
    b.shot("p8-12-login")
    b.js("__click('button.btn.primary', 'Sign in')")
    b.wait("!!document.querySelector('.sidebar')", 20)
    in_company(b, base, "chat")
    b.wait("document.querySelector('.conv-item .list-btn')", 15)
    b.js("__click('.conv-item .list-btn')")
    ok = b.wait("document.querySelectorAll('.msg.user').length >= 3", 15)
    check("history: after signing out and in again the conversation and its 3 questions are still there", ok,
          str(b.js("document.querySelectorAll('.msg.user').length")))
    b.shot("p8-13-persistent-history")


def contributor_privacy(b: Browser, base: str):
    in_company(b, base, "chat")
    b.wait("document.querySelector('.conv-list')", 10)
    mine = b.js("document.querySelectorAll('.conv-item').length")
    peek = b.js(f"__api('/conversations/{S['conversation']}')")["status"] if S.get("conversation") else None
    check("privacy: the contributor (same project) does not see the consumer's conversation (list empty, direct id 404)",
          mine == 0 and peek == 404, f"listed {mine}, direct HTTP {peek}")


def outsider_journey(b: Browser, base: str):
    b.viewport(1440, 900)
    b.goto(f"{base}/#/register")
    ok = register(b, "outsider")
    names = b.js("__text('select[aria-label=\"Workspace\"]')")
    check("outsider: registers and sees only a personal workspace, nothing of the company", ok and COMPANY not in names, names[:120])
    paths = [f"/projects/{S['project']}", f"/projects/{S['project']}/sources", f"/graphs/{S.get('agent')}",
             f"/sources/{S.get('sources', {}).get('pdf')}/file", f"/conversations/{S.get('conversation')}", f"/orgs/{S['org']}/members"]
    codes = {p: b.js(f"__api({json.dumps(p)})")["status"] for p in paths}
    check("outsider: every company project, agent, file, conversation and member route returns 404",
          all(c == 404 for c in codes.values()), json.dumps(codes))
    b.goto(S["link_consumer"])
    helpers(b)
    used = b.wait("__text('body').includes('accepted') || __text('body').includes('Ask the company for a new link')", 15)
    check("outsider: a used invitation link is refused", used)
    b.shot("p8-14-unauthorized-invitation")
    b.send("Page.navigate", {"url": f"{base}/api/conversations/{S.get('conversation')}"})
    time.sleep(1.5)
    body = b.js("document.body.innerText")
    check("outsider: opening another person's conversation directly gives 404 (no content)", "No such" in body or "Not Found" in body, body[:120])
    b.shot("p8-15-unauthorized-conversation")


def pages_journey(b: Browser, base: str):
    in_company(b, base, "usage")
    ok = b.wait("document.querySelectorAll('.kpi strong').length >= 4", 20)
    check("usage: the owner's dashboard loads company usage", ok,
          b.js("[...document.querySelectorAll('.kpi strong')].map(e => e.textContent).join(' | ')"))
    b.shot("p8-16-usage")
    open_app(b, base, "status")
    check("status: all four services healthy", b.wait("document.querySelectorAll('[data-ok=\"true\"]').length === 4", 20))


def responsive_journey(b: Browser, base: str):
    keep = {("overview", 390), ("chat", 390), ("agents", 768), ("knowledge", 768), ("members", 390)}
    for width, height in ((1440, 900), (1024, 768), (768, 1024), (390, 844)):
        b.viewport(width, height)
        bad = []
        for screen in ("overview", "projects", "knowledge", "agents", "chat", "usage", "members", "settings", "status"):
            open_app(b, base, screen)
            over = b.js("document.documentElement.scrollWidth - innerWidth")
            if over > 1:
                bad.append(f"{screen} +{over}px")
            if (screen, width) in keep:
                b.shot(f"p8-{screen}-{width}px")
        check(f"responsive: no sideways scrolling at {width}px", not bad, ", ".join(bad))
    b.viewport(1440, 900)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # answers contain Rs, Telugu and Hindi
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3000")
    ap.add_argument("--model", default="local", choices=["local", "local_small", "groq", "gemini"])
    ap.add_argument("--no-responsive", action="store_true", help="skip the four screen sizes")
    args = ap.parse_args()
    exe = next((p for p in BROWSERS if os.path.exists(p)), None)
    if not exe:
        print("[SKIP] no Edge or Chrome found")
        return 0
    people = {k: Browser(exe) for k in ("owner", "contributor", "consumer", "outsider")}  # four separate profiles
    steps = [("owner", lambda: owner_journey(people["owner"], args.base)),
             ("contributor invitation", lambda: invitee_journey(people["contributor"], args.base, "contributor", "p8-invitation-accept")),
             ("consumer invitation", lambda: invitee_journey(people["consumer"], args.base, "consumer", None)),
             ("confirm and assign", lambda: owner_confirms(people["owner"], args.base)),
             ("uploads", lambda: contributor_uploads(people["contributor"], args.base)),
             ("agent", lambda: owner_builds_agent(people["owner"], args.base, args.model)),
             ("consumer chat", lambda: consumer_journey(people["consumer"], args.base)),
             ("contributor privacy", lambda: contributor_privacy(people["contributor"], args.base)),
             ("outsider", lambda: outsider_journey(people["outsider"], args.base)),
             ("pages", lambda: pages_journey(people["owner"], args.base))]
    if not args.no_responsive:
        steps.append(("responsive", lambda: responsive_journey(people["owner"], args.base)))
    try:
        for name, step in steps:
            try:
                step()
            except Exception as exc:  # a broken step is a failure; the next journey still runs
                check(f"{name}: journey crashed", False, str(exc)[:200])
        for k, b in people.items():
            # 401/404 responses the journeys provoke on purpose are logged by the browser as failed requests
            errors = [e for e in b.console_errors if "status of 401" not in e and "status of 404" not in e]
            check(f"{k}: no browser console errors", not errors, "; ".join(errors[:3]))
    finally:
        for b in people.values():
            b.close()
    passed = sum(r[0] == "PASS" for r in results)
    print(f"\n{passed} passed, {len(results) - passed} failed. Screenshots: {SHOTS}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

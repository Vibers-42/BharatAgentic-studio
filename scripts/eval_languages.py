"""Runs the Telugu/Hindi/English test questions (eval/questions.json) against every model and records
which model gets which question right, as the handoff asks. Standard library only, so it runs with any
Python 3.10+ against the running api:

    python scripts/eval_languages.py                         # all models, all languages
    python scripts/eval_languages.py --models groq,local --languages te,hi
    python scripts/eval_languages.py --report eval/results/<run>.json --compare eval/results/<older>.json
        (rebuild eval/RESULTS.md from saved results, with a before/after table; sends no requests)

A question counts as RIGHT when the requested model itself answered (not a fallback) and:
  answerable:   the answer is supported, cites the expected file, contains every expected number,
                and is written in the question's language;
  unanswerable: the answer is "I don't know" (not supported).
A Gemini/Groq quota refusal is BLOCKED (says nothing about the model); after a daily-quota block the
rest of that model's questions are skipped. Results go to eval/results/<time>.json and eval/RESULTS.md.

Every answer is put in one category: correct; incorrect (wrong file, missing number, or answered an
unanswerable question); unsupported (said "I don't know" to an answerable question, including when the
claim check removed every sentence); wrong language; quota-blocked; or error/fallback."""

import argparse
import datetime
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://127.0.0.1:8000"
DIGITS = str.maketrans("०१२३४५६७८९౦౧౨౩౪౫౬౭౮౯", "01234567890123456789")
SCRIPTS = {"te": (0x0C00, 0x0C7F), "hi": (0x0900, 0x097F)}


def call(method: str, path: str, body: bytes | None = None, content_type: str = "application/json") -> dict:
    req = urllib.request.Request(API + path, data=body, method=method, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}") from exc


def upload(source_id: str, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return call("POST", f"/sources/{source_id}/pdf", body, f"multipart/form-data; boundary={boundary}")


def graph(source_ids: list[str], model: str) -> dict:
    nodes = [{"id": "n1", "type": "input", "config": {}},
             {"id": "n2", "type": "guardrails", "config": {"block": ["empty", "abuse", "off_topic"]}},
             {"id": "n3", "type": "router", "config": {}},
             {"id": "n4", "type": "agent", "config": {"model": model, "language": "en"}},
             {"id": "n5", "type": "output", "config": {}}]
    edges = [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}, {"from": "n3", "to": "n4"}, {"from": "n4", "to": "n5"}]
    for sid in source_ids:
        nodes.append({"id": sid, "type": "source_doc", "config": {}})
        edges.append({"from": sid, "to": "n4"})
    return {"nodes": nodes, "edges": edges}


def numbers(text: str) -> set[str]:
    return {str(int(n)) for n in re.findall(r"\d+", text.translate(DIGITS).replace(",", ""))}


def in_language(text: str, lang: str) -> bool:
    letters = [ch for ch in re.sub(r"\[\d+\]", "", text) if ch.isalpha()]
    if not letters:
        return False
    if lang == "en":
        return sum(ch.isascii() for ch in letters) / len(letters) > 0.8
    lo, hi = SCRIPTS[lang]
    return sum(lo <= ord(ch) <= hi for ch in letters) / len(letters) > 0.5


def score(q: dict, lang: str, model: str, result: dict) -> tuple[str, str]:
    """(verdict, reason). verdict: RIGHT, WRONG, BLOCKED, FALLBACK, ERROR."""
    if result.get("type") == "error":
        quota = [a for a in result.get("attempts", []) if str(a.get("code", "")).startswith("quota_")]
        return ("BLOCKED", quota[0]["code"]) if quota else ("ERROR", result.get("code", "error"))
    used = (result.get("model") or "").split(":")[0]
    if used and used != model:
        code = (result.get("fallback") or {}).get("code", "")
        return ("BLOCKED" if code.startswith("quota_") else "FALLBACK"), f"answered by {used} ({code})"
    answer = result.get("answer", "")
    if not q["answerable"]:
        return ("RIGHT", "said it does not know") if not result.get("supported") else ("WRONG", "answered an unanswerable question")
    if not result.get("supported"):
        return "WRONG", "said it does not know"
    files = {c["file"] for c in result.get("citations", [])}
    if q["file"] not in files:
        return "WRONG", f"cited {sorted(files)} instead of {q['file']}"
    missing = [f for f in q["facts"] if f not in numbers(answer)]
    if missing:
        return "WRONG", f"missing {', '.join(missing)}"
    if not in_language(answer, lang):
        return "WRONG", f"not written in {lang}"
    return "RIGHT", ""


CATEGORIES = ["correct", "incorrect", "unsupported", "wrong_language", "quota_blocked", "error"]


def category(row: dict) -> str:
    if row["verdict"] == "RIGHT":
        return "correct"
    if row["verdict"] == "BLOCKED":
        return "quota_blocked"
    if row["verdict"] in ("FALLBACK", "ERROR"):
        return "error"
    if row["reason"].startswith("said it does not know"):
        return "unsupported"
    if row["reason"].startswith("not written in"):
        return "wrong_language"
    return "incorrect"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="gemini,groq,local")
    ap.add_argument("--languages", default="en,te,hi")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between cloud requests (rate limits)")
    ap.add_argument("--user", default="eval")
    ap.add_argument("--report", help="rebuild eval/RESULTS.md from this saved results file; sends no requests")
    ap.add_argument("--compare", help="an older results file, to show which answers changed")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    spec = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    older = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else None
    if args.report:
        saved = json.loads(Path(args.report).read_text(encoding="utf-8"))
        rows = saved["rows"]
        models = list(dict.fromkeys(r["model"] for r in rows))
        languages = list(dict.fromkeys(r["lang"] for r in rows))
        write_markdown(saved["run"], models, languages, spec["questions"], rows, older)
        return 0
    call("POST", "/users", json.dumps({"name": args.user}).encode())
    for sid, path in spec["sources"].items():
        info = upload(sid, ROOT / path)
        print(f"source {sid}: {info['file']}, {info['chunks']} chunks")
    sources = list(spec["sources"])
    models, languages = args.models.split(","), args.languages.split(",")

    rows = []
    for model in models:
        daily_block = None
        for lang in languages:
            for q in spec["questions"]:
                if daily_block:
                    rows.append({"model": model, "lang": lang, "id": q["id"], "verdict": "BLOCKED",
                                 "reason": f"{daily_block} (not sent)", "answer": "", "answered_by": None})
                    continue
                body = {"question": q[lang], "graph": graph(sources, model), "model": model, "user": args.user}
                started = time.time()
                try:
                    events = call("POST", "/pipeline/run?stream=false", json.dumps(body).encode())["events"]
                    result = events[-1]
                except RuntimeError as exc:
                    result = {"type": "error", "code": "http_error", "message": str(exc)}
                verdict, reason = score(q, lang, model, result)
                if verdict == "BLOCKED" and "quota_daily" in reason:
                    daily_block = "quota_daily"
                rows.append({"model": model, "lang": lang, "id": q["id"], "verdict": verdict, "reason": reason,
                             "answer": result.get("answer", result.get("message", ""))[:300],
                             "answered_by": result.get("model"), "seconds": round(time.time() - started, 1),
                             "usage": result.get("usage"), "cost": (result.get("cost") or {}).get("inr")})
                print(f"{model:6s} {lang} {q['id']} {verdict:8s} {reason[:60]:60s} {rows[-1]['answer'][:70]!r}")
                if model != "local" and args.delay:
                    time.sleep(args.delay)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stamp.replace(':', '').replace(' ', '_')}.json").write_text(
        json.dumps({"run": stamp, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(stamp, models, languages, spec["questions"], rows, older)
    return 0


def write_markdown(stamp, models, languages, questions, rows, older=None):
    def sel(model, lang, data=None):
        return [r for r in (data or rows) if r["model"] == model and r["lang"] == lang]

    def name(model):
        """'local (qwen3:14b)': the key plus the model that actually answered, so runs stay comparable
        when a key is pointed at a different model."""
        ids = {r["answered_by"].split(":", 1)[1] for r in rows
               if r["model"] == model and r.get("answered_by") and r["answered_by"].startswith(model + ":")}
        return f"{model} ({', '.join(sorted(ids))})" if ids else model

    def score_cell(model, lang):
        rs = sel(model, lang)
        judged = [r for r in rs if category(r) != "quota_blocked"]
        right = sum(category(r) == "correct" for r in rs)
        blocked = len(rs) - len(judged)
        text = f"{right}/{len(judged)}" if judged else "-"
        return text + (f" ({blocked} blocked)" if blocked else "")

    mark = {"correct": "yes", "incorrect": "wrong", "unsupported": "IDK", "wrong_language": "lang",
            "quota_blocked": "blocked", "error": "error"}
    lines = [f"# Language evaluation, {stamp}", "",
             "Generated by `scripts/eval_languages.py` from `eval/questions.json`: 12 questions per language over "
             "the two fictional sample PDFs (10 answerable, 2 that should get \"I don't know\").", "",
             "- **correct**: the requested model answered, cited the expected file, kept every expected number and "
             "wrote in the question's language (or said \"I don't know\" to an unanswerable question)",
             "- **incorrect**: wrong file, a missing or wrong number, or an answer to an unanswerable question",
             "- **unsupported**: \"I don't know\" to an answerable question (the model gave up, or the claim check "
             "removed every sentence)",
             "- **wrong_language**: right facts, but not in the question's language",
             "- **quota_blocked**: the provider's quota refused the request; says nothing about the model", "",
             "## Score per model and language (correct / judged)", "",
             "| Model | " + " | ".join(languages) + " |", "|---|" + "---|" * len(languages)]
    for m in models:
        lines.append(f"| {name(m)} | " + " | ".join(score_cell(m, l) for l in languages) + " |")
    lines += ["", "## Breakdown", "", "| Model | Language | " + " | ".join(CATEGORIES) + " |",
              "|---|---|" + "---|" * len(CATEGORIES)]
    for m in models:
        for l in languages:
            counts = [sum(category(r) == c for r in sel(m, l)) for c in CATEGORIES]
            lines.append(f"| {name(m)} | {l} | " + " | ".join(str(c) for c in counts) + " |")
    lines += ["", "## Every question", "", "yes = correct, wrong = incorrect, IDK = unsupported, lang = wrong language.", "",
              "| Question | " + " | ".join(f"{m} {l}" for m in models for l in languages) + " |",
              "|---|" + "---|" * (len(models) * len(languages))]
    for q in questions:
        cells = []
        for m in models:
            for l in languages:
                r = next((r for r in sel(m, l) if r["id"] == q["id"]), None)
                cells.append(mark[category(r)] if r else "-")
        lines.append(f"| {q['id']} {q['en']} | " + " | ".join(cells) + " |")
    if older:
        lines += ["", f"## What changed since the previous run ({older['run']})", "",
                  "| Model | Language | Question | Before | After |", "|---|---|---|---|---|"]
        changed = [(r, o) for r in rows for o in older["rows"]
                   if (o["model"], o["lang"], o["id"]) == (r["model"], r["lang"], r["id"]) and category(o) != category(r)]
        lines += [f"| {r['model']} | {r['lang']} | {r['id']} | {mark[category(o)]} | {mark[category(r)]} |"
                  for r, o in changed] or ["| - | - | - | - | - |"]
    lines += ["", "## Answers that were not correct", ""]
    lines += [f"- {r['model']} {r['lang']} {r['id']} ({category(r)}): {r['reason']}. Answer: {r['answer'][:160]!r}"
              for r in rows if category(r) not in ("correct", "quota_blocked")] or ["- none"]
    (ROOT / "eval" / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote eval/RESULTS.md")


if __name__ == "__main__":
    sys.exit(main())

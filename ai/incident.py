"""Week 10 — Incident assistant: root-cause guess + postmortem for every incident (Person 3).

Runs AFTER incidents, never on the request path (an LLM there is slow and not repeatable).

For each incident it collects what an on-call engineer would see (controller events, AI alerts,
error/latency numbers from Prometheus) and asks for a root-cause guess plus ONE follow-up action.
Safety (the plan's rules):
  - the answer must pick from ALLOWED_ACTIONS; anything else is thrown away and logged
  - it only suggests; nothing is executed (a person decides)
  - every decision is logged to data/ai.jsonl
  - no LLM configured? a rule-based analyst writes the same report, so the demo always works

The guess sees only symptoms. The chaos log (what really broke) is shown afterwards as a check.

    python -m ai.incident                         # all incidents in data/, rule-based
    LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1 python -m ai.incident   # Ollama
    LLM_BASE_URL=<hosted /v1 URL> LLM_MODEL=<model> LLM_API_KEY=<key> python -m ai.incident
Keys go in .env (git-ignored), never in code.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from ai.anomaly import DEFAULT_PROM, Fault, load_faults, log_ai
from experiments.mttr import DATA, read_jsonl

ALLOWED_ACTIONS = {
    "restart_server": "restart the server (the controller already does this for crashes)",
    "lower_traffic": "keep the server at reduced traffic weight until it is fixed",
    "adjust_rate_limit": "change the gateway rate limit",
    "add_server": "add another app server",
    "none": "no action needed",
}
OUT = Path(__file__).resolve().parent.parent / "experiments" / "results" / "postmortems"
BEFORE, AFTER = 10, 30  # seconds of context around an incident
DID = {  # controller event -> plain words for the postmortem
    "server_down": "took it out of traffic",
    "restart": "restarted it",
    "ramp": "brought its traffic back step by step",
    "recovered": "marked it recovered",
    "warning": "lowered its traffic after the AI warning",
    "quarantined": "quarantined it",
    "emergency_on": "switched on emergency mode",
    "emergency_off": "switched emergency mode off",
}


# ---------------------------------------------------------------- 1. what was seen


def peaks(prom: str, server: str, start: float, end: float) -> dict:
    """Worst error ratio / p95 of that server during the incident. {} if Prometheus is away."""
    from ai.anomaly import fetch

    try:
        rows = [r for r in fetch(prom, start, end) if r["server_id"] == server]
    except (OSError, RuntimeError):
        return {}
    if not rows:
        return {}
    return {
        "peak_error_ratio": max(r["err_ratio"] for r in rows),
        "peak_p95_ms": max(r["p95_ms"] for r in rows),
    }


def symptoms(f: Fault, data: Path, prom: str | None) -> dict:
    """Everything visible during the incident, WITHOUT the chaos log (that's the answer)."""
    lo, hi = f.start - BEFORE, f.end + AFTER
    events = [
        e
        for e in read_jsonl(data / "events.jsonl")
        if lo <= e["time"] <= hi and e.get("server") in (f.server_id, None)
    ]
    ai = [
        e for e in read_jsonl(data / "ai.jsonl") if lo <= e["time"] <= hi and e.get("server") == f.server_id
    ]
    timeline = [
        {
            "t": round(e["time"] - f.start, 1),
            "source": "controller",
            "what": e["type"],
            **{k: v for k, v in e.items() if k in ("reason", "weight", "recovery_s", "action")},
        }
        for e in events
    ] + [
        {
            "t": round(e["time"] - f.start, 1),
            "source": "ai",
            "what": f"anomaly: errors {e['err_ratio']:.0%}, p95 {e['p95_ms']:.0f} ms",
        }
        for e in ai
        if e.get("type") == "anomaly_detected"
    ]
    return {
        "server": f.server_id,
        "duration_s": round(f.end - f.start, 1),
        "timeline": sorted(timeline, key=lambda x: x["t"]),
        **(peaks(prom, f.server_id, f.start, f.end + 15) if prom else {}),
    }


# ---------------------------------------------------------------- 2. the two analysts


def rule_analyst(s: dict) -> dict:
    """No LLM: the same reasoning an engineer would do from the symptoms, as rules."""
    whats = {x["what"] for x in s["timeline"]}
    err, p95 = s.get("peak_error_ratio", 0), s.get("peak_p95_ms", 0)
    if "server_down" in whats:
        return {
            "root_cause": "The server stopped answering its health checks (crash, freeze or unreachable).",
            "confidence": 0.8,
            "action": "none",
            "why": "The controller removed and restarted it automatically; no extra step needed "
            "unless it happens again.",
            "evidence": ["controller logged server_down"],
        }
    if err >= 0.05:
        return {
            "root_cause": "The application returned errors while its process stayed healthy.",
            "confidence": 0.7,
            "action": "lower_traffic",
            "why": "Restarting doesn't fix an app-level bug; keep its share low until it's fixed.",
            "evidence": [f"peak error ratio {err:.0%}", "no server_down: health checks passed"],
        }
    if p95 >= 300:
        return {
            "root_cause": "The server became slow (overload or a slow dependency).",
            "confidence": 0.6,
            "action": "lower_traffic",
            "why": "A slow server drags every request it gets; send it less until it recovers.",
            "evidence": [f"peak p95 {p95:.0f} ms"],
        }
    return {
        "root_cause": "Unclear from the numbers available.",
        "confidence": 0.2,
        "action": "none",
        "why": "Not enough signal; check the server's own logs.",
        "evidence": [],
    }


def llm_analyst(s: dict) -> dict:
    """OpenAI-compatible chat API (Ollama and most hosted providers speak it)."""
    actions = "\n".join(f"- {k}: {v}" for k, v in ALLOWED_ACTIONS.items())
    prompt = (
        "You are the on-call engineer for a web platform with 3 app servers behind a gateway and a "
        "self-healing controller. One incident happened. Symptoms (times in seconds from the start):\n"
        f"{json.dumps(s, indent=1)}\n\nAllowed follow-up actions (pick exactly one):\n{actions}\n\n"
        'Answer with JSON only: {"root_cause": str, "confidence": number 0-1, "evidence": [str], '
        '"action": one of the allowed names, "why": str}'
    )
    body = {
        "model": os.environ["LLM_MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,  # same incident, same answer as far as possible
    }
    headers = {"Content-Type": "application/json"}
    if os.getenv("LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['LLM_API_KEY']}"
    req = urllib.request.Request(
        f"{os.environ['LLM_BASE_URL'].rstrip('/')}/chat/completions", json.dumps(body).encode(), headers
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", text, re.S)  # models sometimes wrap JSON in prose or ``` fences
    if not match:
        raise ValueError(f"no JSON in answer: {text[:200]}")
    return json.loads(match.group())


def analyse(s: dict, use_llm: bool) -> dict:
    """Pick the analyst, then enforce the safety rules on whatever came back."""
    who = "rules"
    if use_llm:
        try:
            out, who = llm_analyst(s), f"LLM {os.environ['LLM_MODEL']}"
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            out = rule_analyst(s)
            out["note"] = f"LLM failed ({e}); rule-based answer used"
    else:
        out = rule_analyst(s)
    out["analyst"] = who
    if out.get("action") not in ALLOWED_ACTIONS:  # safety: outside the list -> refused
        out["refused_action"] = out.get("action")
        out["action"], out["why"] = "none", ""  # its reason argued for the refused action
    try:
        out["confidence"] = min(1.0, max(0.0, float(out.get("confidence", 0))))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["executed"] = False  # suggestions only; a person decides
    return out


# ---------------------------------------------------------------- 3. the postmortem


def did_text(s: dict) -> str:
    seen = [x["what"] for x in s["timeline"] if x["source"] == "controller" and x["what"] in DID]
    words = list(dict.fromkeys(DID[w] for w in seen))  # in order, no repeats
    return ("The controller " + ", then ".join(words) + ".") if words else "Nothing."


def postmortem(f: Fault, s: dict, a: dict) -> str:
    when = datetime.fromtimestamp(f.start).strftime("%Y-%m-%d %H:%M:%S")
    rows = (
        "\n".join(
            f"| {x['t']:+.1f} s | {x['source']} | {x['what']}"
            + "".join(f", {k} {v}" for k, v in x.items() if k not in ("t", "source", "what"))
            + " |"
            for x in s["timeline"]
        )
        or "| – | – | nothing logged |"
    )
    impact = []
    if "peak_error_ratio" in s:
        impact.append(f"worst error ratio {s['peak_error_ratio']:.0%}, worst p95 {s['peak_p95_ms']:.0f} ms")
    if f.health_check_sec is not None:
        impact.append(f"health check noticed after {f.health_check_sec:.1f} s")
    else:
        impact.append("health checks never noticed (the server kept answering /health)")
    ai_hits = [x["t"] for x in s["timeline"] if x["source"] == "ai"]
    if ai_hits:
        impact.append(f"AI noticed after {min(ai_hits):.1f} s")
    refused = (
        f"\n> The analyst suggested `{a['refused_action']}`, which is not on the allowed list. "
        "It was refused and replaced with `none`.\n"
        if a.get("refused_action")
        else ""
    )
    note = f"\n> {a['note']}\n" if a.get("note") else ""
    evidence = "\n".join(f"- {e}" for e in a.get("evidence", [])) or "- none given"
    return f"""# Postmortem: {f.server_id}, {when}

- **Duration:** {s["duration_s"]:.0f} s
- **Written by:** {a["analyst"]}
- **Status:** suggestion only, nothing was executed

## Impact
{chr(10).join(f"- {i}" for i in impact)}

## Timeline (seconds from the start)
| Time | Source | What happened |
|---|---|---|
{rows}

## Most likely cause
{a["root_cause"]} (confidence {a["confidence"]:.0%})

Evidence:
{evidence}

## What the system did by itself
{did_text(s)}

## Suggested follow-up
`{a["action"]}`: {ALLOWED_ACTIONS[a["action"]]}. {a.get("why", "")}
{refused}{note}
## Check against what really happened
The chaos log says: `{f.kind}` on {f.server_id}. (The analyst did not see this line.)
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--since", type=float, default=0, help="only incidents after this unix time")
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--prom", default=DEFAULT_PROM, help='"" = skip the numbers')
    a = p.parse_args(argv)

    use_llm = bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL"))
    faults = [f for f in load_faults(a.data, a.since, time.time()) if f.start >= a.since]
    if not faults:
        print(f"no incidents in {a.data}/chaos.jsonl")
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    for f in faults:
        s = symptoms(f, a.data, a.prom or None)
        result = analyse(s, use_llm)
        keep = ("analyst", "action", "refused_action", "confidence", "executed")
        record = {"type": "incident_analysis", "server": f.server_id, "incident_start": f.start}
        log_ai({**record, **{k: result.get(k) for k in keep}}, a.data)
        name = f"{datetime.fromtimestamp(f.start):%Y%m%d-%H%M%S}-{f.server_id}.md"
        (a.out / name).write_text(postmortem(f, s, result), encoding="utf-8")
        print(f"{f.server_id:<5} {f.kind:<6} -> {result['action']:<17} ({result['analyst']})  {a.out / name}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles: same fix as chaos/chaos.py
    sys.exit(main())

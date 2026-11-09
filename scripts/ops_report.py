"""Turn the customer logs into a short report for the store team.

What needs a human (open handoffs), what the guardrails stopped, what customers asked for that the store does not
have (demand signals), and what the assistant costs per conversation.

    python -m scripts.ops_report                 # prints markdown
    python -m scripts.ops_report --out report.md
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from emporio.customer_records import ROOT as CUSTOMERS

# gpt-6-luna list prices in USD per 1M tokens, from developers.openai.com on 2026-11-09.
PRICE_PER_M = {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50}
# Accessories the manual (1 and 7.1) says the store does not sell.
ACCESSORIES = re.compile(r"\b(cordas?|palhetas?|cabos?|cases?|capas?|pedal|pedais|amplificador(es)?|amp)\b", re.I)


def read_events(root: Path) -> list[dict]:
    events = []
    for path in sorted(root.glob("*/*.jsonl")):
        events += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(events, key=lambda e: e["ts"])


def call_cost(e: dict) -> float:
    uncached = max(e["input_tokens"] - e["cached_tokens"] - e["cache_write_tokens"], 0)
    return (uncached * PRICE_PER_M["input"] + e["cached_tokens"] * PRICE_PER_M["cached"]
            + e["cache_write_tokens"] * PRICE_PER_M["cache_write"] + e["output_tokens"] * PRICE_PER_M["output"]) / 1e6


def report(events: list[dict]) -> str:
    sessions = {e["session"] for e in events}
    customer_of = {e["session"]: e["customer_id"] for e in events if e["event"] == "customer_identified"}
    handoffs = [e for e in events if e["event"] == "handoff"]
    guardrails = Counter(e["rule"] for e in events if e["event"] == "guardrail")
    calls = [e for e in events if e["event"] == "llm_call"]
    cost = sum(call_cost(e) for e in calls)
    searches = [e for e in events if e["event"] == "tool_call" and e["tool"] == "search_products"]
    empty = Counter(" / ".join(filter(None, (s["args"].get("query"), s["args"].get("category")))) or "(no filter)"
                    for s in searches if s["result"].get("count") == 0)
    asked = Counter(m.group(1).lower() for e in events if e["event"] == "user_message"
                    for m in ACCESSORIES.finditer(e["text"]))

    out = ["# Assistant report", ""]
    if events:
        out += [f"Logs from {events[0]['ts'][:10]} to {events[-1]['ts'][:10]}.", ""]
    out += [f"- Conversations: {len(sessions)} ({len(customer_of)} with an identified customer)",
            f"- Customer messages: {sum(e['event'] == 'user_message' for e in events)}",
            f"- Handoffs to the team: {len(handoffs)}",
            f"- Model calls: {len(calls)}, cost US$ {cost:.4f}"
            + (f" (US$ {cost / len(sessions):.4f} per conversation)" if sessions else ""), ""]

    out += ["## Handoffs to answer (24 business hours, manual 7.3)", ""]
    out += [f"- {e['ts'][:16].replace('T', ' ')} · customer {customer_of.get(e['session'], 'not identified')} · "
            f"{e['reason']}: {e['summary']}" for e in handoffs] or ["- none"]

    out += ["", "## Guardrails that fired", ""]
    out += [f"- {rule}: {n}" for rule, n in guardrails.most_common()] or ["- none"]

    out += ["", "## Demand signals", "", "Searches with no result:", ""]
    out += [f"- {q}: {n}" for q, n in empty.most_common()] or ["- none"]
    out += ["", "Accessories asked for (not sold, manual 7.1):", ""]
    out += [f"- {a}: {n}" for a, n in asked.most_common()] or ["- none"]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Report for the store team built from customer logs")
    parser.add_argument("--logs", type=Path, default=CUSTOMERS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    text = report(read_events(args.logs))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()

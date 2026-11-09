"""Run the live scenarios several times and write tests/EVAL_RESULTS.md with the pass rate of each one.

Model output changes between runs, so one green run says little; this measures how often each behavior holds.

    python -m scripts.eval_live --runs 5
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path

from emporio.__main__ import load_env
from emporio.agent import DEFAULT_MODEL

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "EVAL_RESULTS.md"


def run_once(junit: Path) -> dict[str, str | None]:
    """One pytest run of tests/test_live.py; maps each scenario to None (passed) or its failure message."""
    subprocess.run([sys.executable, "-m", "pytest", "tests/test_live.py", "-q", "-p", "no:cacheprovider",
                    f"--junitxml={junit}"], cwd=ROOT, env={**os.environ, "RUN_LIVE": "1"}, capture_output=True)
    results = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        problem = next((child for child in case if child.tag in ("failure", "error", "skipped")), None)
        results[case.get("name")] = None if problem is None else (problem.get("message") or problem.tag).splitlines()[0]
    return results


def render(runs: int, outcomes: dict[str, list[str | None]], seconds: float) -> str:
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    total = sum(len(v) for v in outcomes.values())
    passed = sum(m is None for v in outcomes.values() for m in v)
    lines = ["# Live evaluation", "",
             f"`python -m scripts.eval_live --runs {runs}` on {date.today().isoformat()}, model `{model}`, "
             f"{total} checks in {seconds / 60:.1f} min. Scenarios and assertions: `tests/test_live.py`.", "",
             f"**Overall: {passed}/{total} ({passed / total:.0%})**", "",
             "| Scenario | Passed | Rate |", "|---|---|---|"]
    for name, results in outcomes.items():
        ok = sum(m is None for m in results)
        lines.append(f"| {name.removeprefix('test_').replace('_', ' ')} | {ok}/{len(results)} | {ok / len(results):.0%} |")
    failures = [(name, m) for name, results in outcomes.items() for m in results if m is not None]
    lines += ["", "## Failures", ""]
    lines += [f"- `{name}`: {message}" for name, message in failures] or ["- none"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is missing: the evaluation calls the real model.")

    outcomes: dict[str, list[str | None]] = defaultdict(list)
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.runs):
            for name, message in run_once(Path(tmp) / f"run{i}.xml").items():
                outcomes[name].append(message)
            print(f"run {i + 1}/{args.runs}: {sum(m[-1] is None for m in outcomes.values())}/{len(outcomes)} passed")
    OUT.write_text(render(args.runs, outcomes, time.monotonic() - start), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} written")


if __name__ == "__main__":
    main()

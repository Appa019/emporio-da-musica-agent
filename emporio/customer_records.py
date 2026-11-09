"""Per-customer audit log (append-only JSONL) and long-term memory (JSON).

    customers/<customer_id>/log.jsonl     every message, tool call and guardrail decision
    customers/<customer_id>/memory.json   short facts carried into the next conversation
    customers/_anonymous/<session>.jsonl  events before the customer is identified

Phones and emails are masked before anything is written (LGPD, policy section 9).
"""

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "customers"
MAX_FACTS = 8

EMAIL = re.compile(r"\b([\w.+-])[\w.+-]*@([\w-]+\.[\w.]+)")
PHONE = re.compile(r"(?<!\d)(?:\+?55\s?)?(?:\(?0?\d{2}\)?\s?)?9?\d{4}[-\s]?(\d{4})(?!\d)")


def mask(text: str) -> str:
    text = EMAIL.sub(r"\1***@\2", text)
    return PHONE.sub(r"*****-\1", text)


def mask_data(value):
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: mask_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_data(v) for v in value]
    return value


class SessionLog:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.customer_id: str | None = None
        self.path = root / "_anonymous" / f"{self.session_id}.jsonl"

    def write(self, event: str, **data) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": self.session_id,
                  "event": event, **mask_data(data)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def link_customer(self, customer_id: str) -> None:
        """Move what was logged anonymously into the customer's own log."""
        self.customer_id = customer_id
        target = self.root / customer_id / "log.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with open(target, "a", encoding="utf-8") as out, open(self.path, encoding="utf-8") as anon:
                shutil.copyfileobj(anon, out)
            self.path.unlink()
        self.path = target


def load_memory(customer_id: str, root: Path = ROOT) -> list[str]:
    path = root / customer_id / "memory.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["facts"]


def save_memory(customer_id: str, facts: list[str], root: Path = ROOT) -> None:
    path = root / customer_id / "memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "facts": [mask(f) for f in facts][:MAX_FACTS]}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

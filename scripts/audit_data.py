"""Audit the store data and write data/DATA_REPORT.md.

The agent does not silently fix the data: format problems are handled by
explicit rules in the agent's code, and business inconsistencies (an order
total that does not match its items) are reported as recorded and flagged for
a human. This script documents every finding and the decision taken for it.

    python -m scripts.audit_data
"""

import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from emporio.store import STALE_DAYS, load_table, min_installment

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "DATA_REPORT.md"


def audit(today: date) -> list[tuple[str, str, list[str]]]:
    products, customers, orders = load_table("products"), load_table("customers"), load_table("orders")
    items, promotions, categories = load_table("order_items"), load_table("promotions"), load_table("categories")
    product = {p["product_id"]: p for p in products}
    customer_ids = {c["customer_id"] for c in customers}
    items_by_order = defaultdict(list)
    for i in items:
        items_by_order[i["order_id"]].append(i)

    checks = []

    rows = [f"{p['product_id']} {p['name']}: status={p['status']}, stock={p['stock_quantity']}"
            for p in products if p["status"] != "active" or int(p["stock_quantity"]) <= 0]
    checks.append(("Products not available for sale",
                   "Only `active` products with stock > 0 are offered. Discontinued items are reported as out of "
                   "catalog and `coming_soon` as not yet available, even when stock > 0 (policy 7.3).", rows))

    by_category = defaultdict(list)
    for p in products:
        by_category[p["category_id"]].append(p)
    rows = []
    for cat, ps in by_category.items():
        median = statistics.median(float(p["price_brl"]) for p in ps)
        rows += [f"{p['product_id']} {p['name']}: R$ {float(p['price_brl']):.2f} vs category median R$ {median:.2f}"
                 for p in ps if float(p["price_brl"]) < median / 4]
    checks.append(("Price outliers (below 1/4 of the category median)",
                   "The agent quotes the system price (policy 7.1 says never quote without the system), "
                   "but the item is listed here for a human to review.", rows))

    used = {p["category_id"] for p in products}
    rows = [f"{c['category_id']} {c['name']}" for c in categories if c["category_id"] not in used]
    checks.append(("Categories without products",
                   "The agent says the category exists but has no item in the catalog right now; "
                   "it never invents products to fill the gap.", rows))

    rows = []
    for field in ("phone", "email"):
        counts = Counter(c[field].strip().lower() for c in customers)
        rows += [f"{field} {value}: customers {', '.join(c['customer_id'] + ' ' + c['name'] for c in customers if c[field].strip().lower() == value)}"
                 for value, n in counts.items() if n > 1]
    checks.append(("Duplicate customer contact data",
                   "Phone is unique, so it is the identification key. An email shared by two customers "
                   "is rejected as ambiguous and the agent asks for the phone.", rows))

    rows = []
    for o in orders:
        if o["customer_id"] not in customer_ids:
            rows.append(f"order {o['order_id']}: unknown customer {o['customer_id']}")
        if not items_by_order[o["order_id"]]:
            rows.append(f"order {o['order_id']}: no items")
    rows += [f"item of order {i['order_id']}: unknown product {i['product_id']}" for i in items if i["product_id"] not in product]
    checks.append(("Referential integrity", "Nothing to decide when empty.", rows))

    rows = []
    for o in orders:
        listed = sum(float(product[i["product_id"]]["price_brl"]) * int(i["quantity"]) for i in items_by_order[o["order_id"]])
        total = float(o["total_brl"])
        if abs(listed - total) > 0.01:
            rows.append(f"order {o['order_id']} ({o['payment_method']}): total R$ {total:.2f} vs current list price of items R$ {listed:.2f}")
        elif o["payment_method"] == "pix":
            rows.append(f"order {o['order_id']} (pix): total equals list price, the 5% PIX discount is not reflected")
    checks.append(("Order totals vs list price of the items",
                   "Order items have no unit price, so the gap may be a price change or an error. The agent "
                   "reports the total as recorded and never recalculates a customer's bill.", rows))

    rows = []
    for o in orders:
        m = re.fullmatch(r"credit_(\d+)x", o["payment_method"])
        if m:
            n = int(m[1])
            per = float(o["total_brl"]) / n
            if per < min_installment(n):
                rows.append(f"order {o['order_id']}: {n}x of R$ {per:.2f}, minimum is R$ {min_installment(n):.2f}")
    checks.append(("Installments below the policy minimum",
                   "Historical orders are not changed. For new questions the agent applies section 3.1.", rows))

    rows = []
    for o in orders:
        age = (today - date.fromisoformat(o["order_date"])).days
        if o["status"] == "shipped" and o["estimated_delivery"] and date.fromisoformat(o["estimated_delivery"]) < today:
            rows.append(f"order {o['order_id']}: shipped, estimated delivery {o['estimated_delivery']} already passed")
        elif o["status"] in ("pending", "confirmed") and age > STALE_DAYS:
            rows.append(f"order {o['order_id']}: {o['status']} for {age} days")
    checks.append((f"Orders stuck in the same status (reference date {today.isoformat()})",
                   "The agent reports the recorded status, says it looks delayed and offers to hand over "
                   "to the team (complaint flow, policy 7.3).", rows))

    rows = [f"order {i['order_id']} ({o['status']}): product {i['product_id']} {product[i['product_id']]['name']} is {product[i['product_id']]['status']}"
            for o in orders for i in items_by_order[o["order_id"]] if product[i["product_id"]]["status"] != "active"]
    checks.append(("Orders with products that are not active",
                   "Order lookups still show the item; the status only affects new sales.", rows))

    active = [p for p in promotions if p["is_active"] == "1"]
    rows = [f"promotion {p['promotion_id']}: unknown product {p['product_id']}" for p in promotions if p["product_id"] not in product]
    rows += [f"product {pid}: {n} active promotions" for pid, n in Counter(p["product_id"] for p in active).items() if n > 1]
    rows += [f"promotion {p['promotion_id']}: active on {product[p['product_id']]['status']} product {p['product_id']}"
             for p in active if p["product_id"] in product and product[p["product_id"]]["status"] != "active"]
    checks.append(("Promotion integrity",
                   f"{len(active)} of {len(promotions)} promotions are active. Only `is_active = 1` counts; an expired "
                   "campaign is never promised (policy 7.3), even when the manual says it runs this month.", rows))

    policies = (ROOT / "data" / "policies.txt").read_text(encoding="utf-8")
    phones = sorted(set(re.findall(r"\(67\) \d{4}-\d{4}", policies)))
    emails = sorted(set(re.findall(r"[\wÀ-ú.]+@[\wÀ-ú.]+\.br", policies)))
    rows = []
    if len(phones) > 1:
        rows.append(f"phones: {', '.join(phones)}")
    if len(emails) > 1:
        rows.append(f"emails: {', '.join(emails)}")
    rows.append("payment table says credit card has a R$ 100 minimum installment; section 3.1 sets R$ 50 / 80 / 100 by number of installments (found by reading)")
    checks.append(("Policy manual inconsistencies",
                   "The agent gives (67) 3341-4444 (company data table, also on the cover) and the email without "
                   "an accent, and applies the more detailed rule in 3.1.", rows))
    return checks


def render(checks: list[tuple[str, str, list[str]]], today: date) -> str:
    out = ["# Data report", "",
           f"Generated by `scripts/audit_data.py` on {today.isoformat()}. Raw files in `data/raw/` are never edited.", ""]
    for title, decision, rows in checks:
        out += [f"## {title} ({len(rows)})", "", f"**Decision:** {decision}", ""]
        out += [f"- {r}" for r in rows] or ["- none found"]
        out.append("")
    return "\n".join(out)


def main() -> None:
    today = date.today()
    checks = audit(today)
    OUT.write_text(render(checks, today), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: " + ", ".join(f"{t.split(' (')[0]}={len(r)}" for t, _, r in checks))


if __name__ == "__main__":
    main()

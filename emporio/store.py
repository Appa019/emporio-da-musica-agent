"""Read-only access to the store data, exposed to the LLM as tools.

Business rules that an LLM tends to get wrong (price math, date windows,
who may see an order) live here, in code, and are covered by tests.
"""

import csv
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
TZ = ZoneInfo("America/Campo_Grande")
PIX_DISCOUNT = 0.05
STALE_DAYS = 30
MAX_RESULTS = 10


def load_table(name: str, data_dir: Path = RAW) -> list[dict]:
    with open(data_dir / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fold(text: str) -> str:
    # Lowercase, no accents or punctuation: "Violões" ~ "violoes", "GD-20" ~ "gd20".
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", "", ascii_text)


def _phone_key(text: str) -> str:
    # DDD + number: the last 11 digits, so "+55 67 9...", "067 9..." and "(67) 9..." all match.
    return re.sub(r"\D", "", text or "")[-11:]


def min_installment(n: int) -> float:
    # Policy 3.1: R$ 80 from 4x, R$ 100 from 7x; up to 3x says "no minimum (except below R$ 50)", read as a R$ 50 floor.
    return 50.0 if n <= 3 else 80.0 if n <= 6 else 100.0


def max_installments(price: float) -> dict:
    """Largest interest-free plan allowed by policy 3.1."""
    for n in range(12, 1, -1):
        if price / n >= min_installment(n):
            return {"installments": n, "installment_value": round(price / n, 2)}
    return {"installments": 1, "installment_value": price}


class Store:
    def __init__(self, data_dir: Path = RAW, today: date | None = None):
        self.today = today or datetime.now(TZ).date()
        self.products = {p["product_id"]: p for p in load_table("products", data_dir)}
        self.customers = {c["customer_id"]: c for c in load_table("customers", data_dir)}
        self.orders = {o["order_id"]: o for o in load_table("orders", data_dir)}
        self.categories = {c["category_id"]: c["name"] for c in load_table("categories", data_dir)}
        self.items: dict[str, list[dict]] = {}
        for i in load_table("order_items", data_dir):
            self.items.setdefault(i["order_id"], []).append(i)
        self.promotions = {p["product_id"]: p for p in load_table("promotions", data_dir) if p["is_active"] == "1"}

    def identify_customer(self, phone: str = "", email: str = "") -> dict:
        """Phone is unique in the data; one email is shared by two customers, so email alone may be ambiguous."""
        if _phone_key(phone):
            matches = [c for c in self.customers.values() if _phone_key(c["phone"]) == _phone_key(phone)]
        elif email.strip():
            matches = [c for c in self.customers.values() if c["email"].strip().lower() == email.strip().lower()]
        else:
            return {"found": False, "reason": "missing_phone_or_email"}
        if len(matches) > 1:
            return {"found": False, "reason": "ambiguous_email_ask_for_phone"}
        if not matches:
            return {"found": False, "reason": "not_found"}
        c = matches[0]
        # Only what the conversation needs; contact data never goes back to the model.
        return {"found": True, "customer_id": c["customer_id"], "first_name": c["name"].split()[0], "city": c["city"]}

    def search_products(self, query: str = "", category: str = "", max_price: float | None = None) -> dict:
        terms = _fold(query).split()
        results = []
        for p in self.products.values():
            category_name = self.categories.get(p["category_id"], "")
            if category and _fold(category) not in _fold(category_name):
                continue
            haystack = _fold(f"{p['name']} {p['description']} {category_name}")
            if not all(t in haystack for t in terms):
                continue
            offer = self._price(p)
            if max_price is not None and offer["final_price"] > max_price:
                continue
            results.append({"product_id": int(p["product_id"]), "name": p["name"], "category": category_name,
                            **offer, "availability": self._availability(p), "description": p["description"],
                            "specs": p["specs"]})
        # In-stock items first, so unavailable ones never push sellable items out of the top results.
        results.sort(key=lambda r: (r["availability"] != "in_stock", r["final_price"]))
        return {"count": len(results), "results": results[:MAX_RESULTS], "truncated": len(results) > MAX_RESULTS}

    def get_orders(self, customer_id: str | None, order_id: int | None = None) -> dict:
        # Guardrail: customer_id comes from the session after identification, never from the model.
        if not customer_id:
            return {"error": "customer_not_identified"}
        orders = [o for o in self.orders.values() if o["customer_id"] == customer_id]
        if order_id is not None:
            orders = [o for o in orders if o["order_id"] == str(order_id)]
            if not orders:
                # Same answer whether the order does not exist or belongs to someone else.
                return {"error": "order_not_found_for_this_customer"}
        return {"orders": [self._order_view(o) for o in orders]}

    def _price(self, p: dict) -> dict:
        list_price = float(p["price_brl"])
        promo = self.promotions.get(p["product_id"])
        if promo:
            pct = int(promo["discount_percent"])
            # Policy 6.2: promotions are not cumulative, so no PIX discount on a promotional price.
            final = round(list_price * (1 - pct / 100), 2)
            return {"list_price": list_price, "promotion": f"{promo['description']} -{pct}%",
                    "final_price": final, "pix_price": None, "card": max_installments(final)}
        return {"list_price": list_price, "promotion": None, "final_price": list_price,
                "pix_price": round(list_price * (1 - PIX_DISCOUNT), 2), "card": max_installments(list_price)}

    @staticmethod
    def _availability(p: dict) -> str:
        if p["status"] == "discontinued":
            return "discontinued"
        if p["status"] == "coming_soon":
            return "coming_soon"
        return "in_stock" if int(p["stock_quantity"]) > 0 else "out_of_stock"

    def _order_view(self, o: dict) -> dict:
        view = {k: o[k] or None for k in ("order_id", "order_date", "status", "payment_method", "tracking_code",
                                          "estimated_delivery", "notes")}
        view["total_brl"] = float(o["total_brl"])
        view["items"] = [f"{i['quantity']}x {self.products[i['product_id']]['name']}" for i in self.items.get(o["order_id"], [])]
        view["days_since_order"] = (self.today - date.fromisoformat(o["order_date"])).days
        view["looks_delayed"] = self._looks_delayed(o)
        if o["status"] == "delivered" and o["estimated_delivery"]:
            # Assumption: there is no actual delivery date, so the estimate stands in for it.
            days = (self.today - date.fromisoformat(o["estimated_delivery"])).days
            view["days_since_delivery"] = days
            view["within_7_day_withdrawal"] = days <= 7   # policy 4.1: counts from receipt
            view["within_30_day_defect_exchange"] = view["days_since_order"] <= 30   # policy 4.2: counts from purchase
            view["within_90_day_legal_warranty"] = days <= 90   # policy 8.1: counts from receipt
        return view

    def _looks_delayed(self, o: dict) -> bool:
        if o["status"] == "shipped" and o["estimated_delivery"]:
            return date.fromisoformat(o["estimated_delivery"]) < self.today
        if o["status"] in ("pending", "confirmed"):
            return (self.today - date.fromisoformat(o["order_date"])).days > STALE_DAYS
        return False

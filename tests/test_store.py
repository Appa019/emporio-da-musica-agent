from datetime import date

import pytest

from emporio.store import Store, max_installments


@pytest.fixture(scope="module")
def store():
    return Store(today=date(2026, 3, 25))


def test_identify_by_phone_in_any_format(store):
    assert store.identify_customer(phone="67998123456")["customer_id"] == "1"
    assert store.identify_customer(phone="(67) 99812-3456")["first_name"] == "Lucas"


def test_identify_never_returns_contact_data(store):
    found = store.identify_customer(phone="67998123456")
    assert "phone" not in found and "email" not in found


def test_shared_email_is_ambiguous(store):
    assert store.identify_customer(email="Bruno.Martins@jmail.com") == {"found": False, "reason": "ambiguous_email_ask_for_phone"}


def test_unknown_and_missing_identity(store):
    assert store.identify_customer(phone="11 90000-0000")["reason"] == "not_found"
    assert store.identify_customer()["reason"] == "missing_phone_or_email"


def test_orders_require_identified_customer(store):
    assert store.get_orders(None, 1) == {"error": "customer_not_identified"}


def test_cannot_read_someone_elses_order(store):
    # Order 1 belongs to customer 3; same answer as a missing order, so existence is not leaked.
    assert store.get_orders("1", 1) == {"error": "order_not_found_for_this_customer"}
    assert store.get_orders("1", 999) == {"error": "order_not_found_for_this_customer"}


def test_lists_only_own_orders(store):
    ids = [o["order_id"] for o in store.get_orders("3")["orders"]]
    assert ids == ["1", "19"]


def test_delivered_order_return_windows(store):
    # Order 7: estimated delivery 2026-02-17, 36 days before the fixed date.
    order = store.get_orders("14", 7)["orders"][0]
    assert order["days_since_delivery"] == 36
    assert (order["within_7_day_withdrawal"], order["within_30_day_defect_exchange"], order["within_90_day_legal_warranty"]) == (False, False, True)


def test_delayed_orders(store):
    assert store.get_orders("2", 8)["orders"][0]["looks_delayed"] is True     # shipped, estimate passed
    assert store.get_orders("10", 16)["orders"][0]["looks_delayed"] is False  # pending for 3 days


def test_promotion_price_does_not_stack_with_pix(store):
    taylor = next(r for r in store.search_products("Taylor 110e")["results"])
    assert taylor["final_price"] == 5519.08 and taylor["pix_price"] is None


def test_pix_price_without_promotion(store):
    takamine = store.search_products("takamine gd20")["results"][0]
    assert (takamine["final_price"], takamine["pix_price"]) == (2199.0, 2089.05)


def test_max_price_uses_promotional_price(store):
    # Crafter HT-100 lists at R$ 2399 but has an active 18% promotion: R$ 1967.18.
    assert store.search_products("crafter", max_price=2000)["results"][0]["name"] == "Crafter HT-100 Folk Aço Natural"
    assert all(r["final_price"] <= 1000 for r in store.search_products(category="Violões", max_price=1000)["results"])


def test_unavailable_products_are_labelled(store):
    by_id = {r["product_id"]: r["availability"] for r in store.search_products(category="baixos")["results"]}
    assert by_id[136] == "discontinued"
    assert store.search_products("Kala KA-ABP-CTG")["results"][0]["availability"] == "coming_soon"
    assert store.search_products("Giannini GF-3D")["results"][0]["availability"] == "out_of_stock"


def test_search_is_case_insensitive_and_capped(store):
    result = store.search_products(category="violões")
    assert result["count"] == 35 and len(result["results"]) == 10 and result["truncated"]


@pytest.mark.parametrize("price, plan", [
    (2199.0, (12, 183.25)),   # R$ 100 minimum from 7x
    (429.9, (5, 85.98)),      # 6x would be R$ 71.65, below R$ 80
    (159.9, (3, 53.3)),       # up to 3x only needs R$ 50
    (60.0, (1, 60.0)),        # 2x of R$ 30 is below the R$ 50 floor
])
def test_max_installments_follow_policy(price, plan):
    assert tuple(max_installments(price).values()) == plan


def test_defect_exchange_counts_from_purchase_not_delivery():
    # Order 7: bought 2026-02-03, delivered 2026-02-17. On 2026-03-10 it is 21 days after delivery
    # but 35 after purchase, so the 30-day defect exchange (policy 4.2) has already closed.
    order = Store(today=date(2026, 3, 10)).get_orders("14", 7)["orders"][0]
    assert order["days_since_delivery"] == 21 and order["within_30_day_defect_exchange"] is False

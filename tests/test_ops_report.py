import pytest

from emporio.customer_records import SessionLog
from scripts.ops_report import call_cost, read_events, report


def test_report_summarizes_handoffs_guardrails_demand_and_cost(tmp_path):
    anonymous = SessionLog(root=tmp_path)
    anonymous.write("user_message", text="Vocês vendem cordas e um pedal de distorção?")
    anonymous.write("tool_call", tool="search_products", args={"query": "saxofone", "category": None}, result={"count": 0})
    anonymous.write("llm_call", purpose="reply", model="m", input_tokens=1_000_000, cached_tokens=0,
                    cache_write_tokens=0, output_tokens=0)

    customer = SessionLog(root=tmp_path)
    customer.link_customer("14")
    customer.write("customer_identified", customer_id="14")
    customer.write("guardrail", rule="second_identity_in_session")
    customer.write("handoff", reason="delayed_order", summary="Pedido 8 com entrega atrasada")

    text = report(read_events(tmp_path))
    assert "- Conversations: 2 (1 with an identified customer)" in text
    assert "customer 14 · delayed_order: Pedido 8 com entrega atrasada" in text
    assert "- second_identity_in_session: 1" in text
    assert "- saxofone: 1" in text and "- cordas: 1" in text and "- pedal: 1" in text
    assert "cost US$ 0.1000 (US$ 0.0500 per conversation)" in text


def test_call_cost_uses_cache_prices():
    call = {"input_tokens": 1_000_000, "cached_tokens": 900_000, "cache_write_tokens": 0, "output_tokens": 1_000_000}
    assert call_cost(call) == pytest.approx(0.01 + 0.009 + 0.5)


def test_empty_logs(tmp_path):
    assert "- Conversations: 0 (0 with an identified customer)" in report(read_events(tmp_path))

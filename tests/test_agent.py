"""Agent loop and guardrails with a scripted fake of the OpenAI client (no network)."""

import json
import os
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from openai import OpenAIError
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from emporio.__main__ import load_env
from emporio.agent import FALLBACK, MAX_MESSAGE_CHARS, TZ, Agent, price_values, store_status, unverified_prices
from emporio.customer_records import SessionLog, load_memory
from emporio.store import Store

PHONE_CUSTOMER_3 = "(67) 98432-1098"
PHONE_CUSTOMER_1 = "(67) 99812-3456"


def call(name, **args):
    return ResponseFunctionToolCall(type="function_call", name=name, arguments=json.dumps(args),
                                    call_id=f"call_{name}", id=f"fc_{name}", status="completed")


def message(text):
    return ResponseOutputMessage(id="msg_1", type="message", role="assistant", status="completed",
                                 content=[ResponseOutputText(type="output_text", text=text, annotations=[])])


class FakeClient:
    """Returns one scripted list of real SDK output items per responses.create call."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []
        self.responses = self

    def create(self, **request):
        self.requests.append(request)
        output = self.turns.pop(0)
        text = "".join(part.text for item in output if item.type == "message" for part in item.content)
        return SimpleNamespace(output=output, output_text=text, status=self.status, incomplete_details=None)

    status = "completed"


@pytest.fixture
def make_agent(tmp_path):
    def make(*turns):
        return Agent(Store(today=date(2026, 3, 25)), client=FakeClient(*turns), log=SessionLog(root=tmp_path))
    return make


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_customer_3_phone_fixture_matches_data():
    assert Store().identify_customer(phone=PHONE_CUSTOMER_3)["first_name"] == "Pedro"


def test_order_lookup_before_identification_is_blocked(make_agent):
    agent = make_agent([call("get_orders", order_id=1)], [message("Me passa seu telefone?")])
    assert agent.reply("cadê meu pedido 1?") == "Me passa seu telefone?"
    outputs = [h for h in agent.history if isinstance(h, dict) and h.get("type") == "function_call_output"]
    assert json.loads(outputs[0]["output"]) == {"error": "customer_not_identified"}
    assert any(e["event"] == "guardrail" and e.get("rule") == "customer_not_identified" for e in events(agent.log.path))


def test_identification_links_log_and_blocks_second_identity(make_agent, tmp_path):
    agent = make_agent([call("identify_customer", phone=PHONE_CUSTOMER_3, email=None)],
                       [call("identify_customer", phone=PHONE_CUSTOMER_1, email=None)],
                       [message("Oi, Pedro!")])
    agent.reply(f"oi, meu telefone é {PHONE_CUSTOMER_3}")
    assert agent.customer_id == "3" and agent.log.path == tmp_path / "3" / "log.jsonl"
    log = events(agent.log.path)
    assert log[0]["event"] == "session_start"  # anonymous events were moved into the customer log
    assert any(e.get("rule") == "second_identity_in_session" for e in log)
    assert PHONE_CUSTOMER_3 not in agent.log.path.read_text()  # masked


def test_identification_attempts_are_capped(make_agent):
    wrong = [call("identify_customer", phone="67900000000", email=None)]
    agent = make_agent(wrong, wrong, wrong, wrong, [message("Vou te passar pra equipe.")])
    agent.reply("tenta esses números")
    results = [json.loads(h["output"]) for h in agent.history if isinstance(h, dict) and h.get("type") == "function_call_output"]
    assert [r["reason"] for r in results] == ["not_found"] * 3 + ["too_many_attempts_offer_handoff"]


def test_unverified_price_gets_one_retry(make_agent):
    agent = make_agent([call("search_products", query="takamine gd20", category=None, max_price=None)],
                       [message("Sai por R$ 1.999,00!")],
                       [message("Custa R$ 2.199,00, ou R$ 2.089,05 no PIX.")])
    assert agent.reply("quanto custa o Takamine GD20?") == "Custa R$ 2.199,00, ou R$ 2.089,05 no PIX."
    assert any(e.get("rule") == "unverified_price" for e in events(agent.log.path))


def test_unverified_price_twice_falls_back(make_agent):
    agent = make_agent([message("Faço por R$ 1.500.")], [message("Ok, R$ 1.600 então.")])
    assert agent.reply("faz um desconto?") == FALLBACK


def test_unverified_prices_accepts_only_known_and_policy_values():
    known = {2399.0, 1967.18}
    assert unverified_prices("De R$ 2.399 por R$ 1.967,18. Frete grátis acima de R$ 500,00.", known) == []
    # Derived amounts are not trusted, even when the arithmetic is right (2000 - 500 used to slip through).
    assert unverified_prices("Fica R$ 1.900, economia de R$ 431,82, ou R$ 1.500", known) == [1900.0, 431.82, 1500.0]


def test_price_values_reads_only_price_fields():
    order = {"orders": [{"order_id": "7", "total_brl": 159.9, "days_since_delivery": 36}]}
    product = {"results": [{"product_id": 95, "final_price": 2199.0, "pix_price": None,
                            "card": {"installments": 12, "installment_value": 183.25}}]}
    assert price_values(order) | price_values(product) == {159.9, 2199.0, 183.25}


def test_customer_budget_can_be_echoed(make_agent):
    agent = make_agent([call("search_products", query=None, category="Violões", max_price=1000)],
                       [message("Separei opções até R$ 1.000: o Yamaha C40 sai por R$ 599,90.")])
    assert agent.reply("Quais violões até 1000?").startswith("Separei")
    assert not any(e.get("rule") == "unverified_price" for e in events(agent.log.path))


def test_non_price_numbers_from_tools_are_not_prices(make_agent):
    # After an order lookup, "36" is days_since_delivery, not something the agent may quote as R$ 36.
    agent = make_agent([call("identify_customer", phone="(67) 99789-0123", email=None), call("get_orders", order_id=7)],
                       [message("Te faço por R$ 36.")], [message("Te faço por R$ 36.")])
    assert agent.reply("meu pedido 7, tel (67) 99789-0123") == FALLBACK
    assert any('"days_since_delivery": 36' in h.get("output", "") for h in agent.history if isinstance(h, dict))


def test_long_message_is_rejected_without_calling_the_model(make_agent):
    agent = make_agent()
    assert "muito longa" in agent.reply("a" * (MAX_MESSAGE_CHARS + 1))
    assert agent.client.requests == []


def test_memory_is_saved_and_returned_next_session(make_agent):
    agent = make_agent([call("identify_customer", phone=PHONE_CUSTOMER_3, email=None)], [message("Oi, Pedro!")],
                       [message(json.dumps({"facts": ["Procura violão folk até R$ 2.500"]}))])
    agent.reply(f"oi, {PHONE_CUSTOMER_3}")
    agent.close()
    assert load_memory("3", root=agent.log.root) == ["Procura violão folk até R$ 2.500"]

    again = make_agent([call("identify_customer", phone=PHONE_CUSTOMER_3, email=None)], [message("Oi de novo!")])
    again.reply(f"oi, {PHONE_CUSTOMER_3}")
    returned = json.loads([h for h in again.history if isinstance(h, dict) and h.get("type") == "function_call_output"][0]["output"])
    assert returned["memory"] == ["Procura violão folk até R$ 2.500"]


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 9, 10, 0, tzinfo=TZ), "open until 18:00"),                    # Monday
    (datetime(2026, 11, 14, 12, 30, tzinfo=TZ), "open until 13:00"),                  # Saturday
    (datetime(2026, 11, 13, 19, 0, tzinfo=TZ), "closed; reopens Saturday 14/11 at 9:00"),
    (datetime(2026, 11, 15, 10, 0, tzinfo=TZ), "closed; reopens Monday 16/11 at 9:00"),  # Sunday
    (datetime(2026, 11, 10, 7, 0, tzinfo=TZ), "closed; reopens Tuesday 10/11 at 9:00"),
])
def test_store_status(now, expected):
    assert store_status(now) == expected


def test_replayed_items_have_no_null_fields(make_agent):
    # The SDK models carry optional fields as None (e.g. `async_`); sending them back is rejected by the API.
    agent = make_agent([call("search_products", query="yamaha", category=None, max_price=None)], [message("Achei!")])
    agent.reply("tem yamaha?")
    replayed = [h for h in agent.history if isinstance(h, dict) and h.get("type") in ("function_call", "message")]
    assert replayed and all(None not in item.values() and "async_" not in item for item in replayed)


def test_incomplete_response_falls_back(make_agent):
    agent = make_agent([message("Os violões disponíveis s")])
    agent.client.status = "incomplete"
    assert agent.reply("quais violões?") == FALLBACK


def test_api_error_falls_back_and_is_reported(make_agent, caplog):
    agent = make_agent()
    def fail(**_):
        raise OpenAIError("invalid api key")
    agent.client.create = fail
    assert agent.reply("oi") == FALLBACK
    assert "invalid api key" in caplog.text


def test_env_values_lose_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    (tmp_path / ".env").write_text('OPENAI_MODEL="gpt-6-luna"\n# OPENAI_API_KEY=commented\n')
    load_env(tmp_path / ".env")
    assert os.environ["OPENAI_MODEL"] == "gpt-6-luna"

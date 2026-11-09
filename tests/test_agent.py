"""Agent loop and guardrails with a scripted fake of the OpenAI client (no network)."""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from emporio.agent import FALLBACK, MAX_MESSAGE_CHARS, TZ, Agent, store_status, unverified_prices
from emporio.customer_records import SessionLog, load_memory
from emporio.store import Store

PHONE_CUSTOMER_3 = "(67) 98432-1098"
PHONE_CUSTOMER_1 = "(67) 99812-3456"


def call(name, **args):
    data = {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": f"call_{name}"}
    return SimpleNamespace(**data, model_dump=lambda exclude=None: data)


def message(text):
    data = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    return SimpleNamespace(type="message", text=text, model_dump=lambda exclude=None: data)


class FakeClient:
    """Returns one scripted list of output items per responses.create call."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []
        self.responses = self

    def create(self, **request):
        self.requests.append(request)
        output = self.turns.pop(0)
        text = "".join(item.text for item in output if item.type == "message")
        return SimpleNamespace(output=output, output_text=text)


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


def test_unverified_prices_accepts_only_tool_and_policy_values():
    tool = json.dumps({"list_price": 2399.0, "final_price": 1967.18})
    assert unverified_prices("De R$ 2.399 por R$ 1.967,18. Frete grátis acima de R$ 500,00.", [tool]) == []
    # Derived amounts are not trusted, even when the arithmetic is right (2000 - 500 used to slip through).
    assert unverified_prices("Fica R$ 1.900, economia de R$ 431,82, ou R$ 1.500", [tool]) == [1900.0, 431.82, 1500.0]


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

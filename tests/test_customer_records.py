import json

from emporio.customer_records import SessionLog, load_memory, mask, save_memory


def test_mask_phone_and_email():
    text = "meu zap é (67) 99812-3456, ou 67998123456, email lucas.mendes@jmail.com"
    assert mask(text) == "meu zap é *****-3456, ou *****-3456, email l***@jmail.com"


def test_mask_phone_with_country_code():
    assert mask("+55 67 99812-3456 ou 5567998123456") == "*****-3456 ou *****-3456"


def test_mask_keeps_prices_orders_and_tracking():
    text = "pedido 15, R$ 2.089,05, 12x de R$ 183,25, rastreio BRAB1234567BR, 2026-02-17"
    assert mask(text) == text


def test_anonymous_events_move_to_customer_log(tmp_path):
    log = SessionLog(root=tmp_path)
    log.write("user_message", text="oi, tel 67998123456")
    log.link_customer("1")
    log.write("assistant_message", text="Oi, Lucas!")
    lines = [json.loads(l) for l in (tmp_path / "1" / "log.jsonl").read_text().splitlines()]
    assert [l["event"] for l in lines] == ["user_message", "assistant_message"]
    assert lines[0]["text"] == "oi, tel *****-3456"
    assert not list((tmp_path / "_anonymous").iterdir())


def test_memory_round_trip_is_masked_and_capped(tmp_path):
    save_memory("7", [f"fato {i}" for i in range(10)] + ["email a@b.com"], root=tmp_path)
    facts = load_memory("7", root=tmp_path)
    assert len(facts) == 8 and load_memory("8", root=tmp_path) == []
    save_memory("7", ["email ana@jmail.com"], root=tmp_path)
    assert load_memory("7", root=tmp_path) == ["email a***@jmail.com"]

from scripts.eval_live import render


def test_render_counts_pass_rate_and_lists_failures():
    report = render(2, {"test_price_question": [None, None], "test_memory_carries_over": [None, "AssertionError: assert []"]}, 90)
    assert "**Overall: 3/4 (75%)**" in report
    assert "| price question | 2/2 | 100% |" in report and "| memory carries over | 1/2 | 50% |" in report
    assert "- `test_memory_carries_over`: AssertionError: assert []" in report

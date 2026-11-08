"""S2: chat history is charged at its real token cost, over the window actually sent."""

from app.search.context_builder import (
    HISTORY_WINDOW,
    compute_context_budget,
    count_tokens_uncached,
)
from app.search.llm_client import LLMClient


def _msgs(n: int, text: str) -> list[dict[str, str]]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": text} for i in range(n)]


def test_short_history_does_not_collapse_small_class_to_floor():
    base = compute_context_budget("3b_local", None)
    history = _msgs(20, "ok thanks")  # old: 20 * 400 -> pinned at the 1,000 floor
    budget = compute_context_budget("3b_local", history)
    assert budget > 1000
    assert budget == base - HISTORY_WINDOW * count_tokens_uncached("ok thanks")


def test_messages_outside_the_window_are_not_charged():
    recent = _msgs(HISTORY_WINDOW, "short")
    huge = _msgs(40, "word " * 2000)  # would blow any budget if charged
    # 7b_local: 10 short msgs stay well above the floor, so the comparison is not
    # vacuously 1000 == 1000 (as it would be on 3b_local).
    assert compute_context_budget("7b_local", huge + recent) == compute_context_budget(
        "7b_local", recent
    )
    assert compute_context_budget("7b_local", recent) > 1000


def test_long_message_inside_the_window_is_charged_by_real_tokens():
    history = [{"role": "user", "content": "word " * 600}]
    cost = count_tokens_uncached("word " * 600)
    assert cost > 400  # more than the old flat per-message charge
    assert (
        compute_context_budget("7b_local", history)
        == compute_context_budget("7b_local", None) - cost
    )


def test_llm_client_sends_exactly_the_window_the_budget_charges():
    client = LLMClient.__new__(LLMClient)  # _build_messages reads no instance state
    sent = client._build_messages("q", _msgs(50, "x"))
    assert len(sent) == HISTORY_WINDOW + 1

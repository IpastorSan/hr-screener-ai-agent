import pytest

from evals.model_selection.judge import pairings, points, position_bias, vs_anchor, win_rates


def test_round_robin_lists_every_pair_in_both_orders() -> None:
    pairs = pairings(["a", "b", "c"])
    assert len(pairs) == 6
    assert set(pairs) == {("a", "b"), ("b", "a"), ("a", "c"), ("c", "a"), ("b", "c"), ("c", "b")}


def test_anchor_keeps_only_its_pairs() -> None:
    pairs = pairings(["a", "b", "c", "d"], anchor="a")
    assert sorted(pairs) == sorted(
        [("a", "b"), ("b", "a"), ("a", "c"), ("c", "a"), ("a", "d"), ("d", "a")]
    )


def test_anchor_must_be_judged() -> None:
    with pytest.raises(ValueError, match="anchor"):
        pairings(["a", "b"], anchor="z")


def test_points_follow_the_shown_position() -> None:
    assert points("A", first=True) == 1.0
    assert points("A", first=False) == 0.0
    assert points("tie", first=True) == 0.5
    assert points(None, first=True) is None


def test_swapped_order_cancels_position_bias() -> None:
    # A judge that always picks the first message: every model wins half its comparisons.
    comparisons = [
        {"a": "x", "b": "y", "winner": "A"},
        {"a": "y", "b": "x", "winner": "A"},
    ]
    rates = win_rates(comparisons)
    assert rates["x"]["win_rate"] == rates["y"]["win_rate"] == 0.5
    assert position_bias(comparisons) == 1.0


def test_failed_verdicts_are_left_out() -> None:
    rates = win_rates([{"a": "x", "b": "y", "winner": "B"}, {"a": "y", "b": "x", "winner": None}])
    assert rates["y"] == {"win_rate": 1.0, "comparisons": 1, "wins": 1, "ties": 0}


def test_vs_anchor_scores_the_challenger_in_either_position_and_resamples_cases() -> None:
    comparisons = [
        {"case_id": "c1", "a": "anchor", "b": "x", "winner": "B"},
        {"case_id": "c1", "a": "x", "b": "anchor", "winner": "A"},
        {"case_id": "c2", "a": "anchor", "b": "x", "winner": "A"},
        {"case_id": "c2", "a": "x", "b": "anchor", "winner": "tie"},
        {"case_id": "c2", "a": "y", "b": "z", "winner": "A"},
    ]

    result = vs_anchor(comparisons, "anchor", resamples=200)

    assert list(result["models"]) == ["x"]
    x = result["models"]["x"]
    assert (x["wins"], x["ties"], x["losses"], x["judgments"]) == (2, 1, 1, 4)
    assert x["score"] == 0.625
    assert (x["cases"], x["order_consistent_cases"]) == (2, 1)
    # Two cases: a resample holds c1 twice (1.0), c2 twice (0.25) or one of each (0.625).
    assert x["ci95"] == [0.25, 1.0]

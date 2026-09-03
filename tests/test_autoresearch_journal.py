"""The sweep runner's bookkeeping, which is the part that can silently lie.

Not marked ``eval``: none of this runs an experiment. It covers the journal and
the ranking, which are pure functions over JSON, so they belong in the default
suite.

Two properties matter more than the rest. **Resume must be exact** - a key that
does not round-trip means an overnight run repeats work it already paid for, or
worse, skips work it did not do. And **the leaderboard must rank on the floor**,
because CLAUDE.md 8.7d records a configuration whose mean was better while its
worst build was no better than doing nothing; ranking on the mean would have
shipped it.
"""

from __future__ import annotations

import json

import pytest

from scripts.autoresearch import _configs, _done, _key, _leaderboard, _parse_sweep


def test_parse_sweep_expands_values():
    assert _parse_sweep(["PMA_CHUNK_SIZE=512,1024"]) == {"PMA_CHUNK_SIZE": ["512", "1024"]}


def test_parse_sweep_rejects_non_pma_settings():
    """A knob outside app/config.py would change the run without appearing in
    provenance, which is how a measurement stops being reproducible."""
    with pytest.raises(SystemExit):
        _parse_sweep(["PATH=/tmp"])
    with pytest.raises(SystemExit):
        _parse_sweep(["PMA_CHUNK_SIZE"])


def test_configs_is_the_full_grid():
    grid = _configs({"PMA_CHUNK_SIZE": ["512", "2048"], "PMA_RRF_K": ["10", "60"]})
    assert len(grid) == 4
    assert {"PMA_CHUNK_SIZE": "512", "PMA_RRF_K": "60"} in grid


def test_configs_with_no_sweep_is_one_default_run():
    assert _configs({}) == [{}]


def test_key_ignores_dict_ordering():
    """Resume compares keys, so two spellings of one config must not look like
    two configs - that would silently double an overnight run."""
    a = _key({"PMA_CHUNK_SIZE": "512", "PMA_RRF_K": "10"}, 0)
    b = _key({"PMA_RRF_K": "10", "PMA_CHUNK_SIZE": "512"}, 0)
    assert a == b
    assert _key({"PMA_CHUNK_SIZE": "512"}, 0) != _key({"PMA_CHUNK_SIZE": "512"}, 1)


def test_done_round_trips_written_rows(tmp_path):
    journal = tmp_path / "journal.jsonl"
    config = {"PMA_CHUNK_SIZE": "1024"}
    rows = [{"config": config, "build": b, "result": {"failed": False}} for b in (0, 1)]
    journal.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    done = _done(journal)
    assert _key(config, 0) in done
    assert _key(config, 1) in done
    assert _key(config, 2) not in done


def test_done_survives_a_half_written_line(tmp_path):
    """A killed run leaves a truncated line. That must not block a resume."""
    journal = tmp_path / "journal.jsonl"
    good = json.dumps({"config": {"PMA_CHUNK_SIZE": "512"}, "build": 0})
    journal.write_text(good + '\n{"config": {"PMA_CHUNK', encoding="utf-8")
    assert _done(journal) == {_key({"PMA_CHUNK_SIZE": "512"}, 0)}


def test_done_on_a_missing_journal_is_empty(tmp_path):
    assert _done(tmp_path / "nope.jsonl") == set()


def _row(chunk_size: str, build: int, recall: float) -> str:
    return json.dumps(
        {
            "config": {"PMA_CHUNK_SIZE": chunk_size},
            "build": build,
            "result": {
                "failed": False,
                "summary": {
                    "reranker_on": {
                        "generation": {
                            "gemma4-local": {"model_class": "7b_local", "recall": recall}
                        }
                    }
                },
            },
        }
    )


def test_leaderboard_reports_the_floor_not_just_the_mean(tmp_path):
    """The 8.7d case, reproduced: 'unstable' has the better mean (0.70 vs 0.65)
    and the worse floor (0.40 vs 0.60). Both numbers must be visible, and the
    floor column is the one the docstring says to rank on."""
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        "\n".join(
            [
                _row("512", 0, 0.90),
                _row("512", 1, 0.80),
                _row("512", 2, 0.40),  # unstable: great mean, bad floor
                _row("2048", 0, 0.65),
                _row("2048", 1, 0.60),
                _row("2048", 2, 0.70),  # steady
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = _leaderboard(journal)
    body = [line for line in report.splitlines() if "PMA_CHUNK_SIZE" in line]
    assert len(body) == 2

    unstable = next(line for line in body if '"512"' in line)
    steady = next(line for line in body if '"2048"' in line)
    assert "0.400" in unstable and "0.700" in unstable
    assert "0.600" in steady and "0.650" in steady


def test_leaderboard_skips_failed_runs(tmp_path):
    """A crashed run must not be averaged in as if it were a measurement."""
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        _row("512", 0, 0.90)
        + "\n"
        + json.dumps({"config": {"PMA_CHUNK_SIZE": "512"}, "build": 1, "result": {"failed": True}})
        + "\n",
        encoding="utf-8",
    )
    report = _leaderboard(journal)
    assert "0.900" in report


def test_generation_arm_is_part_of_experiment_identity():
    """A delivery-only run and a generation run of the same config are different
    experiments. Sharing a key would let --resume skip the expensive one because
    the cheap one already exists, and would average the two in the leaderboard."""
    cfg = {"PMA_CHUNK_SIZE": "2048"}
    assert _key(cfg, 0, "") != _key(cfg, 0, "gemma2-2b")
    assert _key(cfg, 0, "gemma2-2b") == _key(cfg, 0, "gemma2-2b")


def test_done_keeps_delivery_and_generation_rows_apart(tmp_path):
    journal = tmp_path / "journal.jsonl"
    cfg = {"PMA_CHUNK_SIZE": "2048"}
    journal.write_text(
        json.dumps({"config": cfg, "build": 0, "gen": "", "result": {"failed": False}}) + "\n",
        encoding="utf-8",
    )
    done = _done(journal)
    assert _key(cfg, 0, "") in done
    assert _key(cfg, 0, "gemma2-2b") not in done, "a screening run must not satisfy a gen run"

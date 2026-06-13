"""Multi-session population selection in `select_rank` (mem-apg.6 flat + mem-apg.7
convoy/epic extension). Pure SQL/set membership over a synthetic in-memory store; the
ranking + replay machinery is covered elsewhere."""

import importlib.util
import json
import sqlite3
from pathlib import Path

# select_rank lives in scripts/ (not an importable package), so load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "select_rank", Path(__file__).resolve().parents[1] / "scripts" / "select_rank.py"
)
select_rank = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(select_rank)


def _store(tmp_path, records, agents):
    """A minimal store with the two tables the population SQL reads. ``records`` is a
    list of (work_id, issue_ref|None); ``agents`` is (work_id, agent_id, suspect)."""
    db = tmp_path / "store.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE work_records (work_id TEXT PRIMARY KEY, record TEXT NOT NULL)")
    conn.execute("CREATE TABLE record_agents (work_id TEXT, agent_id TEXT, suspect INTEGER)")
    for work_id, issue_ref in records:
        meta = {"gc.var.issue": issue_ref} if issue_ref else {}
        record = json.dumps({"work_id": work_id, "metadata": meta})
        conn.execute("INSERT INTO work_records VALUES (?, ?)", (work_id, record))
    conn.executemany("INSERT INTO record_agents VALUES (?, ?, ?)", agents)
    conn.commit()
    conn.close()
    return db


def test_flat_requires_two_distinct_nonsuspect_agents(tmp_path):
    db = _store(
        tmp_path,
        [("w1", None), ("w2", None), ("w3", None)],
        [
            ("w1", "a", 0),
            ("w1", "b", 0),  # w1: 2 distinct -> flat
            ("w2", "a", 0),
            ("w2", "a", 0),  # w2: 1 distinct -> not flat
            ("w3", "a", 0),
            ("w3", "b", 1),  # w3: b is suspect -> 1 counted -> not flat
        ],
    )
    assert select_rank.load_multi_session_ids(db) == {"w1"}


def test_convoy_epic_admits_single_agent_members_of_a_multisession_group(tmp_path):
    # epic E fanned out to c1, c2 -- each touched by ONE agent, but the group spans 2.
    db = _store(
        tmp_path,
        [("c1", "E"), ("c2", "E"), ("solo", "F")],
        [("c1", "a", 0), ("c2", "b", 0), ("solo", "a", 0)],
    )
    assert select_rank.load_multi_session_ids(db) == set()  # no per-bead multi-session
    assert select_rank.load_convoy_epic_ids(db) == {"c1", "c2"}  # group spans 2 agents


def test_convoy_epic_skips_single_agent_groups(tmp_path):
    # epic F worked entirely by agent 'a' -> not multi-session at the group level.
    db = _store(tmp_path, [("f1", "F"), ("f2", "F")], [("f1", "a", 0), ("f2", "a", 0)])
    assert select_rank.load_convoy_epic_ids(db) == set()


def test_convoy_epic_respects_alias_guard_across_the_group(tmp_path):
    # Group G: one real agent + one suspect alias -> only 1 counted -> not multi-session.
    db = _store(tmp_path, [("g1", "G"), ("g2", "G")], [("g1", "a", 0), ("g2", "b", 1)])
    assert select_rank.load_convoy_epic_ids(db) == set()


def test_population_convoy_epic_is_additive_over_flat(tmp_path):
    # w1 is flat (2 agents on itself, no issue); c1/c2 are convoy-epic only.
    db = _store(
        tmp_path,
        [("w1", None), ("c1", "E"), ("c2", "E")],
        [("w1", "a", 0), ("w1", "b", 0), ("c1", "x", 0), ("c2", "y", 0)],
    )
    assert select_rank.multi_session_ids(db, "flat") == {"w1"}
    assert select_rank.multi_session_ids(db, "convoy-epic") == {"w1", "c1", "c2"}


def test_unknown_population_raises(tmp_path):
    db = _store(tmp_path, [("w1", None)], [("w1", "a", 0)])
    try:
        select_rank.multi_session_ids(db, "bogus")
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown population")

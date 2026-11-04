from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pi_rsi import store


def record(**overrides) -> store.SessionRecord:
    values = {
        "id": "s-1",
        "harness": "pi",
        "project": "proj-a",
        "cwd": "/tmp/proj-a",
        "source": "/tmp/s-1.jsonl",
        "started_ts": "2026-01-01T00:00:00Z",
        "tool_calls": 3,
        "user_turns": 2,
    }
    values.update(overrides)
    return store.SessionRecord(**values)


def turns(*texts: str) -> list[store.Turn]:
    return [
        store.Turn(turn=index, ts=f"2026-01-0{index + 1}T00:00:00Z", kind="turn", text=text)
        for index, text in enumerate(texts)
    ]


@pytest.fixture
def con(tmp_path: Path):
    connection = store.connect(tmp_path / "state" / "recall.sqlite3")
    yield connection
    connection.close()


def test_migrate_sets_user_version(tmp_path: Path) -> None:
    connection = store.connect(tmp_path / "db.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    connection.close()
    # Second open is a no-op, not an error.
    connection = store.connect(tmp_path / "db.sqlite3")
    connection.close()


def test_missing_tokenizer_blames_the_interpreter(tmp_path: Path, monkeypatch) -> None:
    def no_tokenizer(con):
        raise sqlite3.OperationalError("no such tokenizer: trigram")

    monkeypatch.setattr(store, "migrate", no_tokenizer)
    with pytest.raises(store.StoreError, match="RSI_PYTHON"):
        store.connect(tmp_path / "db.sqlite3")


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = store.connect(path)
    connection.execute(f"PRAGMA user_version={store.SCHEMA_VERSION + 1}")
    connection.close()
    with pytest.raises(store.StoreError, match="newer"):
        store.connect(path)


def test_index_is_idempotent(con: sqlite3.Connection) -> None:
    store.index_session(con, record(), turns("first turn", "second turn"))
    store.index_session(con, record(), turns("first turn", "second turn"))
    assert store.stats(con)["turns"] == 2
    assert store.stats(con)["sessions"] == 1


def test_reindexing_replaces_turns(con: sqlite3.Connection) -> None:
    store.index_session(con, record(), turns("alpha", "beta"))
    store.index_session(con, record(), turns("gamma"))
    stats = store.stats(con)
    assert stats["turns"] == 1
    hits = store.search(con, "alpha")
    assert hits == []


def test_search_ranks_and_filters(con: sqlite3.Connection) -> None:
    store.index_session(con, record(id="s-1"), turns("the deploy failed with a stale image tag"))
    store.index_session(
        con,
        record(id="s-2", project="proj-b"),
        turns("the deploy failed because of a missing secret"),
    )
    hits = store.search(con, "deploy failed", project="proj-a")
    assert [hit.session for hit in hits] == ["s-1"]
    assert hits[0].project == "proj-a"

    all_hits = store.search(con, "deploy failed")
    assert {hit.session for hit in all_hits} == {"s-1", "s-2"}


def test_search_survives_quotes_and_punctuation(con: sqlite3.Connection) -> None:
    store.index_session(con, record(), turns('error: "connection reset" by peer'))
    hits = store.search(con, '"connection reset"')
    assert len(hits) == 1


def test_short_query_falls_back_to_like(con: sqlite3.Connection) -> None:
    store.index_session(con, record(), turns("the CI job is red"))
    assert len(store.search(con, "CI")) == 1
    assert store.search(con, "") == []


def test_recent_sort(con: sqlite3.Connection) -> None:
    store.index_session(con, record(), turns("older deploy note", "newer deploy note"))
    hits = store.search(con, "deploy note", sort="recent")
    assert hits[0].text == "newer deploy note"


def test_fts_query_drops_short_tokens() -> None:
    assert store.fts_query("a of the deploy") == '"the" OR "deploy"'
    assert store.fts_query("ab") is None
    assert store.fts_query("") is None


def test_cascade_delete(con: sqlite3.Connection) -> None:
    store.index_session(con, record(), turns("alpha"))
    assert store.drop_session(con, "s-1") is True
    assert store.stats(con)["turns"] == 0
    assert store.drop_session(con, "s-1") is False


def test_stats_groups_by_project(con: sqlite3.Connection) -> None:
    store.index_session(con, record(id="a", project="proj-a"), turns("one", "two"))
    store.index_session(con, record(id="b", project=None), turns("three"))
    stats = store.stats(con)
    assert stats["sessions"] == 2
    by_project = {row["project"]: row["turns"] for row in stats["projects"]}
    assert by_project["proj-a"] == 2
    assert by_project[None] == 1


def test_fingerprint_changes_after_indexing(con: sqlite3.Connection) -> None:
    assert store.session_fingerprint(con, "s-1") is None
    store.index_session(con, record(), turns("alpha"), indexed_at="2026-01-02T00:00:00Z")
    assert store.session_fingerprint(con, "s-1") == "2026-01-02T00:00:00Z|3|2"

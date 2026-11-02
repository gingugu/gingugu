"""X4: the repository's history as a second graph - churn, authors, co-change.

``git log`` read once per HEAD on the caller's machine and cached beside the
import graph. Renames are followed so history lands on today's path; author
identities never leave the parser (only per-file counts are kept); a commit
past ``MAX_CHANGESET`` files counts toward churn but not toward coupling, as in
code-maat, whose thresholds these are.
"""

from __future__ import annotations

import json
import os

import pytest

from gingugu.xray import history
from gingugu.xray.scan import XrayError
from tests.test_xray_edit_hook import _git, _repo

SHA = "a" * 40


def _log(*commits) -> bytes:
    """Bytes as ``git log -z --name-status --format=%x01%H%x1f%aE`` writes them."""
    out = b""
    for n, (author, changes) in enumerate(commits):
        out += b"\x01" + f"{n:040x}".encode() + b"\x1f" + author.encode() + b"\0"
        if changes:
            out += b"\n"
        for status, *paths in changes:
            out += status.encode() + b"\0" + b"".join(p.encode() + b"\0" for p in paths)
    return out


# --- parse ---------------------------------------------------------------


def test_parse_reads_each_commit_newest_first():
    got = history.parse(_log(("x@y", [("M", "a.py"), ("A", "b.py")]), ("z@y", [("D", "c.py")])))
    assert [(c.author, sorted(c.files)) for c in got] == [
        ("x@y", ["a.py", "b.py"]),
        ("z@y", ["c.py"]),
    ]


def test_a_rename_carries_older_history_onto_todays_name():
    # newest first: b -> c, then a -> b, then an edit of a
    out = _log(
        ("x@y", [("R100", "b.py", "c.py")]),
        ("x@y", [("R095", "a.py", "b.py")]),
        ("x@y", [("M", "a.py")]),
    )
    assert [sorted(c.files) for c in history.parse(out)] == [["c.py"], ["c.py"], ["c.py"]]


def test_a_new_file_reusing_a_renamed_name_keeps_its_own_history():
    out = _log(
        ("x@y", [("A", "old.py")]),  # newest: a fresh old.py
        ("x@y", [("R100", "old.py", "new.py")]),
        ("x@y", [("M", "old.py")]),  # the original, now new.py
    )
    assert [sorted(c.files) for c in history.parse(out)] == [["old.py"], ["new.py"], ["new.py"]]


def test_a_copy_is_an_added_file_and_aliases_nothing():
    out = _log(("x@y", [("C075", "a.py", "b.py")]), ("x@y", [("M", "a.py")]))
    assert [sorted(c.files) for c in history.parse(out)] == [["b.py"], ["a.py"]]


def test_a_commit_with_no_files_is_kept_and_empty():
    got = history.parse(_log(("x@y", []), ("x@y", [("M", "a.py")])))
    assert [sorted(c.files) for c in got] == [[], ["a.py"]]


def test_a_path_holding_the_header_byte_is_a_path_not_a_commit():
    evil = "\x01" + "f" * 40 + "\x1fevil@x"
    got = history.parse(_log(("x@y", [("M", evil), ("M", "a.py")])))
    assert len(got) == 1
    assert got[0].files == frozenset({evil, "a.py"})


def test_a_path_starting_with_a_newline_is_kept_whole():
    got = history.parse(_log(("x@y", [("M", "a.py"), ("M", "\nb.py")])))
    assert got[0].files == frozenset({"a.py", "\nb.py"})


def test_truncated_output_never_raises():
    out = _log(("x@y", [("M", "a.py"), ("R100", "b.py", "c.py")]))
    for cut in range(len(out)):
        history.parse(out[:cut])


def test_non_utf8_names_decode_as_the_scan_decodes_them():
    raw = b"\x01" + SHA.encode() + b"\x1fx@y\0\nM\0caf\xe9.py\0"
    (c,) = history.parse(raw)
    assert c.files == frozenset({b"caf\xe9.py".decode("utf-8", errors="replace")})


# --- summarize -----------------------------------------------------------


def _commits(*rows):
    return [history.Commit(a, frozenset(f)) for a, f in rows]


def test_churn_and_author_counts_per_file():
    h = history.summarize(
        _commits(("x", ["a", "b"]), ("y", ["a"]), ("X", ["a"]), ("x", ["b"])), head=SHA
    )
    assert h.commits == 4
    assert h.churn == {"a": 3, "b": 2}
    assert h.authors == {"a": 2, "b": 1}  # authors compared case-insensitively
    assert h.head == SHA


def test_pairs_keep_only_those_shared_min_shared_times():
    rows = [("x", ["a", "b"])] * history.MIN_SHARED + [("x", ["a", "c"])] * (history.MIN_SHARED - 1)
    h = history.summarize(_commits(*rows))
    assert h.pairs == {("a", "b"): history.MIN_SHARED}


def test_a_bulk_commit_counts_for_churn_but_not_coupling():
    big = [f"f{i}" for i in range(history.MAX_CHANGESET + 1)]
    h = history.summarize(_commits(*[("x", big)] * 6))
    assert h.bulk == 6
    assert h.churn["f0"] == 6
    assert h.pairs == {}


def test_a_commit_at_the_changeset_limit_still_couples():
    edge = [f"f{i}" for i in range(history.MAX_CHANGESET)]
    h = history.summarize(_commits(*[("x", edge)] * history.MIN_SHARED))
    assert h.bulk == 0
    assert h.pairs[("f0", "f1")] == history.MIN_SHARED


def test_the_summary_holds_no_author_identity():
    h = history.summarize(_commits(("secret@corp.example", ["a"])))
    assert "secret" not in json.dumps(history.to_json(h))


def test_json_round_trip():
    h = history.summarize(_commits(*[("x", ["a", "b"])] * 5), head=SHA)
    assert history.from_json(json.loads(json.dumps(history.to_json(h)))) == h


@pytest.mark.parametrize(
    "bad",
    [None, [], {"head": 1}, {"pairs": "x"}, {"churn": {"a": "x"}}, {"pairs": [["a"]]}],
)
def test_a_corrupt_cache_reads_as_missing(bad):
    assert history.from_json(bad) is None


# --- read: a real checkout -----------------------------------------------


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


def _commit(repo, files: dict[str, str], author="dev@x") -> None:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", f"user.email={author}", "commit", "-q", "-m", "c")


def test_read_a_real_repo_and_follow_a_rename(tmp_path, state):
    repo = _repo(tmp_path, {"a.py": "1\n", "b.py": "1\n"})
    for i in range(5):
        _commit(repo, {"a.py": f"{i}\n", "b.py": f"{i}\n"}, author=f"p{i % 2}@x")
    _git(repo, "mv", "a.py", "a2.py")
    _git(repo, "commit", "-q", "-m", "mv")
    h = history.read(repo, state)
    assert h.churn["a2.py"] == 7  # init, five edits, the rename
    assert "a.py" not in h.churn
    assert h.pairs[("a2.py", "b.py")] == 6
    assert h.authors["b.py"] == 3  # t@t from the init, p0, p1


def test_read_is_cached_by_head(tmp_path, state, monkeypatch):
    repo = _repo(tmp_path, {"a.py": "1\n"})
    first = history.read(repo, state)
    calls = []
    real = history._log
    monkeypatch.setattr(history, "_log", lambda r: calls.append(r) or real(r))
    assert history.read(repo, state) == first
    assert calls == []
    _commit(repo, {"a.py": "2\n"})
    assert history.read(repo, state).churn["a.py"] == 2
    assert len(calls) == 1


def test_the_cache_file_is_owner_only_and_holds_no_email(tmp_path, state):
    repo = _repo(tmp_path, {"a.py": "1\n"})
    _commit(repo, {"a.py": "2\n"}, author="private@corp.example")
    history.read(repo, state)
    (cache,) = state.glob("xray-history-*.json")
    if os.name != "nt":  # Windows has no POSIX mode bits
        assert cache.stat().st_mode & 0o077 == 0
    assert "private" not in cache.read_text()


def test_an_unborn_branch_has_no_history(tmp_path, state):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    h = history.read(repo, state)
    assert h.commits == 0 and h.head is None


def test_merges_are_left_out(tmp_path, state):
    repo = _repo(tmp_path, {"a.py": "1\n"})
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, {"b.py": "1\n"})
    _git(repo, "checkout", "-q", "-")
    _commit(repo, {"c.py": "1\n"})
    _git(repo, "merge", "-q", "--no-ff", "-m", "m", "side")
    assert history.read(repo, state).commits == 3


def test_a_repo_config_cannot_reshape_the_log(tmp_path, state):
    repo = _repo(tmp_path, {"a.py": "1\n"})
    _git(repo, "config", "log.showSignature", "true")
    _git(repo, "config", "diff.renames", "false")
    _git(repo, "config", "format.pretty", "oneline")
    _git(repo, "mv", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "mv")
    h = history.read(repo, state)
    assert h.churn == {"b.py": 2}


def test_the_window_bounds_the_commits_read(tmp_path, state, monkeypatch):
    monkeypatch.setattr(history, "WINDOW", 2)
    repo = _repo(tmp_path, {"a.py": "0\n"})
    for i in range(4):
        _commit(repo, {"a.py": f"{i + 1}\n"})
    assert history.read(repo, state).commits == 2


def test_a_git_timeout_is_an_xray_error(tmp_path, state, monkeypatch):
    repo = _repo(tmp_path, {"a.py": "1\n"})

    def slow(*a, **k):
        raise XrayError("git log took over 10s")

    monkeypatch.setattr(history.scan, "git", slow)
    with pytest.raises(XrayError):
        history.read(repo, state)


def test_a_failed_log_is_not_rerun_by_the_hook_until_head_moves(tmp_path, state, monkeypatch):
    repo = _repo(tmp_path, {"a.py": "1\n"})
    calls = []

    def slow(r):
        calls.append(r)
        raise XrayError("git log took over 10s")

    real = history._log
    monkeypatch.setattr(history, "_log", slow)
    with pytest.raises(XrayError):
        history.read(repo, state, retry=False)
    with pytest.raises(XrayError):
        history.read(repo, state, retry=False)
    assert len(calls) == 1  # the hook pays one timeout per HEAD, not one per file
    with pytest.raises(XrayError):
        history.read(repo, state)  # the tool always tries again
    assert len(calls) == 2
    monkeypatch.setattr(history, "_log", real)
    _commit(repo, {"a.py": "2\n"})
    assert history.read(repo, state, retry=False).churn["a.py"] == 2
    assert not list(state.glob("xray-history-failed-*"))

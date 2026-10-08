"""e2e_fake tier: the snapshot-plus-feed contract between real processes (card 2.2.8).

Single-store design D:386-389. A consumer reads a snapshot (`am runs
--all-projects`), then follows `am watch --all --follow --since-seq
<as_of_seq>` and gets every later event exactly once: no gap, no repeat, even
while a separate writer process commits. A cursor above head is not an error:
the hello says `cursor_reset: true` and the stream starts at head. A replaced
database has a new `store_id`, the signal a consumer needs when the new head
is at or above its cursor. `--from-now` starts at head; `--since-seq` resumes.
The writer is a plain Python child (`WRITER`), as `storehelpers.DB_HOLDER` is.
Order comes from pipe lines and process exits, never from sleeping, and the
expected gseqs come from the store's own reads, never from arithmetic.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import textwrap
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import cli, models, paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import writer as store_writer

AM_WAIT = 240.0
"""Must equal the e2e conftest's `AM_WAIT`: the bound on every wait for a line
or an exit. It only bounds a broken child; a healthy one never waits this long."""

WRITER = textwrap.dedent(
    """
    import sys
    from datetime import datetime, timezone
    from pathlib import Path
    from agent_manager import models
    from agent_manager.store import events, writer
    repo = Path(sys.argv[1])
    for line in sys.stdin:
        words = line.split()
        if words == ["quit"]:
            break
        if not words or words[0] != "write":
            print(f"unknown {line!r}", flush=True)
            sys.exit(2)
        gseqs = []
        for run_id in words[1:]:
            opened = writer.Store.open(repo, run_id)
            try:
                opened.record_run(
                    models.Run(
                        id=run_id,
                        workflow="task",
                        repo_dir=repo,
                        base_branch="main",
                        branch_prefix="m1",
                        status="started",
                        started_at=datetime.now(timezone.utc),
                    )
                )
                gseqs.append(events.head(opened.connection))
            finally:
                opened.close()
        print("wrote " + " ".join(str(gseq) for gseq in gseqs), flush=True)
    """
)
"""The writer child: for each `write <run_id>...` line on stdin, one
`Store.record_run` per id (its `runs` row and `run_upsert` event in one
transaction), then `wrote <gseq>...`, the global `seq` of each event, read as
the head right after its commit (the child is the only writer then). `quit`
exits 0. Every report is flushed, so the parent never waits on a buffer."""


def _record(repo: Path, *run_ids: str) -> list[int]:
    """Record one run per id in the test process; each event's gseq, in order.

    Only for writes nothing else races: the gseq is the head read right after
    the commit, so it is exact only while this is the one writer."""
    gseqs = []
    for run_id in run_ids:
        opened = store_writer.Store.open(repo, run_id)
        try:
            opened.record_run(
                models.Run(
                    id=run_id,
                    workflow="task",
                    repo_dir=repo,
                    base_branch="main",
                    branch_prefix="m1",
                    status="started",
                    started_at=datetime.now(timezone.utc),
                )
            )
            gseqs.append(store_events.head(opened.connection))
        finally:
            opened.close()
    return gseqs


def _head() -> int:
    """`events`' head, read through a WAL reader in the test process."""
    reader = store_db.open_reader(paths.db_path())
    try:
        return store_events.head(reader)
    finally:
        reader.close()


def _replace_database() -> None:
    """Remove `am.db` and its `-wal` and `-shm` siblings, so the next open
    creates a new database with a new `store_id`. Only once no child holds it."""
    db = paths.db_path()
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)


def _data(code: int, envelope: dict) -> dict:
    """The `data` of an ok envelope from a child that exited 0."""
    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _snapshot(am) -> dict:
    """`am runs --all-projects`: `{runs, as_of_seq, store_id}`."""
    return _data(*am("runs", "--all-projects"))


def _events(am, *args: str) -> list[dict]:
    """The one-shot `am watch --all *args`: its event lines."""
    return _data(*am("watch", "--all", *args))["events"]


def _gseqs(events: list[dict]) -> list[int]:
    return [event["gseq"] for event in events]


class _Lines:
    """A child's stdout read line by line on a daemon thread, every wait
    bounded: a child that exits or hangs fails the test, naming the child and
    its stderr, instead of blocking pytest."""

    def __init__(self, child: subprocess.Popen, processes: Any) -> None:
        self.child = child
        self._processes = processes
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        for line in self.child.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def _fail(self, why: str) -> None:
        argv = " ".join(str(part) for part in self.child.args)
        pytest.fail(f"`{argv}` {why}\nstderr: {self._processes.stderr_of(self.child)}")

    def next(self, timeout: float = AM_WAIT) -> str:
        """The next line, without its newline."""
        try:
            line = self._queue.get(timeout=timeout)
        except queue.Empty:
            self._fail(f"printed no line within {timeout}s")
        if line is None:
            self._queue.put(None)
            self._fail(f"exited {self.child.wait()} before its next line")
        return line.rstrip("\n")

    def next_json(self, timeout: float = AM_WAIT) -> dict:
        return json.loads(self.next(timeout))

    def until_gseq(self, last: int) -> list[dict]:
        """Event lines up to and including `gseq == last`; fails on one past it."""
        lines = []
        while not lines or lines[-1]["gseq"] != last:
            event = self.next_json()
            assert event["gseq"] <= last, (event, last)
            lines.append(event)
        return lines

    def close(self) -> None:
        """Kill the child if it still runs, reap it, and join the reader."""
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait()
        self._thread.join(timeout=AM_WAIT)


class _Writer:
    """The `WRITER` child, one `write` line and one `wrote` report at a time."""

    def __init__(self, lines: _Lines) -> None:
        self._lines = lines

    def send(self, *run_ids: str) -> None:
        self._lines.child.stdin.write("write " + " ".join(run_ids) + "\n")
        self._lines.child.stdin.flush()

    def report(self) -> list[int]:
        word, *gseqs = self._lines.next().split()
        assert word == "wrote", (word, gseqs)
        return [int(gseq) for gseq in gseqs]

    def quit(self) -> None:
        self._lines.child.stdin.write("quit\n")
        self._lines.child.stdin.close()
        assert self._lines.child.wait(timeout=AM_WAIT) == 0
        self._lines.close()


class _Children:
    """The writer and followers one test starts; every one closed at teardown."""

    def __init__(self, processes: Any) -> None:
        self._processes = processes
        self._opened: list[_Lines] = []

    def writer(self, repo: Path) -> _Writer:
        self._processes.log_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = self._processes.log_dir / f"writer-{len(self._opened)}.stderr"
        with stderr_path.open("w", encoding="utf-8") as stderr:
            child = subprocess.Popen(
                [sys.executable, "-c", WRITER, str(repo)],
                env=self._processes.child_env(None),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
        self._processes.stderr_paths[child.pid] = stderr_path
        self._processes.track(child)
        lines = _Lines(child, self._processes)
        self._opened.append(lines)
        return _Writer(lines)

    def follow(self, *args: str) -> tuple[_Lines, dict]:
        """`am watch --all --follow *args` and its hello line."""
        lines = _Lines(self._processes.spawn("watch", "--all", "--follow", *args), self._processes)
        self._opened.append(lines)
        hello = lines.next_json()
        assert hello["event"] == "watch", hello
        assert hello["schema"] == 2, hello
        assert set(hello) == {
            "event", "schema", "am", "head", "cursor_reset", "store_id", "runs_dir"
        }, hello
        return lines, hello

    def close(self) -> None:
        for lines in self._opened:
            lines.close()


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A plain directory to record runs against, with `XDG_DATA_HOME` in tmp,
    so this test's `am.db` is its own; every child inherits it."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture
def children(am_processes):
    """`_Children` for this test, closed before `am_processes` reaps the rest."""
    started = _Children(am_processes)
    yield started
    started.close()


BATCHES = 3
"""How many batches the snapshot test's writer commits."""

BATCH_SIZE = 5
"""How many runs each of those batches records."""


@pytest.mark.e2e_fake
def test_snapshot_then_follow_from_as_of_seq_has_no_gap_and_no_repeat(repo, am, children):
    """D:386-388, C1-C3. Each snapshot is spawned right after its batch's
    `write` line and before the writer's report is read, so it races the
    batch; the bounds hold wherever it lands."""
    writer = children.writer(repo)
    reports: list[int] = [0]
    snapshots = []
    followers = []
    for k in range(BATCHES):
        writer.send(*(f"snap-{k}-{i}" for i in range(BATCH_SIZE)))
        snapshot = _snapshot(am)
        snapshots.append(snapshot)
        followers.append(children.follow("--since-seq", str(snapshot["as_of_seq"])))
        batch = writer.report()
        assert len(batch) == BATCH_SIZE, batch
        reports.append(batch[-1])
    writer.quit()

    final = _events(am)
    head = _head()
    assert _gseqs(final) and _gseqs(final)[-1] == head == reports[-1]
    upserted = {event["run_id"]: event["gseq"] for event in final}
    store_ids = {snapshot["store_id"] for snapshot in snapshots}
    assert len(store_ids) == 1 and isinstance(next(iter(store_ids)), str), store_ids
    assert next(iter(store_ids))

    for k, (snapshot, (follower, hello)) in enumerate(zip(snapshots, followers)):
        cursor = snapshot["as_of_seq"]
        assert reports[k] <= cursor <= reports[k + 1], (k, cursor, reports)
        # C2: the listing holds exactly the runs whose event is at or below as_of_seq.
        assert {run["id"] for run in snapshot["runs"]} == {
            run_id for run_id, gseq in upserted.items() if gseq <= cursor
        }, k
        assert hello["cursor_reset"] is False, hello
        assert hello["store_id"] == snapshot["store_id"], hello
        assert hello["head"] >= cursor, hello
        # A snapshot that landed after the last batch has nothing left to follow.
        feed = follower.until_gseq(head) if cursor < head else []
        assert feed == [event for event in final if event["gseq"] > cursor], k
        assert _gseqs(feed) == sorted(set(_gseqs(feed))), k
        before = [event for event in final if event["gseq"] <= cursor]
        assert set(_gseqs(before)).isdisjoint(_gseqs(feed)), k
        assert sorted(_gseqs(before) + _gseqs(feed)) == _gseqs(final), k
        assert _events(am, "--since-seq", str(cursor)) == feed, k

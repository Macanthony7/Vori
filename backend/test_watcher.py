"""watcher.py as a process: one instance per database, --once, --max-seconds, stop on a signal.
(The watcher's payment logic is covered by test_payments.py and the E2E tests.)"""
import os
import tempfile

os.environ.setdefault("GATEWAY_DB", os.path.join(tempfile.mkdtemp(), "w.db"))
os.environ.setdefault("GATEWAY_DB_KEY", os.urandom(32).hex())

import watcher  # noqa: E402


def test_only_one_watcher_per_database(tmp_path):
    lock = str(tmp_path / "x.lock")
    first = watcher.single_instance(lock)
    assert first is not None
    assert watcher.single_instance(lock) is None, "a second watcher must exit"
    first.close()
    again = watcher.single_instance(lock)
    assert again is not None, "the lock is released when the first one stops"
    again.close()


def test_once_and_time_limit(monkeypatch):
    passes = []
    monkeypatch.setattr(watcher, "tick", lambda conn: passes.append(1))
    monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
    watcher.main(["--once"])
    assert passes == [1]

    clock = iter(range(0, 1000, 3))
    monkeypatch.setattr(watcher.time, "monotonic", lambda: next(clock))
    watcher.main(["--max-seconds", "20"])
    assert 2 <= len(passes) - 1 <= 7, "loops, then exits before the limit"


def test_a_failing_pass_does_not_stop_the_loop(monkeypatch):
    calls = []

    def tick(conn):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("provider exploded")
        watcher._request_stop(None, None)  # like SIGTERM: finish this pass, then exit

    monkeypatch.setattr(watcher, "tick", tick)
    monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(watcher, "_stop", False)
    watcher.main([])
    assert len(calls) == 2
    monkeypatch.setattr(watcher, "_stop", False)

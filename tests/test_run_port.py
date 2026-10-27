"""run.py waits for a port that a stopped dashboard still holds instead of exiting."""

import run


def test_wait_for_port_returns_once_free(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(run, "_port_free", lambda host, port: next(answers))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    assert run._wait_for_port("127.0.0.1", 8000, timeout=10) is True


def test_wait_for_port_gives_up(monkeypatch):
    monkeypatch.setattr(run, "_port_free", lambda host, port: False)
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(run.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    assert run._wait_for_port("127.0.0.1", 8000, timeout=20) is False

import socket
from types import SimpleNamespace

from app import llm
from app.config import port_is_free


def test_never_attaches_before_starting_its_own_process():
    r = llm.Runtime()
    assert r.url is None and r.alive() is False


def test_free_port_skips_a_port_held_by_another_app(monkeypatch):
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        taken = other.getsockname()[1]
        monkeypatch.setattr(llm, "OLLAMA_PORT_START", taken)
        assert llm.Runtime._free_port() != taken


def test_port_on_all_interfaces_counts_as_taken():
    # The case that shadowed another project: it listens on 0.0.0.0 and we would bind 127.0.0.1.
    with socket.socket() as other:
        other.bind(("0.0.0.0", 0))
        other.listen()
        assert port_is_free(other.getsockname()[1]) is False


def test_orphan_cleanup_spares_other_projects_ollama(monkeypatch):
    r = llm.Runtime()
    stopped = []

    def proc(pid, exe):
        return SimpleNamespace(pid=pid, info={"exe": exe}, terminate=lambda: stopped.append(pid))

    own = str(r.exe())
    monkeypatch.setattr(llm.psutil, "process_iter", lambda _: [
        proc(1, own.upper()),
        proc(2, r"D:\GithubProjects\ai-girlfriend-bot\tools\ollama\ollama.exe"),
        proc(3, r"D:\GithubProjects\security-modeler\llm\ollama\ollama.exe"),
        proc(4, None),
        proc(5, str(r.exe().parent / "lib" / "ollama" / "llama-server.exe")),
    ])
    r._kill_own_orphans()
    assert stopped == [1, 5]  # including our model runner, so stopping really frees the memory


def test_only_hand_loaded_models_stay_in_memory():
    r = llm.Runtime()
    assert r._keep("a") == "30m"
    r.pinned.add("a")
    assert r._keep("a") == -1 and r._keep("b") == "30m"


def test_stopped_engine_reports_stopped_and_can_restart(monkeypatch):
    r = llm.Runtime()
    r.url, r.state = "http://x", "gata"
    r.pinned.add("a")
    monkeypatch.setattr(llm.psutil, "process_iter", lambda _: [])
    r.stop_engine()
    assert (r.state, r.url, r.pinned) == ("oprit", None, set()) and r.alive() is False
    started = []
    monkeypatch.setattr(r, "bootstrap", lambda: started.append(1))
    r.start_engine()
    assert started == [1]

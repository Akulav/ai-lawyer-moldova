"""Portable Ollama runtime: download, start, and manage models. Nothing is installed system-wide."""
import json
import logging
import os
import subprocess
import threading
import time
import zipfile
from typing import Iterator

import httpx
import psutil

from . import db
from .config import MODELS_DIR, OLLAMA_DIR, OLLAMA_PORT_START, OLLAMA_ZIP, USER_AGENT, port_is_free

log = logging.getLogger("llm")

CATALOG = [
    dict(name="hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL", size_gb=13.1, title="Qwen3.8 27B (Q3, pentru GPU de 16 GB)", note="Același model, comprimat ca să încapă integral într-un GPU de 16 GB, deci mult mai rapid. Folosit pentru încadrarea actelor în domenii.", min_ram=24),
    dict(name="qwen3:14b", size_gb=9.3, title="Qwen3 14B", note="Mult mai rapid, dar în teste a greșit calcule și a interpretat greșit legea. Pentru GPU de 12–16 GB.", min_ram=24),
    dict(name="qwen3:8b", size_gb=5.2, title="Qwen3 8B", note="Mai rapid, pentru GPU de 8 GB sau calculatoare mai modeste.", min_ram=0),
    dict(name="qwen3.8:27b", size_gb=17.7, title="Qwen3.8 27B", note="Recomandat: cel mai corect raționament juridic în teste. Mai lent (câteva minute pe răspuns), nu încape integral într-un GPU de 16 GB.", min_ram=32),
    dict(name="deepseek-r1:14b", size_gb=9.0, title="DeepSeek-R1 14B", note="Raționament detaliat, răspunsuri mai lente.", min_ram=24),
    dict(name="gpt-oss:20b", size_gb=13.8, title="gpt-oss 20B", note="Model mare, cu raționament. Are nevoie de un GPU de 16 GB.", min_ram=32),
    dict(name="ornith-1.5:9b", size_gb=6.6, title="Ornith 1.5 9B", note="Alternativă compactă, cu raționament.", min_ram=16),
    dict(name="nimble-v3:9b", size_gb=9.5, title="Nimble v3 9B", note="Alternativă cu raționament.", min_ram=16),
    dict(name="bonsai-2:9b", size_gb=3.2, title="Bonsai 2 9B", note="Foarte mic, pentru calculatoare slabe. Calitate juridică mai redusă.", min_ram=0),
]


def _ram_gb() -> int:
    import ctypes

    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("p", ctypes.c_ulong), ("t", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong),
                    ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong),
                    ("av", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = M()
    m.l = ctypes.sizeof(M)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.t / 2**30)
    except Exception:
        return 16


def default_model() -> str:
    # Legal reasoning decides the default: in testing, 14B made arithmetic and legal errors that 27B avoided.
    ram = _ram_gb()
    return "qwen3.8:27b" if ram >= 32 else "qwen3:14b" if ram >= 24 else "qwen3:8b"


def tie_to_this_process(proc: subprocess.Popen):
    """End `proc` and everything it starts when this process ends, however it ends. Closing the console window
    kills Python without running the app's shutdown, which left Ollama and its model runner holding RAM and
    VRAM. A Windows job with KILL_ON_JOB_CLOSE does it: the only handle to the job belongs to this process,
    the system closes it when the process exits, and that ends every process in the job. Ollama's model
    runners, started later, join the job automatically. Returns the job handle, which must stay referenced."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = (job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9: extended limits
          and k32.AssignProcessToJobObject(job, int(proc._handle)))
    if not ok:
        log.warning("could not tie the AI engine to the app (error %s); it is still stopped on a normal exit",
                    ctypes.get_last_error())
    return job


class Runtime:
    def __init__(self):
        self.state = "nepornit"  # nepornit | descarc_runtime | pornesc | gata | eroare
        self.error = None
        self.runtime_progress = 0.0
        self.pulls: dict[str, dict] = {}
        self.proc = None
        self.url = None  # only ever set to an Ollama this app started itself
        self.pinned: set[str] = set()  # models the user loaded by hand
        self.loading: dict[str, str | None] = {}  # model -> None while loading, or the error
        self.lock = threading.Lock()
        self.cancelled: set[str] = set()
        self.asking = 0  # questions being answered now; background work yields to them
        self.job = None  # Windows job tying the engine to this process; must stay referenced
        self.log_file = None

    # --- runtime ----------------------------------------------------------------
    def exe(self):
        return OLLAMA_DIR / "ollama.exe"

    def alive(self) -> bool:
        if not self.url:
            return False
        try:
            return httpx.get(f"{self.url}/api/version", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def bootstrap(self):
        threading.Thread(target=self._bootstrap, daemon=True, name="llm-bootstrap").start()

    def _bootstrap(self):
        try:
            if not self.exe().exists():
                self._download_runtime()
            self._start()
            con = db.connect()
            try:
                active = db.get_setting(con, "active_model")
                if not active:
                    active = default_model()
                    db.set_setting(con, "active_model", active)
            finally:
                con.close()
            if active not in [m["name"] for m in self.installed()]:
                self.pull(active)
        except Exception as e:
            log.exception("bootstrap")
            self.state, self.error = "eroare", str(e)

    def _download_runtime(self):
        self.state = "descarc_runtime"
        OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = OLLAMA_DIR.parent / "ollama.zip.part"
        pos = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if pos:
            headers["Range"] = f"bytes={pos}-"
        with httpx.stream("GET", OLLAMA_ZIP, headers=headers, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            if r.status_code != 206:
                pos = 0
            total = int(r.headers.get("content-length", 0)) + pos
            with open(tmp, "ab" if pos else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    pos += len(chunk)
                    self.runtime_progress = pos / total if total else 0
        with zipfile.ZipFile(tmp) as z:
            z.extractall(OLLAMA_DIR)
        tmp.unlink()

    def _kill_own_orphans(self):
        """Stop this app's Ollama and its model runners. Other projects' binaries are never touched."""
        own = str(OLLAMA_DIR).lower() + os.sep
        for p in psutil.process_iter(["exe"]):
            if (p.info["exe"] or "").lower().startswith(own):
                log.info("stopping own engine process pid %s", p.pid)
                try:
                    p.terminate()
                except psutil.Error:
                    pass  # it exited meanwhile (a runner ends with its server), or is not ours to stop

    def stop_engine(self):
        """Free all memory the AI uses, including the model runner. Indexing and search keep working."""
        self.pinned.clear()
        self.loading.clear()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        self._kill_own_orphans()
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        self.url = None
        self.state = "oprit"

    def start_engine(self):
        if self.state in ("oprit", "eroare"):
            self.error = None
            self.state = "pornesc"
            self.bootstrap()

    def _keep(self, model: str):
        """A model loaded by hand stays in memory until unloaded. Otherwise it expires when idle."""
        return -1 if model in self.pinned else "30m"

    def load(self, name: str):
        """Load in the background, because a large model takes a while to reach the GPU."""
        self.loading[name] = None
        self.pinned.add(name)

        def work():
            try:
                r = httpx.post(f"{self.url}/api/generate", json=dict(model=name, keep_alive=-1), timeout=600)
                r.raise_for_status()
                self.loading.pop(name, None)
            except (httpx.HTTPError, TypeError) as e:
                log.warning("load %s failed: %s", name, e)
                self.pinned.discard(name)
                self.loading[name] = str(e) or "Modelul nu s-a putut încărca."
        threading.Thread(target=work, daemon=True, name=f"load-{name}").start()

    def unload(self, name: str):
        self.pinned.discard(name)
        self.loading.pop(name, None)
        httpx.post(f"{self.url}/api/generate", json=dict(model=name, keep_alive=0), timeout=120).raise_for_status()

    @staticmethod
    def _free_port() -> int:
        for port in range(OLLAMA_PORT_START, OLLAMA_PORT_START + 200):
            if port_is_free(port):
                return port
        raise RuntimeError("Nu am găsit un port liber pentru motorul AI.")

    def _start(self):
        """Always run our own process, so model actions can never reach another app's Ollama."""
        self.state = "pornesc"
        self._kill_own_orphans()
        port = self._free_port()
        self.url = f"http://127.0.0.1:{port}"
        env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{port}", OLLAMA_MODELS=str(MODELS_DIR), OLLAMA_KEEP_ALIVE="30m",
                   # Read by the llama-server runner Ollama starts. With a 27B model wholly on the GPU these took ~20 GB
                   # of RAM: the memory-mapped model file stayed resident after its copy to VRAM (13 GB), and the
                   # prompt cache kept the last requests up to 8 GB, ~0.8 GB each, though tagging prompts never repeat.
                   # Measured without them: 2.1 GB, the same tokens/s, and the model loads faster.
                   LLAMA_ARG_LOAD_MODE="none", LLAMA_ARG_CACHE_RAM="0")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        if self.log_file:
            self.log_file.close()  # each engine restart opened another handle that was never closed
        self.log_file = open(OLLAMA_DIR.parent / "ollama.log", "ab")
        self.proc = subprocess.Popen([str(self.exe()), "serve"], env=env, stdout=self.log_file, stderr=self.log_file,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.job = tie_to_this_process(self.proc)
        for _ in range(120):
            if self.proc.poll() is not None:
                raise RuntimeError("Motorul Ollama s-a oprit la pornire. Vezi .runtime/ollama.log")
            if self.alive():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Motorul Ollama nu a pornit. Vezi .runtime/ollama.log")
        self.state = "gata"

    def shutdown(self):
        self.stop_engine()

    # --- models -----------------------------------------------------------------
    def installed(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=10)
            return [dict(name=m["name"], size_gb=round(m["size"] / 1e9, 1), family=m.get("details", {}).get("family"),
                         params=m.get("details", {}).get("parameter_size"), quant=m.get("details", {}).get("quantization_level"))
                    for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return []

    def loaded(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.url}/api/ps", timeout=5)
            return [dict(name=m["name"], gpu_pct=round(100 * m.get("size_vram", 0) / max(m.get("size", 1), 1)),
                         size_gb=round(m.get("size", 0) / 1e9, 1))
                    for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return []

    def pull(self, name: str):
        with self.lock:
            cur = self.pulls.get(name)
            if cur and cur["state"] == "descarc":
                return
            self.pulls[name] = dict(name=name, state="descarc", progress=0.0, status="pornesc", error=None)
            self.cancelled.discard(name)
        threading.Thread(target=self._pull, args=(name,), daemon=True, name=f"pull-{name}").start()

    def _pull(self, name: str):
        p = self.pulls[name]
        try:
            with httpx.stream("POST", f"{self.url}/api/pull", json={"name": name, "stream": True}, timeout=None) as r:
                for line in r.iter_lines():
                    if name in self.cancelled:
                        p.update(state="anulat", status="Anulat")
                        return
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    p["status"] = ev.get("status", "")
                    if ev.get("total"):
                        p["progress"] = ev.get("completed", 0) / ev["total"]
            p.update(state="gata", progress=1.0, status="Instalat")
        except Exception as e:
            log.warning("pull %s failed: %s", name, e)
            p.update(state="eroare", error=str(e), status="Eroare")

    def cancel_pull(self, name: str):
        self.cancelled.add(name)

    def delete(self, name: str):
        r = httpx.request("DELETE", f"{self.url}/api/delete", json={"name": name}, timeout=60)
        r.raise_for_status()

    # --- inference --------------------------------------------------------------
    def json_chat(self, model: str, system: str, user: str, schema: dict, num_predict: int = 500) -> dict | None:
        """Quick structured call used for tree traversal. The schema constrains what the model can emit.
        Reasoning is off, because this runs several times per question."""
        body = dict(model=model, stream=False, format=schema, think=False, keep_alive=self._keep(model),
                    options=dict(temperature=0, num_ctx=8192, num_predict=num_predict),
                    messages=[dict(role="system", content=system), dict(role="user", content=user)])
        try:
            r = httpx.post(f"{self.url}/api/chat", json=body, timeout=300)
            if r.status_code == 400:  # model without a thinking mode
                body.pop("think")
                r = httpx.post(f"{self.url}/api/chat", json=body, timeout=300)
            r.raise_for_status()
            return json.loads(r.json()["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("json_chat failed: %s", e)
            return None

    def json_reason(self, model: str, system: str, user: str, schema: dict, cancel=lambda: False,
                    num_ctx: int = 16384, num_predict: int = 10000, think: bool | None = True,
                    stats: dict | None = None) -> tuple[dict | None, str]:
        """Structured call with reasoning on and a large budget, for background work where accuracy beats speed.
        Streams so that `cancel()` can stop it at once, e.g. when the user asks a question. Returns (json, thinking).
        `stats`, if given, is updated as tokens arrive, for a live view: thinking and answer token counts, the
        time of the first token, and at the end Ollama's own counts and timings."""
        # think is sent either way: left out, a reasoning model like Qwen3.8 reasons by default. None leaves it
        # out, for a model that rejects the field.
        body = dict(model=model, stream=True, format=schema, keep_alive=self._keep(model),
                    **({} if think is None else dict(think=think)),
                    options=dict(temperature=0, num_ctx=num_ctx, num_predict=num_predict),  # reasoning counts toward this
                    messages=[dict(role="system", content=system), dict(role="user", content=user)])
        content, thinking = [], []
        with httpx.stream("POST", f"{self.url}/api/chat", json=body, timeout=httpx.Timeout(60, read=900)) as r:
            if r.status_code == 400 and think is not None:  # model without a thinking mode
                r.read()
                return self.json_reason(model, system, user, schema, cancel, num_ctx, num_predict, think=None, stats=stats)
            if r.status_code != 200:
                raise RuntimeError(f"Modelul a returnat eroarea {r.status_code}: {r.read().decode()[:300]}")
            st = stats if stats is not None else {}
            st.update(thinking=0, answer=0, first=None)
            for line in r.iter_lines():
                if cancel():
                    return None, ""  # closing the stream makes Ollama stop generating
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                msg = ev.get("message", {})
                if msg.get("thinking") or msg.get("content"):
                    st["first"] = st["first"] or time.time()  # generation starts after loading and reading the prompt
                    st["thinking" if msg.get("thinking") else "answer"] += 1  # one streamed event is one token
                thinking.append(msg.get("thinking") or "")
                content.append(msg.get("content") or "")
                if ev.get("done"):
                    st.update(done_reason=ev.get("done_reason"), eval_count=ev.get("eval_count", 0),
                              eval_s=ev.get("eval_duration", 0) / 1e9, prompt_s=ev.get("prompt_eval_duration", 0) / 1e9,
                              load_s=ev.get("load_duration", 0) / 1e9)
        try:
            return json.loads("".join(content)), "".join(thinking)
        except ValueError:
            return None, "".join(thinking)

    CHAT_CTX, ANSWER_ROOM, MAX_THINK = 16384, 3500, 9000
    NOTES = ("\n\nNOTELE TALE DE ANALIZĂ (raționamentul tău de până acum asupra acestei întrebări):\n{notes}\n\n"
             "Pe baza SURSELOR de mai sus și a acestor note, formulează acum răspunsul final, cu trimiteri [S#].")

    def stream_chat(self, model: str, messages: list[dict], think: bool = True,
                    think_budget: int | None = None) -> Iterator[tuple[str, str]]:
        """Yield ('budget'|'thinking'|'token'|'retry', text). Handles both native thinking and inline <think> tags.

        A reasoning model can spend all of num_predict thinking and never answer (Qwen3.8-27B reasoned for the full
        6000 tokens, ~3 min, and returned an empty reply). So the reasoning gets a budget, as much as the context
        leaves after the prompt and room for the answer (yielded as 'budget' for the UI). Past it, or at the end with
        no answer, we stop, yield ('retry', '') and ask for the answer with thinking off, handing over the reasoning
        so far as notes so the work is not lost. think=False must be sent explicitly: left out, Qwen3.8 reasons by
        default, which is how the first version of this fallback reasoned for another 6000 tokens."""
        est = sum(len(m["content"]) for m in messages) // 3  # Romanian legal text runs ~3 characters per token
        room = self.CHAT_CTX - est - self.ANSWER_ROOM
        budget = think_budget or max(1500, min(self.MAX_THINK, room))
        if not think:
            yield from self._stream(model, messages, False, self.ANSWER_ROOM)
            return
        yield "budget", str(budget)
        thought, said = [], False
        gen = self._stream(model, messages, True, budget + self.ANSWER_ROOM)
        for kind, text in gen:
            if kind == "thinking":
                thought.append(text)
            elif text.strip():
                said = True
            yield kind, text
            if not said and len(thought) >= budget:
                break
        gen.close()  # closes the connection, which stops the generation in Ollama
        if not said and thought:
            notes = "".join(thought)[-max(room, 1000) * 3:]  # the tail holds the conclusions; keep it within context
            last = dict(messages[-1], content=messages[-1]["content"] + self.NOTES.format(notes=notes))
            yield "retry", ""
            yield from self._stream(model, messages[:-1] + [last], False, self.ANSWER_ROOM)

    def _stream(self, model: str, messages: list[dict], think: bool | None, num_predict: int) -> Iterator[tuple[str, str]]:
        body = dict(model=model, stream=True, messages=messages, keep_alive=self._keep(model),
                    **({} if think is None else dict(think=think)),
                    options=dict(temperature=0.2, num_ctx=self.CHAT_CTX, num_predict=num_predict))  # reasoning counts
        in_tag = False
        with httpx.stream("POST", f"{self.url}/api/chat", json=body, timeout=None) as r:
            if r.status_code == 400 and think is not None:  # model without a thinking mode
                r.read()
                yield from self._stream(model, messages, None, num_predict)
                return
            if r.status_code != 200:
                raise RuntimeError(f"Modelul a returnat eroarea {r.status_code}: {r.read().decode()[:300]}")
            for line in r.iter_lines():
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                msg = ev.get("message", {})
                if msg.get("thinking"):
                    yield "thinking", msg["thinking"]  # one streamed event is one token
                text = msg.get("content") or ""
                if "<think>" in text:
                    in_tag, text = True, text.replace("<think>", "")
                if "</think>" in text:
                    before, text = text.split("</think>", 1)
                    if before:
                        yield "thinking", before
                    in_tag = False
                if text:
                    yield ("thinking" if in_tag else "token"), text


runtime = Runtime()

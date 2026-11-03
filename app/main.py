"""HTTP API and static UI. Bound to localhost unless MAL_HOST says otherwise."""
import json
import logging
import os
import re
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, indexer, rag, sysinfo, tree, verify
from .config import APP_HOST, APP_NAME, APP_PORT, ROOT, RUNTIME, STATIC, VERSION, port_is_free
from .llm import CATALOG, runtime
from .tagger import tagger, tagger_status
from .text import fold, fts_query, tokens

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_):
    db.init()
    runtime.bootstrap()
    indexer.crawler.start()
    tagger.start()
    yield
    indexer.crawler.stop.set()
    tagger.stop.set()
    runtime.shutdown()


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)


# ---- status ----------------------------------------------------------------------------
@app.get("/api/identity")
def identity():
    # "app" is what the tray launcher checks to recognise this server, so it must not change.
    return dict(app="moldova-ai-lawyer", name=APP_NAME, root=str(ROOT), version=os.environ.get("AVOCAT_VERSION"))


_server = None  # the running uvicorn server, so /api/quit can stop it


@app.post("/api/quit")
def quit_app(request: Request):
    """Stop the server the normal way, so shutdown stops the AI engine and frees its RAM and VRAM. Used by the
    tray icon's Quit. Only from this computer: with run-lan.bat the page is reachable from the network."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Programul poate fi oprit doar de pe calculatorul pe care rulează.")
    if _server is not None:
        _server.should_exit = True
    return dict(ok=True)


@app.get("/api/status")
def status():
    con = db.connect()
    try:
        active = db.get_setting(con, "active_model")
        return dict(
            llm=dict(state=runtime.state, error=runtime.error, runtime_progress=runtime.runtime_progress, active=active,
                     ready=runtime.state == "gata" and active in [m["name"] for m in runtime.installed()],
                     pull=runtime.pulls.get(active)),
            index=indexer.index_status(con),
            tagger=tagger_status(con),
            memory=dict(sysinfo.memory(), models=runtime.loaded() if runtime.state == "gata" else []),
            disclaimer_accepted=db.get_setting(con, "disclaimer") == "1",
        )
    finally:
        con.close()


@app.post("/api/disclaimer")
def accept_disclaimer():
    con = db.connect()
    try:
        db.set_setting(con, "disclaimer", "1")
        return dict(ok=True)
    finally:
        con.close()


# ---- chat ------------------------------------------------------------------------------
class ChatIn(BaseModel):
    question: str
    conversation_id: int | None = None


@app.post("/api/chat")
def chat(body: ChatIn):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Întrebare goală")

    def gen():
        con = db.connect()
        try:
            cid = body.conversation_id
            if not cid or not con.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
                cid = con.execute("INSERT INTO conversations(title,created) VALUES(?,?)",
                                  (q[:80], datetime.now().isoformat(timespec="seconds"))).lastrowid
            history = [dict(r) for r in con.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 6", (cid,))][::-1]
            con.execute("INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)", (cid, "user", q))
            con.commit()
            yield "data: " + json.dumps(dict(type="conversation", id=cid), ensure_ascii=False) + "\n\n"
            for ev in rag.answer(con, q, history):
                if ev["type"] == "done":
                    meta = {k: ev.get(k) for k in ("sources", "warnings", "future", "thinking")}
                    con.execute("INSERT INTO messages(conversation_id,role,content,meta) VALUES(?,?,?,?)",
                                (cid, "assistant", ev["answer"], json.dumps(meta, ensure_ascii=False)))
                    con.commit()
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        except Exception as e:
            logging.exception("chat")
            yield "data: " + json.dumps(dict(type="error", message=f"Eroare internă: {e}"), ensure_ascii=False) + "\n\n"
        finally:
            con.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/conversations")
def conversations():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT id,title,created FROM conversations ORDER BY id DESC LIMIT 100")]
    finally:
        con.close()


@app.get("/api/conversations/{cid}")
def conversation(cid: int):
    con = db.connect()
    try:
        out = []
        for r in con.execute("SELECT role,content,meta FROM messages WHERE conversation_id=? ORDER BY id", (cid,)):
            out.append(dict(role=r["role"], content=r["content"], meta=json.loads(r["meta"]) if r["meta"] else None))
        return out
    finally:
        con.close()


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: int):
    con = db.connect()
    try:
        con.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        con.execute("DELETE FROM conversations WHERE id=?", (cid,))
        con.commit()
        return dict(ok=True)
    finally:
        con.close()


# ---- search ----------------------------------------------------------------------------
def _snippet(text: str, query: str, width: int = 300) -> str:
    flat = re.sub(r"\s+", " ", text)
    f = fold(flat)
    best = 0
    for t in tokens(query):
        if len(t) > 3:
            i = f.find(t[:max(4, len(t) - 2)])
            if i >= 0:
                best = i
                break
    start = max(0, best - width // 3)
    return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")


@app.get("/api/search")
def search(q: str = "", kind: str = "", status: str = "", domain: str = "", year_from: int | None = None,
           year_to: int | None = None, page: int = 1):
    page = max(1, page)  # page 0 or below would slice from the end of the list
    con = db.connect()
    try:
        filters, params = [], []
        if kind:
            filters.append("a.kind=?"); params.append(kind)
        if status:
            filters.append("a.status=?"); params.append(status)
        if year_from:
            filters.append("a.year>=?"); params.append(year_from)
        if year_to:
            filters.append("a.year<=?"); params.append(year_to)
        if domain:
            filters.append("a.id IN (SELECT act_id FROM act_domain WHERE domain=?)"); params.append(domain)
        where = (" AND " + " AND ".join(filters)) if filters else ""
        scores: dict[int, dict] = {}

        # "95/2021" or "nr. 95 din 2021" goes straight to the act.
        ref = re.search(r"\b(\d{1,5})\s*(?:/|din)\s*(\d{4})\b", q)
        if ref:
            for a in con.execute(f"SELECT a.id FROM acts a WHERE a.num=? AND a.year=?{where}", (int(ref[1]), int(ref[2]), *params)):
                scores[a["id"]] = dict(score=1000.0, snippet=None, art=None)

        fq = fts_query(q)
        if fq:
            for r in con.execute(
                f"""SELECT a.id, bm25(acts_fts) AS s FROM acts_fts JOIN acts a ON a.id=acts_fts.rowid
                    WHERE acts_fts MATCH ?{where} ORDER BY s LIMIT 200""", (fq, *params)):
                scores.setdefault(r["id"], dict(score=0.0, snippet=None, art=None))["score"] += -r["s"] * 4
            for r in con.execute(
                f"""SELECT a.id, c.text, c.art, bm25(chunks_fts) AS s FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
                    JOIN acts a ON a.id=c.act_id WHERE chunks_fts MATCH ? AND c.version_id=a.current_version{where}
                    ORDER BY s LIMIT 400""", (fq, *params)):
                e = scores.setdefault(r["id"], dict(score=0.0, snippet=None, art=None))
                e["score"] += -r["s"] if e["snippet"] is None else -r["s"] * 0.15
                if e["snippet"] is None:
                    e["snippet"], e["art"] = _snippet(r["text"], q), r["art"]
        elif not ref:
            for r in con.execute(f"SELECT a.id FROM acts a WHERE 1=1{where} ORDER BY a.pinned DESC, a.adopted DESC LIMIT 200", params):
                scores[r["id"]] = dict(score=0.0, snippet=None, art=None)

        if not scores:
            return dict(total=0, page=page, results=[])
        ids = list(scores)
        rows = {r["id"]: r for r in con.execute(f"SELECT * FROM acts WHERE id IN ({','.join(map(str, ids))})")}
        for i, e in scores.items():
            r = rows[i]
            # Key acts first, in-force before repealed, among otherwise similar matches.
            e["score"] *= (1.6 if r["pinned"] else 1.0) * (0.5 if r["status"] == "abrogat" else 1.0)
        ordered = sorted(ids, key=lambda i: scores[i]["score"], reverse=True)
        size = 20
        out = []
        for i in ordered[(page - 1) * size:page * size]:
            out.append(dict(rag._act_short(rows[i]), snippet=scores[i]["snippet"], art=scores[i]["art"]))
        return dict(total=len(ordered), page=page, results=out)
    finally:
        con.close()


@app.get("/api/acts/{act_id}")
def act(act_id: int):
    con = db.connect()
    try:
        a = con.execute("SELECT * FROM acts WHERE id=?", (act_id,)).fetchone()
        if not a:
            raise HTTPException(404)
        amended = [dict(rel=x["rel"], whole=x["whole"], effective=x["effective"], id=x["id"], ref=rag._ref(x), title=x["title"],
                        evidence=x["evidence"]) for x in rag.amendments_of(con, a, 200)]
        targets = []
        for r in con.execute("SELECT * FROM relations WHERE from_act=?", (act_id,)):
            t = con.execute("SELECT id,title,kind,number,year FROM acts WHERE num=? AND year=? LIMIT 1", (r["to_num"], r["to_year"])).fetchone()
            targets.append(dict(rel=r["rel"], whole=r["whole"], label=f"nr. {r['to_num']}/{r['to_year']}", id=t["id"] if t else None,
                                title=t["title"] if t else None))
        replaced = con.execute("SELECT id,title,kind,number,year FROM acts WHERE id=?", (a["replaced_by"],)).fetchone() if a["replaced_by"] else None
        versions = [dict(id=v["id"], effective=v["effective"], label=v["label"], source=v["source"], consolidated=bool(v["consolidated"]),
                         current=v["id"] == a["current_version"], url=v["url"])
                    for v in con.execute("SELECT * FROM versions WHERE act_id=? ORDER BY COALESCE(effective,'') DESC", (act_id,))]
        chunks = [dict(id=c["id"], art=c["art"], path=c["path"], text=c["text"]) for c in con.execute(
            "SELECT * FROM chunks WHERE version_id=? ORDER BY ord", (a["current_version"] or 0,))]
        domains = [dict(id=r["domain"], name=tree.DOMAINS[r["domain"]]["name"], group=tree.DOMAINS[r["domain"]]["group_name"])
                   for r in con.execute("SELECT domain FROM act_domain WHERE act_id=?", (act_id,)) if r["domain"] in tree.DOMAINS]
        return dict(rag._act_short(a), amended_by=amended, targets=targets, versions=versions, chunks=chunks, domains=domains,
                    replaced_by=dict(id=replaced["id"], title=replaced["title"], ref=rag._ref(replaced)) if replaced else None,
                    future_effective=a["future_effective"])
    finally:
        con.close()


@app.get("/api/tree")
def get_tree():
    con = db.connect()
    try:
        counts = {r["domain"]: r["n"] for r in con.execute("SELECT domain, COUNT(*) n FROM act_domain GROUP BY domain")}
        out = []
        for g in tree.TREE:
            doms = []
            for d in g["domains"]:
                key = [rag._act_short(r) for r in con.execute(
                    """SELECT a.* FROM acts a JOIN act_domain x ON x.act_id=a.id WHERE x.domain=? AND a.pinned=1 ORDER BY a.title""", (d["id"],))]
                doms.append(dict(id=d["id"], name=d["name"], desc=d["desc"], count=counts.get(d["id"], 0), key_acts=key))
            out.append(dict(id=g["id"], name=g["name"], domains=doms))
        return out
    finally:
        con.close()


# ---- index -----------------------------------------------------------------------------
@app.post("/api/index/pause")
def pause():
    indexer.crawler.paused.set()
    return dict(ok=True)


@app.post("/api/index/resume")
def resume():
    indexer.crawler.paused.clear()
    indexer.crawler.start()
    return dict(ok=True)


@app.get("/api/verify")
def verify_index():
    con = db.connect()
    try:
        return verify.run(con)
    finally:
        con.close()


@app.post("/api/verify/repair")
def verify_repair():
    con = db.connect()
    try:
        return dict(fixed=verify.repair(con), check=verify.run(con))
    finally:
        con.close()


@app.post("/api/tagger/{action}")
def tagger_toggle(action: str):
    if action not in ("pause", "resume"):
        raise HTTPException(404)
    con = db.connect()
    try:
        tagger.set_paused(con, action == "pause")
        return dict(ok=True)
    finally:
        con.close()


@app.get("/api/problems")
def problems():
    con = db.connect()
    try:
        # A failed issue put back in the queue keeps its error until it succeeds, so it stays listed here.
        rows = con.execute(
            """SELECT id, title, CASE WHEN status='listed' THEN 'retry' ELSE status END AS status, attempts, error, n_acts
               FROM issues WHERE status IN ('error','no_text') OR (status='listed' AND error IS NOT NULL)
               ORDER BY 3, id DESC""").fetchall()
        missing = con.execute("SELECT COUNT(*) FROM issues WHERE status='missing'").fetchone()[0]
        return dict(issues=[dict(r) for r in rows], missing=missing)
    finally:
        con.close()


def _requeue(con, where: str, params=()) -> int:
    """Put issues at the front of the queue with a clean attempt count."""
    n = con.execute(
        f"""UPDATE issues SET status='listed', attempts=0, error=COALESCE(error,'Fără text extractibil'), priority=priority+100000
            WHERE (status IN ('error','no_text') OR (status='listed' AND error IS NOT NULL)) {where}""", params).rowcount
    con.commit()
    indexer.crawler.wake.set()
    return n


@app.post("/api/problems/retry-all")
def retry_all():
    con = db.connect()
    try:
        return dict(queued=_requeue(con, ""))
    finally:
        con.close()


@app.post("/api/problems/{issue_id}/retry")
def retry_one(issue_id: int):
    con = db.connect()
    try:
        n = _requeue(con, "AND id=?", (issue_id,))
        if not n:
            raise HTTPException(404, "Ediția nu are o problemă activă.")
        return dict(queued=n)
    finally:
        con.close()


@app.post("/api/import")
async def import_doc(file: UploadFile = File(...), effective: str = Form("")):
    data = await file.read()
    con = db.connect()
    try:
        if effective and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective):
            raise HTTPException(400, "Data trebuie să fie în formatul AAAA-LL-ZZ.")
        return indexer.import_document(con, file.filename or "document", data, effective or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        con.close()


# ---- models ----------------------------------------------------------------------------
class ModelIn(BaseModel):
    name: str


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:]{0,200}$")


def _check(name: str) -> str:
    name = name.strip()
    if not _NAME.match(name):
        raise HTTPException(400, "Nume de model invalid. Exemplu: qwen3:14b sau hf.co/utilizator/model-GGUF")
    return name


@app.get("/api/models")
def models():
    con = db.connect()
    try:
        return dict(active=db.get_setting(con, "active_model"), installed=runtime.installed(), loaded=runtime.loaded(),
                    catalog=CATALOG, pulls=list(runtime.pulls.values()), state=runtime.state,
                    loading=runtime.loading, pinned=sorted(runtime.pinned))
    finally:
        con.close()


def _require_engine():
    if not runtime.alive():
        raise HTTPException(409, "Motorul AI este oprit. Porniți-l mai întâi.")


@app.post("/api/models/pull")
def pull(m: ModelIn):
    _require_engine()
    runtime.pull(_check(m.name))
    return dict(ok=True)


@app.post("/api/models/load")
def load_model(m: ModelIn):
    _require_engine()
    name = _check(m.name)
    if name not in [x["name"] for x in runtime.installed()]:
        raise HTTPException(400, "Modelul nu este instalat.")
    runtime.load(name)
    return dict(ok=True)


@app.post("/api/models/unload")
def unload_model(m: ModelIn):
    _require_engine()
    try:
        runtime.unload(_check(m.name))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Modelul nu a putut fi scos din memorie: {e}")
    return dict(ok=True)


@app.post("/api/engine/stop")
def engine_stop():
    runtime.stop_engine()
    return dict(ok=True)


@app.post("/api/engine/start")
def engine_start():
    runtime.start_engine()
    return dict(ok=True)


@app.post("/api/models/cancel")
def cancel(m: ModelIn):
    runtime.cancel_pull(m.name)
    return dict(ok=True)


@app.post("/api/models/active")
def set_active(m: ModelIn):
    name = _check(m.name)
    if name not in [x["name"] for x in runtime.installed()]:
        raise HTTPException(400, "Modelul nu este instalat.")
    con = db.connect()
    try:
        db.set_setting(con, "active_model", name)
        return dict(ok=True)
    finally:
        con.close()


@app.delete("/api/models/{name:path}")
def delete_model(name: str):
    _require_engine()
    name = _check(name)  # the stripped name, so the pull entry and the active setting below still match
    try:
        runtime.delete(name)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Modelul nu a putut fi șters: {e}")
    con = db.connect()
    try:
        runtime.pulls.pop(name, None)
        if db.get_setting(con, "active_model") == name:
            rest = runtime.installed()
            db.set_setting(con, "active_model", rest[0]["name"] if rest else "")
        return dict(ok=True)
    finally:
        con.close()


# ---- UI --------------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def revalidate_static(request, call_next):
    # The page, its script and its stylesheet must change together after an update, or a cached old
    # stylesheet breaks the new page. no-cache still uses the cache, after a cheap ETag check.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _is_this_app(port: int) -> bool:
    """A port answering proves nothing, so check that the instance belongs to this folder."""
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/identity", timeout=2)
        return r.status_code == 200 and r.json().get("root") == str(ROOT)
    except (httpx.HTTPError, ValueError):
        return False


def _lan_addresses() -> list[str]:
    """IPv4 addresses other devices can use, LAN and VPN adapters included."""
    import socket
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos if not i[4][0].startswith("127.")})


def _choose_port() -> tuple[int, bool]:
    """Return (port, already_running), moving past ports held by other programs."""
    for port in range(APP_PORT, APP_PORT + 50):
        if _is_this_app(port):
            return port, True
        if port_is_free(port):
            return port, False
    raise SystemExit("Nu am găsit un port liber.")


if __name__ == "__main__":
    import uvicorn
    # Started by AvocatAI.exe: the tray launcher opens the browser itself, once the server answers.
    launched = os.environ.get("AVOCAT_LAUNCHER") == "1"
    port, running = _choose_port()
    url = f"http://127.0.0.1:{port}"
    # The launcher reads the port from here, since the preferred one may be taken.
    (RUNTIME / "app.port").write_text(str(port))
    if running:
        if not launched:
            webbrowser.open(url)
        raise SystemExit(0)
    print(f"  Adresa: {url}")
    if APP_HOST != "127.0.0.1":
        for ip in _lan_addresses():
            print(f"  Din rețea: http://{ip}:{port}")
        if port != APP_PORT:
            print(f"  Atenție: portul {APP_PORT} era ocupat, deci se folosește {port}. Deschideți-l în firewall.")
    if not launched:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    _server = uvicorn.Server(uvicorn.Config(app, host=APP_HOST, port=port, log_level="warning"))
    _server.run()

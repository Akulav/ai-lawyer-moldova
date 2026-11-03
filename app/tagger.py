"""Background tagging: the model places in the tree the acts whose title matched no domain keyword.

Each act is judged once, from its title and the opening of its text, with reasoning on. Every verdict is
kept in ai_tags (including "no domain"), and the domains it adds are marked source='ai' in act_domain:
    SELECT * FROM ai_tags JOIN acts ON acts.id=ai_tags.act_id
"""
import collections
import logging
import re
import threading
import time

from . import db, tree
from .config import today
from .text import fold
from .llm import runtime

log = logging.getLogger("tagger")

BATCH = 3          # small, so each act gets real attention in the reasoning
TEXT_CHARS = 700   # the opening of the text usually states the act's object and scope
MAX_MISSES = 3     # an act the model keeps skipping is recorded as unclassified rather than retried forever
# The same model as Q4_K_M, quantized to fit wholly in a 16 GB GPU with its cache. Q4_K_M spills onto the CPU,
# which slows every token. Used when installed; the "tagger_model" setting overrides it.
PREFERRED_MODEL = "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL"
# The prompt is about 2.5k tokens. A larger window costs VRAM (about 130 KB per token for the 27B) and
# would push the model off the GPU again.
NUM_CTX = 16384
# Good batches reason in 900-1,500 tokens. Without a tighter limit the model sometimes deliberates until the
# limit (12,000 tokens, over 5 minutes) trying to recall unrelated laws, then never writes its answer.
NUM_PREDICT = 4000
# Then the batch is asked again with reasoning off: the schema-constrained answer always arrives. Such
# verdicts are marked in ai_tags.model so they can be found and redone later.
NO_REASONING = " [fără raționament]"

SYSTEM = """Clasifici acte normative ale Republicii Moldova în domeniile unui arbore juridic, ca un jurist cu experiență.
Pentru fiecare act primești tipul, numărul, titlul și începutul textului (de obicei obiectul și scopul actului).

Arbore (id: ce cuprinde):
{tree}

Reguli:
1. Pentru fiecare act, înainte de a alege, analizează pe rând:
   a) ce reglementează concret și ce drepturi sau obligații creează, folosind începutul textului, nu doar titlul;
   b) cine l-ar căuta și cu ce întrebare (o persoană, o firmă, o autoritate);
   c) 2-3 domenii candidate, cu argumente pentru și contra fiecăruia.
   Abia apoi alege 1-2 domenii, cel principal primul.
2. Judecă după obiectul reglementat, nu după cuvinte izolate din titlu. Un act care modifică alte acte se încadrează după domeniul modificărilor.
3. Lasă lista goală doar dacă niciun domeniu nu se potrivește cu adevărat, de exemplu numiri sau eliberări din funcție, decorații, acte individuale fără reguli generale.
4. „motiv”: o propoziție scurtă despre ce reglementează actul și de ce ai ales domeniul.
5. Răspunde pentru fiecare act din listă, cu numărul lui de ordine.
6. Judecă doar după titlul și începutul textului primite. Nu încerca să-ți amintești alte legi, numere sau date: nu e nevoie pentru alegerea domeniului. Analiza ta să fie scurtă, 2-4 propoziții pe act, apoi răspunde.
Răspunde numai JSON: {{"acte":[{{"nr":1,"domenii":["id"],"motiv":"..."}}]}}"""

SCHEMA = dict(type="object", required=["acte"], properties=dict(acte=dict(type="array", items=dict(
    type="object", required=["nr", "domenii", "motiv"], properties=dict(
        nr=dict(type="integer"),
        domenii=dict(type="array", maxItems=2, items=dict(type="string", enum=list(tree.DOMAINS))),
        motiv=dict(type="string"))))))

# Individual acts, recognisable from the title: appointments and dismissals by name, decorations, pardons,
# allocations of funds, a delegation's members. They set no general rules, so they belong in no domain, and the
# model is not needed for them (about 16% of the backlog; 40 of 40 sampled titles were individual acts).
# Laws and codes are never decided this way.
_INDIVIDUAL = re.compile(
    r"\b(numirea|eliberarea|rechemarea|destituirea|revocarea|incetarea (mandatului|raporturilor de serviciu)|suspendarea mandatului)\b"
    r".{0,60}\b(domnului|doamnei|dlui|dnei)\b"
    r"|\b(conferirea|decorarea)\b|\bdistinctii de stat\b|\btitlului onorific\b|\bgratier(e|ea|i|ii)?\b"
    r"|\balocarea (de |unor )?mijloace(lor)? financiare\b|\bcomponentei nominale\b")
BY_RULE = "regulă: act individual"

_SUBSTANCE = re.compile(r"Parlamentul adoptă|Guvernul HOTĂR[ĂÂÎ]ȘTE|Art(?:icolul)?\.?\s*(?:I|1)\b", re.I)

PENDING = """FROM acts a WHERE NOT EXISTS (SELECT 1 FROM act_domain d WHERE d.act_id=a.id)
             AND NOT EXISTS (SELECT 1 FROM ai_tags t WHERE t.act_id=a.id)"""


class Tagger:
    def __init__(self):
        self.stop = threading.Event()
        self.paused = threading.Event()  # mirrored in the tagger_paused setting so it survives restarts
        self.thread = None
        self.state = "pornire"
        self.model = None
        self.last_error = None
        self.rule_at = 0.0  # last pass recording individual acts by rule
        self.misses: dict[int, int] = {}
        # Live view of the batch in progress, and totals since the app started, for the Index page.
        self.live = None  # dict(titles, started, phase, stats) while a batch runs
        self.session = dict(acts=0, batches=0, seconds=0.0, tokens=0, gen_s=0.0, fallback=0, failed=0, by_rule=0)
        self.recent = collections.deque(maxlen=8)  # last verdicts: title, domains, how

    @property
    def sec_per_act(self):
        s = self.session
        return s["seconds"] / s["acts"] if s["acts"] else None

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="tagger")
            self.thread.start()

    def set_paused(self, con, paused: bool):
        (self.paused.set if paused else self.paused.clear)()
        db.set_setting(con, "tagger_paused", int(paused))

    def _model(self, con):
        if runtime.state != "gata":
            return None
        installed = [m["name"] for m in runtime.installed()]
        for model in (db.get_setting(con, "tagger_model"), PREFERRED_MODEL, db.get_setting(con, "active_model")):
            if model and model in installed:
                return model
        return None

    def _run(self):
        con = db.connect()
        if db.get_setting(con, "tagger_paused") == "1":
            self.paused.set()
        while not self.stop.is_set():
            try:
                if self.paused.is_set():
                    self.state = "pauză"
                elif runtime.asking:
                    self.state = "aștept să se termine răspunsul"
                elif self._by_rule(con):
                    continue
                elif not (model := self._model(con)):
                    self.state = "aștept motorul AI"
                elif not (batch := con.execute(
                        f"""SELECT a.id, a.kind, a.number, a.year, a.title, a.current_version {PENDING}
                            ORDER BY a.kind IN ('cod','lege') DESC, a.has_text DESC, COALESCE(a.year,0) DESC, a.id DESC
                            LIMIT ?""", (BATCH,)).fetchall()):
                    self.state = "la zi"
                    time.sleep(30)
                    continue
                else:
                    self.state, self.model = "etichetez", model
                    self._tag(con, model, batch)
                    continue
            except Exception as e:  # keep the worker alive through engine restarts
                if self.stop.is_set():
                    break  # the app is shutting down and stopped the engine mid-request; not an error
                log.exception("tagger error")
                self.last_error = str(e)[:300]
                time.sleep(30)
                continue
            time.sleep(2)

    def _by_rule(self, con) -> bool:
        """Record individual acts as having no domain without asking the model. Runs over the backlog once an
        hour, which also picks up newly indexed acts. Returns whether it recorded any."""
        if time.time() - self.rule_at < 3600:
            return False
        self.rule_at = time.time()
        found = [(a["id"],) for a in con.execute(f"SELECT a.id, a.kind, a.title {PENDING}").fetchall()
                 if a["kind"] not in ("lege", "cod") and _INDIVIDUAL.search(fold(a["title"]))]
        con.executemany("""INSERT OR IGNORE INTO ai_tags(act_id,domains,reason,model,tagged) VALUES(?,'',
                           'Act individual (numire, eliberare, decorare, grațiere, alocare de mijloace), fără reguli generale; recunoscut după titlu.',?,?)""",
                        [(i, BY_RULE, today()) for (i,) in found])
        con.commit()
        self.session["by_rule"] += len(found)
        return bool(found)

    def _excerpt(self, con, version_id) -> str:
        if not version_id:
            return ""
        text = re.sub(r"\s+", " ", " ".join(r["text"] for r in con.execute(
            "SELECT text FROM chunks WHERE version_id=? ORDER BY ord LIMIT 6", (version_id,))))
        # Laws often open with the title and the President's signature, which say nothing about the content
        # and left the model guessing; start where the provisions do.
        m = _SUBSTANCE.search(text[:4000])
        return text[m.start():m.start() + TEXT_CHARS] if m else text[:TEXT_CHARS]

    def _tag(self, con, model, batch):
        lines = []
        for n, a in enumerate(batch, 1):
            ref = f"{a['kind']} nr. {a['number']}/{a['year']}" if a["number"] and a["year"] else a["kind"]
            excerpt = self._excerpt(con, a["current_version"])
            lines.append(f"{n}. [{ref}] {a['title']}" + (f"\n   Început: {excerpt}" if excerpt else "\n   (fără text)"))
        t0 = time.time()
        stats: dict = {}
        self.live = dict(titles=[a["title"][:140] for a in batch], started=t0, phase="reasoning", stats=stats)
        def interrupted():
            return bool(runtime.asking) or self.stop.is_set() or self.paused.is_set()
        ask = dict(system=SYSTEM.format(tree=tree.tree_for_prompt()), user="Acte de clasificat:\n\n" + "\n\n".join(lines),
                   schema=SCHEMA, num_ctx=NUM_CTX, stats=stats, cancel=interrupted)
        try:
            res, _ = runtime.json_reason(model, num_predict=NUM_PREDICT, **ask)
            label, tokens, gen_s = model, stats.get("eval_count", 0), stats.get("eval_s", 0.0)
            if res is None and not interrupted():
                self.live["phase"] = "fallback"
                res, _ = runtime.json_reason(model, num_predict=1500, think=False, **ask)
                label = model + NO_REASONING
                tokens, gen_s = tokens + stats.get("eval_count", 0), gen_s + stats.get("eval_s", 0.0)
        finally:
            self.live = None
        if res is None:
            if interrupted():
                return  # interrupted on purpose; the same batch is retried later
            self.last_error = "Modelul nu a returnat un JSON valid."
            self.session["failed"] += 1
            verdicts = {}
        else:
            verdicts = {v.get("nr"): v for v in res.get("acte", []) if isinstance(v, dict)}
        done = 0
        for n, a in enumerate(batch, 1):
            v = verdicts.get(n)
            if v is None:
                self.misses[a["id"]] = self.misses.get(a["id"], 0) + 1
                if self.misses[a["id"]] < MAX_MISSES:
                    continue
                doms, why = [], "neclasificat: modelul nu a răspuns pentru acest act"
            else:
                doms = list(dict.fromkeys(d for d in v.get("domenii") or [] if d in tree.DOMAINS))[:2]
                why = str(v.get("motiv") or "")[:400]
            con.execute("INSERT OR REPLACE INTO ai_tags(act_id,domains,reason,model,tagged) VALUES(?,?,?,?,?)",
                        (a["id"], ",".join(doms), why, label, today()))
            con.executemany("INSERT OR IGNORE INTO act_domain(act_id,domain,source) VALUES(?,?,'ai')",
                            [(a["id"], d) for d in doms])
            self.misses.pop(a["id"], None)
            self.recent.appendleft(dict(title=a["title"][:110], domains=[tree.DOMAINS[d]["name"] for d in doms],
                                        how="fallback" if label != model else "ai", at=time.time()))
            done += 1
        con.commit()
        s = self.session
        s["batches"] += 1
        s["tokens"] += tokens
        s["gen_s"] += gen_s
        if done:
            s["acts"] += done
            s["seconds"] += time.time() - t0
        if res is not None:
            self.last_error = None
            s["fallback"] += label != model


tagger = Tagger()


_counts = dict(at=0.0)


def tagger_status(con) -> dict:
    # The counts scan every act (about half a second), so they are refreshed at most every 30 s; the live part
    # of the batch in progress is read on every call.
    if time.time() - _counts["at"] > 30:
        one = lambda q, p=(): con.execute(q, p).fetchone()[0]
        _counts.update(
            at=time.time(),
            pending=one(f"SELECT COUNT(*) {PENDING}"),
            pending_laws=one(f"SELECT COUNT(*) {PENDING} AND a.kind IN ('cod','lege')"),
            done=one("SELECT COUNT(*) FROM ai_tags"),
            by_rule=one("SELECT COUNT(*) FROM ai_tags WHERE model=?", (BY_RULE,)),
            fallback=one("SELECT COUNT(*) FROM ai_tags WHERE model LIKE ?", (f"%{NO_REASONING}",)),
            unclassified=one("SELECT COUNT(*) FROM ai_tags WHERE reason LIKE 'neclasificat%'"),
            with_domain=one("SELECT COUNT(*) FROM ai_tags WHERE domains<>''"))
    c = dict(_counts)
    c["reasoned"] = c["done"] - c["by_rule"] - c["fallback"] - c["unclassified"]
    s, now = tagger.session, time.time()
    live = None
    if (lv := tagger.live) is not None:
        st = lv["stats"]
        tokens = st.get("thinking", 0) + st.get("answer", 0)
        live = dict(titles=lv["titles"], phase=lv["phase"] if not st.get("answer") else "answering",
                    elapsed=round(now - lv["started"]), thinking=st.get("thinking", 0), answer=st.get("answer", 0),
                    limit=NUM_PREDICT if lv["phase"] == "reasoning" else 1500,
                    tok_s=round(tokens / (now - st["first"]), 1) if st.get("first") and now > st["first"] + 1 else None)
    per = tagger.sec_per_act
    return dict(
        state=tagger.state, model=tagger.model, last_error=tagger.last_error, paused=tagger.paused.is_set(),
        counts={k: v for k, v in c.items() if k != "at"}, live=live,
        session=dict(acts=s["acts"], batches=s["batches"], fallback=s["fallback"], failed=s["failed"], by_rule=s["by_rule"],
                     sec_per_act=round(per, 1) if per else None,
                     tok_s=round(s["tokens"] / s["gen_s"], 1) if s["gen_s"] else None),
        eta_hours=round(c["pending"] * per / 3600, 1) if per else None,
        eta_laws_hours=round(c["pending_laws"] * per / 3600, 1) if per else None,
        recent=[dict(r, ago=round(now - r["at"])) for r in tagger.recent])

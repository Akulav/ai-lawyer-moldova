"""Answering: walk Ramură -> Domeniu -> Act -> Articol, then answer only from what was found."""
import re
from datetime import date

from . import db, tree
from .config import today
from .llm import runtime
from .text import find_refs, fts_query

PICK_POOL = 50  # candidates the model chooses from; anything cut here can never reach the answer
GLOBAL_DEPTH = 200  # fragments taken from all of the legislation before the branch bonus is applied
DOMAIN_BOOST = 1.25  # relevance bonus for acts in the chosen branch
PINNED_BOOST = 1.4  # and for the branch's key acts
# Fragments per act in the pool. A key code keeps more, because the deciding article is often not among its
# ten best matches (Codul familiei art. 26 on dividing joint property ranked 12th for a divorce question).
PER_ACT = 10
PER_KEY_ACT = 25
STATUS_RO = {"presupus_in_vigoare": "fără abrogare detectată", "abrogat": "abrogat", "abrogare_viitoare": "va fi abrogat"}

ANSWER_SYSTEM = """Ești un asistent de informare juridică pentru legislația Republicii Moldova. Data de azi: {today}. Răspunzi în limba română.

Reguli obligatorii:
1. Folosești EXCLUSIV fragmentele din secțiunea SURSE. Nu completa din memorie: nu inventa articole, sume, termene sau numere de lege.
2. După fiecare afirmație juridică pui trimiterea la sursă, de forma [S1]. Menționează actul și articolul.
3. Dacă sursele nu conțin răspunsul sau suma exactă, spune clar ce lipsește. Nu ghici.
4. Aplici legea în vigoare în anul curent. Fragmentele marcate „NEÎN VIGOARE ÎNCĂ” nu se aplică azi: le folosești doar în secțiunea despre schimbări.
5. Când răspunsul depinde de o circumstanță (de ex. pe unde intri în țară, valoarea, statutul persoanei), nu alege una singură: tratează fiecare scenariu separat și arată ce se schimbă în fiecare.
6. Un fragment marcat „text inițial, neconsolidat” s-ar putea să fi fost modificat. Fragmentele marcate „ACT DE MODIFICARE” au prioritate față de textul inițial, dacă sunt deja în vigoare.
7. Citește literal condițiile și excepțiile și aplică-le în calcule și exemple. De exemplu, dacă legea spune că valoarea unui articol nu poate fi fracționată, scutirea nu se aplică parțial acelui articol.
8. Afirmi o obligație (de exemplu de a declara) numai dacă o sursă o prevede expres, și o citezi. Dacă sursele vorbesc doar despre scutire, nu despre declarare, spune deschis că nu ai găsit regula de declarare.
9. Trimiterea [S…] se pune doar pentru ceea ce scrie în acel fragment. Actele din lista „MODIFICĂRI ANUNȚATE” se menționează cu numele, fără [S…]. Nu ghici ce schimbă: dacă nu ai textul modificării, spune doar că există și când intră în vigoare.
10. Scrii limpede, pentru cineva fără pregătire juridică, fără introduceri.

Structura răspunsului, cu aceste titluri exacte:
## Pe scurt
Răspunsul direct, în 1-3 propoziții, începând cu „Da”, „Nu” sau „Depinde” când se poate.
## Ce spune legea
Explicația pe scenarii, cu trimiteri [S1].
## Exemplu
Un exemplu concret, cu cifre doar dacă acestea apar în surse.
## Ce s-ar putea schimba
Modificările viitoare din surse, cu data lor, și un exemplu al efectului. Dacă nu există niciuna în surse, spune că legislația se poate modifica și că nu ai identificat o schimbare anunțată.
## Ce să faci
1-3 pași practici. Dacă e cazul, autoritatea de contactat, numai dacă apare în surse."""

ROUTER_SYSTEM = """Ești componenta de rutare a unui motor de căutare juridică pentru Republica Moldova.
Alege din arbore cel mult 2 domenii unde se află răspunsul, cel mai probabil primul. Nu alege domenii doar vag legate. Folosește id-ul domeniului, adică textul dinaintea celor două puncte de pe rândurile cu liniuță.

Arbore:
{tree}

Generează și termenii juridici sub care legea ar reglementa situația, nu cuvintele din întrebare. Exemplu: „iPhone adus cadou la întoarcere” devine „bunuri bagaj călători scutire drepturi de import valoare declarare mod de transport aerian rutier”.
Răspunde numai JSON: {{"domenii":["id"],"termeni":["..."],"motiv":"o propoziție scurtă"}}"""

PICK_SYSTEM = """Selectezi fragmentele de lege utile pentru a răspunde la o întrebare.
Alege cel mult {n} numere de candidați care reglementează direct situația, inclusiv excepții, praguri și condiții. Ignoră fragmentele doar tangențiale.
Răspunde numai JSON: {{"alese":[1,2]}}"""


def _ref(a) -> str:
    kinds = dict(lege="Legea", cod="Codul", hotarare="Hotărârea", decret="Decretul", ordin="Ordinul",
                 regulament="Regulamentul", dispozitie="Dispoziția", decizie="Decizia", tratat="Tratatul", constitutie="Constituția", alt="Actul")
    if a["number"] and a["year"]:
        return f"{kinds.get(a['kind'], 'Actul')} nr. {a['number']}/{a['year']}"
    return kinds.get(a["kind"], "Actul")


def _act_short(a) -> dict:
    return dict(id=a["id"], title=a["title"], ref=_ref(a), kind=a["kind"], number=a["number"], year=a["year"],
                status=a["status"], status_label=STATUS_RO.get(a["status"], a["status"]), status_note=a["status_note"],
                status_date=a["status_date"], consolidated=bool(a["is_consolidated"]), has_text=bool(a["has_text"]),
                url=a["url"], legis_url=a["legis_url"], mo_ref=a["mo_ref"], adopted=a["adopted"], published=a["published"],
                pinned=bool(a["pinned"]))


def amendments_of(con, a, limit: int = 30) -> list:
    if a["num"] is None or a["year"] is None:
        return []
    is_law = a["kind"] in ("lege", "cod")
    # Only a law can amend a law. Orders and decisions merely citing a code are not amendments to it.
    rank = "AND f.kind IN ('lege','cod')" if is_law else ""
    return con.execute(
        f"""SELECT r.rel, r.whole, r.effective, r.evidence, f.* FROM relations r JOIN acts f ON f.id=r.from_act
            WHERE r.to_num=? AND r.to_year=? AND r.to_cls=? AND f.id<>? {rank}
            ORDER BY COALESCE(r.effective, f.published, '') DESC LIMIT ?""",
        (a["num"], a["year"], "lege" if is_law else a["kind"], a["id"], limit)).fetchall()


def search_chunks(con, query: str, act_ids=None, limit: int = 40, future: bool = False):
    """BM25 over the current version of each act; `act_ids` scopes the search to tree branches."""
    if not query:
        return []
    where = "c.version_id=a.current_version" if not future else "c.version_id<>a.current_version"
    scope = ""
    params: list = [query]
    if act_ids is not None:
        if not act_ids:
            return []
        scope = f"AND a.id IN ({','.join(map(str, act_ids))})"
    sql = f"""SELECT c.*, bm25(chunks_fts) AS score, a.title AS act_title FROM chunks_fts
              JOIN chunks c ON c.id=chunks_fts.rowid JOIN acts a ON a.id=c.act_id
              WHERE chunks_fts MATCH ? AND {where} {scope} ORDER BY score LIMIT ?"""
    params.append(limit)
    return con.execute(sql, params).fetchall()


def amendment_clauses(con, chosen, act_rows, limit: int = 4):
    """Yield (amending act, its clause, target act) for later laws that name a chosen article."""
    out, seen = [], set()
    for c in chosen:
        a = act_rows[c["act_id"]]
        if not c["art"] or a["is_consolidated"]:
            continue
        # An instruction to change the article, not a passing citation of it.
        name = re.compile(rf"\b(?:La\s+articolul|Articolul)\s+{re.escape(c['art'])}\b[^.]{{0,160}}?"
                          r"(?:se\s+(?:modific|complet|abrog|substitu|exclud)|va\s+avea|sintagma|cuv[âi]ntul|cuvintele|textul)")
        for x in amendments_of(con, a, 60):
            if x["rel"] != "modifica" or x["id"] in seen:
                continue
            # Omnibus laws name the amended act once ("Art. II. – Codul vamal ...") and list changes
            # under it, so the target of a clause is the last act named before that clause.
            current = None
            for row in con.execute("SELECT * FROM chunks WHERE version_id=? ORDER BY ord", (x["current_version"] or 0,)):
                refs = sorted(find_refs(row["text"]), key=lambda r: r[3])
                m = name.search(row["text"])
                if m:
                    prior = [r for r in refs if r[3] < m.start()]
                    target = prior[-1] if prior else current
                    if target and (target[1], target[2]) == (a["num"], a["year"]):
                        seen.add(x["id"])  # one clause per amending law
                        out.append((x, dict(row, _target_art=c["art"]), a))
                        break
                if refs:
                    current = refs[-1]
    # Newest first: the latest wording is the one most likely to be in force.
    out.sort(key=lambda t: t[0]["effective"] or t[0]["published"] or "", reverse=True)
    return out[:limit]


def rank_candidates(con, query, scope_ids=None, pinned_ids=frozenset()):
    """Fragments for the pick step, from all of the legislation. The chosen branch is a relevance bonus,
    not a filter, so an act filed under another domain (or none) still competes on its merits.
    Returns (shortlist, widened): widened means nothing in the branch matched at all."""
    scope = set(scope_ids or ())
    found = {c["id"]: c for c in search_chunks(con, query, None, GLOBAL_DEPTH)}
    if scope_ids:
        # The branch is also searched on its own, so its weaker matches are not lost to the global cutoff.
        found.update((c["id"], c) for c in search_chunks(con, query, list(scope), 60))
    acts = {a["id"]: a["status"] for a in con.execute(
        f"SELECT id, status FROM acts WHERE id IN ({','.join(str(c['act_id']) for c in found.values()) or 0})")}
    live = [c for c in found.values() if acts.get(c["act_id"]) != "abrogat"]

    def boosted(c):  # bm25 is negative, lower is better, so a factor above 1 ranks a fragment higher
        f = PINNED_BOOST if c["act_id"] in pinned_ids else DOMAIN_BOOST if c["act_id"] in scope else 1.0
        return c["score"] * f
    per_act: dict[int, int] = {}
    shortlist = []
    for c in sorted(live, key=boosted):
        per_act[c["act_id"]] = per_act.get(c["act_id"], 0) + 1
        if per_act[c["act_id"]] <= (PER_KEY_ACT if c["act_id"] in pinned_ids else PER_ACT):
            shortlist.append(c)
    widened = scope_ids is not None and not any(c["act_id"] in scope for c in shortlist)
    return shortlist, widened


def _route(model, question, history):
    ctx = ""
    if history:
        ctx = "Discuție anterioară:\n" + "\n".join(f"- {h['content'][:300]}" for h in history if h["role"] == "user") + "\n\n"
    schema = dict(type="object", required=["domenii", "termeni", "motiv"], properties=dict(
        domenii=dict(type="array", minItems=1, maxItems=2, items=dict(type="string", enum=list(tree.DOMAINS))),
        termeni=dict(type="array", items=dict(type="string")),
        motiv=dict(type="string")))
    res = runtime.json_chat(model, ROUTER_SYSTEM.format(tree=tree.tree_for_prompt()), f"{ctx}Întrebare: {question}", schema)
    doms, terms, why = [], [], ""
    if isinstance(res, dict):
        raw = res.get("domenii") or []
        doms = list(dict.fromkeys(d for d in raw if isinstance(d, str) and d in tree.DOMAINS))[:2]
        terms = [t for t in (res.get("termeni") or []) if isinstance(t, str)][:15]
        why = res.get("motiv") if isinstance(res.get("motiv"), str) else ""
    fallback = not doms
    if fallback:
        doms = tree.keyword_route(question)
    return doms, terms, why, fallback


def _pick(model, question, candidates, n=8):
    if len(candidates) <= n:
        return candidates
    candidates = candidates[:PICK_POOL]
    lines = []
    for i, c in enumerate(candidates, 1):
        snippet = re.sub(r"\s+", " ", c["text"])[:240]
        lines.append(f"{i}. [{c['act_title'][:50]} | art. {c['art'] or '-'} | {(c['path'] or '')[:70]}] {snippet}")
    schema = dict(type="object", required=["alese"], properties=dict(
        alese=dict(type="array", maxItems=n, items=dict(type="integer"))))
    res = runtime.json_chat(model, PICK_SYSTEM.format(n=n), f"Întrebare: {question}\n\nCandidați:\n" + "\n".join(lines), schema, 300)
    idx = []
    if isinstance(res, dict) and isinstance(res.get("alese"), list):
        idx = [i for i in res["alese"] if isinstance(i, int) and 1 <= i <= len(candidates)]
    return [candidates[i - 1] for i in dict.fromkeys(idx)][:n] or candidates[:n]


def answer(con, question: str, history: list[dict]):
    """Generator of UI events. Each tree level is reported as it is walked."""
    # Background tagging stops while this runs, so the model is free for the question.
    with runtime.lock:
        runtime.asking += 1
    try:
        yield from _answer(con, question, history)
    finally:
        with runtime.lock:
            runtime.asking -= 1


def _answer(con, question: str, history: list[dict]):
    model = db.get_setting(con, "active_model")
    if runtime.state != "gata" or not model or model not in [m["name"] for m in runtime.installed()]:
        yield dict(type="error", message="Modelul AI nu este gata încă. Verificați secțiunea Modele. Puteți folosi Căutarea între timp.")
        return

    # Level 1-2: branch and domain.
    yield dict(type="step", id="route", label="Aleg ramura de drept", status="active")
    doms, terms, why, fallback = _route(model, question, history)
    if doms:
        names = [f"{tree.DOMAINS[d]['group_name']} › {tree.DOMAINS[d]['name']}" for d in doms]
        yield dict(type="step", id="route", label="Ramura de drept", detail="; ".join(names), note=why, status="done",
                   path=names)
    else:
        yield dict(type="step", id="route", label="Ramura de drept", detail="Nicio ramură clară, caut în toată legislația",
                   status="done")

    # Level 3: acts. The branch gives a bonus; the whole legislation competes.
    yield dict(type="step", id="acts", label="Selectez actele relevante", status="active")
    query = fts_query(question, terms)
    acts_in_scope = None
    selected_pinned = []
    if doms:
        q = ",".join("?" * len(doms))
        ids = [r["act_id"] for r in con.execute(
            f"""SELECT d.act_id FROM act_domain d JOIN acts a ON a.id=d.act_id
                WHERE d.domain IN ({q}) AND a.status<>'abrogat'""", doms)]
        acts_in_scope = ids
        selected_pinned = con.execute(
            f"""SELECT a.* FROM acts a JOIN act_domain d ON d.act_id=a.id
                WHERE d.domain IN ({q}) AND a.pinned=1 AND a.status<>'abrogat'""", doms).fetchall()
        # Amendments to the key acts belong to the branch even if their title does not say so.
        for p in selected_pinned:
            acts_in_scope.extend(r["id"] for r in amendments_of(con, p, 200))
        acts_in_scope = list(set(acts_in_scope))
    pinned_ids = {p["id"] for p in selected_pinned}
    shortlist, widened = rank_candidates(con, query, acts_in_scope, pinned_ids)
    hit_ids = list(dict.fromkeys(c["act_id"] for c in shortlist))
    act_rows = {a["id"]: a for a in con.execute(
        f"SELECT * FROM acts WHERE id IN ({','.join(map(str, hit_ids)) or 0})")}
    act_names = [_ref(act_rows[i]) + " · " + act_rows[i]["title"][:70] for i in hit_ids[:5]]
    missing_key = [p for p in selected_pinned if not p["has_text"]]
    scanned = len(acts_in_scope) if acts_in_scope is not None else None
    detail = "Toată legislația"
    if scanned is not None:
        outside = len({c["act_id"] for c in shortlist[:PICK_POOL]} - set(acts_in_scope))
        detail = (f"Toată legislația, cu prioritate pentru {scanned} acte din ramură"
                  + (f"; {outside} acte relevante din afara ramurii" if outside else ""))
    if widened:
        detail = "Nimic în ramura aleasă, am extins căutarea"
    yield dict(type="step", id="acts", label="Actele relevante", detail=detail, items=act_names, status="done")

    # Level 4: articles.
    yield dict(type="step", id="articles", label="Parcurg articolele", status="active")
    chosen = _pick(model, question, shortlist) if shortlist else []
    arts = [f"{_ref(act_rows[c['act_id']])}{', art. ' + c['art'] if c['art'] else ''}" for c in chosen]
    yield dict(type="step", id="articles", label="Articolele găsite",
               detail=f"{len(chosen)} din {min(len(shortlist), PICK_POOL)} candidate", items=arts, status="done")

    warnings = []
    for p in missing_key:
        link = "" if not p["legis_url"] else f" Textul: {p['legis_url']}"
        warnings.append(f"{p['title']} nu este încă indexat cu text integral, deci răspunsul nu îl acoperă.{link}")

    if not chosen:
        yield dict(type="warnings", items=warnings)
        msg = ("Nu am găsit în indexul actual fragmente de lege care să răspundă la întrebare, deci nu pot da un răspuns "
               "fundamentat. Indexarea poate fi încă în curs: verificați secțiunea Index sau reformulați întrebarea.")
        yield dict(type="token", text=msg)
        yield dict(type="done", answer=msg, sources=[], warnings=warnings, future=[])
        return

    # Sources, with status and everything the reader needs to verify them.
    sources = []
    context = []
    for n, c in enumerate(chosen, 1):
        a = act_rows[c["act_id"]]
        am = amendments_of(con, a, 8)
        later = [x for x in am if x["published"] and (not a["published"] or x["published"] > a["published"])]
        flag = "text consolidat" if a["is_consolidated"] else "text inițial, neconsolidat"
        v = con.execute("SELECT effective, label FROM versions WHERE id=?", (c["version_id"],)).fetchone()
        src = dict(_act_short(a), n=n, art=c["art"], path=c["path"], excerpt=c["text"][:1500], chunk_id=c["id"],
                   version_effective=v["effective"] if v else None, version_label=v["label"] if v else None,
                   amended_by=[dict(ref=_ref(x), title=x["title"], id=x["id"], effective=x["effective"]) for x in later[:5]])
        sources.append(src)
        mods = "; ".join(f"{_ref(x)} ({x['effective'] or x['published']})" for x in later[:4])
        context.append(
            f"[S{n}] {_ref(a)} — {a['title']}{', art. ' + c['art'] if c['art'] else ''}\n"
            f"Stare: {STATUS_RO.get(a['status'])}; {flag}; versiune din {v['effective'] if v else 'necunoscut'}"
            + (f"\nModificat ulterior prin: {mods}" if mods else "") + f"\nText:\n{c['text'][:1900]}")

    # Published texts are not consolidated, so add the amendment clauses naming the chosen articles.
    now = today()
    for x, c, a in amendment_clauses(con, chosen, act_rows):
        n = len(sources) + 1
        when = x["effective"] or x["published"]
        pending = bool(when and when > now) or bool(x["future_effective"] and x["future_effective"] > now)
        sources.append(dict(_act_short(x), n=n, art=c["art"], path=c["path"], excerpt=c["text"][:1500], chunk_id=c["id"],
                            version_effective=when, version_label="Act de modificare", amended_by=[],
                            amends=f"{_ref(a)}, art. {c['_target_art']}"))
        context.append(
            f"[S{n}] ACT DE MODIFICARE: {_ref(x)} modifică {_ref(a)}, art. {c['_target_art']}\n"
            + (f"NEÎN VIGOARE ÎNCĂ, cel puțin parțial: verifică datele din act (următoarea: {x['future_effective'] or when})\n" if pending
               else f"În vigoare din {when}\n")
            + f"Text:\n{c['text'][:1900]}")

    # Future versions and scheduled changes for the acts in play.
    future = []
    for aid in {c["act_id"] for c in chosen} | pinned_ids:
        a = act_rows.get(aid) or con.execute("SELECT * FROM acts WHERE id=?", (aid,)).fetchone()
        for x in amendments_of(con, a):
            when = x["effective"] if x["effective"] and x["effective"] > now else x["future_effective"]
            if when and when > now:
                future.append(dict(ref=_ref(x), title=x["title"], effective=when, target=_ref(a), id=x["id"], rel=x["rel"]))
        for v in con.execute("SELECT * FROM versions WHERE act_id=? AND effective>?", (aid, now)):
            future.append(dict(ref=_ref(a), title=f"Versiune viitoare: {a['title']}", effective=v["effective"], target=_ref(a), id=a["id"], rel="versiune"))
    future.sort(key=lambda f: f["effective"])
    fut_text = ""
    if future:
        fut_text = "\n\nMODIFICĂRI ANUNȚATE (intră în vigoare în viitor):\n" + "\n".join(
            f"- {f['ref']} ({f['title'][:100]}), de la {f['effective']}, privește {f['target']}" for f in future[:8])
    for c in search_chunks(con, query, list({c["act_id"] for c in chosen} | pinned_ids), 4, future=True):
        v = con.execute("SELECT effective FROM versions WHERE id=?", (c["version_id"],)).fetchone()
        eff = v["effective"] if v else None
        if eff and eff > now:  # other non-current versions are older texts, not future law
            fut_text += f"\n\nNEÎN VIGOARE ÎNCĂ (de la {eff}): {c['act_title']}, art. {c['art']}\n{c['text'][:1000]}"

    if any(not s["consolidated"] for s in sources):
        warnings.append("Unele surse sunt textul publicat inițial în Monitorul Oficial, fără modificările ulterioare integrate. "
                        "Verificați redacția curentă pe legis.md sau importați PDF-ul consolidat în secțiunea Index.")
    yield dict(type="sources", items=sources)
    yield dict(type="warnings", items=warnings)
    yield dict(type="future", items=future[:8])
    yield dict(type="step", id="answer", label="Formulez răspunsul", status="active")

    msgs = [dict(role="system", content=ANSWER_SYSTEM.format(today=date.today().strftime("%d.%m.%Y")))]
    for h in history[-4:]:
        msgs.append(dict(role=h["role"], content=h["content"][:1500]))
    msgs.append(dict(role="user", content=f"SURSE:\n\n" + "\n\n---\n\n".join(context) + fut_text + f"\n\nÎNTREBARE: {question}"))
    full, thinking = [], []
    started = False
    try:
        for kind, text in runtime.stream_chat(model, msgs):
            if kind == "thinking":
                thinking.append(text)
                yield dict(type="thinking", text=text)
            elif kind == "budget":
                yield dict(type="budget", thinking=int(text))
            elif kind == "retry":  # reasoning used its budget; the answer pass builds on it
                full.clear()
                yield dict(type="step", id="answer", label="Formulez răspunsul", status="active",
                           note="Raționamentul a atins limita; formulez răspunsul pe baza lui.")
                yield dict(type="retry")
            else:
                if not started:
                    yield dict(type="step", id="answer", label="Răspuns", status="done")
                    started = True
                full.append(text)
                yield dict(type="token", text=text)
    except Exception as e:
        yield dict(type="error", message=f"Eroare la generare: {e}")
        return
    final = "".join(full)
    if not final.strip():
        yield dict(type="error", message="Modelul nu a formulat un răspuns. Încercați din nou sau reformulați întrebarea.")
        return
    bad =sorted({int(x) for x in re.findall(r"\[S(\d+)\]", final)} - {s["n"] for s in sources})
    if bad:
        warnings.append("Răspunsul conține trimiteri care nu corespund unei surse: " + ", ".join(f"S{b}" for b in bad) + ". Tratați acele afirmații ca neverificate.")
    if not re.search(r"\[S\d+\]", final):
        warnings.append("Răspunsul nu conține trimiteri la surse, deci nu poate fi verificat. Tratați-l cu prudență.")
    yield dict(type="done", answer=final, sources=sources, warnings=warnings, future=future[:8], thinking="".join(thinking))

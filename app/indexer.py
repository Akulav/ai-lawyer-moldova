"""Background indexer: seeds the tree, walks the gazette, stores acts, derives legal status."""
import logging
import re
import sqlite3
import threading
import time

import httpx

from . import db, gazette, tree
from .config import GAZETTE, USER_AGENT, today
from .text import can_repeal, effective_dates, extract_relations, fold, kind_of, parse_date, rel_cls, split_articles, stems

log = logging.getLogger("indexer")


# ---- storing acts -----------------------------------------------------------------------

# A number with a second numeric part, "39/236", "121-b-117". Decisions of one body on one day share the first
# part, so it alone would merge them into one act. "355-XVI" and "174-d" have no second part and keep the
# plain number, which is how laws are cited and how the key acts are seeded.
_COMPOUND = re.compile(r"^\d+\D+\d")


def act_slug(kind: str, num, adopted, fallback: str = "", number: str | None = None) -> str:
    ident = re.sub(r"[^0-9a-z]+", "-", fold(number)).strip("-") if number and _COMPOUND.match(number) else num
    return f"{rel_cls(kind)}-{ident if ident is not None else 'x'}-{(adopted or fallback).replace('-', '')}"


def _plain_title(title: str) -> str:
    return re.sub(r"\s+al republicii moldova$", "", fold(title).strip(" ."))


def key_act_for(con, kind: str, title: str):
    """The key act an unnumbered code or law entry stands for. Issue 1148 lists "Codul muncii al Republicii
    Moldova" with no number or date, which made it a separate act and left the key act Codul muncii empty."""
    if kind not in ("cod", "lege"):
        return None
    plain = _plain_title(title)
    for p in con.execute("SELECT id, title FROM acts WHERE pinned=1 AND kind=?", (kind,)):
        if _plain_title(p["title"]) == plain:
            return p["id"]
    return None


def upsert_act(con, a: dict) -> int:
    if a.get("num") is None and (key := key_act_for(con, a["kind"], a["title"])):
        con.execute("UPDATE acts SET mo_issue=COALESCE(mo_issue,?), mo_art=COALESCE(mo_art,?), mo_ref=COALESCE(mo_ref,?), "
                    "url=COALESCE(url,?), published=COALESCE(published,?) WHERE id=?",
                    (a.get("mo_issue"), a.get("mo_art"), a.get("mo_ref"), a.get("url"), a.get("published"), key))
        return key
    slug = act_slug(a["kind"], a.get("num"), a.get("adopted"), a.get("fallback", ""), a.get("number"))
    row = con.execute("SELECT id FROM acts WHERE slug=?", (slug,)).fetchone()
    if row:
        con.execute(
            """UPDATE acts SET published=COALESCE(published,?), mo_issue=COALESCE(mo_issue,?), mo_art=COALESCE(mo_art,?),
               mo_ref=COALESCE(mo_ref,?), url=COALESCE(url,?) WHERE id=?""",
            (a.get("published"), a.get("mo_issue"), a.get("mo_art"), a.get("mo_ref"), a.get("url"), row["id"]))
        return row["id"]
    cur = con.execute(
        """INSERT INTO acts(slug,kind,number,num,year,title,adopted,published,mo_issue,mo_art,mo_ref,url,legis_url,pinned)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (slug, a["kind"], a.get("number"), a.get("num"), a.get("year"), a["title"], a.get("adopted"), a.get("published"),
         a.get("mo_issue"), a.get("mo_art"), a.get("mo_ref"), a.get("url"), a.get("legis_url"), a.get("pinned", 0)))
    act_id = cur.lastrowid
    con.execute("INSERT INTO acts_fts(rowid,stems) VALUES(?,?)", (act_id, stems(a["title"])))
    for d in tree.classify(a["title"]):
        con.execute("INSERT OR IGNORE INTO act_domain(act_id,domain) VALUES(?,?)", (act_id, d))
    return act_id


def fix_adopted(adopted: str | None, published: str | None) -> str | None:
    """Repair a mistyped year in the gazette's listing (1017, 3000, or a date after publication).
    Keeps day and month and takes the publication year, or the year before if that would still be too late."""
    if not adopted or not published or (adopted <= published and adopted[:4] >= "1900"):
        return None
    year = int(published[:4]) - (adopted[5:] > published[5:])
    return f"{year}{adopted[4:]}"


def repair_adopted(con, where: str = "", params=()) -> None:
    """Rewrite adopted/year for acts whose adoption date is impossible. The slug stays, so upserts still match."""
    for r in con.execute(f"SELECT id, adopted, published FROM acts WHERE adopted IS NOT NULL AND published IS NOT NULL {where}",
                         params).fetchall():
        fixed = fix_adopted(r["adopted"], r["published"])
        if fixed:
            con.execute("UPDATE acts SET adopted=?, year=? WHERE id=?", (fixed, int(fixed[:4]), r["id"]))


def seed_tree(con) -> None:
    """Pre-create the key acts so the tree has its branches even before the crawler reaches them."""
    for dom, kind, num, adopted, title, doc_id in tree.pinned_acts():
        legis = f"https://www.legis.md/cautare/getResults?doc_id={doc_id}&lang=ro" if doc_id else None
        aid = upsert_act(con, dict(kind=kind, number=str(num), num=num, year=int(adopted[:4]), title=title,
                                   adopted=adopted, legis_url=legis, pinned=1))
        con.execute("UPDATE acts SET pinned=1, legis_url=COALESCE(legis_url,?) WHERE id=?", (legis, aid))
        con.execute("INSERT OR IGNORE INTO act_domain(act_id,domain) VALUES(?,?)", (aid, dom))
    con.commit()


def store_version(con, act_id, body, *, effective, source, url=None, label=None, consolidated=False):
    """Save one version's text. A consolidated text replaces the published one as the current version."""
    con.execute("INSERT OR IGNORE INTO versions(act_id,label,effective,source,url,consolidated,added) VALUES(?,?,?,?,?,?,?)",
                (act_id, label, effective, source, url, int(consolidated), today()))
    vid = con.execute("SELECT id FROM versions WHERE act_id=? AND effective IS ? AND source=?",
                      (act_id, effective, source)).fetchone()["id"]
    old = [r["id"] for r in con.execute("SELECT id FROM chunks WHERE version_id=?", (vid,))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in old])
    con.execute("DELETE FROM chunks WHERE version_id=?", (vid,))
    title = con.execute("SELECT title FROM acts WHERE id=?", (act_id,)).fetchone()["title"]
    for n, c in enumerate(split_articles(body)):
        cur = con.execute("INSERT INTO chunks(act_id,version_id,ord,path,art,text) VALUES(?,?,?,?,?,?)",
                          (act_id, vid, n, c["path"], c["art"], c["text"]))
        art = f"art {c['art']}" if c["art"] else ""
        # Titles are weighted in by repeating them with the chunk's heading path.
        con.execute("INSERT INTO chunks_fts(rowid,stems) VALUES(?,?)",
                    (cur.lastrowid, stems(f"{title} {c['path']} {art} {c['text']}")))
    refresh_current_version(con, act_id)
    return vid


def refresh_current_version(con, act_id) -> None:
    """Current = newest version in force today. Consolidated texts beat a same-age published text."""
    row = con.execute(
        """SELECT id, consolidated FROM versions WHERE act_id=? AND (effective IS NULL OR effective<=?)
           AND EXISTS(SELECT 1 FROM chunks c WHERE c.version_id=versions.id)
           ORDER BY consolidated DESC, COALESCE(effective,'') DESC, id DESC LIMIT 1""", (act_id, today())).fetchone()
    if row:
        con.execute("UPDATE acts SET current_version=?, has_text=1, is_consolidated=? WHERE id=?",
                    (row["id"], row["consolidated"], act_id))
    else:
        # Only a future text is left. A version with no chunks does not count: it made has_text 1 for an act
        # with no text, which the health check flagged and its repair, calling this again, could never fix.
        has_future = con.execute("""SELECT 1 FROM versions v WHERE v.act_id=?
                                    AND EXISTS(SELECT 1 FROM chunks c WHERE c.version_id=v.id)""", (act_id,)).fetchone()
        con.execute("UPDATE acts SET has_text=? WHERE id=?", (1 if has_future else 0, act_id))


def recompute_status(con) -> None:
    """Derive 'abrogat' / 'abrogare viitoare' and the replacement act from repeal relations."""
    con.execute("UPDATE acts SET status='presupus_in_vigoare', status_note=NULL, status_date=NULL, replaced_by=NULL")
    rows = con.execute(
        """SELECT r.*, a.title AS from_title, a.number AS from_number, a.year AS from_year, a.kind AS from_kind,
                  a.published AS from_published
           FROM relations r JOIN acts a ON a.id=r.from_act WHERE r.rel='abroga' AND r.whole=1
           ORDER BY COALESCE(r.effective, a.published, '')""").fetchall()
    now = today()
    for r in rows:
        kinds = ("lege", "cod") if r["to_cls"] == "lege" else (r["to_cls"],)
        q = ",".join("?" * len(kinds))
        for t in con.execute(f"SELECT id, pinned FROM acts WHERE num=? AND year=? AND kind IN ({q}) AND id<>?",
                             (r["to_num"], r["to_year"], *kinds, r["from_act"])).fetchall():
            if not can_repeal(r["from_kind"], r["from_title"] or "", r["to_cls"], bool(t["pinned"])):
                continue
            when = r["effective"] or r["from_published"]
            future = bool(when and when > now)
            title = (r["from_title"] or "").lower()
            replaces = not any(w in title for w in ("modific", "abrog", "complet"))
            con.execute(
                "UPDATE acts SET status=?, status_note=?, status_date=?, replaced_by=? WHERE id=?",
                ("abrogare_viitoare" if future else "abrogat",
                 f"Detectat automat din {r['from_title']} (nr. {r['from_number']}/{r['from_year']})",
                 when, r["from_act"] if replaces else None, t["id"]))
    con.commit()


def rebuild_repeals(con) -> None:
    """Re-derive repeal links from stored text, after detection rules change. Downloads nothing."""
    con.execute("DELETE FROM relations WHERE rel='abroga'")
    for a in con.execute("""SELECT id, title, kind, num, year, published FROM acts WHERE has_text=1 AND id IN
                            (SELECT act_id FROM chunks WHERE text LIKE '%abrog%')""").fetchall():
        body = "\n".join(r["text"] for r in con.execute("SELECT text FROM chunks WHERE act_id=? ORDER BY version_id, ord", (a["id"],)))
        eff = effective_dates(body)[0] or a["published"]
        for r in extract_relations(a["title"], body, skip=(rel_cls(a["kind"]), a["num"], a["year"])):
            if r["rel"] == "abroga":
                con.execute("""INSERT OR IGNORE INTO relations(from_act,to_cls,to_num,to_year,rel,whole,effective,evidence)
                               VALUES(?,?,?,?,?,?,?,?)""",
                            (a["id"], r["to_cls"], r["to_num"], r["to_year"], "abroga", r["whole"], eff, r["evidence"]))
    recompute_status(con)


# Older issues' tables of contents have no dot leaders, and before they were recognised an act's stored text
# could be its own table-of-contents line, e.g. "709. Legea cetățeniei ... (nr. 1024-XIV, 2 iunie 2000).".
_TOC_SLICE = re.compile(r"(?:^|\n)\s*\d{1,4}\.\s[^\n]{3,300}\(nr\.[^)]{0,40}\d{4}\)")


def legacy_font_issues(con) -> list[int]:
    """Issues whose stored gazette text still has the legacy font's punctuation for diacritics ("Hot=r]re")."""
    out = set()
    for r in con.execute("""SELECT a.mo_issue, c.text FROM acts a JOIN versions v ON v.act_id=a.id AND v.source='monitorul'
                            JOIN chunks c ON c.version_id=v.id AND c.ord=0 WHERE a.mo_issue IS NOT NULL"""):
        if r["mo_issue"] not in out and gazette.fix_legacy_font(r["text"]) != r["text"]:
            out.add(r["mo_issue"])
    return sorted(out)


def toc_damaged_issues(con) -> list[int]:
    """Issues where at least two acts' gazette text starts with a table-of-contents entry."""
    hits: dict[int, int] = {}
    for r in con.execute("""SELECT a.mo_issue, c.text FROM acts a JOIN versions v ON v.act_id=a.id AND v.source='monitorul'
                            JOIN chunks c ON c.version_id=v.id AND c.ord=0 WHERE a.mo_issue IS NOT NULL"""):
        if _TOC_SLICE.search(r["text"][:700]):
            hits[r["mo_issue"]] = hits.get(r["mo_issue"], 0) + 1
    return sorted(i for i, n in hits.items() if n >= 2)


def purge_issue_text(con, issue_id: int) -> None:
    """Drop the gazette text stored for an issue's acts, and the links derived from it, so it can be read again.
    Re-reading alone would not do: a corrected text can get another effective date and be added as a
    second version beside the bad one."""
    url = f"{GAZETTE}/ro/monitor/{issue_id}"
    act_ids = [r["id"] for r in con.execute("SELECT id FROM acts WHERE mo_issue=?", (issue_id,))]
    for vid in [r["id"] for r in con.execute(
            f"SELECT id FROM versions WHERE source='monitorul' AND url=? AND act_id IN ({','.join(map(str, act_ids)) or 0})", (url,))]:
        con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(r["id"],) for r in con.execute("SELECT id FROM chunks WHERE version_id=?", (vid,))])
        con.execute("DELETE FROM chunks WHERE version_id=?", (vid,))
        con.execute("DELETE FROM versions WHERE id=?", (vid,))
    con.executemany("DELETE FROM relations WHERE from_act=?", [(a,) for a in act_ids])
    for a in act_ids:
        refresh_current_version(con, a)


def delete_act(con, act_id: int, force: bool = False) -> bool:
    """Remove an act created from a misread listing entry, with everything derived from it. Key acts and acts
    holding an imported text are kept, unless `force` (after a merge moved them away). Returns whether it was removed."""
    a = con.execute("SELECT pinned FROM acts WHERE id=?", (act_id,)).fetchone()
    if not a or not force and (a["pinned"] or con.execute(
            "SELECT 1 FROM versions WHERE act_id=? AND source LIKE 'import:%'", (act_id,)).fetchone()):
        return False
    chunk_ids = [(r["id"],) for r in con.execute("SELECT id FROM chunks WHERE act_id=?", (act_id,))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", chunk_ids)
    for table, col in (("chunks", "act_id"), ("versions", "act_id"), ("relations", "from_act"),
                       ("act_domain", "act_id"), ("ai_tags", "act_id")):
        con.execute(f"DELETE FROM {table} WHERE {col}=?", (act_id,))
    con.execute("DELETE FROM acts_fts WHERE rowid=?", (act_id,))
    con.execute("DELETE FROM acts WHERE id=?", (act_id,))
    return True


def merge_act(con, keep: int, drop: int) -> None:
    """Move what `drop` holds (texts, links, issue reference) onto `keep`, then remove `drop`."""
    for (vid,) in con.execute("SELECT id FROM versions WHERE act_id=?", (drop,)).fetchall():
        try:
            con.execute("UPDATE versions SET act_id=? WHERE id=?", (keep, vid))
            con.execute("UPDATE chunks SET act_id=? WHERE version_id=?", (keep, vid))
        except sqlite3.IntegrityError:
            pass  # `keep` already has this version; drop's copy goes with drop below
    con.execute("UPDATE OR IGNORE relations SET from_act=? WHERE from_act=?", (keep, drop))
    con.execute("UPDATE OR IGNORE act_domain SET act_id=? WHERE act_id=?", (keep, drop))
    d = con.execute("SELECT mo_issue, mo_art, mo_ref, url, published FROM acts WHERE id=?", (drop,)).fetchone()
    con.execute("UPDATE acts SET mo_issue=COALESCE(mo_issue,?), mo_art=COALESCE(mo_art,?), mo_ref=COALESCE(mo_ref,?), "
                "url=COALESCE(url,?), published=COALESCE(published,?) WHERE id=?", (*d, keep))
    con.execute("UPDATE acts SET pinned=0 WHERE id=?", (drop,))
    delete_act(con, drop, force=True)
    refresh_current_version(con, keep)


def merge_duplicates(con) -> int:
    """Join acts stored twice: a key act and its unnumbered gazette entry, or one act under two identities
    (number "1272-11", the Roman II read as digits, next to "1272"). Keeps the key act, else the one with text."""
    merged = 0
    for p in con.execute("SELECT id, kind, title FROM acts WHERE pinned=1").fetchall():
        for d in con.execute("SELECT id, title FROM acts WHERE kind=? AND num IS NULL AND pinned=0", (p["kind"],)).fetchall():
            if _plain_title(d["title"]) == _plain_title(p["title"]):
                merge_act(con, p["id"], d["id"])
                merged += 1
    for g in con.execute("""SELECT group_concat(id) ids FROM acts WHERE number IS NOT NULL AND adopted IS NOT NULL
                            GROUP BY kind, number, adopted HAVING COUNT(*) > 1""").fetchall():
        ids = [int(i) for i in g["ids"].split(",")]
        ranked = sorted(ids, key=lambda i: tuple(con.execute(
            "SELECT pinned, has_text, (SELECT COUNT(*) FROM chunks WHERE act_id=acts.id) FROM acts WHERE id=?", (i,)).fetchone()),
            reverse=True)
        for drop in ranked[1:]:
            merge_act(con, ranked[0], drop)
            merged += 1
    return merged


def rekind_acts(con) -> int:
    """Give acts stored as "alt" the type their title shows now that more forms are recognised. The identity
    includes the type, so it changes too, and an act already stored under the new identity absorbs this one."""
    fixed = 0
    for a in con.execute("SELECT id, title, num, adopted, number, mo_issue, mo_art, has_text FROM acts WHERE kind='alt'").fetchall():
        kind = kind_of(a["title"])
        if kind == "alt":
            continue
        slug = act_slug(kind, a["num"], a["adopted"], f"{a['mo_issue']}{a['mo_art']}", a["number"])
        other = con.execute("SELECT id FROM acts WHERE slug=? AND id<>?", (slug, a["id"])).fetchone()
        if other:
            merge_act(con, other["id"], a["id"])
        else:
            con.execute("UPDATE acts SET kind=?, slug=? WHERE id=?", (kind, slug, a["id"]))
            con.execute("DELETE FROM act_domain WHERE act_id=? AND source='kw'", (a["id"],))
            con.executemany("INSERT OR IGNORE INTO act_domain(act_id,domain) VALUES(?,?)",
                            [(a["id"], d) for d in tree.classify(a["title"])])
            if not a["has_text"] and a["mo_issue"]:
                # Its title was not located under the old type either; read the issue again.
                con.execute("UPDATE issues SET status='listed' WHERE id=? AND status='done'", (a["mo_issue"],))
        fixed += 1
    return fixed


def relist_issue(con, issue_id: int, parsed: dict) -> bool:
    """Bring an issue's catalogue in line with a fresh reading of its listing. Acts stored under a misread
    entry (a wrong number, or a title run into the next entry) are removed. Returns whether the issue's
    text must be read again, because entries were added or replaced."""
    new = {a["art"]: act_slug(a["kind"], a.get("num"), a.get("adopted"), f"{issue_id}{a['art']}", a.get("number"))
           for a in parsed["acts"] if not gazette.is_noise_act(a["title"])}
    old = {r["mo_art"]: (r["id"], r["slug"]) for r in con.execute(
        "SELECT id, mo_art, slug FROM acts WHERE mo_issue=? AND mo_art IS NOT NULL", (issue_id,))}
    stale = [aid for art, (aid, slug) in old.items() if art in new and new[art] != slug]
    # An act first stored from another issue (a republication, say) is not new here.
    added = [art for art in new if art not in old
             and not con.execute("SELECT 1 FROM acts WHERE slug=?", (new[art],)).fetchone()]
    for aid in stale:
        delete_act(con, aid)
    return bool(stale or added)


def process_issue_text(con, issue_id: int, acts: list[dict], data: bytes, pdf_url: str) -> int:
    text, published = gazette.pdf_text(data)
    # The issue's web page carries its date reliably; the PDF header does not in every year.
    published = con.execute("SELECT published FROM issues WHERE id=?", (issue_id,)).fetchone()["published"] or published
    if len(text.strip()) < 500:
        return -1
    if not acts:
        # The issue's web page lists nothing; its PDF has the same table of contents.
        acts = gazette.toc_entries(text)
        con.execute("UPDATE issues SET n_acts=? WHERE id=?", (len(acts), issue_id))
    bodies = gazette.split_acts(text, acts)
    # Use the listing's numbering for the issue: one row per act, even when no text was located.
    issue_title = con.execute("SELECT title FROM issues WHERE id=?", (issue_id,)).fetchone()["title"]
    if published:
        con.execute("UPDATE issues SET published=? WHERE id=?", (published, issue_id))
    stored = 0
    for a in acts:
        if gazette.is_noise_act(a["title"]):
            continue
        body = bodies.get(a["art"])
        aid = upsert_act(con, dict(a, mo_issue=issue_id, mo_art=a["art"], published=published, fallback=f"{issue_id}{a['art']}",
                                   mo_ref=f"{issue_title}, art. {a['art']}", url=f"{GAZETTE}/ro/monitor/{issue_id}"))
        if not body or len(body) < 80:
            continue
        eff, staged = effective_dates(body)
        when = eff or published or a.get("adopted")
        # The next date something in this act takes effect, whether all of it or a single provision.
        upcoming = sorted(d for d in [eff, *staged] if d and d > today())
        con.execute("UPDATE acts SET future_effective=? WHERE id=?", (upcoming[0] if upcoming else None, aid))
        store_version(con, aid, body, effective=when, source="monitorul", url=f"{GAZETTE}/ro/monitor/{issue_id}",
                      label="Text publicat în Monitorul Oficial")
        self_ref = (rel_cls(a["kind"]), a.get("num"), a.get("year"))
        for r in extract_relations(a["title"], body, skip=self_ref):
            con.execute(
                """INSERT OR IGNORE INTO relations(from_act,to_cls,to_num,to_year,rel,whole,effective,evidence)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (aid, r["to_cls"], r["to_num"], r["to_year"], r["rel"], r["whole"], when, r["evidence"]))
        stored += 1
    repair_adopted(con, "AND mo_issue=?", (issue_id,))
    return stored


# ---- the crawler ------------------------------------------------------------------------

class Crawler:
    """One polite worker thread. All progress is in the database, so restarts resume."""
    LIST_DELAY = 0.4
    PDF_DELAY = 1.0
    MAX_ATTEMPTS = 20  # cycles before an issue is left for good, about 5 days of retries

    def __init__(self):
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.wake = threading.Event()  # set by a manual retry to end the idle wait
        self.thread = None
        self.con = None
        self.retried: set[int] = set()  # issues cleared and re-read this run because their stored text was wrong
        self.stage = "pornire"
        self.current = ""
        self.last_error = None

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="crawler")
            self.thread.start()

    def _release(self):
        """Commit before anything slow. SQLite allows one writer at a time, and an open write transaction held
        through a download, a retry backoff or a pause made the tagger and the API fail with "database is
        locked" once their 30 s wait ran out. Each step's writes are complete before it fetches the next page."""
        if self.con is not None and self.con.in_transaction:
            self.con.commit()

    def _wait(self, seconds: float):
        self._release()
        end = time.time() + seconds
        while time.time() < end and not self.stop.is_set():
            time.sleep(0.1)
        while self.paused.is_set() and not self.stop.is_set():
            self.stage = "pauză"
            time.sleep(0.3)

    def _get(self, client, url):
        self._release()
        delay = 5
        for attempt in range(5):
            try:
                r = client.get(url)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPError(f"HTTP {r.status_code}")
                return r
            except httpx.HTTPError as e:
                self.last_error = f"{url}: {e}"
                if attempt == 4:
                    raise
                self._wait(delay)
                delay *= 2

    def _run(self):
        con = self.con = db.connect()
        seed_tree(con)
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        while not self.stop.is_set():
            try:
                self._discover(con, client)
                self._repair(con, client)
                self._list_stage(con, client)
                self._text_stage(con, client)
                self.stage = "la zi"
                self.current = ""
                self.wake.clear()
                end = time.time() + 6 * 3600  # look for new issues periodically
                while time.time() < end and not self.stop.is_set() and not self.wake.is_set():
                    self._wait(1)
            except Exception as e:  # keep the worker alive through network outages
                log.exception("crawler error")
                con.rollback()  # an unfinished step must not keep the write lock through the wait
                self.last_error = str(e)
                self.stage = "eroare de rețea, reîncerc"
                self._wait(60)

    def _discover(self, con, client):
        self.stage = "caut ediții noi"
        page =self._get(client, f"{GAZETTE}/ro").text
        ids = [int(x) for x in re.findall(r"/ro/monitor/(\d+)", page)]
        top = max(ids) if ids else 0
        con.executemany("INSERT OR IGNORE INTO issues(id) VALUES(?)", [(i,) for i in range(1, top + 1)])
        # Failures are usually temporary (504s), so retry each cycle, about 6 hours apart.
        con.execute("UPDATE issues SET status='listed' WHERE status='error' AND attempts<?", (self.MAX_ATTEMPTS,))
        # One-off: issues processed before short titles such as "Codul vamal" could be located.
        if not db.get_setting(con, "reparsed_short_titles"):
            con.execute("""UPDATE issues SET status='listed' WHERE status='done' AND id IN
                           (SELECT mo_issue FROM acts WHERE has_text=0 AND length(title)<32)""")
            db.set_setting(con, "reparsed_short_titles", "1")
        if db.get_setting(con, "tree_kw") != tree.KW_VERSION:
            for n, a in enumerate(con.execute("SELECT id, title FROM acts").fetchall()):
                con.executemany("INSERT OR IGNORE INTO act_domain(act_id,domain) VALUES(?,?)",
                                [(a["id"], d) for d in tree.classify(a["title"])])
                if n % 2000 == 1999:
                    con.commit()  # about 20 s in one go would keep other writers past their 30 s wait
            db.set_setting(con, "tree_kw", tree.KW_VERSION)
        # One-off: acts stored before mistyped adoption years were repaired.
        if not db.get_setting(con, "repaired_adopted"):
            repair_adopted(con)
            db.set_setting(con, "repaired_adopted", "1")
            recompute_status(con)
        con.commit()
        if db.get_setting(con, "repeal_rules") != "2":
            self.stage = "reverific abrogările"
            rebuild_repeals(con)
            db.set_setting(con, "repeal_rules", "2")

    def _repair(self, con, client):
        """Re-read every processed issue's listing after the parser changed, and fix what it shows. Resumable,
        because it reads a page per issue: progress is saved and continues after a restart."""
        if db.get_setting(con, "listing_version") != gazette.LISTING_VERSION:
            self.stage = "recitesc cuprinsurile"
            start = int(db.get_setting(con, "relist_from", 0))
            ids = [r["id"] for r in con.execute("SELECT id FROM issues WHERE status='done' AND id>? ORDER BY id", (start,))]
            for n, iid in enumerate(ids):
                if self.stop.is_set():
                    return
                self.current = f"ediția #{iid} ({n + 1} din {len(ids)})"
                r = self._get(client, f"{GAZETTE}/ro/monitor/{iid}")
                if r.status_code == 200:
                    parsed = gazette.parse_listing(r.text)
                    if parsed["published"]:
                        con.execute("UPDATE issues SET published=COALESCE(published, ?) WHERE id=?", (parsed["published"], iid))
                        con.execute("UPDATE acts SET published=? WHERE mo_issue=? AND published IS NULL", (parsed["published"], iid))
                    if relist_issue(con, iid, parsed):
                        purge_issue_text(con, iid)
                        con.execute("UPDATE issues SET status='pending' WHERE id=?", (iid,))
                if n % 20 == 0:
                    db.set_setting(con, "relist_from", iid)  # also commits the work so far
                self._wait(self.LIST_DELAY)
            db.set_setting(con, "listing_version", gazette.LISTING_VERSION)
            db.set_setting(con, "relist_from", 0)
            recompute_status(con)
        # Every cycle, a few seconds: issues whose stored text is wrong are cleared and read again. Their acts got
        # table-of-contents lines as text, or text in the legacy font, which search cannot match. Checking each
        # cycle rather than once catches issues processed by older code; each is retried once per run, so one the
        # parser still cannot read is not fetched again and again.
        for find in (toc_damaged_issues, legacy_font_issues):
            for iid in find(con):
                if iid in self.retried:
                    continue
                self.retried.add(iid)
                self.stage = "curăț textele greșite"
                purge_issue_text(con, iid)
                con.execute("UPDATE issues SET status='listed', priority=priority+50000 WHERE id=? AND status='done'", (iid,))
                con.commit()  # one issue at a time, so other writers are not kept waiting
        if rekind_acts(con) + merge_duplicates(con):
            con.commit()
            recompute_status(con)  # repeals are matched by type, number and year

    def _list_stage(self, con, client):
        """Stage 1: read every issue's act list. Fast, and yields the full catalogue of titles."""
        self.stage = "catalog"
        pending = [r["id"] for r in con.execute("SELECT id FROM issues WHERE status='pending' ORDER BY id DESC")]
        for n, iid in enumerate(pending):
            if self.stop.is_set():
                return
            self.current = f"ediția #{iid}"
            r = self._get(client, f"{GAZETTE}/ro/monitor/{iid}")
            if r.status_code == 404:
                con.execute("UPDATE issues SET status='missing' WHERE id=?", (iid,))
            else:
                parsed = gazette.parse_listing(r.text)
                acts = [a for a in parsed["acts"] if not gazette.is_noise_act(a["title"])]
                # Codes first, then laws, then government decisions, then the rest; newer first within a tier.
                pr = 0
                for a in acts:
                    pr = max(pr, {"cod": 3, "lege": 2, "hotarare": 1}.get(a["kind"], 0))
                year = max([a["year"] or 0 for a in acts] or [0])
                if parsed["published"]:
                    con.execute("UPDATE issues SET published=COALESCE(published, ?) WHERE id=?", (parsed["published"], iid))
                con.execute("UPDATE issues SET status='listed', title=?, n_acts=?, priority=? WHERE id=?",
                            (parsed["title"], len(acts), pr * 10000 + year, iid))
                for a in acts:
                    upsert_act(con, dict(a, mo_issue=iid, mo_art=a["art"], fallback=f"{iid}{a['art']}",
                                         mo_ref=f"{parsed['title']}, art. {a['art']}", url=f"{GAZETTE}/ro/monitor/{iid}"))
            if n % 10 == 0:
                con.commit()
            self._wait(self.LIST_DELAY)
        con.commit()

    def _text_stage(self, con, client):
        """Stage 2: download PDFs by priority and index the full text."""
        self.stage = "text integral"
        since_status = 0
        while not self.stop.is_set():
            row = con.execute(
                "SELECT id, title FROM issues WHERE status='listed' ORDER BY priority DESC, id DESC LIMIT 1"
            ).fetchone()
            if not row:
                break
            iid = row["id"]
            self.current = row["title"] or f"#{iid}"
            try:
                listing = self._get(client, f"{GAZETTE}/ro/monitor/{iid}").text
                acts = gazette.parse_listing(listing)["acts"]
                viewer = self._get(client, f"{GAZETTE}/ro/monitorul/view/pdf/{iid}").text
                url = gazette.pdf_url_from_viewer(viewer, GAZETTE)
                if not url:
                    con.execute("UPDATE issues SET status='no_text', error=NULL WHERE id=?", (iid,))
                    con.commit()
                    continue
                r = self._get(client, url)
                if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                    con.execute("UPDATE issues SET status='no_text', error=? WHERE id=?", (f"HTTP {r.status_code}", iid))
                    con.commit()
                    continue
                n = process_issue_text(con, iid, acts, r.content, url)
                con.execute("UPDATE issues SET status=?, error=NULL WHERE id=?", ("done" if n >= 0 else "no_text", iid))
                con.commit()
                self.last_error = None
                since_status += 1
                if since_status >= 25:
                    recompute_status(con)
                    since_status = 0
            except Exception as e:
                log.warning("issue %s failed: %s", iid, e)
                self.last_error = f"Ediția {iid}: {e}"
                con.rollback()
                # Set aside so one bad issue cannot block the queue. _discover puts it back next cycle.
                con.execute("UPDATE issues SET status='error', attempts=attempts+1, error=? WHERE id=?", (str(e)[:300], iid))
                con.commit()
            self._wait(self.PDF_DELAY)
        recompute_status(con)


crawler = Crawler()


def index_status(con) -> dict:
    issues = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM issues GROUP BY status")}
    one = lambda q: con.execute(q).fetchone()[0]
    span = con.execute("SELECT MIN(year), MAX(year) FROM acts WHERE has_text=1").fetchone()
    return dict(
        stage=crawler.stage, current=crawler.current, paused=crawler.paused.is_set(), last_error=crawler.last_error,
        issues=issues, retrying=one("SELECT COUNT(*) FROM issues WHERE status='listed' AND error IS NOT NULL"),
        issues_total=sum(v for k, v in issues.items() if k != "missing"),
        issues_done=issues.get("done", 0) + issues.get("no_text", 0),
        acts=one("SELECT COUNT(*) FROM acts"), with_text=one("SELECT COUNT(*) FROM acts WHERE has_text=1"),
        consolidated=one("SELECT COUNT(*) FROM acts WHERE is_consolidated=1"),
        abrogated=one("SELECT COUNT(*) FROM acts WHERE status='abrogat'"),
        relations=one("SELECT COUNT(*) FROM relations"), chunks=one("SELECT COUNT(*) FROM chunks"),
        year_min=span[0], year_max=span[1],
    )


# ---- manual import of consolidated PDFs -------------------------------------------------

def import_document(con, filename: str, data: bytes, effective: str | None) -> dict:
    """Import a consolidated text, e.g. a PDF the user saved from legis.md with its Pdf button."""
    if data[:4] == b"%PDF":
        text, _ = gazette.pdf_text(data)
    else:
        text = data.decode("utf-8", "replace")
    if len(text.strip()) < 200:
        raise ValueError("Documentul nu conține text extractibil (posibil scanat).")
    head = text[:4000]
    m = re.search(
        r"\b(LEGE|COD|HOT[ĂA]R[ÂÎ]RE|REGULAMENT|ORDIN|DECRET|DISPOZI[ȚŢT]IE)\b\s*(?:Nr\.?|nr\.?)\s*(\S+)\s+din\s+([\d.\-/]+|\d{1,2}\s+\w+\s+\d{4})\s*\n?\s*(.{3,240})",
        head, re.S)
    if not m:
        raise ValueError("Nu am recunoscut antetul actului (tip, număr, dată). Folosiți butonul Pdf de pe legis.md.")
    adopted = parse_date(m[3])
    n = re.match(r"\d+", m[2])
    num = int(n[0]) if n else None
    title = re.split(r"\n\s*\n|Publicat|MODIFICAT", m[4])[0]
    title = re.sub(r"\s+", " ", title).strip() or filename
    kind = kind_of(m[1])
    pub = re.search(r"Publicat\s*:?\s*([\d\-.]+)\s+[îi]n\s+Monitorul Oficial\s+(Nr\.?\s*[\w\-]+\s*art\.?\s*\d+)", head)
    aid = upsert_act(con, dict(kind=kind, number=m[2], num=num, year=int(adopted[:4]) if adopted else None,
                               title=title, adopted=adopted, published=parse_date(pub[1]) if pub else None,
                               mo_ref=f"Monitorul Oficial {pub[2]}" if pub else None, fallback=filename))
    eff = effective or today()
    store_version(con, aid, text, effective=eff, source=f"import:{filename}", label="Text consolidat importat", consolidated=True)
    con.commit()
    row = con.execute("SELECT title, number, year FROM acts WHERE id=?", (aid,)).fetchone()
    return dict(act_id=aid, title=row["title"], number=row["number"], year=row["year"], effective=eff, future=eff > today())

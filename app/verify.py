"""Quick health check of the whole index: tree, issues, texts, internal consistency, links, tagging.

Every check is read-only SQL over the stored data and returns ok / warn / fail with a count and examples.
The checks marked repairable have a safe fix in `repair()`: they only remove leftovers or correct derived
flags, never act data that came from the gazette.
"""
import time

from . import gazette, tree
from .indexer import legacy_font_issues, refresh_current_version, toc_damaged_issues

LIMIT = 6  # examples per check


def _check(id, label, count, explain, examples=(), warn_at=1, fail_at=None, repairable=False, info=False):
    status = "ok"
    if count >= warn_at:
        status = "info" if info else "warn"
    if fail_at is not None and count >= fail_at:
        status = "fail"
    return dict(id=id, label=label, count=count, status=status, explain=explain,
                examples=[str(e) for e in examples][:LIMIT], repairable=repairable and count > 0)


def run(con) -> dict:
    t0 = time.time()
    one = lambda q, p=(): con.execute(q, p).fetchone()[0]
    rows = lambda q, p=(): con.execute(q, p).fetchall()
    out = []

    # ---- tree -------------------------------------------------------------------------------
    keyless = rows("SELECT title, number, year FROM acts WHERE pinned=1 AND has_text=0 ORDER BY title")
    out.append(_check("tree_key_text", "Acte de bază fără text", len(keyless),
                      "Răspunsurile din ramurile lor nu le pot cita. Cele anterioare anului 2000 nu există pe monitorul.gov.md: importați PDF-ul consolidat de pe legis.md în secțiunea Import.",
                      [f"{r['title']} ({r['number']}/{r['year']})" for r in keyless]))
    empty = [d for d in tree.DOMAINS if not one(
        "SELECT COUNT(*) FROM act_domain x JOIN acts a ON a.id=x.act_id WHERE x.domain=? AND a.has_text=1", (d,))]
    out.append(_check("tree_empty_domain", "Domenii fără niciun act cu text", len(empty),
                      "O întrebare din acest domeniu nu poate găsi nimic în ramură.", [tree.DOMAINS[d]["name"] for d in empty], fail_at=1))

    # ---- issues -----------------------------------------------------------------------------
    err = rows("SELECT id, error FROM issues WHERE status='error' ORDER BY id")
    out.append(_check("issue_errors", "Ediții cu eroare de descărcare", len(err),
                      "Se reîncearcă singure; le găsiți în pagina Probleme.", [f"#{r['id']}: {r['error']}" for r in err]))
    undated = rows("SELECT id, title FROM issues WHERE status='done' AND published IS NULL")
    out.append(_check("issue_undated", "Ediții fără dată de publicare", len(undated),
                      "Actele lor nu au dată de publicare, de care depind intrarea în vigoare și abrogările.",
                      [f"#{r['id']} {r['title']}" for r in undated]))
    empty_issues = rows("""SELECT id, title FROM issues WHERE status='done' AND n_acts=0 AND COALESCE(title,'') NOT LIKE '%BIS%'
                           AND COALESCE(title,'') NOT LIKE '%Specială%' AND COALESCE(title,'') NOT LIKE '%speciala%'""")
    out.append(_check("issue_no_acts", "Ediții obișnuite fără niciun act citit", len(empty_issues),
                      "Ediții BIS și speciale sunt excluse, ele conțin de obicei doar anunțuri. Restul pot avea un cuprins într-un format necunoscut.",
                      [f"#{r['id']} {r['title']}" for r in empty_issues], info=True))
    listed_ver = con.execute("SELECT value FROM settings WHERE key='listing_version'").fetchone()
    stale = (listed_ver[0] if listed_ver else None) != gazette.LISTING_VERSION
    out.append(_check("listing_version", "Cuprinsurile citite cu versiunea curentă a cititorului", int(stale),
                      "Dacă nu, indexarea le recitește automat o dată." if stale else "Toate edițiile au fost recitite după ultima schimbare."))

    # ---- texts ------------------------------------------------------------------------------
    toc = toc_damaged_issues(con)
    out.append(_check("text_toc", "Ediții cu rânduri de cuprins salvate ca text", len(toc),
                      "Actele lor au ca text propriul rând din cuprins. La pornire sunt curățate și recitite.", [f"#{i}" for i in toc]))
    legacy = legacy_font_issues(con)
    out.append(_check("text_legacy", "Ediții cu text în fontul vechi („Hot=r]re”)", len(legacy),
                      "Textul nu poate fi căutat. La pornire sunt curățate și recitite.", [f"#{i}" for i in legacy]))
    sizes = rows("""SELECT a.id, a.kind, a.mo_issue, a.title, SUM(length(c.text)) n FROM acts a JOIN chunks c ON c.version_id=a.current_version
                    WHERE a.has_text=1 GROUP BY a.id""")
    tiny = [r for r in sizes if r["n"] < 200 and r["kind"] in ("lege", "cod")]
    out.append(_check("text_tiny_laws", "Legi cu text sub 200 de caractere", len(tiny),
                      "De obicei doar titlul și semnătura: textul real nu a fost găsit în ediție.",
                      [f"{r['title'][:70]} (ediția #{r['mo_issue']}, {r['n']} car.)" for r in tiny], info=True))
    per_issue: dict = {}
    for r in sizes:
        if r["mo_issue"]:
            per_issue.setdefault(r["mo_issue"], []).append(r)
    swallow = []
    for iid, acts in per_issue.items():
        if len(acts) >= 5:
            big = max(acts, key=lambda r: r["n"])
            if big["kind"] != "cod" and big["n"] > 0.85 * sum(r["n"] for r in acts) and big["n"] > 200_000:
                swallow.append(f"#{iid}: {big['title'][:60]} ({big['n'] // 1000}k car.)")
    out.append(_check("text_swallow", "Acte care cuprind aproape toată ediția", len(swallow),
                      "Fie au o anexă foarte mare, fie au înghițit actele următoare, ale căror titluri nu au fost găsite.", swallow, info=True))

    # ---- consistency (repairable) -----------------------------------------------------------
    flag_on = rows("""SELECT id, title FROM acts a WHERE has_text=1 AND NOT EXISTS (SELECT 1 FROM chunks c JOIN versions v ON v.id=c.version_id
                      WHERE v.act_id=a.id)""")
    flag_off = rows("""SELECT id, title FROM acts a WHERE has_text=0 AND current_version IS NOT NULL
                       AND EXISTS (SELECT 1 FROM chunks c WHERE c.version_id=a.current_version)""")
    out.append(_check("flag_text", "Indicatorul „are text” greșit", len(flag_on) + len(flag_off),
                      "Actul apare cu text dar nu are niciun fragment, sau invers.",
                      [r["title"][:80] for r in (flag_on + flag_off)], repairable=True))
    orphan_chunks = one("SELECT COUNT(*) FROM chunks c WHERE NOT EXISTS (SELECT 1 FROM versions v WHERE v.id=c.version_id)")
    orphan_versions = one("SELECT COUNT(*) FROM versions v WHERE NOT EXISTS (SELECT 1 FROM acts a WHERE a.id=v.act_id)")
    orphan_tags = one("""SELECT (SELECT COUNT(*) FROM act_domain x WHERE NOT EXISTS (SELECT 1 FROM acts a WHERE a.id=x.act_id))
                              + (SELECT COUNT(*) FROM ai_tags t WHERE NOT EXISTS (SELECT 1 FROM acts a WHERE a.id=t.act_id))
                              + (SELECT COUNT(*) FROM relations r WHERE NOT EXISTS (SELECT 1 FROM acts a WHERE a.id=r.from_act))""")
    empty_versions = one("SELECT COUNT(*) FROM versions v WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.version_id=v.id)")
    n = orphan_chunks + orphan_versions + orphan_tags + empty_versions
    out.append(_check("orphans", "Resturi fără act (fragmente, versiuni, etichete, legături)", n,
                      "Rămase după ștergerea sau recitirea unor acte; ocupă loc și pot apărea în căutare.",
                      [f"fragmente {orphan_chunks}", f"versiuni fără act {orphan_versions}", f"versiuni goale {empty_versions}",
                       f"etichete și legături {orphan_tags}"] if n else [], repairable=True))
    fts_chunks, n_chunks = one("SELECT COUNT(*) FROM chunks_fts"), one("SELECT COUNT(*) FROM chunks")
    fts_acts, n_acts = one("SELECT COUNT(*) FROM acts_fts"), one("SELECT COUNT(*) FROM acts")
    out.append(_check("fts", "Indexul de căutare nu corespunde textelor", abs(fts_chunks - n_chunks) + abs(fts_acts - n_acts),
                      "Căutarea ar găsi fragmente care nu mai există sau ar rata fragmente noi.",
                      [f"fragmente {n_chunks}, în index {fts_chunks}", f"acte {n_acts}, în index {fts_acts}"]
                      if fts_chunks != n_chunks or fts_acts != n_acts else [], repairable=True))
    dup = rows("""SELECT kind, number, adopted, COUNT(*) n, group_concat(id) ids FROM acts WHERE number IS NOT NULL AND adopted IS NOT NULL
                  GROUP BY kind, number, adopted HAVING COUNT(*) > 1""")
    out.append(_check("duplicates", "Același act înregistrat de mai multe ori", len(dup),
                      "Același tip, număr și dată sub mai multe înregistrări: textele și etichetele se împart între ele.",
                      [f"{r['kind']} {r['number']} din {r['adopted']} (×{r['n']})" for r in dup]))

    # ---- links and tagging ------------------------------------------------------------------
    missing = rows("""SELECT to_num, to_year, COUNT(*) n FROM relations r WHERE to_cls='lege' AND to_year>=2000
                      AND NOT EXISTS (SELECT 1 FROM acts a WHERE a.num=r.to_num AND a.year=r.to_year AND a.kind IN ('lege','cod'))
                      GROUP BY to_num, to_year ORDER BY n DESC""")
    out.append(_check("links_missing", "Legi din 2000 încoace modificate, dar absente din catalog", len(missing),
                      "Alte acte le modifică, dar legea însăși nu a fost găsită în nicio ediție.",
                      [f"Legea {r['to_num']}/{r['to_year']}: {r['n']} modificări" for r in missing], info=True))
    from .tagger import PENDING
    backlog = one(f"SELECT COUNT(*) {PENDING} AND a.has_text=1")
    out.append(_check("tag_backlog", "Acte cu text încă neîncadrate în domenii", backlog,
                      "Le încadrează modelul AI, în fundal. Până atunci sunt găsite doar prin căutarea în toată legislația.", info=True))

    return dict(checks=out, seconds=round(time.time() - t0, 1),
                summary={s: sum(1 for c in out if c["status"] == s) for s in ("ok", "info", "warn", "fail")})


def repair(con) -> dict:
    """Fix what is safe to fix: leftovers without an act, derived flags, the search index. Returns counts."""
    done = {}
    done["fragmente"] = con.execute("DELETE FROM chunks WHERE version_id NOT IN (SELECT id FROM versions)").rowcount
    done["versiuni fără act"] = con.execute("DELETE FROM versions WHERE act_id NOT IN (SELECT id FROM acts)").rowcount
    done["versiuni goale"] = con.execute(
        "DELETE FROM versions WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.version_id=versions.id)").rowcount
    for table, col in (("act_domain", "act_id"), ("ai_tags", "act_id"), ("relations", "from_act")):
        done[table] = con.execute(f"DELETE FROM {table} WHERE {col} NOT IN (SELECT id FROM acts)").rowcount
    # Search index rows whose text is gone, and texts missing from the index.
    done["index căutare"] = con.execute("DELETE FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)").rowcount
    done["index acte"] = con.execute("DELETE FROM acts_fts WHERE rowid NOT IN (SELECT id FROM acts)").rowcount
    from .text import stems
    missing = con.execute("""SELECT c.id, c.path, c.art, c.text, a.title FROM chunks c JOIN acts a ON a.id=c.act_id
                             WHERE c.id NOT IN (SELECT rowid FROM chunks_fts)""").fetchall()
    for r in missing:
        art = f"art {r['art']}" if r["art"] else ""
        con.execute("INSERT INTO chunks_fts(rowid,stems) VALUES(?,?)", (r["id"], stems(f"{r['title']} {r['path']} {art} {r['text']}")))
    done["fragmente adăugate în index"] = len(missing)
    missing_acts = con.execute("SELECT id, title FROM acts WHERE id NOT IN (SELECT rowid FROM acts_fts)").fetchall()
    con.executemany("INSERT INTO acts_fts(rowid,stems) VALUES(?,?)", [(r["id"], stems(r["title"])) for r in missing_acts])
    done["acte adăugate în index"] = len(missing_acts)
    # Flags derived from the versions: recomputed the same way the indexer does.
    wrong = [r[0] for r in con.execute("""SELECT id FROM acts a WHERE (has_text=1 AND NOT EXISTS (SELECT 1 FROM chunks c JOIN versions v
             ON v.id=c.version_id WHERE v.act_id=a.id)) OR (has_text=0 AND current_version IS NOT NULL
             AND EXISTS (SELECT 1 FROM chunks c WHERE c.version_id=a.current_version))""")]
    for aid in wrong:
        refresh_current_version(con, aid)
    done["indicatori corectați"] = len(wrong)
    con.commit()
    return {k: v for k, v in done.items() if v}

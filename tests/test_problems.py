from fastapi.testclient import TestClient

from app import db, indexer
from app.config import DB_PATH
from app.gazette import split_acts
from app.main import app

client = TestClient(app)  # used without `with`, so no crawler or Ollama starts


def seed():
    assert "mal-test-" in str(DB_PATH)
    db.init()
    con = db.connect()
    con.execute("DELETE FROM issues")
    con.executemany("INSERT INTO issues(id,title,status,attempts,error,priority) VALUES(?,?,?,?,?,?)", [
        (1, "MO 1", "error", 3, "HTTP 504", 5), (2, "MO 2", "error", 1, "HTTP 504", 5),
        (3, "MO 3", "no_text", 0, None, 5), (4, "MO 4", "done", 0, None, 5), (5, None, "missing", 0, None, 0)])
    con.commit()
    return con


def test_listing_splits_entries_run_together():
    from app.gazette import parse_listing
    # Issue 1360 (2007) writes several entries in one paragraph; they were all lost before.
    page = ("<title>MO</title><p>576. Decret pentru promulgarea Legii privind protecția desenelor (nr. 1288-IV, 24 august 2007). "
            "577. Lege privind protecția desenelor și modelelor industriale (nr. 161-XVI, 12 iulie 2007).578. Lege pentru "
            "modificarea Legii nr. 947-XIII din 19 iulie 1996 (nr. 185-XVI, 26 iulie 2007).</p>")
    assert [a["art"] for a in parse_listing(page)["acts"]] == [576, 577, 578]


def test_legacy_font_diacritics_restored():
    from app.gazette import fix_legacy_font
    page = ("Hot=r]re cu privire la statutul judec=torului. Pre[edintele Republicii Moldova, func\\ia de ambasador, "
            "]n mun. Chi[in=u. PRE{EDINTELE ROM`NIEI. Ion C+P+|IN+. Vezi art. [1] [i 2+2=4.")
    out = fix_legacy_font(page)
    assert "Hotărîre" in out and "judecătorului" in out and "Președintele" in out and "funcția" in out
    assert "în mun. Chișinău" in out and "CĂPĂȚINĂ" in out and "PREȘEDINTELE" in out
    assert "[1]" in out and "2+2=4" in out
    modern = "Hotărâre cu privire la funcția de președinte, formula a=b în tabelul [3]."
    assert fix_legacy_font(modern * 5) == modern * 5


def test_listing_formats_seen_on_the_site():
    from app.gazette import parse_listing
    page = ("<title>MO</title><div>Marţi, 22 aprilie 2003</div>"
            # A double-escaped &nbsp; between entries made 148 take 149's number.
            "<p>148. Lege cu privire la sistemul de salarizare în sectorul bugetar (nr. 355-XVI, 23 decembrie 2005). nbsp;"
            "149. Decret pentru promulgarea Legii privind aderarea (nr. 465-IV, 27 februarie 2006).</p>"
            "<p>401. Lege privind administrarea și deetatizarea proprietății publice (nr. 121-XVI, 4 mai 2007). ..........</p>"
            "<p>400. Decret pentru promulgarea Legii privind administrarea (nr. 1171-IV,</p><p>14 iunie 2007).</p>"
            "<p>2731. Ordin cu privire la aprobarea Normelor metodologice (nr. 215, 28 decembrie 2015.)</p>"
            "<p>635. CONSTITUŢIA REPUBLICII MOLDOVA din 29.07.1994</p>"
            "<p>1534. Planul general de conturi contabile</p><p>Acte ale Ministerului Finanțelor</p>")
    p = parse_listing(page)
    acts = {a["art"]: a for a in p["acts"]}
    assert p["published"] == "2003-04-22"
    assert (acts[148]["number"], acts[148]["year"]) == ("355-XVI", 2005) and acts[149]["number"] == "465-IV"
    assert acts[401]["number"] == "121-XVI" and acts[400]["adopted"] == "2007-06-14"
    assert acts[2731]["adopted"] == "2015-12-28"
    assert acts[635]["kind"] == "constitutie" and acts[635]["adopted"] == "1994-07-29"
    assert acts[1534]["title"] == "Planul general de conturi contabile"


def test_individual_acts_recognised_by_title():
    from app.tagger import _INDIVIDUAL
    from app.text import fold
    individual = ["Decret privind numirea domnului Vadim ȘCHIOPU în funcţia de director adjunct",
                  "Hotărîre cu privire la eliberarea din funcție a dnei IFTODI Victoria",
                  "Hotărîre cu privire la alocarea mijloacelor financiare",
                  "Decret privind acordarea unei grațieri individuale", "Decret privind grațierea unor condamnați",
                  "Decret privind conferirea de distincții de stat",
                  "Dispoziţie privind aprobarea componenței nominale a Părții moldovenești"]
    general = ["Ordin cu privire la modificarea Ordinului MAIA nr. 136/2023",
               "Hotărâre pentru aprobarea Regulamentului privind numirea în funcții publice",
               "Hotărâre cu privire la aprobarea Regulamentului de alocare a resurselor"]
    assert all(_INDIVIDUAL.search(fold(t)) for t in individual)
    assert not any(_INDIVIDUAL.search(fold(t)) for t in general)


def test_genitive_titles_get_their_type():
    from app.text import kind_of
    from app.gazette import _key
    # Issue 2083 lists "Codului serviciilor media audiovizuale" (nr. 174/2018), which was stored as "alt".
    assert kind_of("Codului serviciilor media audiovizuale al Republicii Moldova") == "cod"
    assert kind_of("Legii cu privire la ...") == "lege"
    assert _key("Codului serviciilor media audiovizuale") == _key("Codul serviciilor media audiovizuale")


def test_law_printed_before_its_decree_keeps_its_text():
    # Issue 3049: the listing has decree 117 before law 118, but the PDF prints the law first.
    text = ("L E G E\nprivind indexarea pensiilor la 1 aprilie 2025\nParlamentul adoptă prezenta lege organică.\n"
            "Art. 1. – Pensiile se indexează. " + "Text al legii. " * 30 + "\nPREŞEDINTELE PARLAMENTULUI\n"
            "D E C R E T\npentru promulgarea Legii privind indexarea pensiilor la 1 aprilie 2025\n"
            "Articol unic. – Se promulgă Legea privind indexarea pensiilor la 1 aprilie 2025.\nPREŞEDINTELE REPUBLICII MOLDOVA\n")
    acts = [dict(art=117, title="Decret pentru promulgarea Legii privind indexarea pensiilor la 1 aprilie 2025"),
            dict(art=118, title="Lege privind indexarea pensiilor la 1 aprilie 2025")]
    assert "Pensiile se indexează" in split_acts(text, acts)[118]


def test_unnumbered_code_entry_joins_its_key_act():
    from app import indexer
    con = seed()
    key = con.execute("SELECT id FROM acts WHERE pinned=1 AND title='Codul muncii'").fetchone()
    if key is None:
        indexer.seed_tree(con)
        key = con.execute("SELECT id FROM acts WHERE pinned=1 AND title='Codul muncii'").fetchone()
    # Issue 1148 lists it as "Codul muncii al Republicii Moldova", with no number or date.
    aid = indexer.upsert_act(con, dict(kind="cod", title="Codul muncii al Republicii Moldova", num=None, mo_issue=1148, mo_art=1))
    assert aid == key["id"]
    con.rollback(); con.close()


def test_compound_numbers_keep_acts_apart():
    from app.indexer import act_slug
    # Decisions 50/1 and 50/10 of one body on one day were merged into a single act.
    assert act_slug("hotarare", 50, "2024-10-30", number="50/1") != act_slug("hotarare", 50, "2024-10-30", number="50/10")
    # Laws keep their plain number, so citations "nr. 355/2005" and the seeded key acts still match.
    assert act_slug("lege", 355, "2005-12-23", number="355-XVI") == act_slug("lege", 355, "2005-12-23")


def test_split_skips_old_toc_without_dot_leaders():
    # Issue 2668 (2000): no dot leaders, and the skipped promulgation decree's entry repeats the law's title.
    text = ("SUMAR\n708. Decret privind promulgarea Legii cetățeniei Republicii Moldova (nr. 1591-II, 31 iulie 2000).\n"
            "709. Legea cetățeniei Republicii Moldova (nr. 1024-XIV, 2 iunie 2000).\n"
            "710. Hotărîre asupra proiectului de Lege pentru modificarea Legii învățămîntului (nr. 1143-XIV, 20 iulie 2000).\n"
            + "x\n" * 40 +
            "D E C R E T\nprivind promulgarea Legii cetățeniei Republicii Moldova\n708\nArticol unic. - Se promulgă Legea.\n"
            "L E G E A\ncetățeniei Republicii Moldova\n709\nArticolul 1. Noțiuni\n" + "Text al legii. " * 20 + "\n"
            "H O T Ă R Î R E\nasupra proiectului de Lege pentru modificarea Legii învățămîntului\n710\n" + "Text al hotărîrii. " * 10)
    acts = [dict(art=708, title="Decret privind promulgarea Legii cetățeniei Republicii Moldova"),
            dict(art=709, title="Legea cetățeniei Republicii Moldova"),
            dict(art=710, title="Hotărîre asupra proiectului de Lege pentru modificarea Legii învățămîntului")]
    bodies = split_acts(text, acts)
    assert "Articolul 1" in bodies[709] and "(nr." not in bodies[709]
    assert "Text al hotărîrii" in bodies[710]


def test_quit_only_from_this_computer():
    # The test client is not 127.0.0.1, like a device on the LAN under run-lan.bat: it must not stop the app.
    assert client.post("/api/quit").status_code == 403
    assert "version" in client.get("/api/identity").json()


def statuses(con):
    return {r["id"]: r["status"] for r in con.execute("SELECT id,status FROM issues")}


def test_lists_only_retryable_problems():
    seed().close()
    p = client.get("/api/problems").json()
    assert {i["id"] for i in p["issues"]} == {1, 2, 3} and p["missing"] == 1


def test_retry_one_requeues_first_and_wakes_crawler():
    con = seed()
    indexer.crawler.wake.clear()
    assert client.post("/api/problems/1/retry").status_code == 200
    r = con.execute("SELECT status,attempts,error,priority FROM issues WHERE id=1").fetchone()
    # The error is kept until a retry succeeds, so the issue stays on the page while it waits.
    assert (r["status"], r["attempts"], r["error"]) == ("listed", 0, "HTTP 504") and r["priority"] > 5
    assert statuses(con)[2] == "error" and indexer.crawler.wake.is_set()
    listed = {i["id"]: i["status"] for i in client.get("/api/problems").json()["issues"]}
    assert listed[1] == "retry"
    con.close()


def test_restart_requeue_keeps_failed_issue_visible():
    # _discover puts failed issues back in the queue on every start; they must not vanish from the page.
    con = seed()
    con.execute("UPDATE issues SET status='listed' WHERE status='error' AND attempts<20")
    con.commit()
    assert {i["id"]: i["status"] for i in client.get("/api/problems").json()["issues"]} == {1: "retry", 2: "retry", 3: "no_text"}
    con.close()


def test_retry_one_rejects_healthy_issue():
    seed().close()
    assert client.post("/api/problems/4/retry").status_code == 404


def test_retry_all_leaves_done_and_missing_alone():
    con = seed()
    assert client.post("/api/problems/retry-all").json()["queued"] == 3
    assert statuses(con) == {1: "listed", 2: "listed", 3: "listed", 4: "done", 5: "missing"}
    con.close()


def test_empty_version_does_not_count_as_text():
    # A version without chunks set has_text=1, which the health check flagged and its repair could not clear.
    con = seed()
    aid = indexer.upsert_act(con, dict(kind="lege", title="Lege de test fără text", num=9901, year=2030,
                                       adopted="2030-01-01", number="9901"))
    con.execute("INSERT INTO versions(act_id,effective,source) VALUES(?,?,?)", (aid, "2030-01-01", "monitorul"))
    indexer.refresh_current_version(con, aid)
    assert con.execute("SELECT has_text FROM acts WHERE id=?", (aid,)).fetchone()[0] == 0
    con.rollback(); con.close()


def test_search_page_below_one_is_first_page():
    seed().close()
    assert client.get("/api/search", params=dict(page=0)).json()["page"] == 1


def test_short_title_found_only_as_heading_line():
    text = ("Se promulgă Codul vamal nr. 95.\nDin Codul vamal rezultă ceva.\n237\nC O D U L V A M A L\n"
            "Articolul 1. Obiect\nText.\nH O T Ă R Â R E\ncu privire la aprobarea Regulamentului pentru vize\nPunct.\n")
    acts = [dict(art=238, title="Codul vamal"), dict(art=239, title="Hotărâre cu privire la aprobarea Regulamentului pentru vize")]
    out = split_acts(text, acts)
    assert out[238].startswith("C O D U L V A M A L") and "Articolul 1" in out[238]
    assert "Punct" in out[239]

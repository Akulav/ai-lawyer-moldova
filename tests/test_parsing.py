from app.gazette import _key, parse_listing, split_acts
from app.text import can_repeal, effective_date, effective_dates, extract_relations, fts_query, parse_date, split_articles, stems
from app.tree import classify, keyword_route


def rel(rels, num, year, kind):
    return next((r for r in rels if (r["to_num"], r["to_year"], r["rel"]) == (num, year, kind)), None)


def test_parse_date_formats():
    assert parse_date("8 octombrie 2026") == "2026-10-08"
    assert parse_date("24-08-2021") == "2021-08-24"
    assert parse_date("28.12.23") == "2023-12-28"


def test_stemming_ignores_diacritics_and_inflection():
    assert stems("declarație") == stems("declaratie")
    assert stems("cadoul") == stems("cadou")


def test_listing_keeps_parentheses_in_title():
    page = ("<title>Monitorul Oficial Nr. 92-98</title>"
            "<p>229. Lege pentru modificarea unor acte normative (în domeniul controlului tutunului) (nr. 238, 8 octombrie 2026)</p>"
            "<p>230. Hot&#259;r&acirc;re privind X (nr. 5-XV, 1 mai 2020).</p>")
    p = parse_listing(page)
    assert p["acts"][0]["title"].endswith("(în domeniul controlului tutunului)")
    assert (p["acts"][0]["num"], p["acts"][0]["year"]) == (238, 2026)
    assert p["acts"][1]["kind"] == "hotarare" and p["acts"][1]["adopted"] == "2020-05-01"


def test_listing_handles_div_wrapped_entries():
    page = ("<title>Monitorul Oficial Nr. 543-544</title><div class=\"release-intro-text\"><div>&nbsp;</div>\r\n"
            "<h3><strong>PARTEA I</strong></h3><div>580. Lege pentru modificarea unor acte normative "
            "(&icirc;n domeniul controlului tutunului) (nr. 238, 8 octombrie 2026)</div>\r\n<div>&nbsp;</div>\r\n"
            "<div>765. Lege privind actele de stare civilă (nr. 100-XV, 26 aprilie 2001)</div></div>"
            "<a>Monitorul Oficial Nr. 543-544 din 31.10.2026</a>")
    acts = parse_listing(page)["acts"]
    assert [(a["art"], a["num"], a["year"]) for a in acts] == [(580, 238, 2026), (765, 100, 2001)]
    assert "în domeniul" in acts[0]["title"]


def test_title_amendment_detected():
    r = extract_relations("Lege pentru modificarea Legii nr. 182/2010 cu privire la parcurile industriale", "")
    assert rel(r, 182, 2010, "modifica")


def test_old_style_reference():
    r = extract_relations("Lege pentru modificarea Legii nr. 451-XV din 30 iulie 2001 privind licențierea", "")
    assert rel(r, 451, 2001, "modifica")


def test_whole_repeal_vs_partial():
    r = extract_relations("Lege X", "Art. 5. – Legea nr. 100/2005 privind ceva se abrogă.")
    assert rel(r, 100, 2005, "abroga")["whole"] == 1
    r = extract_relations("Lege X", "Art. 5. – Articolul 7 din Legea nr. 100/2005 privind ceva se abrogă.")
    a = rel(r, 100, 2005, "abroga")
    assert a is None or a["whole"] == 0


def test_repeal_list():
    body = "Art. 9. – Se abrogă: a) Legea nr. 1/2001 privind A; b) Legea nr. 2/2002 privind B; Art. 10. – Altceva."
    r = extract_relations("Lege X", body)
    assert rel(r, 1, 2001, "abroga")["whole"] == 1 and rel(r, 2, 2002, "abroga")["whole"] == 1


def whole_repeals(body, title="Lege X"):
    return {(r["to_num"], r["to_year"]) for r in extract_relations(title, body) if r["rel"] == "abroga" and r["whole"]}


def test_real_false_positives_are_not_whole_repeals():
    # Each is a clause from the live index that wrongly marked an act as repealed.
    for body in [
        "Prezenta hotărâre intră în vigoare la data intrării în vigoare a Legii nr. 82/2024 privind controalele oficiale și se abrogă.",
        "articolele II și III din Legea nr. 318/2018 privind modificarea anexei la Legea nr. 668/1995 se abrogă.",
        "„(1) Constatarea faptelor se efectuează conform termenelor prevăzute de Legea nr. 548/1995 cu privire la Banca Națională se abrogă.",
        "maximum 5% din venitul anual, conform procedurii stabilite la art. 20 din Legea nr. 174/2017 se abrogă.",
    ]:
        assert whole_repeals(body) == set(), body


def test_real_true_repeals_survive():
    assert (1149, 2000) in whole_repeals(
        "Art. 427. – La data intrării în vigoare a prezentului cod se abrogă: a) Codul vamal al Republicii Moldova nr. 1149/2000 "
        "(republicat în Monitorul Oficial, ediție specială), cu modificările ulterioare, cu excepția art. 85–92; "
        "b) Legea nr. 1380/1997 cu privire la tariful vamal (Monitorul Oficial, 2007, art. 5);")
    assert (1380, 1997) in whole_repeals("Se abrogă: a) Legea nr. 1/2001 privind A; b) Legea nr. 1380/1997 cu privire la tariful vamal.")
    assert (142, 2005) in whole_repeals("", "Lege pentru abrogarea Legii nr. 142/2005 privind aprobarea Nomenclatorului")
    assert (5, 2000) in whole_repeals("Se abrogă Legea nr. 5/2000 privind ceva. Altă propoziție despre art. 3.")
    assert (5, 2000) in whole_repeals("(2) La data intrării în vigoare a prezentei legi, Legea nr. 5/2000 privind ceva se abrogă.")


def test_hierarchy_guard():
    assert not can_repeal("ordin", "Ordin X", "lege", False)
    assert not can_repeal("lege", "Lege pentru modificarea unor acte normative", "lege", True)  # a code
    assert can_repeal("cod", "Codul vamal", "lege", True)
    assert can_repeal("lege", "Lege pentru modificarea unor acte", "lege", False)


def test_amended_article_reference_is_not_a_repeal():
    r = extract_relations("Lege X", "La articolul 25 din Legea nr. 320/2012 cu privire la Poliție, punctul 3 se modifică.")
    assert rel(r, 320, 2012, "modifica") and not rel(r, 320, 2012, "abroga")


def test_effective_date():
    assert effective_date("Prezenta lege intră în vigoare la 1 ianuarie 2027.") == "2027-01-01"
    assert effective_date("Prezenta lege intră în vigoare la data publicării.") is None


def test_staged_commencement_does_not_postpone_the_whole_act():
    body = "Art. VI. – Art. I pct. 8 intră în vigoare la 1 ianuarie 2029. Restul se aplică imediat."
    assert effective_dates(body) == (None, ["2029-01-01"])
    body = "Prezenta lege intră în vigoare la 1 ianuarie 2027, iar art. 3 intră în vigoare la 1 iunie 2028."
    assert effective_dates(body) == ("2027-01-01", ["2028-06-01"])


def test_split_articles_builds_path():
    body = "TITLUL I\nDISPOZIȚII\nCapitolul 2\nBagaje\nArticolul 12. Scutiri\n(1) Text unu.\nArticolul 13. Alte\nText doi.\n"
    ch = split_articles(body)
    assert [c["art"] for c in ch] == ["12", "13"]
    assert "Capitolul 2" in ch[0]["path"] and "TITLUL I" in ch[0]["path"]


def test_split_acts_skips_table_of_contents():
    acts = [dict(art=1, title="Lege privind reglementarea importului de bunuri casnice"),
            dict(art=2, title="Hotărâre cu privire la aprobarea Regulamentului pentru vize")]
    text = ("1. Lege privind reglementarea importului de bunuri casnice ...........\n"
            "2. Hotărâre cu privire la aprobarea Regulamentului pentru vize ...........\n\n"
            "L E G E\nprivind reglementarea importului de bunuri casnice\nArt. 1. Conținut lege.\n"
            "H O T Ă R Â R E\ncu privire la aprobarea Regulamentului pentru vize\nPunctul 1. Conținut hotărâre.\n")
    out = split_acts(text, acts)
    assert "Conținut lege" in out[1] and "Conținut hotărâre" not in out[1]
    assert "Conținut hotărâre" in out[2]


def test_tree_routes_customs_question():
    q = "trebuie sa declar la intoarcerea in tara un iphone nedespachetat daca este drept cadou"
    assert keyword_route(q)[0] == "vamal"  # the user's exact wording, with no customs vocabulary
    assert "vamal" in classify("Lege pentru modificarea Codului vamal")


def test_fts_query_drops_stopwords():
    q = fts_query("trebuie sa declar un cadou")
    assert '"sa"' not in q and '"cado"' in q

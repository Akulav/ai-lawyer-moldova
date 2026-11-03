"""The legal tree: Ramură -> Domeniu -> acte de bază.

The router walks this tree first, so a customs question only ever searches customs acts.
Key acts ("pinned") are always tried first in their domain. An act is assigned to a domain by
its title keywords when it is indexed.
"""
import hashlib
import re

from .text import fold

# (kind, number, adoption date, title, verified legis.md doc id or None)
TREE = [
    {"id": "fiscal", "name": "Fiscal și vamal", "domains": [
        {"id": "vamal", "name": "Vamă și călătorii",
         "desc": "trecerea frontierei, bagaje, cadouri aduse din străinătate, declararea bunurilor, import/export, colete, taxe vamale, numerar la frontieră",
         "kw": ["vamal", "vama", "vamale", "frontier", "import", "export", "tarif vamal", "bagaj", "colet", "calator"],
         "acts": [("cod", 95, "2021-08-24", "Codul vamal", 137957),
                  ("lege", 1380, "1997-11-20", "Legea cu privire la tariful vamal", None)]},
        {"id": "fiscal", "name": "Impozite și taxe",
         "desc": "impozit pe venit, TVA, accize, impozit pe bunuri imobiliare, declarații fiscale, SFS, facilități fiscale",
         "kw": ["fiscal", "impozit", "tva", "accize", "taxe", "serviciul fiscal", "contribuabil"],
         "acts": [("cod", 1163, "1997-04-24", "Codul fiscal", None)]},
        {"id": "financiar", "name": "Bănci, valută și finanțe",
         "desc": "bănci, credite, schimb valutar, plăți, conturi, asigurări comerciale, piața de capital, criptoactive și monede virtuale, buget public",
         "kw": ["banc", "credit", "valut", "asigurar", "buget", "piata de capital", "plati", "nebancar", "cripto",
                "active virtuale", "activelor virtuale", "valori mobiliare", "valorilor mobiliare"],
         "acts": [("lege", 62, "2008-03-21", "Legea privind reglementarea valutară", None)]},
    ]},
    {"id": "munca", "name": "Muncă și protecție socială", "domains": [
        {"id": "munca", "name": "Muncă",
         "desc": "contract de muncă, salariu, concediu, demisie, concediere, program de lucru, inspecția muncii",
         "kw": ["muncii", "munca", "salari", "angajat", "concediu", "ocupar", "forta de munca", "sindicat"],
         "acts": [("cod", 154, "2003-03-28", "Codul muncii", None)]},
        {"id": "social", "name": "Pensii și asigurări sociale",
         "desc": "pensii, indemnizații, ajutor social, asigurări medicale, dizabilitate, CNAS",
         "kw": ["pensi", "asigurari sociale", "indemnizat", "ajutor social", "asistenta sociala", "dizabilit", "asigurare medical"],
         "acts": [("lege", 156, "1998-10-14", "Legea privind pensiile de asigurări sociale de stat", None),
                  ("lege", 489, "1999-07-08", "Legea privind sistemul public de asigurări sociale", None)]},
    ]},
    {"id": "civil", "name": "Civil și familie", "domains": [
        {"id": "civil", "name": "Contracte și drept civil",
         "desc": "contracte, vânzare, împrumut, donație, răspundere civilă, proprietate, prejudicii, prescripție, moștenire, testament",
         "kw": ["civil", "contract", "proprietat", "mostenir", "succesiun", "notar", "donat"],
         "acts": [("cod", 1107, "2002-06-06", "Codul civil", None)]},
        {"id": "familie", "name": "Familie",
         "desc": "căsătorie, divorț, pensie de întreținere, copii, adopție, violență în familie",
         "kw": ["familie", "casator", "divort", "copil", "adoptie", "intretinere", "minor"],
         "acts": [("cod", 1316, "2000-10-26", "Codul familiei", None)]},
        {"id": "imobiliar", "name": "Locuințe și terenuri",
         "desc": "chirie, locuințe, condominiu, terenuri, cadastru, construcții, autorizații de construire",
         "kw": ["locuint", "chiri", "condomini", "funciar", "teren", "cadastr", "construct", "urbanism", "imobil"],
         "acts": [("lege", 75, "2015-04-30", "Legea locuinței", None),
                  ("cod", 828, "1991-12-25", "Codul funciar", None)]},
        {"id": "consumator", "name": "Protecția consumatorilor",
         "desc": "garanții, returnarea produselor, reclamații, magazine, cumpărături online, servicii publice comunale",
         "kw": ["consumator", "garanti", "comert", "reclam", "energie", "gaze", "servicii publice"],
         "acts": [("lege", 105, "2003-03-13", "Legea privind protecția consumatorilor", None)]},
    ]},
    {"id": "penal", "name": "Penal și contravențional", "domains": [
        {"id": "penal", "name": "Infracțiuni",
         "desc": "infracțiuni, pedepse, furt, escrocherie, plângere penală, proces penal, procuror, detenție",
         "kw": ["penal", "infractiun", "pedeaps", "detent", "procuror", "urmarire penala"],
         "acts": [("cod", 985, "2002-04-18", "Codul penal", None),
                  ("cod", 122, "2003-03-14", "Codul de procedură penală", None)]},
        {"id": "contraventional", "name": "Contravenții și amenzi",
         "desc": "amenzi, contravenții, procese-verbale, contestarea unei sancțiuni",
         "kw": ["contraven", "amend", "sanctiun"],
         "acts": [("cod", 218, "2008-10-24", "Codul contravențional", None)]},
    ]},
    {"id": "transport", "name": "Transport", "domains": [
        {"id": "rutier", "name": "Circulație și automobile",
         "desc": "permis de conducere, reguli de circulație, înmatriculare, import auto, asigurare RCA, accidente, transport rutier",
         "kw": ["rutier", "circulat", "autovehicul", "automobil", "conducere", "transport", "inmatricul"],
         "acts": [("cod", 150, "2014-07-17", "Codul transporturilor rutiere", None),
                  ("hotarare", 357, "2009-05-13", "Regulamentul circulației rutiere", None)]},
    ]},
    {"id": "afaceri", "name": "Afaceri", "domains": [
        {"id": "societati", "name": "Firme și antreprenoriat",
         "desc": "SRL, înregistrarea unei firme, acționari, licențe, activitate independentă, faliment, insolvabilitate, concurență",
         "kw": ["societat", "intreprinz", "antreprenor", "licent", "insolvab", "concurent", "actiuni", "srl"],
         "acts": [("lege", 135, "2007-06-14", "Legea privind societățile cu răspundere limitată", None),
                  ("lege", 149, "2012-06-29", "Legea insolvabilității", None)]},
        {"id": "achizitii", "name": "Achiziții și proprietate intelectuală",
         "desc": "achiziții publice, licitații, mărci, brevete, drepturi de autor",
         "kw": ["achizit", "licitat", "marc", "brevet", "drept de autor", "proprietate intelectuala"], "acts": []},
    ]},
    {"id": "public", "name": "Stat și administrație", "domains": [
        {"id": "administrativ", "name": "Administrație și contencios",
         "desc": "plângeri împotriva autorităților, petiții, acces la informații, funcționari publici, administrație locală",
         "kw": ["administrativ", "autoritat", "functionar", "petit", "administratie publica", "primar"],
         "acts": [("cod", 116, "2018-07-19", "Codul administrativ", None)]},
        {"id": "procedura", "name": "Instanțe și executare",
         "desc": "proces civil, chemare în judecată, taxă de stat, apel, avocați, executori judecătorești, justiție",
         "kw": ["procedura civila", "instant", "judecat", "executor", "avocat", "executare"],
         "acts": [("cod", 225, "2003-05-30", "Codul de procedură civilă", None),
                  ("cod", 443, "2004-12-24", "Codul de executare", None)]},
        {"id": "persoane", "name": "Cetățenie, străini și date personale",
         "desc": "cetățenie, pașapoarte, acte de identitate, vize, permis de ședere, date cu caracter personal, stare civilă",
         "kw": ["cetatenie", "strain", "pasaport", "viz", "date cu caracter personal", "stare civila", "buletin"],
         "acts": [("lege", 1024, "2000-06-02", "Legea cetățeniei", None),
                  ("lege", 200, "2010-07-16", "Legea privind regimul străinilor", None),
                  ("lege", 133, "2011-07-08", "Legea privind protecția datelor cu caracter personal", None)]},
        {"id": "constitutional", "name": "Constituție și alegeri",
         "desc": "drepturi fundamentale, Constituție, alegeri, partide, referendum",
         "kw": ["constitut", "electoral", "alegeri", "partid", "referendum"],
         "acts": [("cod", 325, "2022-12-08", "Codul electoral", None)]},
    ]},
    {"id": "sectoare", "name": "Sectoare", "domains": [
        {"id": "educatie", "name": "Educație și sănătate",
         "desc": "școală, universitate, burse, medicină, drepturile pacienților, medicamente",
         "kw": ["educat", "invatamant", "scoal", "universit", "sanatat", "medic", "pacient"],
         "acts": [("cod", 152, "2014-07-17", "Codul educației", None)]},
        {"id": "mediu", "name": "Mediu și agricultură",
         "desc": "mediu, ape, păduri, vânătoare, agricultură, subvenții, siguranța alimentelor",
         "kw": ["mediu", "apelor", "silvic", "padur", "vanat", "agricol", "agricultur", "subventi", "aliment"], "acts": []},
        {"id": "aparare", "name": "Armată și ordine publică",
         "desc": "serviciu militar, poliție, stare de urgență, securitate",
         "kw": ["militar", "armat", "polit", "aparar", "securitat", "urgent"], "acts": []},
    ]},
]

DOMAINS = {d["id"]: {**d, "group": g["id"], "group_name": g["name"]} for g in TREE for d in g["domains"]}
_KW = {d: [fold(k) for k in v["kw"]] for d, v in DOMAINS.items()}
# Stored acts are re-tagged when this changes, since classify() otherwise only runs on insert.
KW_VERSION = hashlib.md5(repr(sorted(_KW.items())).encode()).hexdigest()[:12]


def classify(title: str) -> list[str]:
    """Pick up to two domains for an act based on its title."""
    f = fold(title)
    scored = []
    for dom, kws in _KW.items():
        n = sum(1 for k in kws if re.search(r"\b" + re.escape(k), f))
        if n:
            scored.append((n, dom))
    scored.sort(reverse=True)
    return [d for _, d in scored[:2]]


# How people phrase a situation, as opposed to the words used in act titles. Kept apart from
# the title keywords, because "cadou" or "declar" would misfile acts during classification.
_ASKED = {
    "vamal": ["intoarcer", "cadou", "strainatat", "aeroport", "valiz", "aduc", "declar la"],
    "munca": ["demisi", "concedia", "patron", "angajator"],
    "imobiliar": ["proprietar", "chiria"],
    "contraventional": ["politia", "viteza"],
    "familie": ["fost sot", "fosta sot", "copii"],
}


def keyword_route(question: str) -> list[str]:
    """Routing fallback for when the model is unavailable or returns junk."""
    f = fold(question)
    scored = []
    for dom, v in DOMAINS.items():
        hay = _KW[dom] + _ASKED.get(dom, []) + [w for w in re.findall(r"\w{5,}", fold(v["desc"]))]
        n = sum(1 for k in set(hay) if re.search(r"\b" + re.escape(k[:7]), f))
        if n:
            scored.append((n, dom))
    scored.sort(reverse=True)
    return [d for _, d in scored[:2]]


def tree_for_prompt() -> str:
    out = []
    for g in TREE:
        out.append(f"{g['name']}:")
        out.extend(f"  - {d['id']}: {d['desc']}" for d in g["domains"])
    return "\n".join(out)


def pinned_acts():
    for d in DOMAINS.values():
        for kind, num, adopted, title, doc_id in d["acts"]:
            yield d["id"], kind, num, adopted, title, doc_id

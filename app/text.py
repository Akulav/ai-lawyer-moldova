"""Romanian text handling: folding, stemming, act-reference and article parsing."""
import re
import unicodedata
from functools import lru_cache

import snowballstemmer

_stemmer = snowballstemmer.stemmer("romanian")

STOP = set(
    "a ai ale alt am ar are as asa au ca care ce cel cea cei cele cu cum da dar daca de din dintre dupa e este "
    "eu fi fie fiind in intr intre iar il la le li lui mai ma mea meu mi nu o ori pe pentru prin sa sau se si "
    "sub sunt te tu un una unei unor unui va vor voi trebuie poate pot".split()
)
MONTHS = {
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5, "iunie": 6, "iulie": 7,
    "august": 8, "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12,
}


def fold(s: str) -> str:
    """Lowercase without diacritics, so 'declarație' and 'declaratie' compare equal."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    # Snowball leaves cadou/cadoul/cadouri apart, so merge the -ou noun family first.
    word = re.sub(r"(ou)ri(le|lor)?$", r"\1", word)
    s = _stemmer.stemWord(word)
    return s[:-1] if len(s) > 4 and s.endswith("u") else s


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def stems(text: str) -> str:
    return " ".join(_stem(t) for t in tokens(text))


# Everyday words mapped to the term the law uses, keyed by stem. The router model is asked for
# legal terms too, but small models often just echo the question.
LEGAL_TERMS = {
    "criptomoned": "criptoactive active virtuale", "criptoval": "criptoactive active virtuale",
    "cripto": "criptoactive", "bitcoin": "criptoactive", "crypto": "criptoactive",
}


def fts_query(text: str, extra=()) -> str:
    """Build an OR query of stemmed terms for FTS5."""
    seen = []
    words = tokens(" ".join([text, *extra]))
    words += tokens(" ".join(LEGAL_TERMS[_stem(t)] for t in words if _stem(t) in LEGAL_TERMS))
    for t in words:
        if t in STOP or len(t) < 2:
            continue
        s = _stem(t)
        if s not in seen:
            seen.append(s)
    return " OR ".join(f'"{s}"' for s in seen[:40])


def parse_date(s: str | None) -> str | None:
    """'8 octombrie 2026', '24-08-2021', '28.12.23' -> ISO date."""
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{1,2})\s+(?:de\s+)?([A-Za-zăîâșțşţĂÎÂȘȚ]+)\s+(\d{4})", s)
    if m and fold(m[2]) in MONTHS:
        return f"{m[3]}-{MONTHS[fold(m[2])]:02d}-{int(m[1]):02d}"
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4}|\d{2})\b", s)
    if m:
        y = m[3] if len(m[3]) == 4 else ("20" if int(m[3]) < 70 else "19") + m[3]
        return f"{y}-{int(m[2]):02d}-{int(m[1]):02d}"
    return None


KINDS = [
    # Genitive forms too: issue 2083 lists "Codului serviciilor media audiovizuale" (nr. 174/2018).
    (r"^leg(e|ea|ii)\b", "lege"), (r"^cod(ul|ului)?\b", "cod"), (r"^hot[aă]r[aâî]re", "hotarare"), (r"^decret", "decret"),
    (r"^ordin", "ordin"), (r"^regulament", "regulament"), (r"^dispozi", "dispozitie"), (r"^decizi", "decizie"),
    (r"^instruc", "ordin"), (r"^acord|^tratat|^conven", "tratat"), (r"^constitut", "constitutie"),
]


def kind_of(title: str) -> str:
    f = fold(title).strip()
    for pat, k in KINDS:
        if re.match(fold(pat), f):
            return k
    return "alt"


def rel_cls(kind: str) -> str:
    """Laws and codes share a numbering series, so references match across both."""
    return "lege" if kind in ("lege", "cod") else kind


# Act references such as "Legea nr. 182/2010", "Legii nr. 451-XV din 30 iulie 2001",
# "Codul fiscal nr. 1163-XIII din 24 aprilie 1997", "Hotărârea Guvernului nr. 357/2009".
_REF = re.compile(
    r"(?P<kw>Leg(?:ea|ii|e)|Cod(?:ul|ului)\b[^,;:()\n]{0,70}?|Hot[ăa]r[âîa]r(?:ea|ii)\s+(?:Guvernului|Parlamentului)|"
    r"Regulamentul(?:ui)?[^,;:()\n]{0,50}?|Ordinul(?:ui)?[^,;:()\n]{0,50}?)"
    r"\s+nr\.?\s*(?P<num>\d+)(?:-[IVXLC]+)?"
    r"(?:\s*/\s*(?P<y1>\d{4})|\s+din\s+\d{1,2}[.\s]+\S+?[.\s]+(?P<y2>\d{4}))",
    re.I,
)
_NAMED_CODES = {
    "codului vamal": (95, 2021), "codul vamal": (95, 2021),
    "codului fiscal": (1163, 1997), "codul fiscal": (1163, 1997),
    "codului muncii": (154, 2003), "codul muncii": (154, 2003),
    "codului civil": (1107, 2002), "codul civil": (1107, 2002),
    "codului penal": (985, 2002), "codul penal": (985, 2002),
    "codului contraventional": (218, 2008), "codul contraventional": (218, 2008),
    "codului de procedura penala": (122, 2003), "codul de procedura penala": (122, 2003),
    "codului de procedura civila": (225, 2003), "codul de procedura civila": (225, 2003),
}
_PARTIAL = re.compile(r"\b(art\.|articol\w*|alin\w*|punct\w*|pct\.|lit\.|liter\w*|capitol\w*|anex\w*|sintagm\w*|textul|cuvint\w*)\b", re.I)


def find_refs(text: str):
    """Yield (cls, num, year, start, end) for each act reference found."""
    for m in _REF.finditer(text):
        kw = fold(m["kw"])
        cls = "lege" if kw.startswith(("leg", "cod")) else "hotarare" if kw.startswith("hot") else (
            "ordin" if kw.startswith("ord") else "regulament")
        yield cls, int(m["num"]), int(m["y1"] or m["y2"]), m.start(), m.end()
    f = fold(text)
    for name, (num, year) in _NAMED_CODES.items():
        for m in re.finditer(name, f):
            yield "lege", num, year, m.start(), m.end()


def extract_relations(title: str, body: str, skip=None) -> list[dict]:
    """Detect which acts this act amends or repeals from its title and text."""
    out: dict[tuple, dict] = {}

    def add(cls, num, year, rel, whole, ev):
        if skip and (cls, num, year) == skip:
            return
        key = (cls, num, year, rel)
        cur = out.get(key)
        if cur is None or (whole and not cur["whole"]):
            out[key] = dict(to_cls=cls, to_num=num, to_year=year, rel=rel, whole=int(whole),
                            evidence=re.sub(r"\s+", " ", ev)[:300])

    ftitle = fold(title)
    if re.search(r"modificar|completar|modifica", ftitle):
        for cls, num, year, *_ in find_refs(title):
            add(cls, num, year, "modifica", False, title)
    t = re.search(r"abrogarea\s+(.*)", title, re.I)
    if t and (ref := _first_ref(t[1])):  # "pentru abrogarea Legii nr. 142/2005"
        add(ref[0], ref[1], ref[2], "abroga", True, title)

    flat = re.sub(r"\s+", " ", body or "")
    # "La articolul 25 din Legea nr. 320/2012 ..." and "Legea nr. X/Y ... se modifică"
    for cls, num, year, s, e in find_refs(flat):
        before, after = flat[max(0, s - 90):s], flat[e:e + 200]
        if re.search(r"\b(la|din)\s+(art\.|articolul|alineatul|punctul)", before, re.I) or re.match(
            r"[^.;]{0,150}?se (modific|complete)", after, re.I
        ):
            add(cls, num, year, "modifica", False, flat[max(0, s - 60):e + 120])
    for cls, num, year, whole, ev in _repeals(flat):
        add(cls, num, year, "abroga", whole, ev)
    return list(out.values())


_LEAD_IN = re.compile(
    r"^[\s–\-]*(?:(?:Art\.|Articolul)\s+[\dIVXLC]+\s*\.?[\s–\-]*)?(?:\(\d+\)\s*)?"
    r"(?:(?:la data|de la data|odat[ăa] cu|[îi]ncep[âa]nd cu|pe data)\b[^,]{0,120},\s*)?", re.I)
_LIST_MARK = re.compile(r"^\W*(?:[a-z]|\d{1,2})\)\s*|^\s*[–\-]\s*", re.I)


def _first_ref(text: str):
    """The reference `text` opens with, if any. An act named later in the clause is only mentioned."""
    hits = [r for r in find_refs(text) if r[3] <= 2]
    return min(hits, key=lambda r: r[3]) if hits else None


def _tail_is_partial(text: str, end: int) -> bool:
    """After the reference, words like 'art. 5' narrow the repeal. Citations in brackets do not."""
    return bool(_PARTIAL.search(re.sub(r"\([^)]*\)", " ", text[end:])))


def _repeals(flat: str):
    """Yield (cls, num, year, whole, evidence), only where the act is the object of the repeal.

    A false "abrogat" is the costly error: it would drop a law in force from every answer. Hence an
    act must open the clause, and any other act merely mentioned near "se abrogă" is ignored.
    """
    for m in re.finditer(r"se\s+abrog[ăa]\b", flat, re.I):
        before = flat[max(0, m.start() - 500):m.start()]
        # Clauses end at ; or : or at a full stop followed by a capital, so "nr. 100" stays whole.
        clause = re.split(r"(?<=[;:])\s|\.\s+(?=[A-ZĂÂÎȘȚŞŢ„(])", before)[-1]
        clause = _LEAD_IN.sub("", clause, count=1)
        ref = _first_ref(clause)
        if ref:  # "Legea nr. 5/2000 privind X se abrogă"
            yield ref[0], ref[1], ref[2], not _tail_is_partial(clause, ref[4]), clause + m.group(0)
        elif _PARTIAL.match(clause.strip()):  # "Articolul 7 din Legea nr. 5/2000 se abrogă"
            for r in find_refs(clause):
                yield r[0], r[1], r[2], False, clause + m.group(0)

        tail = flat[m.end():m.end() + 2500]
        direct = _first_ref(tail.lstrip())
        if direct:  # "se abrogă Legea nr. 5/2000"
            body = tail.lstrip()
            sentence = re.split(r"[;]|\.\s+(?=[A-ZĂÂÎȘȚŞŢ„(])", body)[0]
            yield direct[0], direct[1], direct[2], not _tail_is_partial(sentence, direct[4]), m.group(0) + " " + sentence
        lst = re.match(r"\s*(?:prin prezenta\s*)?:(.*)", tail, re.S)
        if lst:  # "Se abrogă: a) Legea nr. 1/2001; b) Legea nr. 2/2002."
            for item in re.split(r"(?<=;)\s", re.split(r"\s(?:Art\.|Articolul)\s", lst[1])[0]):
                stripped = _LIST_MARK.sub("", item.strip(), count=1)
                ref = _first_ref(stripped)
                if not ref:
                    break  # the enumeration has ended
                # An exception clause ("cu excepția art. 85–92") still repeals the act as such.
                head = re.split(r"cu excep[țţt]ia", stripped, flags=re.I)[0]
                yield ref[0], ref[1], ref[2], not _tail_is_partial(head, ref[4]), item
                if not item.rstrip().endswith(";"):
                    break

    # Titles: "Lege pentru abrogarea Legii nr. 142/2005 privind ..." is handled in the caller.


def can_repeal(from_kind: str, from_title: str, to_cls: str, to_pinned: bool) -> bool:
    """Legal hierarchy guard: an act cannot repeal a higher-ranking one."""
    rank = {"constitutie": 4, "lege": 3, "cod": 3, "hotarare": 2, "decret": 2, "regulament": 1, "ordin": 1, "dispozitie": 1, "decizie": 0}
    if rank.get(from_kind, 0) < rank.get(to_cls, 1):
        return False
    # A code is replaced by a new code, not repealed in passing by an omnibus amending law.
    return not (to_pinned and re.search(r"modific|complet", fold(from_title)))


_IN_FORCE = re.compile(
    r"(.{0,70}?)(?:intr[ăa]|intra)\s+[îi]n\s+vigoare\s+(?:la|de la|începând cu)\s+(?:data\s+de\s+)?"
    r"(\d{1,2}\s+[A-Za-zăîâșțşţ]+\s+\d{4})", re.I)
_WHOLE_ACT = re.compile(r"prezent(?:a|ul)\s+(?:lege|hot[ăa]r[âîa]re|ordin|regulament|cod|decret)\s*$", re.I)


def effective_dates(body: str) -> tuple[str | None, list[str]]:
    """Return (date the whole act enters into force, dates of individual provisions).

    Staged commencement is common: "art. I pct. 8 intră în vigoare la 1 ianuarie 2029" says nothing
    about the rest of the law, which is normally already in force.
    """
    flat = re.sub(r"\s+", " ", body or "")
    whole, staged = None, []
    for m in _IN_FORCE.finditer(flat):
        when = parse_date(m[2])
        if not when:
            continue
        if whole is None and _WHOLE_ACT.search(m[1].strip()):
            whole = when
        else:
            staged.append(when)
    return whole, sorted(set(staged))


def effective_date(body: str) -> str | None:
    return effective_dates(body)[0]


_ART = re.compile(r"^\s*(?:Articolul|Art\.)\s+((?:\d+[\d¹²³⁴⁵⁶⁷⁸⁹]*)|(?:[IVXLC]+\b))\s*[.\-–]?\s*(.*)$")
_ART_UNIC = re.compile(r"^\s*(Articol unic)\b\s*[.\-–]?\s*(.*)$", re.I)
_HEAD = re.compile(r"^\s*((?:TITLUL|Titlul|CAPITOLUL|Capitolul|SEC[ŢȚT]IUNEA|Sec[ţțt]iunea|PARTEA|Partea)\s+[\wIVXLC]+.*)$")


def _level(head: str) -> int:
    f = fold(head)
    return 0 if f.startswith("parte") else 1 if f.startswith("titl") else 2 if f.startswith("capit") else 3


def _unwrap(text: str) -> str:
    text = re.sub(r"(\w)[\-­]\n(\w)", r"\1\2", text)
    lines, out = text.split("\n"), []
    for ln in lines:
        ln = re.sub(r"[ \t\xa0]+", " ", ln).strip()
        if not ln:
            continue
        if out and not re.search(r"[.;:!?]$", out[-1]) and not re.match(r"^(\(\d+\)|[a-z]\)|\d+\)|–|-)", ln):
            out[-1] += " " + ln
        else:
            out.append(ln)
    return "\n".join(out)


def split_articles(body: str, max_len: int = 2400) -> list[dict]:
    """Split an act into article-level chunks with a Titlu > Capitol > Secțiune path."""
    heads: dict[int, str] = {}
    chunks: list[dict] = []
    cur = {"art": None, "lines": [], "path": ""}

    def flush():
        txt = _unwrap("\n".join(cur["lines"])).strip()
        if not txt:
            return
        pieces = [txt[i:i + max_len] for i in range(0, len(txt), max_len - 200)] if len(txt) > max_len else [txt]
        for p in pieces:
            chunks.append(dict(art=cur["art"], path=cur["path"], text=p))

    lines = (body or "").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        hm = _HEAD.match(ln)
        am = _ART.match(ln) or _ART_UNIC.match(ln)
        if hm and len(ln) < 160:
            flush()
            lvl, head = _level(hm[1]), hm[1].strip()
            # The chapter name is often on the next line.
            if i + 1 < len(lines) and lines[i + 1].strip() and len(lines[i + 1]) < 120 and not _ART.match(lines[i + 1]) \
                    and not _HEAD.match(lines[i + 1]):
                head += " " + lines[i + 1].strip()
                i += 1
            heads = {k: v for k, v in heads.items() if k < lvl}
            heads[lvl] = head
            cur = {"art": None, "lines": [], "path": " › ".join(heads[k] for k in sorted(heads))}
        elif am:
            flush()
            cur = {"art": am[1], "lines": [ln], "path": " › ".join(heads[k] for k in sorted(heads))}
        else:
            cur["lines"].append(ln)
        i += 1
    flush()
    return chunks

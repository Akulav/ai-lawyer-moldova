"""Parsing Monitorul Oficial: an issue's act listing (HTML) and its full text (PDF)."""
import bisect
import html
import re

import pymupdf

from .text import fold, kind_of, parse_date

# Raised whenever the listing parser changes: the crawler then re-reads every processed issue's listing
# once, and re-indexes those whose act list came out different.
LISTING_VERSION = "2"

# Matched on tag-free lines, because issues wrap their entries in <p> or <div> depending on the year.
_ITEM = re.compile(
    r"^(?P<art>\d{1,4})\.\s+(?P<title>.+?)\s*"
    # Some issues end entries with dot leaders: "(nr. 121-XVI, 4 mai 2007). ..........".
    # In a PDF's table of contents the leaders end in a page number.
    # Some put a dot inside the parenthesis: "(nr. 215, 28 decembrie 2015.)".
    r"\((?:nr\.?\s*(?P<nr>[^,()]{1,20}),\s*)?(?P<date>\d{1,2}[\s.]+(?:de\s+)?[^\s.,()]+[\s.]+\d{4})\.?\)\.?[ .…]*(?:\d{1,4})?$",
    re.M,
)
# Older issues run several entries together in one paragraph: "... 24 august 2007). 577. Lege ...".
_RUN_ON = re.compile(r"(\d{4}\.?\)\.?)[ \t]*(?=\d{1,4}\.\s*\S)")
# Entries without "(nr. X, date)": "1215. HOTĂRÎRE cu privire la ...", "635. CONSTITUŢIA REPUBLICII MOLDOVA din 29.07.1994",
# "1534. Planul general de conturi contabile". Only taken from the web listing, whose content area has no
# other numbered lines.
_LOOSE = re.compile(r"^(?P<art>\d{1,4})\.\s+(?P<title>[A-ZĂÂÎȘȚŞŢ„\"«].{8,}?)[ .…]*$")
_LOOSE_NR = re.compile(r"\bnr\.?\s*(?P<nr>[\w\-/]{1,20})", re.I)
_LOOSE_DATE = re.compile(r"\s+din\s+(?P<date>\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}|\d{1,2}\s+(?:de\s+)?[^\s\d]+\s+\d{4})\s*$", re.I)
_ENTRY_START = re.compile(r"^\d{1,4}\.\s")
_ENTRY_END = re.compile(r"\d{4}\.?\)\.?[ .…]*(?:\d{1,4})?$")


def _join_wrapped(lines: list[str]) -> list[str]:
    """Rejoin an entry the page broke over lines: "400. Decret ... (nr. 1171-IV," then "30 iunie 2007).".
    Only an open parenthesis or a lowercase continuation counts, so an entry without a date does not absorb
    the section heading after it; the length cap is a further guard."""
    out: list[str] = []
    for ln in lines:
        prev = out[-1] if out else ""
        if (_ENTRY_START.match(prev) and not _ENTRY_END.search(prev) and len(prev) < 600 and not _ENTRY_START.match(ln)
                and (prev.count("(") > prev.count(")") or not ln or ln[:1].islower())):
            if ln:
                out[-1] = f"{prev} {ln}"
            continue
        out.append(ln)
    return out


# The issue's own date on its page, "Marţi, 22 aprilie 2003". The list of recent issues beside it uses
# "din 31.10.2026" instead, so the weekday picks out the right one.
_ISSUE_DATE = re.compile(r"\b(?:Luni|Mar[țţ]i|Miercuri|Joi|Vineri|S[âî]mb[ăa]t[ăa]|Duminic[ăa]),\s*(\d{1,2}\s+\S+\s+\d{4})")


def parse_listing(page: str) -> dict:
    """Extract the issue title, publication date and listed acts from /ro/monitor/{id}."""
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    title = html.unescape(m[1]).strip() if m else ""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    # Block tags end a line. Inline ones (<strong>) may sit inside a title.
    body = re.sub(r"</?(?:p|div|li|br|h\d|tr|td|ul|ol|section|article)\b[^>]*>", "\n", body, flags=re.I)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    d = _ISSUE_DATE.search(body)
    return dict(title=title, published=parse_date(d[1]) if d else None, acts=_entries(body, loose=True))


def toc_entries(text: str) -> list[dict]:
    """Acts listed in the table of contents at the start of an issue's PDF. Used when the issue's web page
    lists nothing, which happens for some issues: the PDF's own SUMAR has the same entries."""
    acts, seen = [], set()
    for a in _entries(text[:80_000]):
        if a["art"] in seen:
            break  # numbering starts over: past the table of contents and into the acts
        seen.add(a["art"])
        acts.append(a)
    return acts


def _entries(body: str, loose: bool = False) -> list[dict]:
    """Listing entries "NNN. Title (nr. X, date)." in tag-free text, one per line after normalising, in order.
    With `loose`, a line "NNN. <act type> ..." without the parenthesis counts too."""
    body = body.replace("\xa0", " ")
    # Some pages double-escape the entity, which leaves "nbsp;" in the text and hides where an entry ends.
    body = re.sub(r"&?nbsp;", " ", body)
    body = _RUN_ON.sub(r"\1\n", body)
    acts, seen = [], set()
    for ln in _join_wrapped([re.sub(r"[ \t\r]+", " ", ln).strip() for ln in body.split("\n")]):
        if it := _ITEM.match(ln):
            t, nr, date = it["title"].strip(), (it["nr"] or "").strip() or None, it["date"]
        elif loose and (it := _LOOSE.match(ln)):
            t = it["title"].strip()
            d = _LOOSE_DATE.search(t)
            date = d["date"] if d else None
            t = t[:d.start()] if d else t
            n = _LOOSE_NR.search(t)
            nr = n["nr"] if n else None
        else:
            continue
        if int(it["art"]) in seen:
            continue
        seen.add(int(it["art"]))
        n = re.match(r"\d+", nr or "")
        adopted = parse_date(date)
        acts.append(dict(art=int(it["art"]), title=t, number=nr, num=int(n[0]) if n else None,
                         adopted=adopted, year=int(adopted[:4]) if adopted else None, kind=kind_of(t)))
    return acts


def pdf_url_from_viewer(page: str, base: str) -> str | None:
    doc = re.search(r"startDocument\s*=\s*'([^']+)'", page)
    sub = re.search(r"subfolder=([^&]*)&", page)
    if not doc:
        return None
    return f"{base}/flow/services/view.php?doc={doc[1]}&format=pdf&subfolder={(sub[1] if sub else '').replace(chr(92), '')}"


_NOISE = re.compile(
    r"^\s*(?:\d{1,4}|monitorul\.gov\.md|www\.[\w.]+|PARTEA\s+[IVX]+|\d{1,2}\s+\w+\s+\d{4}|"
    r"nr\.\s*[\d\-a-z]+(?:\s*\([\d\-]+\))?)\s*$", re.I)


# Issues from about 2001-2004 use a pre-Unicode Romanian font: diacritics come out as punctuation,
# "Hot=r]re" for "Hotărîre", "func\ia" for "funcția", "PRE{EDINTELE" for "PREȘEDINTELE".
_LEGACY = {"=": "ă", "]": "î", "\\": "ț", "[": "ș", "`": "â", "+": "Ă", "{": "Ș", "|": "Ț", "}": "Î"}
_LEGACY_SIGN = re.compile(r"[a-z][=\]\\\[][a-z]")
_LEGACY_CHAR = re.compile(r"(?<=[A-Za-z])[=\]\\\[`+{|}]|[=\]\\\[`+{|}](?=[A-Za-z])")


def fix_legacy_font(page: str) -> str:
    """Restore diacritics on a page set in the legacy font. A page qualifies only when such signs are common
    and real diacritics nearly absent, and only characters touching a letter change, so "[1]" or "2+2" stay."""
    signs = len(_LEGACY_SIGN.findall(page))
    if signs < 5 or sum(page.count(c) for c in "ăîâșțşţ") * 10 > signs:
        return page
    return _LEGACY_CHAR.sub(lambda m: _LEGACY[m[0]], page)


def pdf_text(data: bytes) -> tuple[str, str | None]:
    """Return the PDF's text without running page headers, and the issue's publication date."""
    pages, published = [], None
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            lines = fix_legacy_font(page.get_text()).split("\n")
            n = 0
            while n < min(8, len(lines)) and _NOISE.match(lines[n]):
                if published is None:
                    published = parse_date(lines[n])
                n += 1
            pages.append("\n".join(lines[n:]))
    return "\n".join(pages), published


def _normalized(text: str) -> tuple[str, list[int]]:
    """Alphanumeric-only folded text, with a map back to original offsets."""
    chars, idx = [], []
    for i, c in enumerate(text):
        f = fold(c)
        for ch in f:
            if ch.isalnum():
                chars.append(ch)
                idx.append(i)
    return "".join(chars), idx


def _key(title: str) -> str:
    """The title without its type word, because headings are typeset 'L E G E' and then the title."""
    t = re.sub(r"^\s*(?:Lege|Legea|Legii|Cod|Codul|Codului|Hot[ăa]r[âîa]re|Decret|Ordin|Regulament|Dispozi[țţt]ie|Decizie)\b",
               "", title, flags=re.I)
    return "".join(c for c in fold(t) if c.isalnum())


# A table-of-contents entry starts its line with the act's number and a dot: "709. Legea cetățeniei ...".
# In the body the heading comes first and the number follows without a dot.
_ENTRY = re.compile(r"(?:^|\n)[ \t]*(\d{1,4})\.\s", re.M)


def _in_toc(text: str, at: int, arts: set[int]) -> bool:
    """Whether a title match sits in the table of contents rather than at the act itself. Older issues have
    no dot leaders, and an entry can hold another act's title too, as in "708. Decret privind promulgarea
    Legii cetățeniei ...", so the entry's own number is the reliable sign."""
    if re.search(r"\.{6,}", text[at:at + 500]):
        return True
    # A title wraps over at most a few lines, so the entry's number is close before the match. The match must
    # still be inside that entry: one that already closed with "(nr. ..., date)", dot leaders or a blank line
    # is followed by the acts themselves, whose first heading can come right after it.
    entries = list(_ENTRY.finditer(text, max(0, at - 220), at))
    if not entries or int(entries[-1][1]) not in arts:
        return False
    between = text[entries[-1].end():at]
    return ")" not in between and "\n\n" not in between and "......" not in between


def split_acts(text: str, acts: list[dict]) -> dict[int, str]:
    """Slice issue text into acts, walking listed titles in order. Returns {art: body}."""
    norm, idx = _normalized(text)
    found: list[tuple[int, int]] = []
    pos = 0
    arts = {a["art"] for a in acts}  # every listed number counts for spotting the table of contents
    lines = None
    # Promulgation decrees and the like are dropped anyway, and they must not steer the walk: in newer issues
    # a law is printed before its decree although the listing has the decree first, so walking the decree
    # skipped past the law, whose title was then only found inside the decree ("Se promulgă Legea ...").
    for a in [a for a in acts if not is_noise_act(a["title"])]:
        key = _key(a["title"])[:48]
        if len(key) < 12:
            # Short titles like "Codul vamal" would match ordinary sentences, so require a whole
            # heading line ("C O D U L V A M A L"), with or without the article on the type word.
            if lines is None:
                lines, off = [], 0
                for ln in text.split("\n"):
                    lines.append((off, "".join(c for c in fold(ln) if c.isalnum())))
                    off += len(ln) + 1
            full = "".join(c for c in fold(a["title"]) if c.isalnum())
            names = {full, re.sub(r"^codul", "cod", full), re.sub(r"^cod(?!ul)", "codul", full)}
            floor = idx[pos] if pos < len(idx) else len(text)
            hit = next((o for o, n in lines if o >= floor and n in names), None)
            if hit is not None:
                found.append((a["art"], hit))
                pos = bisect.bisect_left(idx, hit) + len(full)
            continue
        at = norm.find(key, pos)
        while at >= 0:
            raw = idx[at]
            if not _in_toc(text, raw, arts):
                break
            at = norm.find(key, at + 1)
        if at >= 0:
            found.append((a["art"], idx[at]))
            pos = at + len(key)
    bodies = {}
    for i, (art, start) in enumerate(found):
        end = found[i + 1][1] if i + 1 < len(found) else len(text)
        # Back up over the next act's spaced-out type word so it does not end this body.
        body = text[start:end]
        body = re.sub(r"\n\s*(?:[A-ZĂÂÎȘȚŞŢ]\s){3,}[A-ZĂÂÎȘȚŞŢ]?\s*$", "", body)
        bodies[art] = body.strip()
    return bodies


def is_noise_act(title: str) -> bool:
    """Promulgation decrees and individual decorations add nothing to the corpus."""
    f = fold(title)
    return f.startswith("decret") and bool(re.search(r"promulgar|conferir|cetatenie|distinct", f))

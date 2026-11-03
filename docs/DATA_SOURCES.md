# Data sources

## Sources

| Source | Use | Access | Limits |
| --- | --- | --- | --- |
| [Monitorul Oficial](https://monitorul.gov.md) | Automatic: catalogue and PDF text | Open; `robots.txt` allows crawling | Text as originally published; issues from about 2001 |
| [legis.md](https://www.legis.md) | Manual PDF import, outbound links | Readable in a browser; scripts get a Cloudflare challenge | No automated download |

### Why legis.md isn't crawled

`legis.md` is the State Register of Legal Acts and has consolidated texts for each effective date, including future ones. Its `robots.txt` allows all crawlers and lists sitemaps, but in testing the sitemaps and documents returned a Cloudflare 403 challenge to scripts.

This project does not use browser impersonation, challenge solvers or similar techniques to get past that. Please don't contribute them. The right route to bulk access is permission from the register's operator, the Ministry of Justice through the Agenția Resurse Informaționale Juridice. If access is granted, a crawler can be added as another source, since the data model already supports versions.

### Importing consolidated texts

1. Open the act on `legis.md` and choose the version.
2. Click **Pdf** to save it.
3. Drag it into the **Index** page. For a future version, set the "în vigoare de la" date first.

Imports with no date are treated as in force from the import date. A consolidated text then replaces the published text in answers, and a future-dated version is only used for upcoming changes. Later amendments still appear as separate acts until you import a newer PDF.

## How text is processed

1. Page headers are removed from the PDF text.
2. The issue's listing gives the authoritative title, number and date for each act.
3. Each listed title is located in the text, skipping the table of contents, and the text between titles becomes that act. Short titles like "Codul vamal" must match a whole heading line, such as `C O D U L V A M A L`.
4. Acts are split at `Articolul N` and `Art. N`, with a Titlu › Capitol › Secțiune path, and long articles are cut into overlapping chunks.
5. Promulgation decrees and individual decorations and citizenship decrees are skipped as noise.

An act whose title can't be found stays in the catalogue without text. Scanned PDFs have no extractable text, because OCR isn't implemented, and they are listed on the **Probleme** page.

The gazette's PDFs, back to 2000, have a real text layer, so OCR would not recover the missing acts. They are missing for these reasons:

- **Published before 2000.** The site has no earlier issues, so Codul fiscal (1997) and Legea cu privire la tariful vamal (1997) can only be imported from `legis.md`.
- **Listing formats.** An issue's web page lists its acts in several formats over the years, and each used to lose entries:
  - several entries in one paragraph;
  - a double-escaped `&nbsp;` between entries, which gave an act the next entry's number. Legea 355/2005 was stored as "465-IV";
  - dot leaders after the entry;
  - an entry broken over two lines;
  - `28 de decembrie`, or a dot inside the parenthesis;
  - entries with no `(nr., date)` at all, such as `635. CONSTITUŢIA REPUBLICII MOLDOVA din 29.07.1994`.

  All are read now. A page that lists nothing falls back to the table of contents in the issue's PDF. Issues that list nothing in either place are announcement-only "BIS" editions.
- **Acts merged into one.** Decisions numbered `50/1`, `50/10`, `50/11` of one body on one day were stored as one act, with each text replacing the last. About 7,000 acts were missing this way. A number with a second numeric part now identifies the act in full.
- **Legacy font.** Issues from about 2001–2004, and some up to 2012, use a pre-Unicode font whose diacritics come out as punctuation, as in `Hot=r]re` for Hotărîre. Titles were not found in that text, and the text that was stored could not be searched. Such pages are converted, and only pages with that signature are touched.
- **Publication dates** come from the issue's web page ("Marţi, 22 aprilie 2003"). The PDF header was missing for 193 issues.
- **A law printed before its promulgation decree.** Newer issues print the law first, while the listing has the decree first. Walking the decree skipped past the law, whose title was then found only inside the decree ("Se promulgă Legea ..."). Promulgation decrees no longer steer the walk.
- **Genitive titles.** "Codului serviciilor media audiovizuale" (nr. 174/2018) was stored as "alt", so the 55 laws amending it could not find it. Titles are now recognised in that form, and acts already stored are re-typed.
- **A key act listed without a number.** Codul muncii appears in issue 1148 as "Codul muncii al Republicii Moldova", with no number or date. It now joins the key act instead of becoming a second, separate act. Duplicates of one act under two identities are merged.

Still open: two acts with identical titles in one issue, such as two laws "pentru modificarea unor acte normative", cannot be told apart by title, and either one can receive the other's text.

### Checking the index

The **Verificarea indexului** panel on the Index page runs read-only checks in a few seconds:
- key acts without text, and domains without any text;
- issues with errors, without a date, or with no acts read;
- texts with table-of-contents slices or the legacy font, very short law texts, and acts holding most of their issue;
- internal consistency: text flags, leftovers without an act, the search index, and acts stored twice;
- laws that are amended but missing from the catalogue;
- the tagging backlog.

**Repară automat** fixes only what is safe to fix: leftovers, the derived flags and the search index. Wrong texts are re-read by the crawler, which checks for them on every cycle.

When the listing parser changes, `LISTING_VERSION` in `gazette.py` is raised. The crawler then re-reads every processed issue's listing once, in a resumable pass: it removes acts stored under a misread entry, fills in missing dates, and re-indexes issues whose act list changed. Afterwards it clears and re-reads the issues whose stored text has table-of-contents slices or the legacy font.
- **Tables of contents without dot leaders.** In issues from about 2000–2001 the table of contents has no dot leaders, and titles were matched there instead of at the acts. Each act got its own table-of-contents line as text, and the last one got the rest of the issue. A title is now only accepted at the act itself: a match on a line that starts with a listed act's number and a dot (`709. Legea cetățeniei ...`) belongs to the table of contents. The 52 issues that stored such text are cleared and read again once. Legea cetățeniei (issue 2668) is one of the laws that now has its full text.

## How status is derived

| Label | Meaning |
| --- | --- |
| `presupus_in_vigoare` ("Fără abrogare detectată") | No repeal was detected. This doesn't prove the act is in force. |
| `abrogat` | A repeal was detected and is already effective |
| `abrogare_viitoare` | A repeal was detected with a future date |

**Repeals.** A whole-act repeal is only recorded when the act is the object of the repeal:

- `Legea nr. 5/2000 privind X se abrogă`
- `Se abrogă Legea nr. 5/2000`
- `Se abrogă: a) Legea nr. 1/2001; b) Legea nr. 2/2002`
- A title like `Lege pentru abrogarea Legii nr. 142/2005`

These are ignored or treated as partial:

- `Articolul 7 din Legea nr. 5/2000 se abrogă` (partial)
- An act merely mentioned near the words "se abrogă" (ignored)

Two guards apply when the status is computed: an act can't repeal a higher-ranking one, and a key code can't be repealed by an amending law.

**Replacement.** The repealing act is shown as the replacement unless its title indicates it only amends or repeals.

**Amendments.** Links come from titles and from phrases such as `La articolul 25 din Legea nr. 320/2012`. For laws and codes, only other laws and codes count as amendments, so an order merely citing a code doesn't.

**Future changes.** `Prezenta lege intră în vigoare la ...` sets the date for the whole act. A date attached to a single provision doesn't postpone the rest of the act, and is only flagged as an upcoming change.

### Known weaknesses

- Unusual wording can hide a real repeal, which leaves a repealed act looking valid.
- Exceptions are simplified: an act repealed "except art. 85–92" counts as repealed.
- Acts are matched by number and year, so acts sharing both within the same type can be confused.
- Only the first whole-act commencement date is used.
- `tree.py`'s key acts and their dates were written by hand, and only Codul vamal has a verified `legis.md` link.

## Sources of error in an answer

Treat the gazette and `legis.md` as authoritative over this app. Errors can come from PDF extraction, act splitting, status detection, retrieval missing the relevant article, or the model's reasoning.

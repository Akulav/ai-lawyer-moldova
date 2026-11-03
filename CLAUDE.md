# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

**Avocat AI Moldova**: a local, Windows-only legal information assistant for the laws of the Republic of Moldova. A single Python process (FastAPI) serves a Romanian single-page UI, crawls Monitorul Oficial into a SQLite FTS5 index in a background thread, tags acts into a legal tree with an LLM in another thread, and supervises a private Ollama instance. A Rust tray launcher (`launcher/`) packages it all as `AvocatAI.exe`.

Read `docs/ARCHITECTURE.md` (pipeline, schema, design rules) and `docs/DATA_SOURCES.md` (sources, status derivation) before changing behaviour.

## Commands

The shell is Windows PowerShell 5.1. `git` may not be on PATH.

```bash
uv sync --group dev                 # or use the existing .venv
uv run pytest                       # or: .\.venv\Scripts\python.exe -m pytest -q
uv run python -m app.main           # run the app from source (same as run.bat)
cd launcher && cargo build --release  # build AvocatAI.exe (needs Rust + MSVC)
node --check app/static/app.js      # quick JS syntax check; there is no JS build
```

Tests use a temporary `MAL_DATA` folder (`tests/conftest.py`) and need no network or model. `tests/test_problems.py` uses `TestClient(app)` without `with`, so no crawler or Ollama starts.

## Layout

| Path | Role |
| --- | --- |
| `app/main.py` | HTTP API, static UI, port selection, entry point |
| `app/rag.py` | Answering: route → acts → articles → amendments → answer → checks |
| `app/indexer.py` | Crawler thread, storing acts/versions/chunks, status derivation, PDF import |
| `app/gazette.py` | Parsing gazette listings and PDFs |
| `app/text.py` | Folding, stemming, references, repeal/date detection, article splitting |
| `app/tree.py` | Legal taxonomy, key (pinned) acts, keyword classification |
| `app/tagger.py` | Background LLM tagging of acts into domains |
| `app/verify.py` | Index health checks and safe repairs |
| `app/llm.py` | Portable Ollama lifecycle, model catalogue, inference |
| `app/db.py`, `app/config.py` | Schema; paths, ports, name/version, User-Agent |
| `app/static/` | Vanilla JS UI, no framework, no build step |
| `launcher/` | Rust tray app; embeds `app/`, `pyproject.toml`, `uv.lock` |
| `data/`, `.runtime/` | Generated at runtime (index, models, Ollama, uv, logs); git-ignored, many GB |

## Conventions

- **Language:** UI strings, prompts and user-facing errors are in Romanian with correct diacritics (ș ț ă â î). Code, comments and technical docs are in English. Internal state values are Romanian too (`gata`, `oprit`, `eroare`, `pauză`); match them.
- **Encoding:** save every file as UTF-8. Don't edit sources with PowerShell `Set-Content`/`Get-Content` (ANSI by default); `tests/test_encoding.py` catches mojibake.
- **Style:** dense, compact Python: `dict(...)` literals, short helpers, comments that explain *why* (often citing the real gazette issue that motivated a rule). Match it; don't reformat files.
- **DB connections:** every endpoint opens `db.connect()` and closes it in `finally`. SQLite has one writer; commit before anything slow (downloads, waits), as `Crawler._release` does.
- **Schema changes:** `CREATE ... IF NOT EXISTS` in `db.SCHEMA`; columns added later need an explicit `ALTER TABLE` in `db.init()`.
- **Re-deriving data:** one-off migrations are gated by settings keys in `Crawler._discover` / `_repair` (`repeal_rules`, `tree_kw`, `listing_version`, ...). Bump the key when detection rules change.
- **Detection rules** (repeals, parsing) need a test with the real wording, both a match and a non-match (`tests/test_parsing.py`).

## Do not break

- `/api/identity` must keep returning `app="moldova-ai-lawyer"`: the launcher (`launcher/src/setup.rs`) recognises the server by it. Likewise the `app.port` file and the `AVOCAT_*` env vars.
- Keep `pyproject.toml` and `uv.lock` in sync (commit the lock with any dependency change). The launcher and CI run `uv run --frozen`, which installs from the lock without re-resolving.
- Never attach to another Ollama: only a process this app started, from its own binary, on a port `port_is_free` verified. Process cleanup matches our own exe path only.
- Never invent law: answers come only from retrieved sources. Don't weaken prompt rules, citation checks, or disclaimers. "Fără abrogare detectată" deliberately avoids "în vigoare".
- Don't bypass access controls on data sources (no impersonation or challenge solvers); keep the crawler's User-Agent and delays.
- Server binds `127.0.0.1` by default; `/api/quit` only accepts local clients.

## Releases

Every push to `main` touching non-doc files runs `.github/workflows/release.yml`: tests, then builds `AvocatAI.exe` with version `MAJOR.MINOR` from `launcher/Cargo.toml` plus the next patch tag. The exe passes its version to the app as `AVOCAT_VERSION` (`config.VERSION`, `"dev"` from source).

## Branding

Name: **Avocat AI Moldova** (short: "Avocat AI"; exe: `AvocatAI.exe`). Colours: navy `#0b3a6f`, gold `#e6c563`. Logo: scales of justice, in `docs/assets/logo.svg` and `banner.svg`, the favicon in `app/static/index.html`, and `launcher/assets/icon.ico`.

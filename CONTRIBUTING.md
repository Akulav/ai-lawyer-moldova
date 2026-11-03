# Contributing

This project is source-available, not open source. You may fork it on GitHub to send changes back as pull requests, as section 1(e) of the [LICENSE](LICENSE) allows; please open an issue to discuss larger changes before starting work.

## Setup

You need [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`, so commit it with any dependency change.

```bash
uv sync --group dev
```

```bash
uv run pytest
```

```bash
uv run python -m app.main
```

Tests use a temporary data folder set in `tests/conftest.py`, so they never touch your index. They don't need network access or a model.

## The tray launcher

`launcher/` is a small Rust program that builds `AvocatAI.exe`. You need [Rust](https://rustup.rs/) with the MSVC target.

```bash
cd launcher && cargo build --release
```

The exe embeds `app/`, `pyproject.toml` and `uv.lock`. On start it does the following, in order:

1. Puts itself in a Windows job that ends every child process when the launcher exits.
2. Extracts the embedded sources next to itself, unless the folder holds a `.git` checkout.
3. Downloads uv into `.runtime/uv`.
4. Runs `uv run --frozen python -m app.main` hidden, logging to `.runtime/app.log`.
5. Opens the browser once `/api/identity` answers on the port written to `.runtime/app.port`.

**Ieșire** calls `POST /api/quit`, so the app shuts its AI engine down cleanly. The job ends whatever is left.

To try a change against this checkout, use the variables below, then run `target\debug\AvocatAI.exe`:

| Variable | Effect |
| --- | --- |
| `AVOCAT_ROOT` | The repository folder. The launcher uses this folder's sources as they are. |
| `AVOCAT_NO_BROWSER=1` | Keeps the browser from opening, for automated runs. |

## Releases

Every push to `main` that touches more than documentation runs `.github/workflows/release.yml`. It runs the tests, then builds `AvocatAI.exe` and publishes it as a GitHub Release.

The version is `MAJOR.MINOR` from `launcher/Cargo.toml`, plus the next patch number after the latest matching `vMAJOR.MINOR.*` tag. Nothing is committed back. To start a new series such as 1.1.0, change `version` in `launcher/Cargo.toml`.

## Project rules

- **Don't bypass access controls.** No browser impersonation, challenge solvers or similar techniques for any data source. See [Data sources](docs/DATA_SOURCES.md).
- **Keep the crawler polite.** Identify it in the User-Agent, keep the delays, and respect `robots.txt`.
- **Never invent law.** Answers must come only from retrieved sources. Don't weaken the prompt rules or citation checks, and keep the disclaimers.
- **Be honest about limits.** Status and coverage labels must not imply more certainty than the data supports. Note that "Fără abrogare detectată" deliberately avoids saying "în vigoare".
- **Stay isolated.** Never attach to another Ollama instance or a port the app didn't verify as free.
- **Keep the UI in Romanian.** Code and technical docs stay in English.

## Changing detection logic

Repeal and parsing rules affect legal correctness, so add a test using the real wording that prompted the change, for both what should match and what shouldn't. Examples are in `tests/test_parsing.py`.

Existing data needs re-deriving after a rule change. For repeal rules, bump the `repeal_rules` setting check in `Crawler._discover`, which rebuilds the relations from stored text without downloading anything.

Save source files as UTF-8. `tests/test_encoding.py` catches Romanian text corrupted by a wrong encoding. On Windows PowerShell 5.1, `Get-Content` and `Set-Content` default to ANSI, so avoid them for editing source files.

## Reporting a wrong answer

Please include:

- the question
- the answer and its listed sources
- the model used
- the correct provision, with a link to the official text

Don't include personal details from your legal situation in public issues.

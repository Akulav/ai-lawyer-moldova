# Contributing

This project is view-only, not open source. Running or modifying it requires written permission from the copyright holder, so please ask before starting work. Contributions are accepted under section 3 of the [LICENSE](LICENSE).

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

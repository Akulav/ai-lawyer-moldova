from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_sources_have_no_mojibake():
    """UTF-8 misread as ANSI turns Romanian text into sequences like these, and the UI then shows them."""
    bad = ("Ã®", "È™", "È›", "Äƒ", "â€")
    files = [*ROOT.glob("app/**/*.py"), *ROOT.glob("app/static/*"), *ROOT.glob("*.md"), *ROOT.glob("docs/*.md")]
    hits = [f.name for f in files if any(b in f.read_text(encoding="utf-8") for b in bad)]
    assert not hits

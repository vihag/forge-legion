"""Console copy must not use mock/simulated/test/fake wording."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE_DIR = ROOT / "console"
FORBIDDEN = ("mock", "simulated", "test", "fake")


def test_console_copy_has_no_discouraged_wording() -> None:
    """User-visible copy in HTML must avoid mock/simulated/test/fake wording."""
    names = ("index.html",)
    for name in names:
        text = (CONSOLE_DIR / name).read_text(encoding="utf-8").lower()
        for word in FORBIDDEN:
            assert word not in text, f"{name} contains {word!r}"
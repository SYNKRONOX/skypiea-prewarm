"""tools/check_claims.py (`make check-claims`): the docs agree with the committed evidence, and the
checker itself catches a wrong number, a broken link and an unknown make target."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_claims as cc  # noqa: E402


def test_the_repository_passes() -> None:
    rep = cc.run()
    assert rep.errors == []
    assert {"numbers", "links", "make", "paths", "commands"} <= set(rep.counts)


def test_make_calls_parsing() -> None:
    assert list(cc.make_calls("make lint check-sizes")) == [
        ("make lint check-sizes", ["lint", "check-sizes"])
    ]
    assert list(cc.make_calls("PF_BASE=1 make demo PYTHON=x"))[0][1] == ["demo"]
    assert list(cc.make_calls("make -f Makefile -f mk/console.mk watch-pods"))[0][1] == [
        "watch-pods"
    ]
    assert list(cc.make_calls("make setup: python3.12 not found"))[0][1] == []
    assert list(cc.make_calls("make demo stopped before changing anything"))[0][1] == ["demo"]


def test_slug_matches_github() -> None:
    assert cc.slug("Tier 0: tests only (no Docker)") == "tier-0-tests-only-no-docker"
    assert (
        cc.slug("LLMs used by the console (not distributed by us)")
        == "llms-used-by-the-console-not-distributed-by-us"
    )


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("results", "agent", "mk", "Makefile", "configs"):
        (tmp_path / name).symlink_to(ROOT / name)
    (tmp_path / "docs").mkdir()
    for f in (ROOT / "docs").glob("*.md"):
        shutil.copy(f, tmp_path / "docs" / f.name)
    shutil.copy(ROOT / "README.md", tmp_path / "README.md")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    return tmp_path


def test_a_changed_number_is_caught(fake_root: Path) -> None:
    readme = fake_root / "README.md"
    readme.write_text(
        readme.read_text().replace("median 2,572 to 8,366 ms", "median 2,000 to 8,366 ms")
    )
    rep = cc.Report()
    cc.check_numbers(rep)
    assert any("2,572 to 8,366" in e for e in rep.errors)


def test_a_broken_link_and_an_unknown_target_are_caught(fake_root: Path) -> None:
    (fake_root / "docs" / "x.md").write_text("[gone](nowhere.md) and `make no-such-target`\n")
    rep = cc.Report()
    cc.check_links(rep, ["docs/x.md"])
    cc.check_make(rep, ["docs/x.md"])
    assert any("broken link (nowhere.md)" in e for e in rep.errors)
    assert any("no make target 'no-such-target'" in e for e in rep.errors)

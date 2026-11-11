# File: tests/test_public_files.py
# What ships is written for its readers: no internal pipeline ids in any tracked file, the catalog entry matches the
# manifest, and every relative link and image in the README resolves to a tracked file.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JARGON = re.compile(r"DEV-\d|PRD-F|ARCH-F|PLAN-F|\bMUST\b|STRETCH|§|fact-check|PLAN\.md|AMEND|warroom|pg-probe|"
                    r"\[VERIFIED|\[ASSUMED|[Dd]ebug P\d|perspective point|\bDT-\d|STAGE-0|ADR-|test_debug_p\d")
THIS_FILE = Path(__file__).name


def tracked() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / line for line in out.splitlines() if line]


def test_no_internal_pipeline_ids_in_shipped_files() -> None:
    hits = []
    for path in tracked():
        if path.name == THIS_FILE or path.suffix in {".png", ".json", ".html"} or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if JARGON.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
    assert hits == []


def test_no_internal_working_files_are_tracked() -> None:
    names = {p.name for p in tracked()}
    internal = {"ARCHITECTURE.md", "PRD.md", "PLAN.md", "DECISIONS.md", "INVARIANTS.md", "SUBMISSION_STATUS.md",
                "BUILD-REPORT.md", "BUILD-RESULTS.md", "CRITIQUE-REPORT.md", "FEATURE-OBSERVABLES.md",
                "USER-ONBOARDING.md", "FOR[Dami].md"}
    assert names & internal == set()
    assert not any("debug-results" in p.parts or "phases" in p.parts or "drafts" in p.parts for p in tracked())


def _yaml(path: Path):
    from ruamel.yaml import YAML

    return YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))


def test_catalog_entry_matches_the_manifest() -> None:
    manifest, entry = _yaml(ROOT / "plugin.yaml"), _yaml(ROOT / "catalog" / "proving-ground.yaml")
    assert entry["capabilities"]["provides_hooks"] == manifest["provides_hooks"]
    assert entry["capabilities"]["provides_middleware"] == manifest["provides_middleware"]
    for key in ("requires_hermes", "version", "platforms", "name"):
        assert entry[key] == manifest[key], key


def test_readme_links_and_images_resolve() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s#]+)\)", text)
    local = [t for t in targets if not t.startswith(("http://", "https://", "mailto:"))]
    names = {str(p.relative_to(ROOT)) for p in tracked()}
    assert local and [t for t in local if t not in names] == []

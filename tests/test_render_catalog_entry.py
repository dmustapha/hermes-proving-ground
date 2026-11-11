# File: tests/test_render_catalog_entry.py
# scripts/render-catalog-entry.py pins the catalog entry to one full commit SHA: the comment header is stripped,
# every placeholder is replaced, and the result parses with ruamel and matches plugin.yaml. Bad input writes nothing.
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "render-catalog-entry.py"
TEMPLATE = ROOT / "catalog" / "proving-ground.yaml"


def _head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True)


def _yaml(text: str):
    from ruamel.yaml import YAML

    return YAML(typ="safe", pure=True).load(text)


def test_renders_the_entry_pinned_to_a_real_commit(tmp_path) -> None:
    sha, out = _head(), tmp_path / "proving-ground.yaml"
    before = TEMPLATE.read_bytes()
    done = _run(sha, "--out", str(out))
    assert done.returncode == 0, done.stderr
    text = out.read_text(encoding="utf-8")
    entry = _yaml(text)
    assert entry["sha"] == sha and "{{" not in text and not text.startswith("#")
    assert all(f"/{sha}/" in url for url in [entry["image"], *entry["screenshots"]])
    assert TEMPLATE.read_bytes() == before  # the template keeps its placeholder


def test_stdout_is_the_entry_when_no_out_is_given() -> None:
    done = _run(_head())
    assert done.returncode == 0 and _yaml(done.stdout)["name"] == "proving-ground"


@pytest.mark.parametrize("sha, why", [("a42a3bf", "not a full 40-character"),
                                      ("A" * 40, "not a full 40-character"),
                                      ("0" * 40, "is not a commit in this checkout")])
def test_short_uppercase_or_unknown_shas_are_refused_and_nothing_is_written(tmp_path, sha, why) -> None:
    out = tmp_path / "entry.yaml"
    done = _run(sha, "--out", str(out))
    assert done.returncode == 1 and why in done.stderr and not out.exists()


def test_problems_catch_an_unpinned_url_and_a_manifest_mismatch() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_catalog_entry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    sha = "1" * 40
    rendered = module.render(TEMPLATE.read_text(encoding="utf-8"), sha)
    manifest = module.load_yaml((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert module.problems(rendered, sha, manifest) == []
    broken = rendered.replace(f"{sha}/docs/matrix.png", "main/docs/matrix.png").replace('version: "0.1.0"',
                                                                                     'version: "9.9.9"')
    found = module.problems(broken, sha, manifest)
    assert any("matrix.png is not pinned" in p for p in found) and "version differs from plugin.yaml" in found
    with pytest.raises(ValueError):
        module.render(rendered, sha)  # already rendered: no placeholder left to substitute

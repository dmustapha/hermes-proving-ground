# File: tests/test_author_ci_recipe.py
# The copyable CI recipe (examples/github-action.yml + examples/author-calls.yaml) is the one PROVING GROUND's own
# CI runs: its calls pass the same validator and closed grammar a drill applies, it installs the same pinned Hermes
# as the hermes-validate job, and the author-recipe job overrides only the env values the recipe itself declares.
from __future__ import annotations

import argparse
from pathlib import Path

from proving_ground import cli

ROOT = Path(__file__).resolve().parent.parent
RECIPE = ROOT / "examples" / "github-action.yml"
CALLS = ROOT / "examples" / "author-calls.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "validate.yml"


def _yaml(path: Path):
    from ruamel.yaml import YAML

    return YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))


def _install_step(steps: list[dict]) -> str:
    return next(s["run"] for s in steps if "official installer" in s.get("name", ""))


def test_author_calls_pass_the_drill_validator_and_grammar() -> None:
    args = argparse.Namespace(calls=str(CALLS), only=None)
    templates = {t.id: t for t in cli.select_templates(args)}
    assert {"U01", "U02"} <= set(templates)
    assert dict(templates["U01"].expect) == {"baseline": ("PASSED_THROUGH",), "solo:fx_guard": ("BLOCKED",)}
    assert dict(templates["TC02"].expect) == {"solo:fx_guard": ("BLOCKED",)}


def test_recipe_installs_the_same_pinned_hermes_as_ci() -> None:
    recipe, workflow = _yaml(RECIPE), _yaml(WORKFLOW)
    recipe_install = _install_step(recipe["jobs"]["wiring-test"]["steps"])
    assert recipe_install == _install_step(workflow["jobs"]["hermes-validate"]["steps"])
    assert recipe_install == _install_step(workflow["jobs"]["linux-drill"]["steps"])


def test_recipe_runs_the_contract_check_on_the_declared_calls_file() -> None:
    steps = _yaml(RECIPE)["jobs"]["wiring-test"]["steps"]
    runs = " ".join(s.get("run", "") for s in steps)
    assert 'hermes plugins install "$PG_REPO" --ref "$PG_REF"' in runs
    assert 'hermes provingground --calls "$CALLS_FILE" --check' in runs


def test_recipe_holds_no_recursive_delete() -> None:
    """Hermes's install scan reads every file in the repo; a recursive delete in the recipe made the whole plugin
    a dangerous verdict (destructive_home_rm) and blocked `hermes plugins install`."""
    assert "rm -" not in RECIPE.read_text(encoding="utf-8")


def test_ci_overrides_only_the_env_the_recipe_declares() -> None:
    declared = set(_yaml(RECIPE)["env"])
    job = _yaml(WORKFLOW)["jobs"]["author-recipe"]
    runner_step = next(s for s in job["steps"] if "examples/github-action.yml" in s.get("run", ""))
    assert set(runner_step["env"]) <= declared
    assert "TMPDIR" not in _yaml(RECIPE)["env"]  # Hermes's Linux scratch TMPDIR falls back to /tmp on its own
    assert runner_step["env"]["PLUGIN_DIR"] == "fixtures/fx_guard"
    assert runner_step["env"]["CALLS_FILE"] == "examples/author-calls.yaml"

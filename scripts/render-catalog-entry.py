# File: scripts/render-catalog-entry.py
# stdlib + ruamel.yaml (the Hermes core pin). Usage:
#   python scripts/render-catalog-entry.py <40-char sha> [--out PATH] [--hermes-validator PATH]
# --hermes-validator runs Hermes's own scripts/validate_plugin_catalog.py on the result as a second, independent check.
"""Write the catalog entry pinned to one commit: the comment header is stripped, every {{PINNED_SHA}} becomes the
full 40-character SHA, and the result is parsed back with ruamel and checked against plugin.yaml. Exit 1 on any
problem, and nothing is written."""
from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "catalog" / "proving-ground.yaml"
MANIFEST = ROOT / "plugin.yaml"
PLACEHOLDER = "{{PINNED_SHA}}"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
RAW_HOST = "https://raw.githubusercontent.com/"
PARITY_KEYS = ("name", "version", "requires_hermes", "platforms")
PARITY_CAPABILITIES = ("provides_hooks", "provides_middleware")


def strip_header(text: str) -> str:
    """Drop the leading comment block (and blank lines around it); comments inside the entry are kept."""
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    return "\n".join(lines) + "\n"


def render(template: str, sha: str) -> str:
    body = strip_header(template)
    if PLACEHOLDER not in body:
        raise ValueError(f"the template has no {PLACEHOLDER} to substitute; it may be rendered already")
    return body.replace(PLACEHOLDER, sha)


def load_yaml(text: str) -> Any:
    """Read the way the catalog validator does: ruamel safe loader, YAML 1.1."""
    from ruamel.yaml import YAML

    reader = YAML(typ="safe", pure=True)
    reader.version = (1, 1)
    return reader.load(io.StringIO(text))


def _urls(entry: dict[str, Any]) -> list[str]:
    shots = entry.get("screenshots") or []
    return [u for u in [entry.get("image"), *shots] if isinstance(u, str)]


def problems(rendered: str, sha: str, manifest: dict[str, Any]) -> list[str]:
    """Every reason the rendered entry is not ready to submit; empty when it is."""
    try:
        entry = load_yaml(rendered)
    except Exception as exc:  # ruamel's YAMLError family
        return [f"rendered entry is not valid YAML ({type(exc).__name__})"]
    if not isinstance(entry, dict):
        return ["rendered entry is not a mapping"]
    found = [] if entry.get("sha") == sha else [f"sha field is {entry.get('sha')!r}, expected {sha}"]
    found += [f"a template placeholder is left: {line.strip()}" for line in rendered.splitlines() if "{{" in line]
    found += [f"{url} is not pinned to {sha}" for url in _urls(entry) if url.startswith(RAW_HOST) and f"/{sha}/" not in url]
    found += [f"{key} differs from plugin.yaml" for key in PARITY_KEYS if entry.get(key) != manifest.get(key)]
    capabilities = entry.get("capabilities") or {}
    found += [f"capabilities.{key} differs from plugin.yaml" for key in PARITY_CAPABILITIES
              if capabilities.get(key) != manifest.get(key)]
    return found


def commit_problem(sha: str) -> str | None:
    """The pin must be a commit this checkout has; outside a git checkout the check is skipped (said on stderr)."""
    try:
        done = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=ROOT, capture_output=True)
    except OSError:
        print("note: git is not available; the SHA was not checked against the history", file=sys.stderr)
        return None
    return None if done.returncode == 0 else f"{sha} is not a commit in this checkout (git rev-parse HEAD gives one)"


def hermes_validator_problems(validator: Path, rendered: str) -> list[str]:
    """Hermes's own catalog validator on the rendered entry, under the file name the catalog will use."""
    with tempfile.TemporaryDirectory(prefix="pg-catalog-") as scratch:
        target = Path(scratch) / TEMPLATE.name
        target.write_text(rendered, encoding="utf-8")
        done = subprocess.run([sys.executable, "-B", str(validator), str(target)], capture_output=True, text=True)
    output = (done.stdout + done.stderr).replace(str(target), TEMPLATE.name).strip()
    if output:
        print(f"Hermes catalog validator: {output}", file=sys.stderr)
    return [] if done.returncode == 0 else [f"Hermes's catalog validator exited {done.returncode}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", help="the full 40-character commit SHA to pin (git rev-parse HEAD)")
    parser.add_argument("--out", type=Path, default=None, help="write here instead of standard output")
    parser.add_argument("--hermes-validator", type=Path, default=None,
                        help="path to Hermes's scripts/validate_plugin_catalog.py, run on the result too")
    args = parser.parse_args(argv)
    if not FULL_SHA.match(args.sha):
        print(f"error: {args.sha!r} is not a full 40-character lowercase SHA (git rev-parse HEAD)", file=sys.stderr)
        return 1
    try:
        rendered = render(TEMPLATE.read_text(encoding="utf-8"), args.sha)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    found = problems(rendered, args.sha, load_yaml(MANIFEST.read_text(encoding="utf-8")))
    found += [p for p in [commit_problem(args.sha)] if p]
    if not found and args.hermes_validator is not None:
        found += hermes_validator_problems(args.hermes_validator, rendered)
    for problem in found:
        print(f"error: {problem}", file=sys.stderr)
    if found:
        return 1
    if args.out is None:
        sys.stdout.write(rendered)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out} pinned to {args.sha}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

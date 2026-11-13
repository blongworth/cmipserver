"""Write site/data/site_config.json from site/_variables.yml.

Run by Quarto as a pre-render step (see site/_quarto.yml) so the OJS dataset
browser sees the same prefixes and audience setting as the rest of the site.

With --production FILE (the nightly site build, in a throwaway worktree), it
first rewrites site/_variables.yml in place with the values from FILE
(site/_production.yml) and any --set KEY=VALUE, keeping comments and key
order. Only top-level, single-line keys that already exist can be replaced.
Values still holding a _TBD placeholder are listed on stderr.

Examples:
    uv run scripts/write_site_config.py
    uv run scripts/write_site_config.py --production site/_production.yml \
        --set catalog_version=2026-11-13
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

SITE = Path(__file__).resolve().parent.parent / "site"
KEYS = (
    "site_url",
    "github_repo",
    "help_email",
    "catalog_path",
    "catalog_version",
    "vast_prefix",
    "https_prefix",
    "globus_url",
    "audience",
    "wget_max_files",
)


def site_config(variables: dict) -> dict:
    missing = [k for k in KEYS if k not in variables]
    if missing:
        raise KeyError(f"_variables.yml is missing {missing}")
    if variables["audience"] not in ("internal", "external"):
        raise ValueError("audience must be 'internal' or 'external'")
    return {k: variables[k] for k in KEYS}


def apply_overrides(text: str, overrides: dict) -> str:
    """Replace top-level ``key: value`` lines in a YAML text, keeping the rest."""
    for key, value in overrides.items():
        pat = re.compile(rf"^{re.escape(key)}:[^\n]*$", re.MULTILINE)
        found = pat.findall(text)
        if len(found) != 1:
            raise KeyError(f"{key}: expected one top-level line, found {len(found)}")
        if found[0].split(":", 1)[1].split("#", 1)[0].strip()[:1] in (">", "|"):
            raise ValueError(f"{key}: block values cannot be overridden")
        if not isinstance(value, str | int | float | bool):
            raise TypeError(f"{key}: only scalar values can be overridden")
        line = f"{key}: {json.dumps(value)}"
        text = pat.sub(lambda _, line=line: line, text)
    if yaml.safe_load(text) is None:
        raise ValueError("overrides produced an empty file")
    return text


def placeholders(variables: dict) -> list[str]:
    return sorted(
        k for k, v in variables.items() if "_TBD" in str(v) or v == "OWNER/REPO"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--production", help="YAML of values to write into _variables.yml")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = ap.parse_args(argv)
    if args.set and not args.production:
        ap.error("--set needs --production")
    path = SITE / "_variables.yml"
    if args.production:
        overrides = yaml.safe_load(Path(args.production).read_text()) or {}
        for kv in args.set:
            key, sep, value = kv.partition("=")
            if not sep:
                ap.error(f"--set {kv}: expected KEY=VALUE")
            overrides[key] = value
        path.write_text(apply_overrides(path.read_text(), overrides))
    variables = yaml.safe_load(path.read_text())
    if args.production and (tbd := placeholders(variables)):
        print(f"placeholders still set: {', '.join(tbd)}", file=sys.stderr)
    out = SITE / "data" / "site_config.json"
    out.parent.mkdir(exist_ok=True)
    text = json.dumps(site_config(variables), indent=2) + "\n"
    if not out.exists() or out.read_text() != text:
        out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Write site/data/site_config.json from site/_variables.yml.

Run by Quarto as a pre-render step (see site/_quarto.yml) so the OJS dataset
browser sees the same prefixes and audience setting as the rest of the site.

Example:
    uv run scripts/write_site_config.py
"""

from __future__ import annotations

import json
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


def main() -> int:
    variables = yaml.safe_load((SITE / "_variables.yml").read_text())
    out = SITE / "data" / "site_config.json"
    out.parent.mkdir(exist_ok=True)
    text = json.dumps(site_config(variables), indent=2) + "\n"
    if not out.exists() or out.read_text() != text:
        out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

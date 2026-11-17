"""Print the output name of every enabled product in catalogs.yaml, one per line.

Used by slurm/build_site.sbatch to pick the live catalogs to summarize.

Example:
    uv run scripts/enabled_catalogs.py manifests/catalogs.yaml
"""

from __future__ import annotations

import argparse

from whoi_cmip_reorg_code.manifest import CatalogsManifest, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest", nargs="?", default="manifests/catalogs.yaml")
    args = ap.parse_args(argv)
    for name in load(args.manifest, CatalogsManifest).enabled_outputs:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

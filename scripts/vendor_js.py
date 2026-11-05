"""Download the pinned JavaScript, CSS, and fonts listed in site/vendor/vendor.yml.

Each file is fetched into its own fresh temporary directory, checked against
its sha256 in the manifest, and only then copied under site/vendor/. Nothing
downloaded is ever executed. A hash mismatch is an error.

With --update, the hashes of the downloaded files are written back into the
manifest instead (use after bumping a version, then review and commit).

Examples:
    uv run scripts/vendor_js.py
    uv run scripts/vendor_js.py --update
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

VENDOR = Path(__file__).resolve().parent.parent / "site" / "vendor"
MANIFEST = VENDOR / "vendor.yml"


def load_files(manifest: Path = MANIFEST) -> list[dict]:
    """Return every file entry in the manifest, with its package name."""
    data = yaml.safe_load(manifest.read_text())
    return [
        {**f, "package": p["name"], "version": str(p["version"])}
        for p in data["packages"]
        for f in p["files"]
    ]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_entry(f: dict) -> None:
    if not f["url"].startswith("https://cdn.jsdelivr.net/npm/"):
        raise ValueError(f"{f['path']}: URL must be on jsDelivr npm: {f['url']}")
    if f"{f['package']}@{f['version']}/" not in f["url"]:
        raise ValueError(f"{f['path']}: URL does not pin {f['package']}@{f['version']}")
    rel = Path(f["path"])
    if (
        rel.is_absolute()
        or ".." in rel.parts
        or not f["path"].startswith(f["package"] + "/")
    ):
        raise ValueError(f"{f['path']}: path must be under {f['package']}/")


def download(url: str) -> Path:
    """Fetch url into a new, empty temporary directory and return the file."""
    tmp = Path(tempfile.mkdtemp(prefix="vendor_js_"))
    out = tmp / Path(url).name
    with urllib.request.urlopen(url, timeout=60) as r, out.open("wb") as fh:
        shutil.copyfileobj(r, fh)
    return out


def set_hash(text: str, path: str, digest: str) -> str:
    pat = re.compile(rf'(path: "?{re.escape(path)}"?\n\s+sha256: )"[0-9a-f]*"')
    new, n = pat.subn(rf'\g<1>"{digest}"', text)
    if n != 1:
        raise ValueError(f"could not find sha256 line for {path}")
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", action="store_true", help="record new hashes")
    args = ap.parse_args(argv)

    files = load_files()
    text = MANIFEST.read_text()
    failed = False
    for f in files:
        check_entry(f)
        dest = VENDOR / f["path"]
        if not args.update and dest.exists() and sha256(dest) == f["sha256"]:
            print(f"ok        {f['path']}")
            continue
        got = download(f["url"])
        digest = sha256(got)
        if args.update:
            text = set_hash(text, f["path"], digest)
        elif digest != f["sha256"]:
            print(f"MISMATCH  {f['path']}: expected {f['sha256']}, got {digest}")
            shutil.rmtree(got.parent)
            failed = True
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(got, dest)
        shutil.rmtree(got.parent)
        print(f"wrote     {f['path']}")
    if args.update and text != MANIFEST.read_text():
        MANIFEST.write_text(text)
        print(f"updated   {MANIFEST.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

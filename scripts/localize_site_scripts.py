"""Point CDN script URLs in the rendered site at the copies in site/vendor/.

Run by Quarto as a post-render step (see site/_quarto.yml). Notebook pages
load require.js and jQuery (added by Quarto for any HTML cell output) and
plotly.js and MathJax (added by plotly) from CDNs, which the site's
Content-Security-Policy blocks. Every URL listed in site/vendor/vendor.yml
(a file's `url` and its `aliases`) is replaced by a relative link to the
vendored file; `integrity` attributes are kept, since the vendored files are
byte-identical. Afterwards any remaining external script or stylesheet is an
error, so a new CDN dependency fails the render instead of the live page.

Examples:
    uv run scripts/localize_site_scripts.py site/_site
    (Quarto runs it with no argument; it reads QUARTO_PROJECT_OUTPUT_DIR.)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import vendor_js

# External (http, https or protocol-relative) script sources, module imports,
# and stylesheet links.
EXTERNAL = [
    re.compile(r"""<script\b[^>]*\ssrc=["']((?:https?:)?//[^"']+)""", re.IGNORECASE),
    re.compile(r"""\bimport\s*["']((?:https?:)?//[^"']+)""", re.IGNORECASE),
    re.compile(
        r"""<link\b(?=[^>]*\brel=["']?stylesheet)[^>]*\shref=["']((?:https?:)?//[^"']+)""",
        re.IGNORECASE,
    ),
]


def url_map(manifest: Path = vendor_js.MANIFEST) -> dict[str, str]:
    """{external URL: path under vendor/} for every url and alias."""
    out = {}
    for f in vendor_js.load_files(manifest):
        for url in [f["url"], *f.get("aliases", [])]:
            out[url] = f["path"]
    return out


def localize(html: str, vendor_prefix: str, urls: dict[str, str]) -> str:
    """Replace each URL (only when it is a whole quoted value) with
    vendor_prefix + its vendored path. Longest URLs first, so a URL that is a
    prefix of another never replaces part of it."""
    for url in sorted(urls, key=len, reverse=True):
        html = re.sub(
            rf"""(?<=["']){re.escape(url)}(?=["'])""",
            lambda _m, p=vendor_prefix + urls[url]: p,
            html,
        )
    return html


def external_refs(html: str) -> list[str]:
    return [m.group(1) for pat in EXTERNAL for m in pat.finditer(html)]


def vendor_prefix(page: Path, site: Path) -> str:
    """Relative path from page's directory to site/vendor/, starting with
    ./ or ../ so it also works as an ES module specifier."""
    depth = len(page.relative_to(site).parent.parts)
    return "../" * depth + "vendor/" if depth else "./vendor/"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "site",
        nargs="?",
        type=Path,
        default=Path(os.environ.get("QUARTO_PROJECT_OUTPUT_DIR", "_site")),
        help="rendered site directory (default: $QUARTO_PROJECT_OUTPUT_DIR)",
    )
    args = ap.parse_args(argv)
    site = args.site
    if not site.is_dir():
        print(f"no such directory: {site}", file=sys.stderr)
        return 2

    urls = url_map()
    changed = 0
    bad = []
    for page in sorted(site.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        new = localize(text, vendor_prefix(page, site), urls)
        if new != text:
            page.write_text(new, encoding="utf-8")
            changed += 1
        bad += [f"{page.relative_to(site)}: {u}" for u in external_refs(new)]
    print(f"localize_site_scripts: {changed} page(s) rewritten")
    if bad:
        print(
            "External scripts or stylesheets left (vendor them in "
            "site/vendor/vendor.yml, with the injected URL as an alias):",
            *bad,
            sep="\n  ",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

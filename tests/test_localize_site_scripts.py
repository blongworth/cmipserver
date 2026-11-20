from pathlib import Path

import localize_site_scripts as ls

URLS = {
    "https://cdn.plot.ly/plotly-4.1.1.min.js": "plotly.js-dist-min/plotly.min.js",
    "https://cdn.plot.ly/plotly-4.1.1.min": "plotly.js-dist-min/plotly.min.js",
    "https://cdn.jsdelivr.net/npm/requirejs@2.3.6/require.min.js": "requirejs/require.min.js",
}


def test_localize_replaces_whole_quoted_urls_only():
    html = (
        '<script src="https://cdn.jsdelivr.net/npm/requirejs@2.3.6/require.min.js" '
        'integrity="sha384-x"></script>\n'
        '<script type="module">import "https://cdn.plot.ly/plotly-4.1.1.min"</script>\n'
        '<script charset="utf-8" src="https://cdn.plot.ly/plotly-4.1.1.min.js"></script>\n'
        "<p>see https://cdn.plot.ly/plotly-4.1.1.min.js</p>"
    )
    out = ls.localize(html, "../vendor/", URLS)
    assert 'src="../vendor/requirejs/require.min.js" integrity="sha384-x"' in out
    assert 'import "../vendor/plotly.js-dist-min/plotly.min.js"' in out
    assert 'src="../vendor/plotly.js-dist-min/plotly.min.js"' in out
    # prose is not a quoted attribute or specifier: untouched
    assert "<p>see https://cdn.plot.ly/plotly-4.1.1.min.js</p>" in out
    assert ls.external_refs(out) == []


def test_external_refs_finds_scripts_imports_and_stylesheets():
    html = (
        '<script src="https://a.example/x.js"></script>'
        '<script src="//b.example/y.js"></script>'
        '<script src="site_libs/local.js"></script>'
        "<script type=module>import 'https://c.example/m.js'</script>"
        '<link rel="stylesheet" href="https://d.example/s.css">'
        '<link href="https://e.example/f.css" rel="stylesheet">'
        '<link rel="icon" href="https://f.example/i.png">'
        '<a href="https://g.example/">link</a>'
    )
    assert ls.external_refs(html) == [
        "https://a.example/x.js",
        "//b.example/y.js",
        "https://c.example/m.js",
        "https://d.example/s.css",
        "https://e.example/f.css",
    ]


def test_vendor_prefix_is_a_valid_module_specifier():
    site = Path("/s")
    assert ls.vendor_prefix(site / "index.html", site) == "./vendor/"
    assert ls.vendor_prefix(site / "notebooks/q.html", site) == "../vendor/"
    assert (
        ls.vendor_prefix(site / "blog/posts/p/index.html", site) == "../../../vendor/"
    )


def test_url_map_covers_injected_notebook_urls():
    urls = ls.url_map()
    for url in (
        "https://cdn.plot.ly/plotly-4.1.1.min.js",
        "https://cdn.plot.ly/plotly-4.1.1.min",
        "https://cdn.jsdelivr.net/npm/requirejs@2.3.6/require.min.js",
        "https://cdn.jsdelivr.net/npm/jquery@3.5.1/dist/jquery.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-svg.min.js",
    ):
        assert url in urls


def test_main_rewrites_and_fails_on_leftovers(tmp_path, capsys):
    (tmp_path / "nb").mkdir()
    page = tmp_path / "nb/p.html"
    page.write_text('<script src="https://cdn.plot.ly/plotly-4.1.1.min.js"></script>')
    assert ls.main([str(tmp_path)]) == 0
    assert 'src="../vendor/plotly.js-dist-min/plotly.min.js"' in page.read_text()
    (tmp_path / "bad.html").write_text(
        '<script src="https://evil.example/x.js"></script>'
    )
    assert ls.main([str(tmp_path)]) == 1
    assert "bad.html: https://evil.example/x.js" in capsys.readouterr().err

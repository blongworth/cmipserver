import pytest
import vendor_js

FILES = vendor_js.load_files()


@pytest.mark.parametrize("f", FILES, ids=[f["path"] for f in FILES])
def test_vendored_file_matches_manifest(f):
    vendor_js.check_entry(f)
    path = vendor_js.VENDOR / f["path"]
    assert path.exists(), "missing; run: uv run scripts/vendor_js.py"
    assert len(f["sha256"]) == 64
    assert vendor_js.sha256(path) == f["sha256"]


def test_set_hash_replaces_one_line():
    text = 'path: a/b.js\n        sha256: ""\npath: a/c.js\n        sha256: ""\n'
    out = vendor_js.set_hash(text, "a/b.js", "ab" * 32)
    assert out.count("ab" * 32) == 1
    assert 'path: a/c.js\n        sha256: ""' in out

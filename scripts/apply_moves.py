"""Apply a move manifest. DRY RUN unless --execute.

--execute needs the manifest's approved_by/approved_on filled in (Brett's
written sign-off) and refuses if any move would overwrite or lacks its src.
Every step goes to the append-only --log; re-running resumes.

Examples:
    uv run scripts/apply_moves.py --manifest manifests/moves/x.yaml --log runs/x.jsonl
    uv run scripts/apply_moves.py ... --execute --limit 10   # first batch only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from whoi_cmip_reorg_code.manifest import MoveManifest, load
from whoi_cmip_reorg_code.movelog import MoveError, MoveLog, apply, check


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--log", required=True, help="append-only JSONL move log")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--limit", type=int, help="apply at most N pending moves")
    args = ap.parse_args(argv)

    m = load(args.manifest, MoveManifest)
    log = MoveLog(Path(args.log))
    p = check(m, log)
    print(
        f"{'EXECUTE' if args.execute else 'DRY RUN'}: {len(m.moves)} moves, "
        f"done={p.done} pending={len(p.todo)} "
        f"resumable={len(p.finish_unlink) + len(p.finish_log)} "
        f"problems={len(p.problems)} approved={m.approved}"
    )
    for msg in p.problems[:50]:
        print("  PROBLEM", msg)
    if not args.execute:
        for _, src, dst, kind in p.todo[:20]:
            print(f"  would {kind}: {src} -> {dst}")
        if len(p.todo) > 20:
            print(f"  ... {len(p.todo) - 20} more")
        return 0
    try:
        apply(m, log, execute=True, limit=args.limit)
    except MoveError as exc:
        print(f"REFUSED/STOPPED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

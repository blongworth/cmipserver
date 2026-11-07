"""Undo moves recorded in a move log, newest first. DRY RUN unless --execute.

Moves each dst back to its src (never overwriting) and removes directories
the run created if they're empty. --run-id limits it to one apply run.

Example:
    uv run scripts/rollback_moves.py --log runs/x.jsonl            # dry run
    uv run scripts/rollback_moves.py --log runs/x.jsonl --execute
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from whoi_cmip_reorg_code.movelog import MoveError, MoveLog, plan_rollback, rollback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True)
    ap.add_argument("--run-id", help="only undo this run (see the log)")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    log = MoveLog(Path(args.log))
    if not log.path.exists():
        ap.error(f"no log at {log.path}")
    rp = plan_rollback(log, args.run_id)
    print(
        f"{'EXECUTE' if args.execute else 'DRY RUN'}: undo {len(rp.unmoves)} moves, "
        f"remove up to {len(rp.rmdirs)} dirs, problems={len(rp.problems)}"
    )
    for msg in rp.problems[:50]:
        print("  PROBLEM", msg)
    if not args.execute:
        for src, dst in rp.unmoves[:20]:
            print(f"  would undo: {dst} -> {src}")
        return 0
    try:
        rollback(log, execute=True, run_id=args.run_id)
    except MoveError as exc:
        print(f"REFUSED/STOPPED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

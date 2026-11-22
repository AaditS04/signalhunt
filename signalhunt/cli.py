"""Command-line entry point.

  python -m signalhunt.cli run "Find companies in Gujarat ..." [--out result.json]
  python -m signalhunt.cli resume            # finish any run interrupted by a crash
  python -m signalhunt.cli result RUN_ID
  python -m signalhunt.cli approve ACTION_ID --by "Name" --to buyer@company.com
"""
from __future__ import annotations

import argparse
import json
import sys

from . import approvals, db, workflow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="signalhunt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("request"); r.add_argument("--out")
    sub.add_parser("resume")
    g = sub.add_parser("result"); g.add_argument("run_id"); g.add_argument("--out")
    a = sub.add_parser("approve"); a.add_argument("action_id"); a.add_argument("--by", required=True); a.add_argument("--to", required=True)
    sub.add_parser("actions")
    args = ap.parse_args(argv)
    db.init_db()
    if args.cmd == "run":
        rid = workflow.create_run(args.request)
        workflow.execute_run(rid)
        return _emit(workflow.run_result(rid), args.out)
    if args.cmd == "resume":
        approvals.reconcile()
        with db.connect() as con:
            ids = [x["id"] for x in con.execute("SELECT id FROM runs WHERE status IN ('RUNNING','QUEUED') ORDER BY created_at")]
        for rid in ids:
            workflow.execute_run(rid)
            print(json.dumps({"resumed": rid, "status": workflow.get_run(rid)["status"]}))
        return 0
    if args.cmd == "result":
        return _emit(workflow.run_result(args.run_id), args.out)
    if args.cmd == "approve":
        print(json.dumps(approvals.approve(args.action_id, args.by, args.to), indent=2, default=str))
        return 0
    if args.cmd == "actions":
        print(json.dumps(approvals.list_actions(), indent=2, default=str))
        return 0
    return 1


def _emit(res: dict, out: str | None) -> int:
    txt = json.dumps(res, indent=2, ensure_ascii=False, default=str)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(txt)
        print(f"wrote {out}: {len(res.get('opportunities', []))} opportunities, status {res.get('status')}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

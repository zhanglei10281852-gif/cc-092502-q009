from __future__ import annotations

import argparse
import json
import sys

from fastapi.testclient import TestClient

from app.database import connection, init_db


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init-db", "check-db", "smoke", "export", "import"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("-o", "--output")
    parser.add_argument("-i", "--input")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--new-code")
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print(json.dumps({"status": "initialized"}, ensure_ascii=False))
        return 0
    if args.command == "check-db":
        init_db()
        db = connection()
        print(json.dumps({"integrity": db.execute("PRAGMA integrity_check").fetchone()[0], "foreign_keys": db.execute("PRAGMA foreign_keys").fetchone()[0], "tables": db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]}, ensure_ascii=False))
        return 0
    if args.command == "smoke":
        from app.main import app
        with TestClient(app) as client:
            root = client.get("/")
            health = client.get("/api/system/health")
            print(json.dumps({"root": root.json(), "health": health.json(), "status_codes": [root.status_code, health.status_code]}, ensure_ascii=False))
        return 0
    if args.command == "export":
        if args.project_id is None:
            parser.error("export 需要 --project-id")
        init_db()
        from app.chrono_service import ChronoService
        bundle = ChronoService().export_bundle(args.project_id)
        text = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(text)
            print(json.dumps({"status": "exported", "path": args.output, "tables": {k: len(v) for k, v in bundle.items() if isinstance(v, list)}}, ensure_ascii=False))
        else:
            sys.stdout.write(text)
        return 0
    if args.command == "import":
        path = args.input or (args.args[0] if args.args else None)
        if not path:
            parser.error("import 需要 -i bundle.json")
        init_db()
        with open(path, encoding="utf-8") as handle:
            bundle = json.load(handle)
        from app.chrono_service import ChronoService
        result = ChronoService().import_bundle(bundle, new_code=args.new_code, actor_id=None)
        print(json.dumps({"status": "imported", **result}, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

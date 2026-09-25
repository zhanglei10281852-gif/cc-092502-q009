from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.database import close_connection, connection, init_db


def _load_client():
    from app.main import app
    return TestClient(app)


def main() -> int:
    parser = argparse.ArgumentParser(description="考古研究协作基础服务命令行")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("check-db")
    sub.add_parser("smoke")
    p_export = sub.add_parser("chron-export", help="导出项目全部年代学数据为 JSON 文件")
    p_export.add_argument("--project-id", type=int, required=True)
    p_export.add_argument("--out", required=True)
    p_import = sub.add_parser("chron-import", help="从 JSON 文件离线导入年代学数据")
    p_import.add_argument("--file", required=True)
    p_import.add_argument("--project-code", default=None)
    p_seed = sub.add_parser("chron-seed-demo", help="写入鲍家遗址演示数据集（固定值）")
    p_seed.add_argument("--project-code", default="BAOJIA-DEMO")
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
        with _load_client() as client:
            root = client.get("/")
            health = client.get("/api/system/health")
            print(json.dumps({"root": root.json(), "health": health.json(), "status_codes": [root.status_code, health.status_code]}, ensure_ascii=False))
        return 0

    init_db()
    if args.command == "chron-export":
        from app.chronology_cli import run_export
        bundle = run_export(args.project_id)
        Path(args.out).write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"exported": args.project_id, "out": args.out,
                          "evidence": len(bundle["evidence"]), "snapshots": len(bundle["snapshots"])}, ensure_ascii=False))
        close_connection()
        return 0
    if args.command == "chron-import":
        from app.chronology_cli import run_import
        bundle = json.loads(Path(args.file).read_text(encoding="utf-8"))
        summary = run_import(bundle, project_code=args.project_code)
        print(json.dumps(summary, ensure_ascii=False))
        close_connection()
        return 0
    if args.command == "chron-seed-demo":
        from app.chronology_cli import seed_demo
        print(json.dumps(seed_demo(args.project_code), ensure_ascii=False))
        close_connection()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

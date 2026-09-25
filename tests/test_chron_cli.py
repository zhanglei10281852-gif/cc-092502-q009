"""离线 CLI 命令：固定演示数据集播种、导出、导入往返与命令行结果可复现。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(db_path: Path, *args: str) -> dict:
    env = {"PATH": __import__("os").environ.get("PATH", ""),
           "ARCHAEOLOGY_DATABASE_PATH": str(db_path),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.run([sys.executable, "-m", "app.cli", *args],
                          cwd=env["PYTHONPATH"], env=env, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_seed_export_import_cli(tmp_path):
    seed_db = tmp_path / "seed.db"
    seeded = _run(seed_db, "init-db")
    assert seeded["status"] == "initialized"
    seeded = _run(seed_db, "chron-seed-demo", "--project-code", "BAOJIA-DEMO")
    assert seeded["sites"] == 3 and seeded["layers"] == 6
    assert seeded["evidence"] == 14
    assert seeded["satisfiable"] is True
    first_hash = seeded["result_hash"]

    # 导出为文件
    out_file = tmp_path / "export.json"
    exported = _run(seed_db, "chron-export", "--project-id", str(seeded["project_id"]),
                    "--out", str(out_file))
    assert exported["evidence"] == 14
    bundle = json.loads(out_file.read_text(encoding="utf-8"))
    assert bundle["format"] == "regional-chronology-export/v1"

    # 全新离线库导入：快照结果哈希与播种库一致（跨库可复现）
    copy_db = tmp_path / "copy.db"
    _run(copy_db, "init-db")
    summary = _run(copy_db, "chron-import", "--file", str(out_file), "--project-code", "BAOJIA-COPY")
    assert summary["evidence"] == 14 and summary["snapshots"] == 1

    import sqlite3
    db = sqlite3.connect(copy_db)
    row = db.execute(
        "SELECT c.result_json FROM ch_computations c JOIN ch_snapshots s ON s.id=c.snapshot_id "
        "WHERE s.snapshot_key='snap-1'").fetchone()
    assert json.loads(row[0])["result_hash"] == first_hash
    db.close()

    # 重复播种不覆盖既有项目（再跑一次报 409 服务错误，退出非 0）
    env = {"PATH": __import__("os").environ.get("PATH", ""),
           "ARCHAEOLOGY_DATABASE_PATH": str(seed_db),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.run([sys.executable, "-m", "app.cli", "chron-seed-demo"],
                          cwd=env["PYTHONPATH"], env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "site_exists" in proc.stderr or "UNIQUE" in proc.stderr

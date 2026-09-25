"""年代学模块的离线命令支持：导入、导出、固定演示数据集。"""
from __future__ import annotations

from typing import Any

from app.database import now, transaction
from app.security import password_hash
from app.chronology_service import ChronologyService

# 固定演示数据集：鲍家遗址及其周边遗址，全部年份为 cal BP。
# 刻意混入：观测测年、观测类型学范围、推断先后约束、暂定标签——
# 以便演示三类材料的区分与区间传播。
DEMO_DATASET: dict[str, Any] = {
    "project": {"code": "BAOJIA-DEMO", "name": "鲍家遗址文化阶段衔接研究", "site_name": "鲍家遗址"},
    "sites": [
        {"site_key": "baojia", "name": "鲍家遗址", "region": "太湖西部", "latitude": None, "longitude": None},
        {"site_key": "qili", "name": "七里亭遗址", "region": "太湖西部", "latitude": None, "longitude": None},
        {"site_key": "sanjiao", "name": "三礁遗址", "region": "钱塘江北岸", "latitude": None, "longitude": None},
    ],
    "layers": [
        {"site_key": "baojia", "layer_key": "bj-l3", "name": "鲍家第3层（下文化层）", "sequence_no": 3},
        {"site_key": "baojia", "layer_key": "bj-l2", "name": "鲍家第2层（中文化层）", "sequence_no": 2},
        {"site_key": "baojia", "layer_key": "bj-l1", "name": "鲍家第1层（上文化层）", "sequence_no": 1},
        {"site_key": "qili", "layer_key": "ql-l2", "name": "七里亭下层", "sequence_no": 2},
        {"site_key": "qili", "layer_key": "ql-l1", "name": "七里亭上层", "sequence_no": 1},
        {"site_key": "sanjiao", "layer_key": "sj-l1", "name": "三礁文化层", "sequence_no": 1},
    ],
    "sources": [
        {"source_key": "bj-report-2019", "title": "鲍家遗址发掘简报", "authors": "区域考古课题组",
         "citation": "考古2019(6)", "year_pub": 2019, "note": ""},
        {"source_key": "qs-survey-2021", "title": "钱塘江北岸区域调查报告", "authors": "调查队",
         "citation": "东南文化2021(3)", "year_pub": 2021, "note": ""},
    ],
    "evidence": [
        # 观测：AMS 测年概率区间（95.4% 两段，外包 6300-5900 BP）
        {"kind": "dating", "layer_key": "bj-l3", "source_key": "bj-report-2019",
         "evidence_key": "ams-bj-l3-a", "sample_code": "BAOJIA-07", "method": "AMS", "probability": "95.4%",
         "bound_lo": None, "bound_hi": None,
         "ranges": [{"lo": 6200, "hi": 6300, "probability": 0.68}, {"lo": 5900, "hi": 6150, "probability": 0.274}],
         "basis": "炭化稻炭样 AMS 测定", "note": ""},
        {"kind": "typology", "layer_key": "bj-l3", "source_key": "bj-report-2019",
         "evidence_key": "typo-majiabang-early", "artifact_type": "腰沿釜（早期马家浜型）",
         "bound_lo": 6100, "bound_hi": 6400, "ranges": [], "basis": "类型学出现范围", "note": ""},
        {"kind": "dating", "layer_key": "bj-l2", "source_key": "bj-report-2019",
         "evidence_key": "ams-bj-l2-a", "sample_code": "BAOJIA-12", "method": "AMS", "probability": "95.4%",
         "bound_lo": 5650, "bound_hi": 5900, "ranges": [], "basis": "炭样 AMS 测定", "note": ""},
        {"kind": "typology", "layer_key": "bj-l2", "source_key": "bj-report-2019",
         "evidence_key": "typo-majiabang-late", "artifact_type": "牛鼻耳罐（晚期马家浜型）",
         "bound_lo": 5600, "bound_hi": 5900, "ranges": [], "basis": "类型学出现范围", "note": ""},
        {"kind": "dating", "layer_key": "bj-l1", "source_key": "bj-report-2019",
         "evidence_key": "ams-bj-l1-a", "sample_code": "BAOJIA-19", "method": "AMS", "probability": "95.4%",
         "bound_lo": 5250, "bound_hi": 5550, "ranges": [], "basis": "炭样 AMS 测定", "note": ""},
        {"kind": "typology", "layer_key": "ql-l2", "source_key": "qs-survey-2021",
         "evidence_key": "typo-ql-l2", "artifact_type": "腰沿釜",
         "bound_lo": 6050, "bound_hi": 6350, "ranges": [], "basis": "调查采集品型式判断", "note": ""},
        {"kind": "dating", "layer_key": "ql-l1", "source_key": "qs-survey-2021",
         "evidence_key": "ams-ql-l1-a", "sample_code": "QILI-03", "method": "AMS", "probability": "95.4%",
         "bound_lo": 5300, "bound_hi": 5550, "ranges": [], "basis": "炭样 AMS 测定", "note": ""},
        {"kind": "typology", "layer_key": "sj-l1", "source_key": "qs-survey-2021",
         "evidence_key": "typo-sj-l1", "artifact_type": "牛鼻耳罐",
         "bound_lo": 5550, "bound_hi": 5950, "ranges": [], "basis": "调查采集品型式判断", "note": ""},
        # 推断：带出处的层位先后
        {"kind": "ordering", "layer_key": "bj-l3", "other_layer_key": "bj-l2",
         "source_key": "bj-report-2019", "evidence_key": "ord-bj-32", "gap_years": 0,
         "basis": "地层叠压：第3层在下", "note": ""},
        {"kind": "ordering", "layer_key": "bj-l2", "other_layer_key": "bj-l1",
         "source_key": "bj-report-2019", "evidence_key": "ord-bj-21", "gap_years": 0,
         "basis": "地层叠压：第2层在下", "note": ""},
        {"kind": "ordering", "layer_key": "ql-l2", "other_layer_key": "ql-l1",
         "source_key": "qs-survey-2021", "evidence_key": "ord-ql-21", "gap_years": 0,
         "basis": "剖面叠压关系", "note": ""},
        # 暂定标签：研究者判断，不参与传播
        {"kind": "label", "layer_key": "bj-l3", "source_key": "bj-report-2019",
         "evidence_key": "lbl-bj-l3", "label_text": "马家浜文化早期", "basis": "整理者初步意见", "note": "暂定"},
        {"kind": "label", "layer_key": "bj-l2", "source_key": "bj-report-2019",
         "evidence_key": "lbl-bj-l2", "label_text": "马家浜文化晚期", "basis": "整理者初步意见", "note": "暂定"},
        {"kind": "label", "layer_key": "bj-l1", "source_key": "bj-report-2019",
         "evidence_key": "lbl-bj-l1", "label_text": "崧泽文化早期", "basis": "整理者初步意见", "note": "暂定"},
    ],
}


def _ensure_seed_user(service: ChronologyService) -> int:
    db = service.db
    row = db.execute("SELECT id FROM users WHERE username='_seed'").fetchone()
    if row:
        return row["id"]
    stamp = now()
    cur = db.execute(
        "INSERT INTO users(username,display_name,password_hash,created_at,updated_at) VALUES(?,?,?,?,?)",
        ("_seed", "离线种子用户", password_hash("SeedPass!23456"), stamp, stamp),
    )
    return cur.lastrowid


def seed_demo(project_code: str = "BAOJIA-DEMO") -> dict[str, Any]:
    """写入固定演示数据集，返回登记结果摘要。重复执行报错而不覆盖。"""
    from app.database import connection, init_db
    init_db()
    service = ChronologyService(connection())
    actor = _ensure_seed_user(service)
    d = DEMO_DATASET
    code = project_code or d["project"]["code"]
    with transaction(immediate=True) as db:
        existing = db.execute("SELECT id FROM projects WHERE code=?", (code,)).fetchone()
        if existing:
            project_id = existing["id"]
        else:
            stamp = now()
            cur = db.execute("INSERT INTO projects(code,name,site_name,created_at,updated_at) VALUES(?,?,?,?,?)",
                             (code, d["project"]["name"], d["project"]["site_name"], stamp, stamp))
            project_id = cur.lastrowid
            db.execute("INSERT INTO project_members(project_id,user_id,role,joined_at) VALUES(?,?, 'owner',?)",
                       (project_id, actor, stamp))

    for site in d["sites"]:
        service.create_site(project_id, actor, site)
    for layer in d["layers"]:
        service.create_layer(project_id, actor, layer)
    for source in d["sources"]:
        service.create_source(project_id, actor, source)
    for ev in d["evidence"]:
        service.add_evidence(project_id, actor, ev["kind"], ev)

    snapshot = service.create_snapshot(project_id, actor, {
        "snapshot_key": "snap-1", "title": "鲍家区域初始证据快照", "evidence_keys": None, "note": "",
    })
    computed = service.compute_snapshot(project_id, actor, "snap-1")
    return {
        "project_id": project_id, "code": code,
        "sites": len(d["sites"]), "layers": len(d["layers"]),
        "sources": len(d["sources"]), "evidence": len(d["evidence"]),
        "snapshot_key": "snap-1",
        "satisfiable": computed["result"]["satisfiable"],
        "result_hash": computed["result"]["result_hash"],
    }


def run_export(project_id: int) -> dict[str, Any]:
    from app.database import connection, init_db
    init_db()
    return ChronologyService(connection()).export_project(project_id)


def run_import(bundle: dict[str, Any], *, project_code: str | None = None) -> dict[str, Any]:
    from app.database import connection, init_db
    init_db()
    service = ChronologyService(connection())
    actor = _ensure_seed_user(service)
    return service.import_project(bundle, actor_id=actor, project_code=project_code)

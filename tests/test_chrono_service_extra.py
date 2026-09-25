"""服务级补充测试：事务内冷缓存计算、区域快照的层位关系覆盖。"""
from __future__ import annotations

from app.chrono_service import ChronoService
from app.database import transaction
from tests.fixtures import baojia_dataset as ds
from tests.seed_helper import seed_dataset


def test_compute_inside_open_transaction_with_cold_cache(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    snap = client.post(f"{ctx['base']}/snapshots", json={"code": "SNAP-TX"}, headers=owner["headers"]).json()
    service = ChronoService()
    # 外层事务未提交、计算缓存为空：求解必须能复用当前事务而不嵌套 BEGIN
    with transaction(immediate=True):
        result = service.compute_snapshot(snap["id"], ctx["project_id"])
    assert result["consistent"] is True
    assert result["strata"]["BJ-H4"]["start"]["latest"] == -4800
    # 提交后缓存可见
    again = service.compute_snapshot(snap["id"], ctx["project_id"])
    assert again["cached"] is True and again["result_hash"] == result["result_hash"]


def test_region_scoped_snapshot_includes_orderings_within_region(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    snap = client.post(f"{ctx['base']}/snapshots", json={"code": "SNAP-REGION", "region_id": ctx["region_id"]}, headers=owner["headers"]).json()
    detail = client.get(f"{ctx['base']}/snapshots/{snap['id']}", headers=owner["headers"]).json()
    codes = {item["code"] for item in detail["items"]}
    # 区域内层位关系（鲍家、神墩、塘湾）应纳入；类型学范围作为共享知识纳入
    assert "O-BJH4-BJH3" in codes and "O-SDH2-BJH3" in codes and "O-TWH2-TWH1" in codes
    assert "T-DINGGELEI" in codes
    result = client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"]).json()
    assert result["consistent"] is True
    assert "TW-H1" in result["strata"]


def test_retraction_inside_transaction_flags_and_computes(client, owner):
    from tests.seed_helper import register_user
    ctx = seed_dataset(client, owner["headers"])
    for name in ("tx_r1", "tx_r2"):
        user, _ = register_user(client, name)
        client.post(f"{ctx['base']}/members", json={"user_id": user["id"], "role": "reviewer"}, headers=owner["headers"])
    snap = client.post(f"{ctx['base']}/snapshots", json={"code": "SNAP-TX2"}, headers=owner["headers"]).json()
    scheme = client.post(f"{ctx['base']}/schemes", json={"code": "SZT", "title": "事务内撤回", "snapshot_id": snap["id"], "phases": ds.PHASES_V1}, headers=owner["headers"]).json()
    client.post(f"{ctx['base']}/schemes/{scheme['id']}/submit", headers=owner["headers"])
    service = ChronoService()
    reviewers = service.db.execute("SELECT user_id FROM project_members WHERE project_id=? AND role='reviewer'", (ctx["project_id"],)).fetchall()
    for row in reviewers:
        service.review_scheme(scheme["id"], ctx["project_id"], {"vote": "approve"}, row["user_id"])
    service.publish_scheme(scheme["id"], ctx["project_id"], owner["user"]["id"])
    outcome = service.retract_citation(ctx["citations"]["BAOJIA-2014"], ctx["project_id"], owner["user"]["id"], "测试撤回")
    assert outcome["flags_created"] >= 1
    flags = service.list_flags(ctx["project_id"], "open")
    assert any("崧泽早期" in f["reason"] for f in flags)

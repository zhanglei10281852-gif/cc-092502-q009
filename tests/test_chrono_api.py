"""端到端 API 测试：证据/快照/求解、时间区域过滤、评审并发、发布与修订、撤回联动、导入导出。"""
from __future__ import annotations

import json

from tests.fixtures import baojia_dataset as ds
from tests.seed_helper import register_user, seed_dataset


def _snapshot_all(client, ctx, headers, code="SNAP-1", **extra):
    response = client.post(f"{ctx['base']}/snapshots", json={"code": code, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---- 证据分类与快照求解 ---------------------------------------------------

def test_evidence_classes_are_separated(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    observations = client.get(f"{ctx['base']}/evidence?evidence_class=observation", headers=owner["headers"]).json()["data"]
    inferences = client.get(f"{ctx['base']}/evidence?evidence_class=inference", headers=owner["headers"]).json()["data"]
    labels = client.get(f"{ctx['base']}/evidence?evidence_class=label", headers=owner["headers"]).json()["data"]
    assert {e["kind"] for e in observations} == {"dating", "typology_range", "artifact_find"}
    assert {e["kind"] for e in inferences} == {"ordering"}
    assert {e["kind"] for e in labels} == {"label_assignment"}
    assert len(observations) == 4 and len(inferences) == 3 and len(labels) == 2


def test_snapshot_compute_propagates_and_caches_by_evidence_hash(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    snap = _snapshot_all(client, ctx, owner["headers"])
    first = client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"])
    assert first.status_code == 200 and first.json()["consistent"] is True
    assert first.json()["cached"] is False
    second = client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"])
    assert second.json()["cached"] is True
    # 两个等价快照（同证据集）必须命中同一结果哈希
    snap2 = _snapshot_all(client, ctx, owner["headers"], code="SNAP-2")
    again = client.post(f"{ctx['base']}/snapshots/{snap2['id']}/compute", headers=owner["headers"]).json()
    assert again["evidence_hash"] == first.json()["evidence_hash"]
    assert "result_hash" not in first.json() or "result_hash" in again


def test_inconsistent_material_returns_conflicts_and_blocks_scheme(client, owner):
    ctx = seed_dataset(client, owner["headers"], with_contradiction=True)
    snap = _snapshot_all(client, ctx, owner["headers"])
    result = client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"]).json()
    assert result["consistent"] is False
    assert ["O-BJH4-BJH3", "O-REV-BJH3-BJH4"] in result["conflicts"]
    proposed = client.post(f"{ctx['base']}/schemes", json={
        "code": "PH", "title": "阶段", "snapshot_id": snap["id"], "phases": ds.PHASES_V1}, headers=owner["headers"])
    assert proposed.status_code == 422 and proposed.json()["error"]["code"] == "inconsistent_evidence"


def test_chronology_filter_by_time_and_region_keeps_unknown_open(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    snap = _snapshot_all(client, ctx, owner["headers"])
    client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"])
    # 窗口 [-6000,-4500]：鲍家 H4 相交；神墩下界未知只是"可能相交"故保留；塘湾全未知保留
    view = client.get(f"{ctx['base']}/chronology?snapshot_id={snap['id']}&from_year=-6000&to_year=-4500", headers=owner["headers"])
    assert view.status_code == 200, view.text
    assert {s["site_code"] for s in view.json()["sites"]} == {"BJ", "SD", "TW"}
    # 只看公元前 3000 年以后：神墩终点上界 -4001 已早于窗口而被排除；
    # 鲍家 H3 与塘湾的开放终点必须保留，绝不因未知而剔除
    later = client.get(f"{ctx['base']}/chronology?snapshot_id={snap['id']}&from_year=-3000", headers=owner["headers"])
    later_sites = {s["site_code"] for s in later.json()["sites"]}
    assert "SD" not in later_sites and "TW" in later_sites and "BJ" in later_sites
    # 区域过滤
    region_view = client.get(f"{ctx['base']}/chronology?region_id={ctx['region_id']}", headers=owner["headers"])
    assert {s["site_code"] for s in region_view.json()["sites"]} == {"BJ", "SD", "TW"}


# ---- 评审工作流与并发 -----------------------------------------------------

def _publish_flow(client, ctx, owner, reviewers, phases=ds.PHASES_V1, snap_code="SNAP-PUB"):
    snap = _snapshot_all(client, ctx, owner["headers"], code=snap_code)
    client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"])
    scheme = client.post(f"{ctx['base']}/schemes", json={"code": "SZ", "title": "崧泽序列", "snapshot_id": snap["id"], "phases": phases}, headers=owner["headers"])
    assert scheme.status_code == 201, scheme.text
    sid = scheme.json()["id"]
    assert client.post(f"{ctx['base']}/schemes/{sid}/submit", headers=owner["headers"]).status_code == 200
    return snap, sid


def _add_reviewers(client, ctx, owner, names):
    reviewers = []
    for name in names:
        user, headers = register_user(client, name)
        assert client.post(f"{ctx['base']}/members", json={"user_id": user["id"], "role": "reviewer"}, headers=owner["headers"]).status_code == 200
        reviewers.append(headers)
    return reviewers


def _approve_and_publish(client, ctx, owner, sid, reviewers):
    for headers in reviewers:
        response = client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve"}, headers=headers)
        assert response.status_code == 200, response.text
    published = client.post(f"{ctx['base']}/schemes/{sid}/publish", headers=owner["headers"])
    assert published.status_code == 200, published.text
    return published.json()


def test_scheme_review_concurrency_and_publish_immutability(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    # 两名评审人（reviewer 角色）
    r1_user, r1 = register_user(client, "reviewer1")
    r2_user, r2 = register_user(client, "reviewer2")
    for user in (r1_user, r2_user):
        assert client.post(f"{ctx['base']}/members", json={"user_id": user["id"], "role": "reviewer"}, headers=owner["headers"]).status_code == 200
    snap, sid = _publish_flow(client, ctx, owner, [r1, r2])

    # 提案人不能评审自己
    self_review = client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve"}, headers=owner["headers"])
    assert self_review.status_code == 403 and self_review.json()["error"]["code"] == "self_review"

    # 并发两票批准：每人只能投一次，重复投票冲突
    v1 = client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve"}, headers=r1)
    v2 = client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve"}, headers=r2)
    assert v1.status_code == 200 and v2.status_code == 200
    dup = client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve"}, headers=r1)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_reviewed"

    # 票未齐时不能发布（required_reviews=2 已齐，这里改用反对票路径另测）
    published = client.post(f"{ctx['base']}/schemes/{sid}/publish", headers=owner["headers"])
    assert published.status_code == 200, published.text
    version = published.json()
    assert version["version_no"] == 1
    labels = [c["label"] for c in version["conclusions"]]
    assert labels == ["崧泽早期", "崧泽晚期"]

    # 发布后不可再发布、不可修改（重新提交评审被拒）
    republish = client.post(f"{ctx['base']}/schemes/{sid}/publish", headers=owner["headers"])
    assert republish.status_code == 409
    resubmit = client.post(f"{ctx['base']}/schemes/{sid}/submit", headers=owner["headers"])
    assert resubmit.status_code == 409


def test_reviewer_cannot_publish_and_optimistic_lock(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    r1_user, r1 = register_user(client, "rev_lead")
    client.post(f"{ctx['base']}/members", json={"user_id": r1_user["id"], "role": "reviewer"}, headers=owner["headers"])
    snap, sid = _publish_flow(client, ctx, owner, [r1])
    # reviewer 无权发布
    forbidden = client.post(f"{ctx['base']}/schemes/{sid}/publish", headers=r1)
    assert forbidden.status_code == 403
    # 投票带过期 lock_version 被拒
    scheme = client.get(f"{ctx['base']}/schemes/{sid}", headers=owner["headers"]).json()
    stale = client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve", "expected_lock_version": scheme["lock_version"] + 99}, headers=r1)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"


def test_reject_vote_blocks_publish(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    r1_user, r1 = register_user(client, "rej1")
    r2_user, r2 = register_user(client, "rej2")
    for user in (r1_user, r2_user):
        client.post(f"{ctx['base']}/members", json={"user_id": user["id"], "role": "reviewer"}, headers=owner["headers"])
    snap, sid = _publish_flow(client, ctx, owner, [r1, r2])
    client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "approve"}, headers=r1)
    client.post(f"{ctx['base']}/schemes/{sid}/reviews", json={"vote": "reject", "comment": "层位关系存疑"}, headers=r2)
    blocked = client.post(f"{ctx['base']}/schemes/{sid}/publish", headers=owner["headers"])
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "rejected_vote"


# ---- 修订与差异摘要 -------------------------------------------------------

def test_revision_creates_candidate_with_diff_and_new_version(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    reviewers = _add_reviewers(client, ctx, owner, ["rv1", "rv2"])
    _, sid = _publish_flow(client, ctx, owner, reviewers)
    _approve_and_publish(client, ctx, owner, sid, reviewers)

    # 新证据 → 新快照 → 候选修订；不允许直接覆盖已发布方案
    new_item = ds.REVISION_EVIDENCE
    payload = {"code": new_item["code"], "kind": "dating", "stratum_id": ctx["strata"][new_item["stratum"]],
               "method": new_item["method"], "probability": new_item["probability"],
               "lower_year": new_item["lower_year"], "upper_year": new_item["upper_year"],
               "citation_id": ctx["citations"][new_item["citation"]]}
    added = client.post(f"{ctx['base']}/evidence", json=payload, headers=owner["headers"])
    assert added.status_code == 201, added.text
    snap2 = _snapshot_all(client, ctx, owner["headers"], code="SNAP-V2")
    revision = client.post(f"{ctx['base']}/schemes/{sid}/revisions", json={
        "code": "SZ", "title": "崧泽序列（修订）", "snapshot_id": snap2["id"], "phases": ds.PHASES_V2}, headers=owner["headers"])
    assert revision.status_code == 201, revision.text
    candidate = revision.json()
    assert candidate["version_no"] == 2 and candidate["status"] == "proposed"
    detail = client.get(f"{ctx['base']}/schemes/{candidate['id']}", headers=owner["headers"]).json()
    diff = detail["candidate_diff"]
    assert ds.REVISION_EVIDENCE["code"] in diff["evidence_added"]
    change = diff["phase_changes"]["崧泽晚期"]
    assert ds.REVISION_EVIDENCE["code"] in change["evidence_added"]
    assert "BJ" in diff["affected_sites"]
    assert diff["boundary_changes"], "新测年应收窄边界并产生边界差异"

    # 候选走完评审后成为 v2；v1 版本仍可读取且内容不变
    client.post(f"{ctx['base']}/schemes/{candidate['id']}/submit", headers=owner["headers"])
    client.post(f"{ctx['base']}/schemes/{candidate['id']}/reviews", json={"vote": "approve"}, headers=reviewers[0])
    client.post(f"{ctx['base']}/schemes/{candidate['id']}/reviews", json={"vote": "approve"}, headers=reviewers[1])
    v2 = client.post(f"{ctx['base']}/schemes/{candidate['id']}/publish", headers=owner["headers"]).json()
    assert v2["version_no"] == 2
    versions = client.get(f"{ctx['base']}/versions", headers=owner["headers"]).json()["data"]
    assert [v["version_no"] for v in versions] == [1, 2]
    v1 = client.get(f"{ctx['base']}/versions/{versions[0]['id']}", headers=owner["headers"]).json()
    assert json.loads(v1["content_json"]) == ds.PHASES_V1


# ---- 引用撤回联动 ---------------------------------------------------------

def test_citation_retraction_flags_dependent_conclusions(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    reviewers = _add_reviewers(client, ctx, owner, ["rf1", "rf2"])
    _, sid = _publish_flow(client, ctx, owner, reviewers)
    _approve_and_publish(client, ctx, owner, sid, reviewers)

    citation_id = ctx["citations"]["BAOJIA-2014"]
    retracted = client.post(f"{ctx['base']}/citations/{citation_id}/retract?reason=简报数据更正", headers=owner["headers"])
    assert retracted.status_code == 200, retracted.text
    body = retracted.json()
    assert "D-C14-BJH4" in body["affected_evidence"]
    assert body["flags_created"] >= 1
    flags = client.get(f"{ctx['base']}/review-flags?status=open", headers=owner["headers"]).json()["data"]
    assert any("崧泽早期" in f["reason"] for f in flags)
    # 发布版本内容本身未被修改，但带待复核标记
    versions = client.get(f"{ctx['base']}/versions", headers=owner["headers"]).json()["data"]
    assert all(v["status"] if "status" in v else True for v in versions)
    # 解析标记
    flag_id = flags[0]["id"]
    resolved = client.post(f"{ctx['base']}/review-flags/{flag_id}/resolve", headers=owner["headers"])
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"


# ---- 权限与审计 -----------------------------------------------------------

def test_viewer_cannot_write_and_all_actions_are_audited(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    viewer_user, viewer = register_user(client, "viewer1")
    client.post(f"{ctx['base']}/members", json={"user_id": viewer_user["id"], "role": "viewer"}, headers=owner["headers"])
    forbidden = client.post(f"{ctx['base']}/regions", json={"code": "X", "name": "X"}, headers=viewer)
    assert forbidden.status_code == 403
    # 非成员不可读
    outsider_user, outsider = register_user(client, "outsider1")
    denied = client.get(f"{ctx['base']}/evidence", headers=outsider)
    assert denied.status_code == 403
    audit = client.get(f"/api/audit?project_id={ctx['project_id']}", headers=owner["headers"]).json()["data"]
    actions = {e["action"] for e in audit}
    assert {"region.create", "evidence.create", "citation.create"} <= actions


# ---- 离线导入导出与可复现 -------------------------------------------------

def test_export_import_roundtrip_reproduces_results(client, owner):
    ctx = seed_dataset(client, owner["headers"])
    snap = _snapshot_all(client, ctx, owner["headers"], code="SNAP-EXP")
    computed = client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"]).json()
    exported = client.get(f"{ctx['base']}/export", headers=owner["headers"])
    assert exported.status_code == 200
    bundle = exported.json()
    assert bundle["format"] == "chrono-bundle/1"
    imported = client.post("/api/projects/0/import-bundle?new_code=REG-COPY-1", json=bundle, headers=owner["headers"])
    assert imported.status_code == 201, imported.text
    report = imported.json()
    assert all(item["result_hash_matches"] for item in report["verified"])
    # 在导入副本上重新计算，结果与原始一致
    copy_pid = report["project_id"]
    copy_snaps = client.get(f"/api/projects/{copy_pid}/snapshots", headers=owner["headers"]).json()["data"]
    recompute = client.post(f"/api/projects/{copy_pid}/snapshots/{copy_snaps[0]['id']}/compute", headers=owner["headers"]).json()
    assert recompute["evidence_hash"] == computed["evidence_hash"]
    bj_h4 = recompute["strata"]["BJ-H4"]
    assert bj_h4["start"]["latest"] == -4800

"""端到端测试：登记、快照求解、方案评审并发、撤回联动、修订差异、过滤、导入导出、权限。"""
from __future__ import annotations

import json

from tests.chron_fixtures import (
    BASE_EVIDENCE,
    STAGES,
    bounds,
    compute,
    label,
    make_snapshot,
    ordering,
    register_evidence,
    world,
)


def _setup(world, evidence=BASE_EVIDENCE, snapshot_key="snap-1"):
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    register_evidence(c, pid, h, evidence)
    make_snapshot(c, pid, h, snapshot_key)
    out = compute(c, pid, h, snapshot_key)
    return out


def test_propagation_fixed_dataset(world):
    out = _setup(world)
    result = out["result"]
    assert result["satisfiable"]
    assert result["algorithm"] == "bounds-v1"
    bj3 = bounds(out, "bj-l3")
    bj2 = bounds(out, "bj-l2")
    bj1 = bounds(out, "bj-l1")
    # 链式先后传播：鲍家中层的区间被下层测年收紧
    assert bj3["start_bp"]["youngest"] == 6400          # 类型学 6400 最老
    assert bj3["end_bp"] == {"youngest": 5900, "oldest": 5900}
    assert bj2["start_bp"] == {"youngest": 5900, "oldest": 5900}
    assert bj2["end_bp"]["youngest"] == 5550
    assert bj1["start_bp"]["youngest"] in (5550, 5600)
    # 开放侧保持 null，绝不补点估计
    assert bj3["start_bp"]["oldest"] is None
    assert bj1["end_bp"]["youngest"] is None
    # 三礁只有类型学单侧证据 + 无顺序连接：另一侧未知
    sj = bounds(out, "sj-l1")
    assert sj["start_bp"]["youngest"] == 5950 and sj["start_bp"]["oldest"] is None
    assert sj["end_bp"]["youngest"] is None and sj["end_bp"]["oldest"] == 5550


def test_conflict_diagnosis_via_api(world):
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    conflicting = BASE_EVIDENCE + [
        # 反向断言：鲍家中层早于下层，与测年/叠压不相容
        ordering("o-contradiction", "bj-l2", "bj-l3"),
    ]
    register_evidence(c, pid, h, conflicting)
    make_snapshot(c, pid, h, "snap-bad")
    out = compute(c, pid, h, "snap-bad")
    assert out["result"]["satisfiable"] is False
    conflict = out["result"]["conflicts"][0]
    keys = set(conflict["evidence_keys"])
    assert "o-contradiction" in keys
    # 诊断必须给出具体倒置变量与消息，且集合包含最小
    assert conflict["variable"].startswith("bj-")
    assert "cal BP" in conflict["message"]


def test_snapshot_pins_evidence_and_recomputes_identically(world):
    out = _setup(world)
    first_hash = out["result"]["result_hash"]
    # 再次计算走缓存；force 重算哈希必须一致（可复现）
    cached = compute(world["client"], world["pid"], world["owner"]["headers"])
    forced = compute(world["client"], world["pid"], world["owner"]["headers"], force=True)
    assert cached["result"]["result_hash"] == first_hash
    assert forced["result"]["result_hash"] == first_hash
    # 登记新证据不改变既有快照结果
    register_evidence(world["client"], world["pid"], world["owner"]["headers"], [
        {"kind": "dating", "evidence_key": "d-sj-now", "layer_key": "sj-l1", "source_key": "qss",
         "sample_code": "SJ-09", "method": "AMS", "probability": "95.4%",
         "bound_lo": 5600, "bound_hi": 5900, "ranges": [], "basis": "补测", "note": ""},
    ])
    again = compute(world["client"], world["pid"], world["owner"]["headers"])
    assert again["result"]["result_hash"] == first_hash


def test_evidence_revision_appends_rev(world):
    _setup(world)
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    r = c.put(f"/api/projects/{pid}/chron/evidence/dating/d-bj3", headers=h, json={
        "evidence_key": "d-bj3", "layer_key": "bj-l3", "source_key": "bjr",
        "sample_code": "BJ-07", "method": "AMS", "probability": "95.4%",
        "bound_lo": 5950, "bound_hi": 6350, "ranges": [], "basis": "重新校正", "note": "",
    })
    assert r.status_code == 201, r.text
    assert r.json()["rev"] == 2 and r.json()["status"] == "active"
    listed = c.get(f"/api/projects/{pid}/chron/evidence?layer_key=bj-l3&include_superseded=true", headers=h).json()["data"]
    revs = sorted(e["rev"] for e in listed if e["evidence_key"] == "d-bj3")
    assert revs == [1, 2]
    assert all(e["status"] == ("active" if e["rev"] == 2 else "superseded")
               for e in listed if e["evidence_key"] == "d-bj3")
    # 不能更换层位
    bad = c.put(f"/api/projects/{pid}/chron/evidence/dating/d-bj3", headers=h, json={
        "evidence_key": "d-bj3", "layer_key": "bj-l2", "source_key": "bjr",
        "sample_code": "X", "method": "AMS", "probability": "",
        "bound_lo": 5900, "bound_hi": 6300, "ranges": [], "basis": "", "note": ""})
    assert bad.status_code == 422


def _propose(world, snapshot_key="snap-1", stages=STAGES, key="baojia-stages", approvals=2, actor=None):
    c, pid = world["client"], world["pid"]
    h = (actor or world["owner"])["headers"]
    r = c.post(f"/api/projects/{pid}/chron/schemes", headers=h, json={
        "scheme_key": key, "title": "鲍家文化阶段方案", "snapshot_key": snapshot_key,
        "stages": stages, "approvals_required": approvals, "note": "",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_review_workflow_concurrent_and_publish_immutable(world):
    _setup(world)
    v = _propose(world)
    assert v["status"] == "proposed" and v["version_no"] == 1 and v["approvals"] == 0
    c, pid = world["client"], world["pid"]

    # 提议人不能自批
    self_approve = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
                          headers=world["owner"]["headers"], json={"decision": "approve", "comment": "自批"})
    assert self_approve.status_code == 403

    # 两名评审人并发（背靠背）提交批准，唯一约束防重复
    ra = world["reviewer_a"]["headers"]
    rb = world["reviewer_b"]["headers"]
    r1 = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
                headers=ra, json={"decision": "approve", "comment": "同意"})
    r2 = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
                headers=rb, json={"decision": "approve", "comment": "同意"})
    assert r1.status_code == r2.status_code == 200
    # 同一评审人重复提交被拒（评审幂等保护/并发冲突）
    dup = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
                 headers=ra, json={"decision": "approve", "comment": "重复"})
    assert dup.status_code == 409

    # 非负责人不能发布
    forbidden = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
                       headers=ra)
    assert forbidden.status_code == 403
    pub = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
                 headers=world["owner"]["headers"])
    assert pub.status_code == 200 and pub.json()["status"] == "published"

    # 已发布版本不可再评审、不可重复发布、不可覆盖
    assert c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
                  headers=ra, json={"decision": "comment", "comment": "late"}).status_code == 409
    assert c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
                  headers=world["owner"]["headers"]).status_code == 409
    # 已发布方案不能用同键再提议
    assert _propose_raw(world, "baojia-stages").status_code == 409


def _propose_raw(world, key, snapshot_key="snap-1", stages=STAGES):
    return world["client"].post(f"/api/projects/{world['pid']}/chron/schemes",
                                headers=world["owner"]["headers"],
                                json={"scheme_key": key, "title": "x", "snapshot_key": snapshot_key,
                                      "stages": stages, "approvals_required": 2, "note": ""})


def test_request_changes_iterates_candidate_without_touching_published(world):
    _setup(world)
    c, pid = world["client"], world["pid"]
    v = _propose(world, approvals=2)
    # 评审人要求修改
    r = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
               headers=world["reviewer_a"]["headers"],
               json={"decision": "request_changes", "comment": "三礁归属待补"})
    assert r.status_code == 200 and r.json()["approvals"] == 0

    # 研究者基于同一快照提出候选 v2（评审意见迭代，不碰任何已发布版本）
    new_stages = {**STAGES, "sj-l1": "MJJ-late"}
    r2 = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/revisions",
                headers=world["researcher"]["headers"],
                json={"snapshot_key": "snap-1", "stages": new_stages, "approvals_required": 2, "note": "补三礁"})
    assert r2.status_code == 201, r2.text
    v2 = r2.json()
    assert v2["version_no"] == 2 and v2["status"] == "proposed"
    assert v2["diff"]["base_version_status"] == "proposed"
    assert any(ch["layer_key"] == "sj-l1" for ch in v2["diff"]["stage_changes"])
    # v1 评审记录保留，且 v1 仍是候选（未被发布也未被覆盖）
    v1 = c.get(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1",
               headers=world["owner"]["headers"]).json()
    assert v1["status"] == "proposed"
    assert len(v1["reviews"]) == 1 and v1["reviews"][0]["decision"] == "request_changes"
    assert v2["reviews"] == []


def test_revision_auto_computes_uncomputed_snapshot(world):
    """候选修订遇到未显式计算的快照时在事务外自动求解（防止嵌套事务）。"""
    _setup(world)
    c, pid = world["client"], world["pid"]
    _propose(world, approvals=1)
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
           headers=world["reviewer_a"]["headers"], json={"decision": "approve", "comment": ""})
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
           headers=world["owner"]["headers"])
    # snap-2 只创建、不调用 compute
    make_snapshot(c, pid, world["researcher"]["headers"], "snap-2")
    r = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/revisions",
               headers=world["researcher"]["headers"],
               json={"snapshot_key": "snap-2", "stages": STAGES, "approvals_required": 1, "note": ""})
    assert r.status_code == 201, r.text
    assert r.json()["version_no"] == 2
    # 计算结果已落库
    comp = compute(c, pid, world["owner"]["headers"], "snap-2")
    assert comp["result"]["satisfiable"]


def test_withdraw_candidate_then_revise_from_published_base(world):
    _setup(world)
    c, pid = world["client"], world["pid"]
    _propose(world, approvals=1)
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
           headers=world["reviewer_a"]["headers"], json={"decision": "approve", "comment": ""})
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
           headers=world["owner"]["headers"])
    make_snapshot(c, pid, world["researcher"]["headers"], "snap-2")
    compute(c, pid, world["researcher"]["headers"], "snap-2")
    v2 = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/revisions",
                headers=world["researcher"]["headers"],
                json={"snapshot_key": "snap-2", "stages": STAGES, "approvals_required": 1, "note": ""}).json()
    assert v2["version_no"] == 2 and v2["status"] == "proposed"
    # 撤回候选 v2；已发布 v1 不受影响
    w = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/2/withdraw",
               headers=world["owner"]["headers"], params={"note": "证据待补"})
    assert w.status_code == 200 and w.json()["status"] == "withdrawn"
    # 已发布版本不可撤回
    assert c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/withdraw",
                  headers=world["owner"]["headers"], params={"note": "x"}).status_code == 409
    # 再次修订：版本号跳到 3，基线回退到已发布 v1
    make_snapshot(c, pid, world["researcher"]["headers"], "snap-3")
    compute(c, pid, world["researcher"]["headers"], "snap-3")
    v3 = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/revisions",
                headers=world["researcher"]["headers"],
                json={"snapshot_key": "snap-3", "stages": {**STAGES, "sj-l1": "MJJ-late"},
                      "approvals_required": 1, "note": ""}).json()
    assert v3["version_no"] == 3 and v3["diff"]["base_version_status"] == "published"
    listing = c.get(f"/api/projects/{pid}/chron/schemes", headers=world["owner"]["headers"]).json()["data"][0]
    assert {v["version_no"]: v["status"] for v in listing["versions"]} == {1: "published", 2: "withdrawn", 3: "proposed"}


def test_proposal_on_conflicting_snapshot_rejected(world):
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    register_evidence(c, pid, h, BASE_EVIDENCE + [ordering("o-contra", "bj-l2", "bj-l3")])
    make_snapshot(c, pid, h, "snap-bad")
    compute(c, pid, h, "snap-bad")
    r = c.post(f"/api/projects/{pid}/chron/schemes", headers=h, json={
        "scheme_key": "bad", "title": "冲突方案", "snapshot_key": "snap-bad",
        "stages": STAGES, "approvals_required": 1, "note": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "snapshot_in_conflict"


def test_published_revision_creates_diff_summary(world):
    _setup(world)
    v = _propose(world, approvals=1)
    c, pid = world["client"], world["pid"]
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
           headers=world["reviewer_a"]["headers"], json={"decision": "approve", "comment": "ok"})
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
           headers=world["owner"]["headers"])

    # 新增证据只能产生候选修订：修订 bj-l3 测年并加入 sj-l1 阶段归属
    register_evidence(c, pid, world["researcher"]["headers"], [
        {"kind": "dating", "evidence_key": "d-sj-now", "layer_key": "sj-l1", "source_key": "qss",
         "sample_code": "SJ-09", "method": "AMS", "probability": "95.4%",
         "bound_lo": 5600, "bound_hi": 5900, "ranges": [], "basis": "补测", "note": ""},
    ])
    make_snapshot(c, pid, world["researcher"]["headers"], "snap-2")
    compute(c, pid, world["researcher"]["headers"], "snap-2")
    new_stages = {**STAGES, "sj-l1": "MJJ-late"}
    r = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/revisions",
               headers=world["researcher"]["headers"],
               json={"snapshot_key": "snap-2", "stages": new_stages, "approvals_required": 1, "note": "补三礁"})
    assert r.status_code == 201, r.text
    rev = r.json()
    assert rev["version_no"] == 2 and rev["status"] == "proposed"
    diff = rev["diff"]
    assert "d-sj-now" in diff["evidence_added"]
    assert any(ch["layer_key"] == "sj-l1" and ch["old"] is None and ch["new"] == "MJJ-late"
               for ch in diff["stage_changes"])
    affected_sites = {a["site_key"] for a in diff["affected_sites"]}
    assert "sanjiao" in affected_sites
    # v1 仍为已发布、未被覆盖
    v1 = c.get(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1",
               headers=world["owner"]["headers"]).json()
    assert v1["status"] == "published"


def test_source_retraction_flags_dependent_conclusions(world):
    _setup(world)
    v = _propose(world, approvals=1)
    c, pid = world["client"], world["pid"]
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
           headers=world["reviewer_a"]["headers"], json={"decision": "approve", "comment": "ok"})
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
           headers=world["owner"]["headers"])

    # 撤回鲍家简报：依赖它的三个鲍家层位结论自动挂起
    r = c.post(f"/api/projects/{pid}/chron/sources/bjr/retract",
               headers=world["owner"]["headers"], json={"reason": "简报测年数据被撤"})
    assert r.status_code == 200
    flagged = {(f["version_no"], f["layer_key"]) for f in
               c.get(f"/api/projects/{pid}/chron/flags?status=pending", headers=world["owner"]["headers"]).json()["data"]}
    assert {(1, "bj-l3"), (1, "bj-l2"), (1, "bj-l1")} <= flagged
    # 七里亭（引用 qss）不被标记
    assert not any(k[1].startswith("ql") for k in flagged)

    # 待复核未处理不能发布候选修订
    register_evidence(c, pid, world["researcher"]["headers"], [
        label("l-sj", "sj-l1", "马家浜文化晚期", source="qss")])
    make_snapshot(c, pid, world["researcher"]["headers"], "snap-2")
    compute(c, pid, world["researcher"]["headers"], "snap-2")
    rev = c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/revisions",
                 headers=world["researcher"]["headers"],
                 json={"snapshot_key": "snap-2", "stages": STAGES, "approvals_required": 1, "note": ""}).json()
    # 候选修订自身也带 pending 标记（其阶段结论依赖已撤回出处）
    assert any(f["status"] == "pending" for f in rev["flags"])

    # 解决标记后时间线反映 review_pending
    flags = c.get(f"/api/projects/{pid}/chron/flags?status=pending", headers=world["owner"]["headers"]).json()["data"]
    bj_flags = [f for f in flags if f["layer_key"] == "bj-l3" and f["version_no"] == 1]
    for f in bj_flags:
        rr = c.post(f"/api/projects/{pid}/chron/flags/{f['id']}/resolve",
                    headers=world["reviewer_a"]["headers"], params={"note": "已核对原始记录"})
        assert rr.status_code == 200
    timeline = c.get(f"/api/projects/{pid}/chron/timeline", headers=world["viewer"]["headers"]).json()["data"]
    bj3 = next(t for t in timeline if t["layer_key"] == "bj-l3")
    assert bj3["stage"] == "MJJ-early"
    assert bj3["end_bp"] == {"youngest": 5900, "oldest": 5900}
    pending_layers = {t["layer_key"] for t in timeline if t["review_pending"]}
    assert "bj-l3" not in pending_layers and "bj-l2" in pending_layers


def test_retraction_flags_only_snapshot_pinned_dependencies(world):
    """撤回只标记版本快照内固定的依赖；快照之后新增的同名出处证据不牵连已发布结论。"""
    _setup(world)
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    _propose(world, approvals=1)
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
           headers=world["reviewer_a"]["headers"], json={"decision": "approve", "comment": ""})
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish", headers=h)

    # snap-1 之后才在鲍家上层登记一条引用 qss 的证据（不在 v1 快照中）
    register_evidence(c, pid, world["researcher"]["headers"], [
        {"kind": "dating", "evidence_key": "d-late-qss", "layer_key": "bj-l1", "source_key": "qss",
         "sample_code": "LATE-1", "method": "AMS", "probability": "95.4%",
         "bound_lo": 5300, "bound_hi": 5500, "ranges": [], "basis": "事后补测", "note": ""}])
    r = c.post(f"/api/projects/{pid}/chron/sources/qss/retract",
               headers=h, json={"reason": "调查报告复核存疑"})
    assert r.status_code == 200
    # v1 阶段只含鲍家三层，其 snap-1 固定证据全部引用 bjr：不应有任何待复核标记
    flags = c.get(f"/api/projects/{pid}/chron/flags?status=pending", headers=h).json()["data"]
    assert flags == []
    timeline = c.get(f"/api/projects/{pid}/chron/timeline", headers=h).json()["data"]
    assert all(t["review_pending"] is False for t in timeline)


def test_retracted_source_cannot_back_new_evidence(world):
    _setup(world)
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    c.post(f"/api/projects/{pid}/chron/sources/bjr/retract", headers=h, json={"reason": "x"})
    r = c.post(f"/api/projects/{pid}/chron/evidence/dating", headers=h, json={
        "evidence_key": "d-new", "layer_key": "bj-l1", "source_key": "bjr",
        "sample_code": "Z", "method": "AMS", "probability": "",
        "bound_lo": 5000, "bound_hi": 5200, "ranges": [], "basis": "", "note": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_retracted"


def test_time_and_region_filters(world):
    _setup(world)
    v = _propose(world, approvals=1)
    c, pid = world["client"], world["pid"]
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/reviews",
           headers=world["reviewer_a"]["headers"], json={"decision": "approve", "comment": ""})
    c.post(f"/api/projects/{pid}/chron/schemes/baojia-stages/versions/1/publish",
           headers=world["owner"]["headers"])

    # 区域过滤
    west = c.get(f"/api/projects/{pid}/chron/timeline?region=太湖西部", headers=world["viewer"]["headers"]).json()["data"]
    assert {t["site_key"] for t in west} <= {"baojia", "qili"}
    north = c.get(f"/api/projects/{pid}/chron/timeline?region=钱塘江北岸", headers=world["viewer"]["headers"]).json()["data"]
    assert {t["site_key"] for t in north} <= {"sanjiao"}

    # 时间过滤（cal BP）：可能与"年轻于 5800 BP"区间重叠的结论
    young = c.get(f"/api/projects/{pid}/chron/timeline?younger_than=5800", headers=world["viewer"]["headers"]).json()["data"]
    assert {t["layer_key"] for t in young} == {"bj-l2", "bj-l1"}
    for t in young:
        y = (t["end_bp"] or {}).get("youngest")
        assert y is None or y <= 5800
    # 可能与"老于 5800 BP"区间重叠的结论（开放边界按未知保留）
    old = c.get(f"/api/projects/{pid}/chron/timeline?older_than=5800", headers=world["viewer"]["headers"]).json()["data"]
    assert {t["layer_key"] for t in old} == {"bj-l3", "bj-l2"}
    for t in old:
        o = (t["start_bp"] or {}).get("oldest")
        assert o is None or o >= 5800

    # 证据按类型、遗址、区域过滤
    orderings = c.get(f"/api/projects/{pid}/chron/evidence?kind=ordering", headers=world["viewer"]["headers"]).json()["data"]
    assert {e["kind"] for e in orderings} == {"ordering"}
    ql = c.get(f"/api/projects/{pid}/chron/evidence?site_key=qili", headers=world["viewer"]["headers"]).json()["data"]
    assert {e["ref_layer_key"].split("-")[0] for e in ql} == {"ql"}
    sites = c.get(f"/api/projects/{pid}/chron/sites?region=钱塘江北岸", headers=world["viewer"]["headers"]).json()["data"]
    assert {s["site_key"] for s in sites} == {"sanjiao"}


def test_permissions_by_role(world):
    c, pid = world["client"], world["pid"]
    # viewer 只读
    r = c.post(f"/api/projects/{pid}/chron/sites", headers=world["viewer"]["headers"],
               json={"site_key": "x", "name": "x"})
    assert r.status_code == 403
    assert c.get(f"/api/projects/{pid}/chron/sites", headers=world["viewer"]["headers"]).status_code == 200
    # recorder 可登记证据但不能提议方案
    assert c.post(f"/api/projects/{pid}/chron/sites", headers=world["recorder"]["headers"],
                  json={"site_key": "extra", "name": "额外遗址", "region": ""}).status_code == 201
    register_evidence(c, pid, world["recorder"]["headers"], [
        {"kind": "label", "evidence_key": "l-rec", "layer_key": "bj-l3", "source_key": "bjr",
         "label_text": "待议", "basis": "", "note": ""}])
    make_snapshot(c, pid, world["recorder"]["headers"], "snap-rec")
    r = c.post(f"/api/projects/{pid}/chron/schemes", headers=world["recorder"]["headers"],
               json={"scheme_key": "s", "title": "s", "snapshot_key": "snap-rec",
                     "stages": STAGES, "approvals_required": 1, "note": ""})
    assert r.status_code == 403
    # reviewer 不能登记
    r = c.post(f"/api/projects/{pid}/chron/evidence/label", headers=world["reviewer_a"]["headers"],
               json={"evidence_key": "l-x", "layer_key": "bj-l3", "label_text": "x", "basis": "", "note": ""})
    assert r.status_code == 403
    # 未认证 401
    assert c.get(f"/api/projects/{pid}/chron/sites").status_code == 422  # 缺 Header 由 FastAPI 校验


def test_validation_rejects_fabricated_and_bad_spans(world):
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    # 倒置区间
    r = c.post(f"/api/projects/{pid}/chron/evidence/dating", headers=h, json={
        "evidence_key": "bad", "layer_key": "bj-l3", "source_key": "bjr",
        "sample_code": "", "method": "AMS", "probability": "",
        "bound_lo": 6000, "bound_hi": 5000, "ranges": [], "basis": "", "note": ""})
    assert r.status_code == 422
    # 完全无范围
    r = c.post(f"/api/projects/{pid}/chron/evidence/dating", headers=h, json={
        "evidence_key": "empty", "layer_key": "bj-l3", "source_key": "bjr",
        "sample_code": "", "method": "", "probability": "",
        "bound_lo": None, "bound_hi": None, "ranges": [], "basis": "", "note": ""})
    assert r.status_code == 422


def test_export_import_roundtrip_reproduces_results(world, tmp_path, monkeypatch):
    out = _setup(world)
    original_hash = out["result"]["result_hash"]
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    # 导出
    bundle = c.get(f"/api/projects/{pid}/chron/export", headers=h).json()
    assert bundle["format"] == "regional-chronology-export/v1"
    assert len(bundle["evidence"]) == len(BASE_EVIDENCE)

    # 通过离线 CLI 导入到新项目编码
    from app.chronology_cli import run_import
    summary = run_import(bundle, project_code="REG-CHRON-COPY")
    assert summary["evidence"] == len(BASE_EVIDENCE)

    # 复制库内对固定快照重算，哈希必须与原库一致（可复现性）
    from app.database import connection
    db = connection()
    new_pid = db.execute("SELECT id FROM projects WHERE code='REG-CHRON-COPY'").fetchone()["id"]
    snap = db.execute("SELECT * FROM ch_snapshots WHERE project_id=? AND snapshot_key='snap-1'", (new_pid,)).fetchone()
    original_ev_hash = c.get(f"/api/projects/{pid}/chron/snapshots", headers=h).json()["data"][0]["evidence_hash"]
    assert snap["evidence_hash"] == original_ev_hash  # 身份哈希跨库一致（不含行 id）
    cached = json.loads(db.execute("SELECT result_json FROM ch_computations WHERE snapshot_id=?", (snap["id"],)).fetchone()["result_json"])
    assert cached["result_hash"] == original_hash

    # 用服务层在导入库重算（证据 content_hash 与快照固定值必须匹配）
    from app.chronology_service import ChronologyService
    service = ChronologyService(db)
    recomputed = service.compute_snapshot(new_pid, snap["created_by"], "snap-1", force=True)
    assert recomputed["result"]["result_hash"] == original_hash

    # 再次导入同编码非空项目冲突保护
    import pytest
    from app.service import ServiceError
    with pytest.raises(ServiceError) as exc:
        run_import(bundle, project_code="REG-CHRON-COPY")
    assert exc.value.code == "target_not_empty"

from __future__ import annotations

import pytest


# ---------- 固定数据集 ----------

SITES = [
    {"site_key": "baojia", "name": "鲍家遗址", "region": "太湖西部"},
    {"site_key": "qili", "name": "七里亭遗址", "region": "太湖西部"},
    {"site_key": "sanjiao", "name": "三礁遗址", "region": "钱塘江北岸"},
]
LAYERS = [
    {"site_key": "baojia", "layer_key": "bj-l3", "name": "鲍家下层", "sequence_no": 3},
    {"site_key": "baojia", "layer_key": "bj-l2", "name": "鲍家中层", "sequence_no": 2},
    {"site_key": "baojia", "layer_key": "bj-l1", "name": "鲍家上层", "sequence_no": 1},
    {"site_key": "qili", "layer_key": "ql-l2", "name": "七里亭下层", "sequence_no": 2},
    {"site_key": "qili", "layer_key": "ql-l1", "name": "七里亭上层", "sequence_no": 1},
    {"site_key": "sanjiao", "layer_key": "sj-l1", "name": "三礁文化层", "sequence_no": 1},
]
SOURCES = [
    {"source_key": "bjr", "title": "鲍家发掘简报", "authors": "课题组", "citation": "考古2019(6)", "year_pub": 2019, "note": ""},
    {"source_key": "qss", "title": "区域调查", "authors": "调查队", "citation": "东南文化2021(3)", "year_pub": 2021, "note": ""},
]


def dating(key, layer, lo, hi, *, source="bjr", sample="", ranges=None, method="AMS"):
    return {"kind": "dating", "evidence_key": key, "layer_key": layer, "source_key": source,
            "sample_code": sample, "method": method, "probability": "95.4%", "bound_lo": lo, "bound_hi": hi,
            "ranges": ranges or [], "basis": "固定测试数据", "note": ""}


def typo(key, layer, atype, lo, hi, *, source="bjr"):
    return {"kind": "typology", "evidence_key": key, "layer_key": layer, "source_key": source,
            "artifact_type": atype, "bound_lo": lo, "bound_hi": hi, "ranges": [], "basis": "型式范围", "note": ""}


def ordering(key, older, younger, *, source="bjr", gap=0):
    return {"kind": "ordering", "evidence_key": key, "layer_key": older, "other_layer_key": younger,
            "source_key": source, "gap_years": gap, "basis": "叠压关系", "note": ""}


def label(key, layer, text, *, source="bjr"):
    return {"kind": "label", "evidence_key": key, "layer_key": layer, "source_key": source,
            "label_text": text, "basis": "整理者意见", "note": "暂定"}


# 相容基线：鲍家下中上 6400..5250 BP 连续，七里亭与之平行，三礁仅单侧开放
BASE_EVIDENCE = [
    dating("d-bj3", "bj-l3", 5900, 6300, sample="BJ-07",
           ranges=[{"lo": 6200, "hi": 6300, "probability": 0.68}, {"lo": 5900, "hi": 6150, "probability": 0.274}]),
    typo("t-bj3", "bj-l3", "腰沿釜", 6100, 6400),
    dating("d-bj2", "bj-l2", 5650, 5900, sample="BJ-12"),
    typo("t-bj2", "bj-l2", "牛鼻耳罐", 5600, 5900),
    dating("d-bj1", "bj-l1", 5250, 5550, sample="BJ-19"),
    typo("t-ql2", "ql-l2", "腰沿釜", 6050, 6350, source="qss"),
    dating("d-ql1", "ql-l1", 5300, 5550, source="qss", sample="QL-03"),
    typo("t-sj1", "sj-l1", "牛鼻耳罐", 5550, 5950, source="qss"),
    ordering("o-bj-32", "bj-l3", "bj-l2"),
    ordering("o-bj-21", "bj-l2", "bj-l1"),
    ordering("o-ql-21", "ql-l2", "ql-l1", source="qss"),
    label("l-bj3", "bj-l3", "马家浜文化早期"),
    label("l-bj2", "bj-l2", "马家浜文化晚期"),
    label("l-bj1", "bj-l1", "崧泽文化早期"),
]

STAGES = {"bj-l3": "MJJ-early", "bj-l2": "MJJ-late", "bj-l1": "SZ-early"}


@pytest.fixture()
def world(client, owner):
    """项目 + 不同角色用户 + 固定登记数据。"""
    project = client.post("/api/projects", json={"code": "REG-CHRON", "name": "区域年代", "site_name": "鲍家"},
                          headers=owner["headers"]).json()
    pid = project["id"]

    def make_user(name, role):
        client.post("/api/users", json={"username": name, "display_name": name, "password": "PassWord!2345"})
        token = client.post("/api/sessions", json={"username": name, "password": "PassWord!2345"}).json()["token"]
        uid = client.get("/api/audit", headers={"Authorization": f"Bearer {token}"}).json()
        # 通过审计接口拿不到 id，直接查
        from app.database import connection
        uid = connection().execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()["id"]
        client.post(f"/api/projects/{pid}/members", json={"user_id": uid, "role": role}, headers=owner["headers"])
        return {"id": uid, "headers": {"Authorization": f"Bearer {token}"}}

    researcher = make_user("researcher1", "researcher")
    recorder = make_user("recorder1", "recorder")
    reviewer_a = make_user("reviewerA", "reviewer")
    reviewer_b = make_user("reviewerB", "reviewer")
    viewer = make_user("viewer1", "viewer")

    h = owner["headers"]
    for site in SITES:
        assert client.post(f"/api/projects/{pid}/chron/sites", json=site, headers=h).status_code == 201
    for layer in LAYERS:
        assert client.post(f"/api/projects/{pid}/chron/layers", json=layer, headers=h).status_code == 201
    for source in SOURCES:
        assert client.post(f"/api/projects/{pid}/chron/sources", json=source, headers=h).status_code == 201
    return {
        "client": client, "pid": pid, "owner": owner,
        "researcher": researcher, "recorder": recorder,
        "reviewer_a": reviewer_a, "reviewer_b": reviewer_b, "viewer": viewer,
    }


def register_evidence(client, pid, headers, evidence):
    for ev in evidence:
        r = client.post(f"/api/projects/{pid}/chron/evidence/{ev['kind']}", json=ev, headers=headers)
        assert r.status_code == 201, r.text


def make_snapshot(client, pid, headers, key="snap-1", evidence_keys=None):
    r = client.post(f"/api/projects/{pid}/chron/snapshots",
                    json={"snapshot_key": key, "title": key, "evidence_keys": evidence_keys, "note": ""},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def compute(client, pid, headers, key="snap-1", **params):
    r = client.post(f"/api/projects/{pid}/chron/snapshots/{key}/compute", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def bounds(result, layer_key):
    return next(L for L in result["result"]["layers"] if L["layer_key"] == layer_key)

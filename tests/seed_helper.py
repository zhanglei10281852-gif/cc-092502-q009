"""通过 API 登记鲍家固定数据集的辅助函数，返回各处的 id 映射。"""
from __future__ import annotations

from typing import Any

from tests.fixtures import baojia_dataset as ds


def _post(client, path: str, payload: dict[str, Any], headers: dict[str, str], *, expected: int = 201) -> dict[str, Any]:
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == expected, (path, response.status_code, response.text)
    return response.json()


def register_user(client, username: str, password: str = "ReviewerPass!234") -> tuple[dict[str, Any], dict[str, str]]:
    created = client.post("/api/users", json={"username": username, "display_name": username, "password": password})
    assert created.status_code == 201, created.text
    login = client.post("/api/sessions", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return created.json(), {"Authorization": f"Bearer {login.json()['token']}"}


def seed_dataset(client, headers: dict[str, str], *, project_code: str = "REG-CHRONO-1",
                 with_contradiction: bool = False, with_revision: bool = False) -> dict[str, Any]:
    project = _post(client, "/api/projects", {"code": project_code, "name": "区域年代序列研究", "site_name": "太湖西部"}, headers)
    pid = project["id"]
    base = f"/api/projects/{pid}"

    region = _post(client, f"{base}/regions", ds.REGION, headers)
    sites: dict[str, str] = {}
    for site in ds.SITES:
        row = _post(client, f"{base}/sites", {**site, "region_id": region["id"]}, headers)
        sites[site["code"]] = row["id"]
    strata: dict[str, int] = {}
    for site_code, items in ds.STRATA.items():
        for stratum in items:
            row = _post(client, f"{base}/sites/{sites[site_code]}/strata", stratum, headers)
            strata[stratum["code"]] = row["id"]
    citations: dict[str, int] = {}
    for citation in ds.CITATIONS:
        row = _post(client, f"{base}/citations", citation, headers)
        citations[citation["cite_key"]] = row["id"]

    def submit_evidence(item: dict[str, Any]) -> dict[str, Any]:
        citation = item.get("citation")
        common = {"code": item["code"], "citation_id": citations.get(citation)}
        if item["kind"] == "dating":
            payload = {**common, "kind": "dating", "stratum_id": strata[item["stratum"]],
                       "method": item["method"], "probability": item.get("probability"),
                       "lower_year": item["lower_year"], "upper_year": item["upper_year"]}
        elif item["kind"] == "typology_range":
            payload = {**common, "kind": "typology_range", "artifact_type": item["artifact_type"],
                       "lower_year": item["lower_year"], "upper_year": item["upper_year"]}
        elif item["kind"] == "artifact_find":
            payload = {**common, "kind": "artifact_find", "stratum_id": strata[item["stratum"]],
                       "artifact_type": item["artifact_type"]}
        elif item["kind"] == "ordering":
            payload = {**common, "kind": "ordering",
                       "subject_stratum_id": strata[item["before"]],
                       "object_stratum_id": strata[item["after"]],
                       "gap_years": item.get("gap_years", 0)}
        else:
            payload = {**common, "kind": "label_assignment", "stratum_id": strata[item["stratum"]],
                       "label_text": item["label_text"]}
        return _post(client, f"{base}/evidence", payload, headers)

    evidence: dict[str, dict[str, Any]] = {}
    for item in ds.EVIDENCE:
        evidence[item["code"]] = submit_evidence(item)
    if with_contradiction:
        evidence[ds.CONTRADICTION_EVIDENCE["code"]] = submit_evidence(ds.CONTRADICTION_EVIDENCE)
    if with_revision:
        evidence[ds.REVISION_EVIDENCE["code"]] = submit_evidence(ds.REVISION_EVIDENCE)

    return {
        "project_id": pid, "base": base, "region_id": region["id"],
        "sites": sites, "strata": strata, "citations": citations, "evidence": evidence,
    }

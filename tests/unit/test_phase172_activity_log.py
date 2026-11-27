"""활동 기록 화면(2026-11-28) — 동작·대상 한국어 이름, 동작·기간 필터, 필터를 따르는 전체 건수, 필터 목록."""

from __future__ import annotations

import ast
from pathlib import Path

import hair_platform
from hair_platform import audit, audit_labels

SRC = Path(hair_platform.__file__).parent
# 실행 중에 만들어지는 동작 코드(f-string) — 가능한 상태값을 모두 적는다.
DYNAMIC = ["approval_approved", "approval_rejected", "stage_lesson_dismissed", "stage_lesson_retired",
           "stage_example_active", "stage_example_dismissed", "stage_example_retired", "stage_action_sent",
           "stage_action_failed"]


def _source_action_codes() -> set[str]:
    codes = set()
    for p in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "record"
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "audit" and len(n.args) >= 3
                    and isinstance(n.args[2], ast.Constant) and isinstance(n.args[2].value, str)):
                codes.add(n.args[2].value)
    return codes


def test_every_action_code_in_source_has_a_korean_label():
    codes = _source_action_codes() | set(DYNAMIC)
    assert len(codes) > 150
    missing = {c: audit_labels.unknown_words(c) for c in codes if audit_labels.unknown_words(c)}
    assert not missing, f"WORDS에 단어를 더하세요: {missing}"
    assert audit_labels.action_label("human_hourly_rate_set") == "사람 시간당 단가 설정"
    assert audit_labels.action_label("approval_transferred") == "결재 넘김"
    assert audit_labels.action_label("brand_new_xyz") == "brand_new_xyz"  # 모르는 코드는 그대로


def test_audit_log_filters_total_and_labels(client_and_conn):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    audit.record(conn, admin, "human_capacity_set", "human_pool", 1, before={"max": 2}, after={"max": 4})
    audit.record(conn, admin, "human_capacity_set", "human_pool", 2)
    audit.record(conn, admin, "brief_budget_set", "briefs", 1)
    conn.execute("INSERT INTO audit_log (actor_id, action, subject_type, created_at) VALUES (?, 'team_created', 'team', '2020-01-05 10:00:00')",
                 (admin,))
    conn.commit()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    r = client.get("/api/audit-log", params={"action": "human_capacity_set"}).json()
    assert r["total"] == 2 and len(r["items"]) == 2  # 전체 건수가 필터를 따른다
    it = r["items"][-1]
    assert it["action_label"] == "사람 처리 용량 설정" and it["subject_label"] == "사람"
    assert '"max": 2' in it["before_value"] or '"max":2' in it["before_value"]
    old = client.get("/api/audit-log", params={"since": "2020-01-05", "until": "2020-01-05"}).json()
    assert [i["action"] for i in old["items"]] == ["team_created"]
    assert client.get("/api/audit-log", params={"since": "어제"}).status_code == 422
    page2 = client.get("/api/audit-log", params={"limit": 1, "offset": 1, "subject_type": "human_pool"}).json()
    assert page2["total"] == 2 and len(page2["items"]) == 1
    f = client.get("/api/audit-log/facets").json()
    assert {"value": "human_capacity_set", "label": "사람 처리 용량 설정", "count": 2} in f["actions"]
    assert any(s["value"] == "briefs" and s["label"] == "업무 요청" for s in f["subject_types"])
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/audit-log/facets").status_code == 403


def test_audit_log_csv_export_filters_labels_and_is_itself_logged(client_and_conn):
    import csv
    import io

    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    # 수식처럼 시작하는 값(기록은 고칠 수 없으니 그렇게 들어온 행을 만든다)
    conn.execute("INSERT INTO audit_log (actor_id, action, subject_type, subject_id, before_value, after_value) "
                 "VALUES (?, 'human_capacity_set', 'human_pool', 7, '=cmd|calc', '{\"max\": 4}')", (admin,))
    audit.record(conn, admin, "brief_budget_set", "briefs", 1)
    conn.commit()
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/audit-log/export.csv").status_code == 403
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    r = client.get("/api/audit-log/export.csv", params={"action": "human_capacity_set"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")  # 엑셀 한글
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert rows[0][:5] == ["번호", "시각(UTC)", "처리자", "한 일", "동작 코드"]
    assert len(rows) == 2
    assert rows[1][2:7] == ["admin1", "사람 처리 용량 설정", "human_capacity_set", "사람", "7"]
    assert rows[1][7] == "'=cmd|calc"  # 엑셀이 수식으로 실행하지 않게
    exported = conn.execute("SELECT after_value FROM audit_log WHERE action = 'audit_log_exported'").fetchone()[0]
    assert '"rows": 1' in exported and '"action": "human_capacity_set"' in exported
    assert client.get("/api/audit-log/export.csv", params={"until": "x"}).status_code == 422

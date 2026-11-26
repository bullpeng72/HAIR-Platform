"""요구사항 정리·검토 역할 기본 추론 끔(2026-11-26 사용자 결정 — 개선 계획 3-2 실측: 34분 → 8분, 품질 점검 같음)."""

from __future__ import annotations

import json

from hair_platform import db

ROLES = ("업무 요구사항 분석가", "요구사항 분석가", "문서 검토자", "재무 검토자")


def _tuning(conn, role):
    return json.loads(conn.execute("SELECT tuning_config FROM agent_pool WHERE role_name = ?", (role,)).fetchone()[0] or "{}")


def test_new_install_has_reasoning_off_for_light_roles_only():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    for role in ROLES:
        assert _tuning(conn, role)["reasoning"] is False, role
    # 본 결과물을 쓰거나 깊은 사고가 필요한 역할은 그대로
    assert "reasoning" not in _tuning(conn, "계약서 검토자")
    assert _tuning(conn, "C-level 보고서 작성자")["reasoning"] is True


def test_existing_install_migrates_old_defaults_but_keeps_admin_choices(tmp_path):
    conn = db.get_connection(str(tmp_path / "old.db"))
    db.init_schema(conn)
    # 예전 기본값(시드 그대로)으로 되돌리고, 한 역할은 admin이 바꾼 것으로
    old = {
        "업무 요구사항 분석가": {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
        "요구사항 분석가": {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
        "문서 검토자": {"temperature": 0.2},
        "재무 검토자": {"reasoning": True, "temperature": 0.5},  # admin이 직접 켬 + 온도 변경
    }
    for role, tuning in old.items():
        conn.execute("UPDATE agent_pool SET tuning_config = ? WHERE role_name = ?", (json.dumps(tuning), role))
    conn.execute("DELETE FROM schema_migrations WHERE name = '2026-11-26-light-roles-reasoning-off'")
    conn.commit()
    db.init_schema(conn)
    assert _tuning(conn, "업무 요구사항 분석가") == {"reasoning": False, "max_reasoning_attempts": 2, "temperature": 0.3}
    assert _tuning(conn, "요구사항 분석가")["reasoning"] is False
    assert _tuning(conn, "문서 검토자") == {"temperature": 0.2, "reasoning": False}
    assert _tuning(conn, "재무 검토자") == {"reasoning": True, "temperature": 0.5}  # admin 선택 유지
    # 한 번만 — admin이 다시 켜도 다음 기동 때 되돌리지 않는다
    conn.execute("UPDATE agent_pool SET tuning_config = ? WHERE role_name = '문서 검토자'",
                 (json.dumps({"temperature": 0.2, "reasoning": True}),))
    conn.commit()
    db.init_schema(conn)
    assert _tuning(conn, "문서 검토자")["reasoning"] is True

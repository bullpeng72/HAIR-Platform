"""Phase 11 — 관리자 기능과 일반 사용자 기능을 화면에서 구분(사용자 요청,
2026-09-26, ADR.md 결정 9).

백엔드 RBAC(ADR 결정 3)는 이미 F1-F14 전부에 대해 역할별 접근을 강제하고
있었지만, 그 전까지 Web UI 내비게이션은 모든 탭을 한 줄에 평평하게
나열해서 "이게 관리자 전용 화면인지" 담당자가 화면만 보고 구분할 수 없었다.
이번에 `webui.py`의 탭 정의에 group("user"/"admin")을 부여하고 내비게이션을
"내 업무"/"관리자 도구" 두 구역으로 나눴다 — 순수 UI 변경이라 브라우저
없이는 실제 렌더링 결과(구역이 시각적으로 분리돼 보이는지)를 검증할 수
없지만, (1) HTML 셸에 새 구조 마커가 있는지, (2) 각 탭의 group 분류가
기존에 검증된 roles 배열과 일관되는지(관리자 전용 판단 기준이 임의가
아니라 RBAC 매트릭스 그대로인지)는 소스 검사로 확인할 수 있다.
"""

from __future__ import annotations

import re

from hair_platform.webui import INDEX_HTML


def _extract_tabs_block() -> str:
    start = INDEX_HTML.index("const TABS = [")
    end = INDEX_HTML.index("];", start)
    return INDEX_HTML[start:end]


def _parse_tab_entries() -> list[dict]:
    """각 탭 정의 한 줄에서 id/group/roles/adminOnly를 뽑아낸다(간단한 소스 파싱 —
    실제 JS를 실행하지 않고도 회귀를 잡기 위한 것)."""
    block = _extract_tabs_block()
    entries = []
    for line in block.splitlines():
        m = re.search(r'id:\s*"(\w+)"', line)
        if not m:
            continue
        tab_id = m.group(1)
        group_m = re.search(r'group:\s*"(\w+)"', line)
        roles_m = re.search(r"roles:\s*\[([^\]]*)\]", line)
        roles = [r.strip().strip('"') for r in roles_m.group(1).split(",")]
        entries.append(
            {
                "id": tab_id,
                "group": group_m.group(1) if group_m else None,
                "roles": roles,
                "admin_only": "adminOnly: true" in line,
            }
        )
    return entries


def test_gc73_html_shell_has_two_nav_groups_and_admin_badge_markup():
    for marker in [
        "내 업무",
        "관리자 도구",
        "nav-group",
        "admin-group",
        "badge-adminonly",
    ]:
        assert marker in INDEX_HTML


def test_gc74_every_tab_has_a_group_assigned():
    entries = _parse_tab_entries()
    assert len(entries) >= 13  # 회귀 방지: 탭이 실수로 통째로 사라지지 않았는지
    for e in entries:
        assert e["group"] in ("user", "admin"), f"{e['id']}에 group이 없습니다"


def test_gc75_group_classification_matches_existing_rbac_roles():
    """group 분류가 임의가 아니라 기존에 검증된 roles 배열 그대로인지 확인한다 —
    member/viewer가 하나라도 포함되면 user, 아니면 admin이어야 한다(ADR 결정 9)."""
    entries = _parse_tab_entries()
    for e in entries:
        has_general_role = "member" in e["roles"] or "viewer" in e["roles"]
        expected_group = "user" if has_general_role else "admin"
        assert e["group"] == expected_group, (
            f"{e['id']}: roles={e['roles']}인데 group={e['group']} "
            f"(기대값 {expected_group})"
        )


def test_gc76_admin_only_tab_is_flagged_and_truly_admin_only():
    entries = {e["id"]: e for e in _parse_tab_entries()}
    admin_tab = entries["admin"]
    assert admin_tab["admin_only"] is True
    assert admin_tab["roles"] == ["admin"]

    # 다른 관리자 그룹 탭들은 admin_only가 아니어야 한다(매니저도 접근 가능하므로
    # "관리자 전용" 배지를 붙이면 오해를 준다).
    for e in entries.values():
        if e["group"] == "admin" and e["id"] != "admin":
            assert e["admin_only"] is False, f"{e['id']}는 admin_only가 아니어야 합니다"

"""Phase 11 — 관리자 기능과 일반 사용자 기능을 화면에서 구분(사용자 요청,
2026-09-26).

백엔드 RBAC는 이미 모든 API 엔드포인트에 대해 역할별 접근을 강제하고
있었지만, 그 전까지 Web UI 내비게이션은 모든 탭을 한 줄에 평평하게
나열해서 "이게 관리자 전용 화면인지" 담당자가 화면만 보고 구분할 수 없었다.
처음엔 `webui.py`의 탭 정의에 group("user"/"admin")을 부여하고 내비게이션을
"내 업무"/"관리자 도구" 두 구역으로 나눴다 — 순수 UI 변경이라 브라우저
없이는 실제 렌더링 결과(구역이 시각적으로 분리돼 보이는지)를 검증할 수
없지만, (1) HTML 셸에 새 구조 마커가 있는지, (2) 각 탭의 group 분류가
기존에 검증된 roles 배열과 일관되는지(관리자 전용 판단 기준이 임의가
아니라 RBAC 매트릭스 그대로인지)는 소스 검사로 확인할 수 있다.

**Phase 11 후속(2026-09-29, "HAIR 관점에서 외부 사례들을 조사하고...
관리자와 사용자 고유 업무와 공통 업무를 구분해줘" → "로그인한 계정
유형에 맞춰 메뉴 노출을 개선해줘")**: 외부 서비스 4곳 리서치로
실측 재분류한 결과를 반영해 group을
셋("user"/"common"/"admin")으로 늘렸다. 계기는 실제로 겪은 혼란 —
"관리자 도구" 11개 중 10개가 매니저도 접근 가능한데 라벨은 admin
전용처럼 보였고, "인력 검색"/"조직도"는 역할과 무관한 순수 조회라
"내 업무"(담당자 개인 업무 처리)와 성격이 다른데 같은 그룹에 섞여
있었다. "관리자 도구" → "운영 관리"로 라벨을 바꾸고, "공용 조회" 그룹을
새로 분리했다.
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
    """**Phase 29(2026-10-08, "Phase
    1→5 순서로" 진행 지시의 마지막 단계)에서 갱신** — 원래 Phase 11은
    "user"/"common"/"admin" 셋을 "내 업무"/"공용 조회"/"운영 관리"
    3구역으로 그대로 렌더링했지만, "탭 재구성"(설계 문서: "신규
    사용자가 매일 보는 화면을 좁힌다")으로 렌더링 단계에서 "user"+
    "common"을 "오늘 할 일" 하나로 합치고 "admin"을 "설정"으로
    바꿨다. `TABS` 배열 자체의 `group` 값(user/common/admin, 아래
    테스트들)은 바뀌지 않았다 — 바뀐 건 `buildNav()`가 그 값들을
    렌더링하는 방식뿐이다.

    **2026-11-02 갱신** — 사용자 요청("설정 메뉴 배치를... 4개 그룹으로
    조정")으로 "설정"이라는 단일 묶음이 "리소스 관리"/"그라운딩
    관리"/"모델 관리"/"운영 관리" 4개로 다시 나뉘었다(`SETTINGS_GROUPS`,
    `buildNav()` 참고) — 이제 "설정"이라는 라벨 자체는 더는 렌더링되지
    않고(`addGroup("설정", ...)` 호출도 없어짐), 그 4개 그룹 라벨로
    대체됐다.

    **2026-11-02 재갱신** — 사용자 요청("'리소스 매칭/배정' 버튼은
    가장 먼저 위치 및 구분선 추가, agent 관리 문구 삭제")로 "Agent
    관리" 하위 소제목 묶음(`.nav-sublabel`/`.nav-subgroup`)을 완전히
    걷어내고 라벨 없는 `.nav-divider` 하나로 단순화했다 — 그 두 CSS
    클래스는 이제 `webui.py`에 실제로 존재하지 않으므로 마커 목록에서
    뺀다(남겨두면 주석 속 글자로만 우연히 통과하는 거짓양성 테스트가
    된다)."""
    for marker in [
        "오늘 할 일",
        "리소스 관리",
        "그라운딩 관리",
        "모델 관리",
        "운영 관리",
        "SETTINGS_GROUPS",
        "nav-group",
        "admin-group",
        "nav-divider",
        "badge-adminonly",
    ]:
        assert marker in INDEX_HTML
    # 옛 3구역 라벨이 실제 렌더링 호출로 되살아나지 않았는지 회귀 방지.
    assert 'addGroup("관리자 도구"' not in INDEX_HTML
    assert 'addGroup("공용 조회"' not in INDEX_HTML
    assert 'addGroup("오늘 할 일"' in INDEX_HTML
    # "설정" 단일 그룹 호출은 4개 그룹으로 쪼개지며 사라졌다 — 되살아나지
    # 않았는지 회귀 방지.
    assert 'addGroup("설정"' not in INDEX_HTML
    # "Agent 관리" 하위 소제목 묶음(CSS 클래스 정의 자체)이 되살아나지
    # 않았는지 회귀 방지 — 역사적 설명을 담은 주석 속 문자열은 걸리지
    # 않도록 실제 CSS 선언/JS 리터럴 모양으로 좁혀서 찾는다.
    assert ".nav-sublabel {" not in INDEX_HTML
    assert ".nav-subgroup {" not in INDEX_HTML
    assert '{ subLabel: "Agent 관리"' not in INDEX_HTML


def test_gc74_every_tab_has_a_group_assigned():
    entries = _parse_tab_entries()
    assert len(entries) >= 13  # 회귀 방지: 탭이 실수로 통째로 사라지지 않았는지
    for e in entries:
        assert e["group"] in (
            "user",
            "common",
            "admin",
        ), f"{e['id']}에 group이 없습니다"


def test_gc75_group_classification_matches_existing_rbac_roles():
    """group 분류가 임의가 아니라 기존에 검증된 roles 배열과 일관되는지
    확인한다 — member/viewer가 하나도 없으면 반드시 admin이어야 한다.
    "user"와 "common"은 둘 다 member/viewer를 포함할 수 있어
    roles만으로 자동 구분은 안 된다 — 그 경계는 아래 테스트가 알려진
    "순수 조회" 화면 집합으로 직접 확인한다."""
    entries = _parse_tab_entries()
    for e in entries:
        has_general_role = "member" in e["roles"] or "viewer" in e["roles"]
        if not has_general_role:
            assert (
                e["group"] == "admin"
            ), f"{e['id']}: roles={e['roles']}인데 group={e['group']} (기대값 admin)"
        else:
            assert e["group"] in (
                "user",
                "common",
            ), f"{e['id']}: roles={e['roles']}인데 group={e['group']}"


def test_gc76_admin_only_tab_is_flagged_and_truly_admin_only():
    """`admin_only` 배지는 `roles == ["admin"]`(manager도 못 들어가는
    진짜 admin 전용 탭)에만 붙어야 한다 — 매니저도 접근 가능한데
    배지를 붙이면 오해를 준다. 사용자 요청(2026-11-02, "모델 관리
    메뉴 영역은 관리자(admin) 전용으로 변경")으로 "사용자 관리"
    하나뿐이던 admin 전용 탭이 "LLM 설정 최적화"/"모델 레지스트리"
    까지 3개로 늘었다 — id를 하드코딩해 나열하는 대신 `roles` 값
    자체로 일반화해 앞으로 admin 전용 탭이 더 늘어도 이 테스트가
    그대로 맞는지 검증한다."""
    entries = {e["id"]: e for e in _parse_tab_entries()}
    admin_tab = entries["admin"]
    assert admin_tab["admin_only"] is True
    assert admin_tab["roles"] == ["admin"]

    for e in entries.values():
        if e["group"] != "admin":
            continue
        expected = e["roles"] == ["admin"]
        assert e["admin_only"] is expected, (
            f"{e['id']}: roles={e['roles']}인데 admin_only={e['admin_only']}"
        )


def test_gc77_common_group_is_exactly_the_pure_reference_screens():
    """ "공용 조회" 그룹은 역할과 무관한 순수 조회 화면(인력 검색·조직도)만
    포함해야 한다 — 사용자 요청(2026-09-29, "관리자와 사용자 고유 업무와
    공통 업무를 구분해줘")에서 실측으로 뽑은 분류를 그대로 반영한다."""
    entries = {e["id"]: e for e in _parse_tab_entries()}
    common_ids = {e_id for e_id, e in entries.items() if e["group"] == "common"}
    # "manual"(이용 가이드, 2026-10-18 추가) — 역할과 무관한 순수 조회
    # 화면이라는 정의에 그대로 들어맞는다(특정 권한이 필요한 동작이
    # 전혀 없음, 전 역할이 같은 내용을 본다).
    assert common_ids == {"search", "orgchart", "manual"}
    for tab_id in common_ids:
        assert entries[tab_id]["roles"] == ["admin", "manager", "member", "viewer"]

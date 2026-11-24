"""결과물 부분 재작성 제안과 수락·거절(2026-11-24, P1-6 — "go ahead").

AI는 고른 섹션·구간만 다시 쓰고, 나머지는 서버가 원문 그대로 이어 붙인다. 수정안은 수락해야 반영되며
(이전 내용은 수정 이력), 거절은 기록만 남는다. 권한은 결과물 편집과 같다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import db, edit_proposals
from hair_platform.auth import Role, create_user

DOC = (
    "# 3분기 실적 보고\n\n머리 문단이다.\n\n"
    "## 1. 요약\n마케팅이 예산을 30% 초과했다. 교육은 43.75%만 집행했다.\n\n"
    "## 2. 권고\n- 마케팅 초과분 확인\n- 교육 잔여 예산 집행 계획 수립\n"
)


@pytest.fixture
def env():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "ep_admin", "pw", Role.ADMIN)
    member = create_user(conn, "ep_member", "pw", Role.MEMBER)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    did = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, content_type, status) "
        "VALUES (?, 'agent', 1, ?, 'markdown', 'pending_review')", (task, DOC),
    ).lastrowid
    conn.commit()
    return conn, admin, member, did


def _llm(answer):
    return patch("hair_platform.llm.call_llm", return_value=answer)


def _resolve():
    return patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m"))


def test_sections_split_by_headings_outside_code():
    doc = "머리\n\n## A\na\n```\n## 코드 안\n```\n## B\nb\n"
    titles = [s["title"] for s in edit_proposals.sections(doc)]
    assert titles == ["(머리말)", "A", "B"]
    assert edit_proposals.sections("제목 없는 글")[0]["title"] == "(문서 전체)"


def test_only_the_chosen_section_changes_and_only_after_accept(env):
    conn, admin, _member, did = env
    idx = next(s["index"] for s in edit_proposals.sections(DOC) if s["title"] == "1. 요약")
    with _resolve(), _llm("```markdown\n## 1. 요약\n마케팅 30% 초과, 교육 43.75% 집행 — 두 항목 조치 필요.\n```"):
        p = edit_proposals.propose(conn, did, admin, "admin", "한 문장으로 줄여 줘", section_index=idx)
    assert p["status"] == "pending" and p["target_label"] == "섹션: 1. 요약"
    assert not p["proposed_text"].startswith("```")  # 통째로 감싼 펜스는 벗긴다
    assert conn.execute("SELECT content FROM deliverables WHERE id = ?", (did,)).fetchone()[0] == DOC  # 아직 그대로

    out = edit_proposals.decide(conn, p["id"], admin, "admin", accept=True)
    new = out["deliverable"]["content"]
    assert "두 항목 조치 필요" in new
    assert new.startswith("# 3분기 실적 보고\n\n머리 문단이다.\n\n")  # 앞 섹션 그대로
    assert new.endswith("## 2. 권고\n- 마케팅 초과분 확인\n- 교육 잔여 예산 집행 계획 수립\n")  # 뒤 섹션 그대로
    assert "## 2. 권고" in new.split("두 항목 조치 필요.")[1]  # 다음 섹션이 붙지 않음(줄바꿈 유지)
    assert conn.execute("SELECT content FROM deliverable_revisions WHERE deliverable_id = ?", (did,)).fetchone()[0] == DOC
    with pytest.raises(edit_proposals.ProposalError, match="이미 처리"):
        edit_proposals.decide(conn, p["id"], admin, "admin", accept=True)


def test_selected_text_must_match_exactly_once(env):
    conn, admin, _m, did = env
    with _resolve(), _llm("교육은 예산의 43.75%(35백만 원)만 집행했다."):
        p = edit_proposals.propose(conn, did, admin, "admin", "금액을 덧붙여", selected_text="교육은 43.75%만 집행했다.")
    assert p["original_text"] == "교육은 43.75%만 집행했다."
    with pytest.raises(edit_proposals.ProposalError, match="찾지 못했"):
        edit_proposals.propose(conn, did, admin, "admin", "x", selected_text="없는 문장")
    with pytest.raises(edit_proposals.ProposalError, match="여러 번"):
        edit_proposals.propose(conn, did, admin, "admin", "x", selected_text="마케팅")


def test_reject_keeps_content(env):
    conn, admin, _m, did = env
    with _resolve(), _llm("바뀐 글"):
        p = edit_proposals.propose(conn, did, admin, "admin", "x", selected_text="머리 문단이다.")
    out = edit_proposals.decide(conn, p["id"], admin, "admin", accept=False)
    assert out["proposal"]["status"] == "rejected" and out["deliverable"] is None
    assert conn.execute("SELECT content FROM deliverables WHERE id = ?", (did,)).fetchone()[0] == DOC


def test_accept_after_other_change_applies_in_place_or_refuses(env):
    conn, admin, _m, did = env
    with _resolve(), _llm("머리 문단을 새로 썼다."):
        p1 = edit_proposals.propose(conn, did, admin, "admin", "x", selected_text="머리 문단이다.")
        p2 = edit_proposals.propose(conn, did, admin, "admin", "x", selected_text="- 마케팅 초과분 확인")
    # 다른 수정이 먼저 반영돼도, 원래 구간이 그대로 남아 있으면 그 자리에만 반영된다.
    conn.execute("UPDATE deliverables SET content = content || '\n## 3. 부록\n추가\n' WHERE id = ?", (did,))
    conn.commit()
    out = edit_proposals.decide(conn, p1["id"], admin, "admin", accept=True)
    assert "머리 문단을 새로 썼다." in out["deliverable"]["content"] and "## 3. 부록" in out["deliverable"]["content"]
    # 원래 구간이 사라졌으면 반영하지 않는다.
    conn.execute("UPDATE deliverables SET content = REPLACE(content, '- 마케팅 초과분 확인', '- 다른 사람이 고침') WHERE id = ?", (did,))
    conn.commit()
    with pytest.raises(edit_proposals.ProposalError, match="찾을 수 없습니다"):
        edit_proposals.decide(conn, p2["id"], admin, "admin", accept=True)


def test_permissions_follow_deliverable_edit(env):
    conn, _admin, member, did = env
    with _resolve(), _llm("x"), pytest.raises(PermissionError):
        edit_proposals.propose(conn, did, member, "member", "x", section_index=0)


def test_api_round_trip_and_member_model_choice(client_and_conn):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    did = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, content_type, status) "
        "VALUES (?, 'agent', 1, ?, 'markdown', 'pending_review')", (task, DOC),
    ).lastrowid
    conn.commit()
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    secs = client.get(f"/api/deliverables/{did}/sections").json()
    assert [s["title"] for s in secs] == ["3분기 실적 보고", "1. 요약", "2. 권고"]
    with _resolve(), _llm("## 2. 권고\n- 마케팅 초과분 원인 확인(이번 주)\n"):
        r = client.post(f"/api/deliverables/{did}/edit-proposals", json={"instruction": "기한을 넣어", "section_index": 2})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert client.get(f"/api/deliverables/{did}/edit-proposals").json()[0]["id"] == pid
    assert client.post(f"/api/edit-proposals/{pid}/accept").status_code == 200
    assert "이번 주" in conn.execute("SELECT content FROM deliverables WHERE id = ?", (did,)).fetchone()[0]
    assert client.post(f"/api/edit-proposals/{pid}/maybe").status_code == 404
    bad = client.post(f"/api/deliverables/{did}/edit-proposals", json={"instruction": "x", "section_index": 99})
    assert bad.status_code == 400


def test_garbled_words_in_proposal_are_warned(env):
    """실측(로컬 35B): 고쳐 쓰며 "순현금흐름"→"순현나름", "좌우하며"→"우우하며"로 옮겼다 — 수락 전에 알린다."""
    conn, admin, _m, did = env
    doc = DOC + "\n## 3. 리스크\n- 초기 투자가 전체 규모 대비 약 30%로 NPV를 크게 좌우하며, 하락 시 음수 전환 가능.\n"
    conn.execute("UPDATE deliverables SET content = ? WHERE id = ?", (doc, did))
    conn.commit()
    instruction = "약 30%를 실제 수치(초기 투자 900백만 원 = 4년 순현금흐름 합계 1,220백만 원의 73.8%)로"
    bad = "## 3. 리스크\n- 초기 투자 900백만 원이 4년 순현나름 합계 1,220백만 원의 73.8%로 NPV를 크게 우우하며, 하락 시 음수 전환 가능.\n"
    good = "## 3. 리스크\n- 초기 투자 900백만 원은 4년 순현금흐름 합계 1,220백만 원의 73.8%로 NPV를 크게 좌우한다.\n"
    idx = next(s["index"] for s in edit_proposals.sections(doc) if s["title"] == "3. 리스크")
    with _resolve(), _llm(bad):
        p = edit_proposals.propose(conn, did, admin, "admin", instruction, section_index=idx)
    assert any("순현나름" in w and "순현금흐름" in w for w in p["warnings"])
    assert any("우우하며" in w for w in p["warnings"])
    with _resolve(), _llm(good):
        q = edit_proposals.propose(conn, did, admin, "admin", instruction, section_index=idx)
    assert q["warnings"] == []

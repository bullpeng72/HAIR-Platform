"""지식베이스 등록 체계 — 경로 B "제출 → admin 결재 → 등록".

사용자 요청(2026-11-20, "지식 베이스는 admin 담당자가 문서로 등록하는
경우와 대화 및 업무배정을 통해 생성된 산출물 중 '지식베이스로 보내기'
및 admin 결재 후 등록되는 것으로 개선") — 지식베이스(`knowledge_chunks`,
전역·영구, 내부지식검색 스킬이 누구에게나 검색해 주는 공간)에 들어오는 길을
둘로 정리한다.

- 경로 A(직접 등록): admin이 "지식 베이스" 탭에서 문서/텍스트를 올린다
  (`documents.ingest_*`, `documents.source_type='direct'`).
- 경로 B(이 모듈): 대화 메시지·업무 산출물의 참여자가 "지식베이스로
  보내기"로 **제출**하면, admin이 결재함에서 승인해야 색인된다
  (`documents.source_type='submission'`).

설계 결정(같은 날 분석에서 확정한 기본값):
- 제출 대상: 대화 메시지(사람·AI 모두), 업무 산출물 중 `approved`만 —
  업무 결재와 지식 결재는 별개이며, 업무에서 승인되지 않은 초안은 지식
  후보가 아니다.
- **내용 스냅샷**: 제출 시점에 전처리(바깥 코드펜스 제거, HTML 본문 텍스트
  추출)까지 마친 텍스트를 `content_snapshot`에 고정한다 — admin이 결재
  화면에서 보는 것 = 실제로 색인되는 것. 원본이 이후 수정·삭제돼도
  등록된 지식은 영향받지 않는다(원본↔지식 동기화 문제를 구조적으로 제거).
- 결재자: admin 전용 1단계(`approvals.subject_type='knowledge_submission'`,
  approver_id 미지정 — 매니저 결재 체인이 아님). manager는 인박스에서도
  이 유형을 보지 못하고, 결재 엔드포인트도 막는다(`app.py`).
- admin 본인 제출: 결재 없이 바로 등록한다(경로 A와 같은 권한이고, 자기
  승인 금지 규칙 때문에 admin이 1명뿐이면 교착되므로) — 감사로그엔 남긴다.
- code 타입: 기본 제외, `allow_code=True`로 명시해야 제출된다.
- 중복: 같은 원본으로 결재 대기 중인 건이 있으면 막고, 이미 등록된
  원본이면 "갱신 요청"(`supersedes_id`)으로 받아 승인 시 기존 문서를
  대체한다.
- 임베딩: 승인 커밋 후 백그라운드(`register_submission()`)에서 실행 —
  큰 문서는 로컬 임베딩에 수 분이 걸릴 수 있어 결재 요청 자체가 시간
  초과되지 않게 한다.

이 모듈은 `conversations`를 import하지 않는다(`conversations`가 이후 이
모듈을 쓰게 될 때 순환 import가 생기지 않도록) — 대화 참여자 확인 등
필요한 조회는 SQL로 직접 한다.
"""

from __future__ import annotations

import re
import sqlite3

from hair_platform import audit, deliverables, documents

SUBJECT_TYPE = "knowledge_submission"
SOURCE_TYPES = ("conversation_message", "deliverable")
_OPEN_STATUSES = ("pending", "registering")

_OUTER_FENCE_CLOSED_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*?)\n?```\s*$", re.DOTALL)
_OUTER_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)$", re.DOTALL)
_FULL_HTML_DOC_RE = re.compile(r"^<!DOCTYPE\s+html|^<html[\s>]", re.IGNORECASE)
_TITLE_MAX_CHARS = 80


def _strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    closed = _OUTER_FENCE_CLOSED_RE.match(stripped)
    if closed:
        return closed.group(1).strip()
    open_only = _OUTER_FENCE_OPEN_RE.match(stripped)
    return open_only.group(1).strip() if open_only else stripped


def prepare_text(raw: str, content_type: str) -> tuple[str, str]:
    """등록될 텍스트와 판정된 content_type을 돌려준다.

    HTML(선언된 타입이든, 펜스를 벗겨 보니 완전한 HTML 문서든)은 본문
    텍스트만 남긴다 — 48k자 발표자료를 그대로 쪼개면 청크 대부분이
    CSS·태그가 돼 검색 품질이 무너진다(2026-11-20 분석)."""
    unwrapped = _strip_outer_fence(raw or "")
    if content_type == "html" or _FULL_HTML_DOC_RE.match(unwrapped):
        return documents._extract_html_text(unwrapped.encode("utf-8")), "html"
    return unwrapped, content_type


def _default_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:_TITLE_MAX_CHARS]
    return fallback


def _load_conversation_message(
    conn: sqlite3.Connection, message_id: int, actor_id: int, actor_role: str
) -> dict:
    row = conn.execute(
        "SELECT m.id, m.conversation_id, m.content, c.title FROM conversation_messages m "
        "JOIN conversations c ON c.id = m.conversation_id WHERE m.id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        raise ValueError("source_not_found")
    participant = conn.execute(
        "SELECT 1 FROM conversation_participants WHERE conversation_id = ? "
        "AND participant_type = 'human' AND participant_id = ?",
        (row["conversation_id"], actor_id),
    ).fetchone()
    if participant is None and actor_role != "admin":
        raise PermissionError("not_a_participant")
    return {
        "content": row["content"],
        "content_type": "markdown",
        "conversation_id": row["conversation_id"],
        "task_id": None,
        "source_label": f"대화 '{row['title'] or '(제목 없음)'}' · 메시지 #{row['id']}",
    }


def _load_deliverable(
    conn: sqlite3.Connection, deliverable_id: int, actor_id: int, actor_role: str
) -> dict:
    row = conn.execute(
        "SELECT d.id, d.task_id, d.content, d.content_type, d.status, t.role_name "
        "FROM deliverables d JOIN tasks t ON t.id = d.task_id WHERE d.id = ?",
        (deliverable_id,),
    ).fetchone()
    if row is None:
        raise ValueError("source_not_found")
    # 갤러리(`list_deliverables_gallery`)와 같은 열람 기준 — admin/manager는
    # 조직 전체, 그 외는 이 업무의 참여자만.
    if actor_role not in ("admin", "manager") and not deliverables.can_comment_on_task(
        conn, row["task_id"], actor_id
    ):
        raise PermissionError("not_a_participant")
    if row["status"] != "approved":
        raise ValueError("deliverable_not_approved")
    return {
        "content": row["content"],
        "content_type": row["content_type"],
        "conversation_id": None,
        "task_id": row["task_id"],
        "source_label": f"업무 '{row['role_name']}' · 산출물 #{row['id']}",
    }


def _create_approval(conn: sqlite3.Connection, requested_by: int, submission_id: int) -> None:
    conn.execute(
        "INSERT INTO approvals (requested_by, subject_type, subject_id, step, "
        "approver_id, status) VALUES (?, ?, ?, 1, NULL, 'pending')",
        (requested_by, SUBJECT_TYPE, submission_id),
    )


def _prepare(
    conn: sqlite3.Connection,
    actor_id: int,
    actor_role: str,
    source_type: str,
    source_id: int,
    title: str | None,
    allow_code: bool,
) -> dict:
    """제출 전 검증·전처리 — `preview()`와 `submit()`이 공유한다(미리보기에서
    본 내용과 실제 제출 내용이 어긋나지 않게)."""
    if source_type not in SOURCE_TYPES:
        raise ValueError("invalid_source_type")
    if actor_role == "viewer":
        raise PermissionError("viewer_cannot_submit")
    loader = (
        _load_conversation_message
        if source_type == "conversation_message"
        else _load_deliverable
    )
    source = loader(conn, source_id, actor_id, actor_role)

    if source["content_type"] == "code" and not allow_code:
        raise ValueError("code_requires_confirmation")
    prepared, content_type = prepare_text(source["content"], source["content_type"])
    if not prepared.strip():
        raise ValueError("empty_after_preprocessing")

    open_row = conn.execute(
        f"SELECT id FROM knowledge_submissions WHERE source_type = ? AND source_id = ? "
        f"AND status IN ({','.join('?' * len(_OPEN_STATUSES))})",
        (source_type, source_id, *_OPEN_STATUSES),
    ).fetchone()
    if open_row is not None:
        raise ValueError("already_pending")
    registered_row = conn.execute(
        "SELECT id FROM knowledge_submissions WHERE source_type = ? AND source_id = ? "
        "AND status = 'registered' ORDER BY id DESC LIMIT 1",
        (source_type, source_id),
    ).fetchone()
    return {
        "source": source,
        "prepared": prepared,
        "content_type": content_type,
        "supersedes_id": registered_row["id"] if registered_row is not None else None,
        "title": (title or "").strip()[:_TITLE_MAX_CHARS]
        or _default_title(prepared, source["source_label"]),
    }


def preview(
    conn: sqlite3.Connection,
    actor_id: int,
    actor_role: str,
    source_type: str,
    source_id: int,
    allow_code: bool = False,
) -> dict:
    """제출 모달용 — 아무것도 저장하지 않고, 등록될 텍스트(전처리 결과)와
    자동 제목·출처·갱신 여부만 돌려준다. 검증 오류도 `submit()`과 같다."""
    p = _prepare(conn, actor_id, actor_role, source_type, source_id, None, allow_code)
    return {
        "source_type": source_type,
        "source_id": source_id,
        "source_label": p["source"]["source_label"],
        "title": p["title"],
        "content_snapshot": p["prepared"],
        "content_type": p["content_type"],
        "is_update": p["supersedes_id"] is not None,
        "requires_approval": actor_role != "admin",
    }


def submit(
    conn: sqlite3.Connection,
    actor_id: int,
    actor_role: str,
    source_type: str,
    source_id: int,
    title: str | None = None,
    note: str | None = None,
    allow_code: bool = False,
) -> dict:
    """제출 건을 만든다. admin이 아니면 결재(`approvals`)를 걸고 `pending`,
    admin이면 결재 없이 `registering`으로 둔다 — 반환값의 `needs_registration`
    이 True면 호출자가 커밋 뒤 `register_submission()`을 백그라운드로
    돌려야 한다."""
    p = _prepare(conn, actor_id, actor_role, source_type, source_id, title, allow_code)
    source = p["source"]
    prepared = p["prepared"]
    content_type = p["content_type"]
    supersedes_id = p["supersedes_id"]
    final_title = p["title"]
    is_admin = actor_role == "admin"
    cur = conn.execute(
        "INSERT INTO knowledge_submissions (source_type, source_id, conversation_id, "
        "task_id, source_label, title, note, content_snapshot, content_type, status, "
        "supersedes_id, submitted_by, decided_by, decided_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? THEN datetime('now') END)",
        (
            source_type,
            source_id,
            source["conversation_id"],
            source["task_id"],
            source["source_label"],
            final_title,
            (note or "").strip() or None,
            prepared,
            content_type,
            "registering" if is_admin else "pending",
            supersedes_id,
            actor_id,
            actor_id if is_admin else None,
            is_admin,
        ),
    )
    submission_id = cur.lastrowid
    assert submission_id is not None
    if not is_admin:
        _create_approval(conn, actor_id, submission_id)
    audit.record(
        conn,
        actor_id,
        "knowledge_submission_created",
        "knowledge_submissions",
        submission_id,
        after={
            "source_type": source_type,
            "source_id": source_id,
            "supersedes_id": supersedes_id,
            "auto_approved_by_admin": is_admin,
        },
    )
    conn.commit()
    return {**get_submission(conn, submission_id), "needs_registration": is_admin}


def decide(
    conn: sqlite3.Connection,
    submission_id: int,
    decider_id: int,
    approve: bool,
    reason: str | None,
) -> dict:
    """결재함 승인/반려를 반영한다(커밋은 호출자 — `app.decide_approval()`
    이 `approvals` 갱신과 한 트랜잭션으로 커밋, `deliverables.resolve_
    deliverable_decision()`과 같은 관례). 승인이면 `registering`으로만
    바꾸고, 실제 색인은 호출자가 커밋 뒤 백그라운드로 건다."""
    row = conn.execute(
        "SELECT status FROM knowledge_submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if row is None:
        raise ValueError("submission_not_found")
    if row["status"] != "pending":
        raise ValueError("submission_not_pending")
    conn.execute(
        "UPDATE knowledge_submissions SET status = ?, decided_by = ?, "
        "decided_at = datetime('now'), reject_reason = ? WHERE id = ?",
        (
            "registering" if approve else "rejected",
            decider_id,
            None if approve else reason,
            submission_id,
        ),
    )
    return {"submission_id": submission_id, "status": "registering" if approve else "rejected"}


def _delete_document_rows(conn: sqlite3.Connection, document_id: int) -> None:
    # 제출 건이 이 문서를 FK로 가리키고 있으면 먼저 끊는다(안 끊으면
    # FOREIGN KEY constraint failed — 갱신 요청으로 이전 문서를 대체할 때).
    conn.execute(
        "UPDATE knowledge_submissions SET document_id = NULL WHERE document_id = ?",
        (document_id,),
    )
    conn.execute("DELETE FROM knowledge_chunks WHERE source_document_id = ?", (document_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def register_submission(conn: sqlite3.Connection, submission_id: int) -> dict:
    """`registering` 제출 건을 실제로 색인한다(임베딩 비용 발생 — 승인된
    건에만 호출됨). 실패하면 반쯤 만들어진 문서/청크를 지우고 `failed`로
    남긴다(`documents._ingest()`는 청크마다 커밋하므로 중간 실패 시
    찌꺼기가 남는다). 갱신 요청이면 성공한 뒤에만 이전 문서를 지운다 —
    실패해도 기존 지식은 그대로 유지된다."""
    row = conn.execute(
        "SELECT * FROM knowledge_submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if row is None:
        raise ValueError("submission_not_found")
    if row["status"] != "registering":
        return get_submission(conn, submission_id)
    try:
        ingested = documents._ingest(
            conn,
            row["decided_by"] or row["submitted_by"],
            row["title"],
            row["content_snapshot"],
            None,
            None,
            source_type="submission",
            submission_id=submission_id,
        )
    except Exception as exc:  # noqa: BLE001 — 임베딩 실패를 제출 건 상태로 남긴다
        conn.rollback()
        for doc in conn.execute(
            "SELECT id FROM documents WHERE submission_id = ?", (submission_id,)
        ).fetchall():
            _delete_document_rows(conn, doc["id"])
        conn.execute(
            "UPDATE knowledge_submissions SET status = 'failed', error = ? WHERE id = ?",
            (str(exc)[:500], submission_id),
        )
        conn.commit()
        return get_submission(conn, submission_id)

    if row["supersedes_id"] is not None:
        old = conn.execute(
            "SELECT document_id FROM knowledge_submissions WHERE id = ?",
            (row["supersedes_id"],),
        ).fetchone()
        if old is not None and old["document_id"] is not None:
            _delete_document_rows(conn, old["document_id"])
        conn.execute(
            "UPDATE knowledge_submissions SET status = 'superseded' WHERE id = ?",
            (row["supersedes_id"],),
        )
    conn.execute(
        "UPDATE knowledge_submissions SET status = 'registered', document_id = ?, "
        "error = NULL WHERE id = ?",
        (ingested["document_id"], submission_id),
    )
    audit.record(
        conn,
        row["decided_by"] or row["submitted_by"],
        "knowledge_submission_registered",
        "knowledge_submissions",
        submission_id,
        after={
            "document_id": ingested["document_id"],
            "chunk_count": ingested["chunk_count"],
            "superseded_id": row["supersedes_id"],
        },
    )
    conn.commit()
    return get_submission(conn, submission_id)


def withdraw(conn: sqlite3.Connection, submission_id: int, actor_id: int) -> dict:
    """제출자 본인이 결재 대기 중인 건을 회수한다."""
    row = conn.execute(
        "SELECT status, submitted_by FROM knowledge_submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        raise ValueError("submission_not_found")
    if row["submitted_by"] != actor_id:
        raise PermissionError("not_the_submitter")
    if row["status"] != "pending":
        raise ValueError("submission_not_pending")
    conn.execute(
        "UPDATE knowledge_submissions SET status = 'withdrawn' WHERE id = ?",
        (submission_id,),
    )
    conn.execute(
        "UPDATE approvals SET status = 'skipped' WHERE subject_type = ? "
        "AND subject_id = ? AND status IN ('pending', 'waiting')",
        (SUBJECT_TYPE, submission_id),
    )
    audit.record(
        conn, actor_id, "knowledge_submission_withdrawn", "knowledge_submissions", submission_id
    )
    conn.commit()
    return get_submission(conn, submission_id)


_SELECT = (
    "SELECT ks.*, su.username AS submitted_by_username, "
    "du.username AS decided_by_username FROM knowledge_submissions ks "
    "LEFT JOIN users su ON su.id = ks.submitted_by "
    "LEFT JOIN users du ON du.id = ks.decided_by "
)


def get_submission(conn: sqlite3.Connection, submission_id: int) -> dict:
    row = conn.execute(_SELECT + "WHERE ks.id = ?", (submission_id,)).fetchone()
    if row is None:
        raise ValueError("submission_not_found")
    return dict(row)


def list_submissions(
    conn: sqlite3.Connection,
    actor_id: int,
    actor_role: str,
    status: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
) -> list[dict]:
    """admin은 전체, 그 외는 본인이 제출한 건만."""
    clauses: list[str] = []
    params: list = []
    if actor_role != "admin":
        clauses.append("ks.submitted_by = ?")
        params.append(actor_id)
    if status is not None:
        clauses.append("ks.status = ?")
        params.append(status)
    if source_type is not None:
        clauses.append("ks.source_type = ?")
        params.append(source_type)
    if source_id is not None:
        clauses.append("ks.source_id = ?")
        params.append(source_id)
    where = ("WHERE " + " AND ".join(clauses) + " ") if clauses else ""
    rows = conn.execute(_SELECT + where + "ORDER BY ks.id DESC", params).fetchall()
    return [dict(r) for r in rows]


def status_map(
    conn: sqlite3.Connection,
    actor_id: int,
    actor_role: str,
    source_type: str,
    source_ids: list[int],
) -> dict[int, dict]:
    """원본별 **최신** 제출 상태 — 대화 메시지/산출물 카드의 배지용.
    제출자가 누구든 상태·제목은 보여준다(그 원본을 볼 수 있는 화면에서만
    호출되고, "이미 결재 중/등록됨"을 알아야 중복 제출을 안 한다). 반려
    사유와 실패 오류는 본인 제출 건이거나 admin일 때만 내려준다."""
    if source_type not in SOURCE_TYPES or not source_ids:
        return {}
    ids = sorted({int(i) for i in source_ids})[:500]
    rows = conn.execute(
        f"SELECT * FROM knowledge_submissions WHERE source_type = ? "
        f"AND source_id IN ({','.join('?' * len(ids))}) ORDER BY id ASC",
        (source_type, *ids),
    ).fetchall()
    result: dict[int, dict] = {}
    for r in rows:  # id 오름차순 — 같은 원본이면 나중 행이 덮어써 최신만 남는다
        visible = actor_role == "admin" or r["submitted_by"] == actor_id
        result[r["source_id"]] = {
            "submission_id": r["id"],
            "status": r["status"],
            "title": r["title"],
            "mine": r["submitted_by"] == actor_id,
            "reject_reason": r["reject_reason"] if visible else None,
            "error": r["error"] if visible else None,
        }
    return result


def describe_for_approval(conn: sqlite3.Connection, submission_id: int) -> dict | None:
    """`approvals.describe_subject()`용 — 결재자가 무엇을 승인하는지
    (제목·출처·실제로 색인될 텍스트)를 결재함에서 바로 보게 한다."""
    row = conn.execute(
        "SELECT ks.*, t.role_name FROM knowledge_submissions ks "
        "LEFT JOIN tasks t ON t.id = ks.task_id WHERE ks.id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        return None
    update_note = " (기존 지식 갱신 요청)" if row["supersedes_id"] else ""
    return {
        "task_id": row["task_id"],
        "task_role_name": row["role_name"],
        "resource_type": SUBJECT_TYPE,
        "resource_name": f"지식 등록 — '{row['title']}' ({row['source_label']}){update_note}",
        "content": row["content_snapshot"],
        "content_type": "markdown" if row["content_type"] != "code" else "code",
    }

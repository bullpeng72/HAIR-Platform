"""여러 routes_* 모듈이 함께 쓰는 헬퍼(2026-11-25 — `create_app()` 분리 때 지역 함수에서 모듈 수준으로)."""

from __future__ import annotations

from fastapi import (
    HTTPException,
)

from hair_platform import (
    documents,
)


# ---------- 외부 자료 업로드(사용자 요청, 2026-10-26, "대화창 및
# 에이전트로 구성된 업무에서 외부 자료(마크다운, pdf, html)를
# 입력받을 수 있도록") ----------
# 2차 결정(2026-10-26, "A안(휘발성) + B안(UI 재배치)") — 처음엔
# 대화창 업로드도 이 전역 VectorDB로 들어갔는데, 사용자 분석으로
# "대화/업무 자료는 그때그때 쓰고 버려야 하는데 전부 영구화되고
# 있다"는 문제가 드러나 대화 첨부는 아래 `/api/conversations/{id}/
# attachments`(저장 없이 메시지로 직접 주입)로 분리했다. 이
# `/api/documents`는 이제 "지식 베이스"(admin/manager 전용 공통·
# 영구 지식)만 다룬다.
#
# 사용자 요청(2026-11-20, "지식 베이스는 admin 담당자가 문서로 등록하는
# 경우와 ... admin 결재 후 등록되는 것으로 개선") — 쓰기(업로드·텍스트
# 입력·문서/조각 삭제)는 **admin 전용**(경로 A "직접 등록")으로 좁혔다.
# manager는 조회만 하고, 지식을 넣으려면 경로 B(제출 → admin 결재)를
# 쓴다.
#
# 사용자 요청(2026-11-18, "자료 업로드 형식으로 csv, excel, word,
# powerpoint를 포함하도록" → "구현 시작") — 상한이 포맷마다 달라져
# (`documents.max_upload_bytes()` 참고) 고정 10MB 상수 대신 파일명
# 기준으로 그때그때 조회한다. 세 업로드 경로(이 엔드포인트·대화
# 첨부·Task 실행 참고자료) 전부 같은 헬퍼를 쓴다.
def _reject_if_too_large(filename: str, raw_bytes: bytes) -> None:
    limit = documents.max_upload_bytes(filename)
    if len(raw_bytes) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"파일이 너무 큽니다(최대 {limit // (1024 * 1024)}MB)",
        )


# 사용자 요청(2026-11-20, 지식베이스 등록 체계 개편) — 경로 B
# "제출 → admin 결재 → 등록". 제출은 원본(대화/업무)의 참여자 누구나
# (viewer 제외) 할 수 있다 — 임베딩 비용은 admin 승인 뒤에만 생긴다.
_KNOWLEDGE_SUBMISSION_ERRORS: dict[str, tuple[int, str]] = {
    "source_not_found": (404, "보낼 원본을 찾을 수 없습니다"),
    "submission_not_found": (404, "지식 등록 요청을 찾을 수 없습니다"),
    "deliverable_not_approved": (
        400,
        "승인된 산출물만 지식베이스로 보낼 수 있습니다",
    ),
    "code_requires_confirmation": (
        400,
        (
            "코드 산출물은 기본적으로 지식베이스 대상이 아닙니다 — 그래도 "
            "보내려면 allow_code를 켜세요"
        ),
    ),
    "empty_after_preprocessing": (400, "등록할 본문 텍스트가 없습니다"),
    "already_pending": (409, "이 원본은 이미 지식 등록 결재가 진행 중입니다"),
    "submission_not_pending": (409, "결재 대기 중인 요청만 처리할 수 있습니다"),
    "not_a_participant": (403, "이 대화/업무의 참여자만 보낼 수 있습니다"),
    "viewer_cannot_submit": (403, "열람 전용 사용자는 보낼 수 없습니다"),
    "not_the_submitter": (403, "본인이 제출한 요청만 회수할 수 있습니다"),
}


def _knowledge_submission_http_error(exc: Exception) -> HTTPException:
    status_code, detail = _KNOWLEDGE_SUBMISSION_ERRORS.get(
        str(exc), (400, str(exc))
    )
    return HTTPException(status_code=status_code, detail=detail)

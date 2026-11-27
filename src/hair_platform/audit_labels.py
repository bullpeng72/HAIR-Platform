"""활동 기록의 동작·대상 이름(2026-11-28) — 활동 기록 화면이 `approval_transferred`·`human_capacity_set` 같은 내부 코드를
그대로 보여 줬다. 동작 코드는 220개가 넘고 기능을 더할 때마다 늘어나서, 코드마다 이름을 손으로 달면 곧 빠진 것이 생긴다.

코드는 이미 "대상 … 동작" 순서(`human_hourly_rate_set`)라 한국어 어순과 같다 — 단어별로 옮겨 붙인다. 단어 순서만으로
어색한 몇 개는 `ACTION_OVERRIDES`에 통째로 둔다. 모르는 단어가 하나라도 있으면 코드를 그대로 돌려준다(틀린 이름보다
낫다). `tests/unit/test_phase172_activity_log.py`가 소스의 모든 `audit.record(...)` 동작 코드에 모르는 단어가 없는지 본다.
"""

from __future__ import annotations

WORDS: dict[str, str] = {
    # 대상
    "pipeline": "파이프라인", "agent": "Agent", "conversation": "대화", "team": "팀", "stage": "단계", "model": "모델",
    "task": "할 일", "deliverable": "결과물", "pool": "", "human": "사람", "config": "설정", "trigger": "트리거",
    "registry": "레지스트리", "run": "실행", "execution": "실행", "approval": "결재", "user": "사용자", "tool": "도구",
    "schedule": "정기 실행", "policy": "정책", "knowledge": "지식", "skill": "스킬", "message": "메시지",
    "inbound": "외부 입력", "generation": "생성", "crew": "Crew", "comment": "댓글", "automation": "자동화",
    "assignment": "배정", "template": "템플릿", "submission": "제출", "resource": "자원", "repository": "저장소",
    "performance": "성과", "participant": "참여자", "mention": "멘션", "mail": "메일", "language": "언어",
    "inbox": "수신함", "document": "문서", "delegation": "위임", "data": "데이터", "calendar": "캘린더",
    "budget": "예산", "brief": "업무 요청", "attachment": "첨부", "status": "상태", "reply": "답장", "qa": "검수",
    "process": "프로세스", "password": "비밀번호", "org": "조직", "member": "구성원", "members": "구성원",
    "llm": "LLM", "key": "키", "hourly": "시간당", "feed": "피드", "escalation": "에스컬레이션", "cost": "비용",
    "context": "컨텍스트", "connection": "연결", "code": "코드", "check": "점검", "benchmark": "성능 테스트",
    "auto": "자동", "api": "API", "ai": "AI", "action": "실행 동작", "absence": "부재", "absences": "부재",
    "window": "길이", "weekly": "주간", "warning": "경고", "visibility": "공개 범위", "tuning": "튜닝",
    "tiers": "단계", "self": "본인", "search": "검색", "sampling": "샘플링", "rework": "재작업", "retry": "재시도",
    "retention": "보존 기간", "rate": "단가", "rag": "RAG", "query": "조회", "purge": "정리", "protection": "보호",
    "priority": "우선순위", "pricing": "가격", "platform": "플랫폼", "persona": "페르소나", "output": "출력",
    "options": "옵션", "optimization": "최적화", "numeric": "숫자", "notify": "알림", "notes": "메모",
    "narration": "설명문", "name": "이름", "mode": "방식", "mission": "임무", "max": "최대", "manager": "상사",
    "login": "로그인", "level": "숙련도", "lesson": "배운 점", "example": "좋은 결과 예시", "leader": "리더",
    "judgment": "판단", "job": "작업", "hours": "시간", "evaluation": "평가", "embedding": "임베딩",
    "defaults": "기본값", "decision": "결정", "content": "내용", "consent": "동의", "concurrent": "동시 실행",
    "chunk": "조각", "change": "변경", "capacity": "처리 용량", "cancel": "취소", "baseline": "기준선",
    "auxiliary": "보조", "approver": "결재자", "adhoc": "즉석", "active": "사용 여부", "employee": "사번", "id": "",
    "edit": "수정", "first": "처음", "dependency": "선행 관계", "time": "작업 시간", "entry": "항목", "logged": "기록",
    # 동작
    "updated": "수정", "deleted": "삭제", "changed": "변경", "created": "생성", "set": "설정", "started": "시작",
    "removed": "제거", "failed": "실패", "completed": "완료", "added": "추가", "registered": "등록",
    "toggled": "켜기·끄기", "requested": "요청", "invited": "초대", "fired": "실행", "executed": "실행",
    "read": "읽기", "recorded": "기록", "repaired": "고침", "reordered": "순서 변경", "rejected": "반려",
    "reset": "초기화", "transferred": "넘김", "edited": "수정", "deprecated": "사용 중단", "escalated": "에스컬레이션",
    "cancelled": "취소", "approved": "승인", "onboarded": "온보딩", "imported": "가져오기", "withdrawn": "철회",
    "used": "사용", "unlinked": "연결 해제", "linked": "연결", "succeeded": "성공", "submitted": "제출",
    "stalled": "멈춤", "saved": "저장", "revoked": "폐기", "reverted": "되돌림", "resumed": "재개",
    "proposed": "제안", "offboarded": "퇴사 처리", "occurred": "발생", "migrated": "전환", "locked": "잠김",
    "left": "나감", "ingested": "등록", "indexed": "색인", "delegated": "위임", "deduplicated": "중복 제거",
    "confirmed": "확정", "committed": "커밋", "applied": "적용", "activated": "켜기", "accepted": "수락",
    "dismissed": "무시", "retired": "끄기", "sent": "보냄", "skipped": "건너뜀", "pending": "대기",
}

ACTION_OVERRIDES: dict[str, str] = {
    "pipeline_stage_awaiting_approval": "파이프라인 단계 결재 대기",
    "task_requires_human_judgment_updated": "할 일 사람 판단 필요 여부 수정",
    "conversation_message_saved_as_deliverable": "대화 메시지를 결과물로 저장",
    "conversation_transferred_to_brief": "대화를 업무 요청으로 전환",
    "deliverable_committed_to_repository": "결과물을 저장소에 커밋",
    "comment_triggered_execution_started": "댓글로 실행 시작",
    "comment_triggered_execution_failed": "댓글로 시작한 실행 실패",
    "agent_created_directly": "Agent 직접 생성",
    "assignment_requested_with_warning": "배정 요청(경고 있음)",
    "stage_lesson_activated": "배운 점 켜기",
    "stage_lesson_dismissed": "배운 점 무시",
    "stage_lesson_retired": "배운 점 끄기",
    "stage_example_active": "좋은 결과 예시 켜기",
    "stage_example_dismissed": "좋은 결과 예시 무시",
    "stage_example_retired": "좋은 결과 예시 끄기",
    "stage_action_sent": "결재 후 실행 동작 보냄",
    "stage_action_failed": "결재 후 실행 동작 실패",
    "stage_action_set": "결재 후 실행 동작 설정",
    "stage_action_removed": "결재 후 실행 동작 제거",
    "human_pool_updated": "사람 정보 수정",
    "human_pool_imported": "인사 자료 가져오기",
    "retention_purge_executed": "보존 기간 지난 기록 정리",
    "login_locked": "로그인 잠김(여러 번 실패)",
    "audit_log_exported": "활동 기록 내보내기",
    "pipeline_schedule_paused": "정기 실행 멈춤(만든 사람 점검)",
    "automation_trigger_paused": "이벤트 트리거 멈춤(만든 사람 점검)",
    "inbound_trigger_paused": "외부 입력 트리거 멈춤(만든 사람 점검)",
    "pipeline_schedule_transferred": "정기 실행 넘김",
    "automation_trigger_transferred": "이벤트 트리거 넘김",
    "inbound_trigger_transferred": "외부 입력 트리거 넘김",
    "task_schedule_updated": "할 일 기한·예상 시간 수정",  # schedule = 정기 실행이 아니라 할 일 일정
    "pipeline_schedule_fired": "파이프라인 정기 실행 작동",
}

SUBJECT_LABELS: dict[str, str] = {
    "agent_pool": "Agent", "agent_execution_jobs": "Agent 실행 작업", "ai_budget_config": "AI 사용 한도",
    "api_keys": "API 키", "approval": "결재", "assignment": "배정", "automation_triggers": "이벤트 트리거",
    "auxiliary_llm_config": "보조 모델 설정", "briefs": "업무 요청", "calendar_feeds": "캘린더",
    "code_repositories": "코드 저장소", "config_check": "설정 점검", "conversation_jobs": "대화 작업",
    "conversation_messages": "대화 메시지", "conversation_participants": "대화 참여자", "conversations": "대화",
    "crew_run": "Crew 실행", "data_connections": "사내 DB 연결", "deliverable": "결과물", "deliverables": "결과물",
    "deliverable_comments": "결과물 댓글", "documents": "문서", "embedding_config": "임베딩 설정",
    "evaluation_config": "평가 기준", "human_pool": "사람", "inbound_triggers": "외부 입력 트리거", "job": "작업",
    "job_item": "작업 항목", "knowledge_chunks": "지식 조각", "knowledge_submissions": "지식 제출",
    "language_qa": "언어 검수", "mail_inboxes": "메일 수신함", "model_registry": "모델 레지스트리",
    "numeric_check": "숫자 점검", "org_policy": "조직 정책", "pipeline_run": "파이프라인 실행",
    "pipeline_runs": "파이프라인 실행", "pipeline_run_stage": "파이프라인 단계 실행",
    "pipeline_schedules": "정기 실행", "pipeline_stage": "파이프라인 단계", "pipeline_stages": "파이프라인 단계",
    "pipeline_template": "파이프라인", "platform_llm_config": "기본 모델 설정", "process_change": "프로세스 변경",
    "process_value_settings": "프로세스 비용 설정", "rag_search_config": "검색 설정",
    "resource_settings": "자원 설정", "skill_pool": "스킬", "stage_example": "좋은 결과 예시",
    "stage_lesson": "배운 점", "system": "시스템", "task": "할 일", "tasks": "할 일",
    "task_dependencies": "할 일 선행 관계", "task_participants": "할 일 참여자", "team": "팀", "tool_pool": "도구",
    "user": "사용자", "users": "사용자",
}


def unknown_words(code: str) -> list[str]:
    if code in ACTION_OVERRIDES:
        return []
    return [w for w in code.split("_") if w not in WORDS]


def action_label(code: str) -> str:
    """동작 코드 → 한국어 이름. 모르는 단어가 있으면 코드 그대로."""
    if code in ACTION_OVERRIDES:
        return ACTION_OVERRIDES[code]
    if unknown_words(code):
        return code
    return " ".join(w for w in (WORDS[p] for p in code.split("_")) if w)


def subject_label(subject_type: str) -> str:
    return SUBJECT_LABELS.get(subject_type, subject_type)

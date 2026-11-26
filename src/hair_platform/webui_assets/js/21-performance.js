/* ---------- 성과 리뷰 ---------- */

async function renderPerformance(content) {
  const rows = await api("GET", "/api/performance-review");
  content.innerHTML = '<section class="card"><h2>성과 리뷰</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>사람의 "성과점수"는 사전 동의가 있어야만 값이 채워집니다 — "사람 인력 관리" 탭에서 동의를 받은 사람만 점수를 입력할 수 있고, 동의를 철회하면 그 순간 점수가 지워집니다.</li>' +
      '<li>AI 에이전트는 동의 개념이 없어 항상 입력·조회 가능합니다.</li>' +
      '<li>이 화면의 점수는 둘 다 관리자가 직접 입력한 수동값입니다 — Agent의 실제 Gate A-G 자동 채점 결과는 이 화면이 아니라 "AI 에이전트 관리" 탭의 "자동평가(Gate/TCR)" 열에서 따로 봅니다(둘을 아직 한 화면에 병기하진 않습니다).</li>' +
      '<li>"확정 배정 수"만 실제 데이터(confirmed 배정 건수)입니다.</li>' +
      '<li>팀(Team) 행은 동의·성과점수 개념이 없어("-") 확정 배정 수만 보여줍니다 — 팀의 성과는 그 멤버 Agent/사람 행에서 개별로 확인하세요.</li>' +
      '</ul>'
    ) +
    table(["타입", "코드", "이름", "역할", "동의", "성과점수", "확정 배정 수"], rows.map(r => [
      esc(label(r.type)), esc(r.code || ("#" + r.id)), esc(r.name || r.role), esc(r.role),
      r.performance_consent === null ? "-" : (r.performance_consent ? "동의함" : "미동의"),
      r.performance_score === null ? "-" : r.performance_score,
      r.confirmed_assignments,
    ])) + "</section>";
}


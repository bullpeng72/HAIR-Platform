/* ---------- 활동 기록 ---------- */

async function renderAudit(content) {
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") — "처리자
  // ID" 필터도 같은 문제라 활성 계정 목록(select)으로 바꾼다. 비활성
  // 계정이 과거에 남긴 기록은 이 select로 못 고르지만(`/api/users/names`가
  // 활성 계정만 주므로), "전체"를 고르면 필터 없이 다 보이니 완전히
  // 막히진 않는다.
  const users = await api("GET", "/api/users/names").catch(() => []);
  content.innerHTML =
    '<section class="card"><h2>활동 기록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>이 로그는 DB 트리거로 수정·삭제 자체가 막혀 있는 append-only 기록입니다 — 다른 탭의 "삭제" 버튼으로 브리프/할 일/사람/에이전트/도구/스킬을 실제로 지워도, "무엇을 언제 누가 지웠는지"는 여기 그대로 남습니다.</li>' +
      '<li>Admin·매니저만 조회할 수 있습니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="audit-form">' +
    '<select id="a-actor"><option value="">처리자: 전체</option>' +
    users.map(u => '<option value="' + u.id + '">' + esc(u.username) + " (" + esc(label(u.role)) + ")</option>").join("") +
    '</select>' +
    '<input id="a-subject" placeholder="대상 종류(예: brief, assignment)">' +
    '<button class="primary" type="submit">조회</button></form>' +
    '<div id="audit-result"></div></section>';
  const load = async () => {
    const body = await api("GET", qs("/api/audit-log", {
      actor_id: document.getElementById("a-actor").value,
      subject_type: document.getElementById("a-subject").value,
      limit: 50,
    }));
    document.getElementById("audit-result").innerHTML =
      "<p class=\"muted\">전체 " + body.total + "건 중 " + body.items.length + "건 표시</p>" +
      // 사용자 요청(2026-10-27, "처리자 ID는 아이디로 대상 ID는 agent
      // code로 변경") — 처리자는 username, 대상은 agent_pool인 경우만
      // code(그 외 종류는 code 개념이 없어 id 그대로).
      table(["시각", "처리자", "동작", "대상 종류", "대상 ID"], body.items.map(a => [
        esc(fmtTime(a.created_at)), esc(a.actor_username || ("#" + a.actor_id)), esc(a.action), esc(label(a.subject_type)),
        a.subject_id === null ? "-" : esc(a.subject_code || a.subject_id),
      ]));
  };
  document.getElementById("audit-form").addEventListener("submit", (ev) => { ev.preventDefault(); load(); });
  await load();
}


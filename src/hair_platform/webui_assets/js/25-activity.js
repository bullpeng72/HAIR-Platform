/* ---------- 활동 기록 ---------- */

async function renderAudit(content) {
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") — "처리자
  // ID" 필터도 같은 문제라 활성 계정 목록(select)으로 바꾼다. 비활성
  // 계정이 과거에 남긴 기록은 이 select로 못 고르지만(`/api/users/names`가
  // 활성 계정만 주므로), "전체"를 고르면 필터 없이 다 보이니 완전히
  // 막히진 않는다.
  const users = await api("GET", "/api/users/names").catch(() => []);
  // 필터·이름·자세히·쪽 넘기기(2026-11-28) — 예전엔 동작이 내부 코드(approval_transferred)로 보이고, 대상 종류는
  // 내부 이름(brief, assignment)을 직접 쳐야 했고, 최근 50건만 보였다.
  const facets = await api("GET", "/api/audit-log/facets").catch(() => ({ subject_types: [], actions: [] }));
  const opt = (f) => '<option value="' + esc(f.value) + '">' + esc(f.label) + " (" + f.count + ")</option>";
  const PAGE = 50;
  let offset = 0;
  content.innerHTML =
    '<section class="card"><h2>활동 기록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>이 로그는 DB 트리거로 수정·삭제 자체가 막혀 있는 append-only 기록입니다 — 다른 탭의 "삭제" 버튼으로 브리프/할 일/사람/에이전트/도구/스킬을 실제로 지워도, "무엇을 언제 누가 지웠는지"는 여기 그대로 남습니다.</li>' +
      '<li>Admin·매니저만 조회할 수 있습니다. 1년이 지난 기록은 보존 기간 정책에 따라 정리됩니다.</li>' +
      '<li>"자세히"를 누르면 바뀌기 전·후 값을 볼 수 있습니다. 한 일 이름에 마우스를 올리면 내부 코드가 보입니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="audit-form">' +
    '<select id="a-actor" aria-label="처리자"><option value="">처리자: 전체</option>' +
    users.map(u => '<option value="' + u.id + '">' + esc(u.username) + " (" + esc(label(u.role)) + ")</option>").join("") +
    '</select>' +
    '<select id="a-subject" aria-label="대상 종류"><option value="">대상 종류: 전체</option>' +
    facets.subject_types.map(opt).join("") + '</select>' +
    '<select id="a-action" aria-label="한 일"><option value="">한 일: 전체</option>' +
    facets.actions.map(opt).join("") + '</select>' +
    '<label class="muted">기간 <input type="date" id="a-since" aria-label="시작일"> ~ <input type="date" id="a-until" aria-label="종료일"></label>' +
    '<button class="primary" type="submit">조회</button>' +
    ' <button type="button" class="small" id="a-export">CSV로 내보내기</button></form>' +
    '<div id="audit-result"></div></section>';
  const detail = (a) => {
    const fmt = (v) => {
      if (v === null || v === undefined || v === "") return "-";
      try { return JSON.stringify(JSON.parse(v), null, 2); } catch (_e) { return String(v); }
    };
    if (!a.before_value && !a.after_value) return '<span class="muted">-</span>';
    return '<details class="audit-detail"><summary>자세히</summary>' +
      '<div class="muted">전</div><pre>' + esc(fmt(a.before_value)) + '</pre>' +
      '<div class="muted">후</div><pre>' + esc(fmt(a.after_value)) + '</pre></details>';
  };
  // CSV 내보내기(2026-11-28) — 지금 고른 필터 그대로, 최신순 최대 5만 건. 내보낸 사실도 활동 기록에 남는다.
  const filters = () => ({
    actor_id: document.getElementById("a-actor").value,
    subject_type: document.getElementById("a-subject").value,
    action: document.getElementById("a-action").value,
    since: document.getElementById("a-since").value,
    until: document.getElementById("a-until").value,
  });
  document.getElementById("a-export").addEventListener("click", () => {
    window.location.href = qs("/api/audit-log/export.csv", filters());  // 첨부 파일 응답이라 화면은 그대로
  });
  const load = async () => {
    const box = document.getElementById("audit-result");
    let body;
    try {
      body = await api("GET", qs("/api/audit-log", { ...filters(), limit: PAGE, offset }));
    } catch (e) {
      box.innerHTML = '<p class="msg error">' + esc(e.message || "조회하지 못했습니다") + "</p>";
      return;
    }
    const from = body.total ? offset + 1 : 0;
    const to = offset + body.items.length;
    box.innerHTML =
      '<p class="muted">' + body.total.toLocaleString() + "건 중 " + from + "–" + to + "</p>" +
      // 사용자 요청(2026-10-27, "처리자 ID는 아이디로 대상 ID는 agent
      // code로 변경") — 처리자는 username, 대상은 agent_pool인 경우만
      // code(그 외 종류는 code 개념이 없어 id 그대로).
      table(["시각", "처리자", "한 일", "대상 종류", "대상 ID", "바뀐 내용"], body.items.map(a => [
        esc(fmtTime(a.created_at)), esc(a.actor_username || ("#" + a.actor_id)),
        '<span title="' + esc(a.action) + '">' + esc(a.action_label || a.action) + "</span>",
        esc(a.subject_label || label(a.subject_type)),
        a.subject_id === null ? "-" : esc(a.subject_code || a.subject_id),
        detail(a),
      ])) +
      '<div class="inline">' +
      '<button type="button" class="small" id="a-prev"' + (offset <= 0 ? " disabled" : "") + ">이전</button>" +
      '<button type="button" class="small" id="a-next"' + (to >= body.total ? " disabled" : "") + ">다음</button></div>";
    document.getElementById("a-prev").addEventListener("click", () => { offset = Math.max(0, offset - PAGE); load(); });
    document.getElementById("a-next").addEventListener("click", () => { offset += PAGE; load(); });
  };
  document.getElementById("audit-form").addEventListener("submit", (ev) => { ev.preventDefault(); offset = 0; load(); });
  await load();
}

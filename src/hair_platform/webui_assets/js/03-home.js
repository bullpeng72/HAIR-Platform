/* ---------- 홈(2026-11-23, P2-8) — 처리할 일 + AI에게 일을 맡기는 방법 + (admin) 관리 점검 ---------- */
async function renderHome(content) {
  const role = ME.role;
  const canManage = ["admin", "manager"].includes(role);
  const [inbox, cfg, notify, usage, worker] = await Promise.all([
    api("GET", "/api/inbox").catch(() => []),
    role === "admin" ? api("GET", "/api/admin/config-check").catch(() => null) : Promise.resolve(null),
    role === "admin" ? api("GET", "/api/integrations/notify").catch(() => null) : Promise.resolve(null),
    canManage ? api("GET", "/api/llm-usage?days=30").catch(() => null) : Promise.resolve(null),
    role === "admin" ? api("GET", "/api/admin/worker-status").catch(() => null) : Promise.resolve(null),  // P1-6 2단계
  ]);
  const count = type => inbox.filter(i => i.type === type).length;
  const todo = [
    ["approval_pending", "결재 대기", "approvals"],
    ["execution_failed", "AI 실행 실패", "inbox"],
    ["pipeline_failed", "파이프라인 실패·중단", "inbox"],
    ["low_gate_score", "낮은 Gate 점수", "inbox"],
    ["completed", "최근 완료", "inbox"],
  ].map(([type, name, tab]) =>
    '<button class="home-stat" data-go="' + tab + '"><b>' + count(type) + "</b><span>" + name + "</span></button>"
  ).join("");
  const ways = [
    { title: "빠르게 묻거나 초안 받기", when: "결재 없이 바로 답이 필요할 때 — 질문·요약·초안·코드 리뷰", how: "대화에서 그냥 묻거나 @Agent이름으로 부르기", tab: "conversations", go: "대화 열기", show: true },
    { title: "정식 업무로 맡기기", when: "결과를 결재받고 이력·Gate 점수를 남겨야 할 때", how: "업무 요청 → 할 일로 나누기 → 사람·Agent·팀 배정 → 결재(대화에서 「업무 요청으로 전환」도 가능)", tab: "briefs", go: "업무 요청 열기", show: role !== "viewer" },
    { title: "여러 단계를 자동으로", when: "보고서·제안서·사업 기획·소프트웨어 개발처럼 조사→작성→검증이 필요한 일", how: "업무 요청 뒤 할 일의 협업 워크스페이스에서 기본 제공 파이프라인 실행 — 계획 단계에서 한 번만 승인", tab: "mywork", go: "내 업무 열기", show: role !== "viewer" },
    { title: "반복 업무 자동화", when: "매주 실적 보고처럼 정해진 때마다 도는 일, 외부 시스템에서 호출", how: "연동 설정 — 정기 실행(admin/manager)·API 키·외부 알림(admin)", tab: "integrations", go: "연동 설정 열기", show: role !== "viewer" },
  ].filter(w => w.show).map(w =>
    '<div class="home-way"><h3>' + esc(w.title) + '</h3><p class="muted">' + esc(w.when) + "</p><p>" + esc(w.how) + "</p>" +
    '<button class="small" data-go="' + w.tab + '">' + esc(w.go) + "</button></div>"
  ).join("");
  const checks = [];
  if (cfg && cfg.length) checks.push('기본 구성과 다른 곳 ' + cfg.length + '건 — <button class="small" data-go="admin">사용자 관리에서 점검</button>');
  if (notify && !notify.webhook_url) checks.push('외부 알림 웹훅이 설정되지 않았습니다 — 결재 대기를 메신저로 받으려면 <button class="small" data-go="integrations">연동 설정</button>');
  (worker && worker.warnings || []).forEach(w => checks.push("⚠ " + esc(w)));  // 작업 프로세스(2026-11-26, P1-6 2단계)
  if (usage && usage.unpriced_models) checks.push("단가가 없는 모델 " + usage.unpriced_models + '개 — 실제 비용을 보려면 <button class="small" data-go="modelregistry">모델 레지스트리</button>에서 단가 입력');
  content.innerHTML =
    "<h2>홈</h2>" +
    // AI에게 일 맡기기 마법사(2026-11-24, P2-8) — 한 문장으로 적으면 방법을 골라 주고 바로 시작한다.
    (role !== "viewer"
      ? '<section class="card"><h3>AI에게 일 맡기기</h3>' +
        '<p class="muted">어떤 일인지 한두 문장으로 적으면, 빠른 질문·특정 Agent·여러 단계 파이프라인·정기 실행 중 맞는 방법을 이유와 함께 추천하고 바로 시작합니다.</p>' +
        // 2026-11-25(사용자 보고 — 입력창이 한쪽에 치우치고 버튼이 입력창에 붙어 어긋남): `.inline`이 flex가 아니라
        // `flex:1`이 안 먹었다. 입력창은 카드 너비 전체, 버튼은 그 아래 오른쪽에 따로 둔다(Ctrl/⌘+Enter로도 추천).
        '<div class="wizard-box"><textarea id="wizard-text" rows="3" placeholder="예: 3분기 부서 예산 집행 실적을 분석해 경영진 보고서를 써 줘 / 매주 월요일 지난주 실적 보고서 / 유지보수 계약서 손해배상 조항 검토"></textarea>' +
        '<div class="wizard-actions"><span class="muted">Ctrl(⌘)+Enter로도 추천받을 수 있습니다</span><button class="primary" id="wizard-go">추천 받기</button></div></div>' +
        '<div id="wizard-result"></div></section>'
      : "") +
    '<section class="card"><h3>처리할 일</h3><div class="home-stats">' + todo + "</div></section>" +
    '<section class="card"><h3>AI에게 일을 맡기는 방법</h3><div class="home-ways">' + ways + "</div>" +
    '<p class="muted">자세한 절차는 <button class="small" data-go="manual">이용 가이드</button>에 있습니다.</p></section>' +
    (role === "admin" ? '<section class="card"><h3>관리 점검</h3>' +
      (checks.length ? "<ul>" + checks.map(c => "<li>" + c + "</li>").join("") + "</ul>" : '<p class="muted">확인할 항목이 없습니다.</p>') +
      "</section>" : "");
  content.querySelectorAll("[data-go]").forEach(btn => btn.addEventListener("click", () => selectTab(btn.dataset.go)));
  const wizardBtn = document.getElementById("wizard-go");
  if (wizardBtn) wizardBtn.onclick = () => _runWizard(content);
  const wizardText = document.getElementById("wizard-text");
  if (wizardText && wizardBtn) wizardText.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); wizardBtn.click(); }
  });
}

// AI에게 일 맡기기(2026-11-24, P2-8) — 추천은 서버(`wizard.py`, LLM 없음), 실행은 기존 API를 이어 부른다
// (권한은 각 API가 그대로 확인). 대화 계열은 새 대화를 만들고(Agent면 초대까지) 입력창에 글을 채워 둔다 —
// 보내는 것은 사람이 확인하고 누른다.
async function _runWizard(content) {
  const text = document.getElementById("wizard-text").value.trim();
  const box = document.getElementById("wizard-result");
  if (!text) { showMsg("global-msg", "맡길 일을 먼저 적어 주세요", "error"); return; }
  let rec;
  try { rec = await api("POST", "/api/wizard/recommend", { text }); }
  catch (e) { showMsg("global-msg", e.message, "error"); return; }
  const icon = { chat: "💬", agent: "🤖", pipeline: "🔁", schedule: "⏰" };
  const actionLabel = r => r.kind === "chat" ? "대화 열기" : r.kind === "agent" ? "이 Agent와 대화 열기"
    : r.kind === "schedule" ? "정기 실행 설정으로" : (["admin", "manager"].includes(ME.role) ? "업무로 만들고 바로 실행" : "업무 요청으로 올리기");
  box.innerHTML = '<div class="home-ways" style="margin-top:0.6rem">' + rec.recommendations.map((r, i) =>
    '<div class="home-way"' + (r.recommended ? ' style="border-color:#2563eb"' : "") + ">" +
    "<h3>" + icon[r.kind] + " " + esc(r.title) + (r.recommended ? ' <span class="badge-adminonly" style="background:#2563eb">추천</span>' : "") + "</h3>" +
    "<p>" + esc(r.why) + "</p>" + (r.note ? '<p class="muted">' + esc(r.note) + "</p>" : "") +
    (r.allowed ? '<button class="small' + (r.recommended ? " primary" : "") + '" data-wizard-pick="' + i + '">' + actionLabel(r) + "</button>" : "") +
    "</div>"
  ).join("") + "</div>";
  box.querySelectorAll("[data-wizard-pick]").forEach(b => b.onclick = async () => {
    const r = rec.recommendations[Number(b.dataset.wizardPick)];
    b.disabled = true;
    try { await _startWizardPick(r, text); } catch (e) { showMsg("global-msg", e.message, "error"); b.disabled = false; }
  });
}

async function _startWizardPick(r, text) {
  if (r.kind === "chat" || r.kind === "agent") {
    const conv = await api("POST", "/api/conversations", { title: text.slice(0, 40) });
    if (r.kind === "agent") {
      await api("POST", "/api/conversations/" + conv.id + "/participants", { participant_type: "agent", participant_id: r.agent_id });
    }
    await selectTab("conversations");
    await renderConversationDetail(conv.id, "conversation-detail");
    const input = document.getElementById("conv-message-input");
    if (input) {
      input.value = (r.kind === "agent" ? "@" + r.role_name + " " : "") + text;
      input.focus();
    }
    showMsg("global-msg", "대화를 열고 메시지를 채워 두었습니다 — 확인 후 보내기를 누르세요.", "ok");
    return;
  }
  if (r.kind === "schedule") {
    await selectTab("integrations");
    const brief = document.getElementById("sch-brief");
    if (brief) { brief.value = text; brief.scrollIntoView({ block: "center" }); }
    showMsg("global-msg", "정기 실행 칸에 업무 내용을 채웠습니다 — 파이프라인·주기·시각을 고르고 추가하세요.", "ok");
    return;
  }
  // pipeline
  if (["admin", "manager"].includes(ME.role)) {
    const brief = await api("POST", "/api/briefs", { raw_text: text });
    const tasks = await api("POST", "/api/briefs/" + brief.id + "/tasks", { tasks: [{ role_name: r.template_name, required_skills: [] }] });
    const taskId = tasks.tasks[0].id;
    await api("POST", "/api/tasks/" + taskId + "/execute-pipeline", { pipeline_template_id: r.template_id });
    showMsg("global-msg", "업무 요청 #" + brief.id + " · 할 일 #" + taskId + "로 '" + r.template_name + "'을 시작했습니다 — 계획 단계 승인이 오면 결재에서 처리하세요.", "ok");
    await selectTab("mywork");
    return;
  }
  const brief = await api("POST", "/api/briefs", { raw_text: text + "\n\n[추천 실행 방식] " + r.template_name });
  showMsg("global-msg", "업무 요청 #" + brief.id + "를 올렸습니다(추천: " + r.template_name + ") — manager가 할 일로 나눠 실행합니다.", "ok");
  await selectTab("briefs");
}


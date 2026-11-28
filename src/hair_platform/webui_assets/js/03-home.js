/* ---------- 홈(2026-11-23, P2-8) — 처리할 일 + AI에게 일을 맡기는 방법 + (admin) 관리 점검 ---------- */
async function renderHome(content) {
  const role = ME.role;
  const canManage = ["admin", "manager"].includes(role);
  const [inbox, cfg, notify, usage, worker, health, myWork, briefs, started] = await Promise.all([
    api("GET", "/api/inbox").catch(() => []),
    role === "admin" ? api("GET", "/api/admin/config-check").catch(() => null) : Promise.resolve(null),
    role === "admin" ? api("GET", "/api/integrations/notify").catch(() => null) : Promise.resolve(null),
    canManage ? api("GET", "/api/llm-usage?days=30").catch(() => null) : Promise.resolve(null),
    role === "admin" ? api("GET", "/api/admin/worker-status").catch(() => null) : Promise.resolve(null),  // P1-6 2단계
    role === "admin" ? api("GET", "/api/admin/connection-status").catch(() => null) : Promise.resolve(null),  // 연결 상태(2026-11-26, 2-3)
    api("GET", "/api/my-work").catch(() => []),  // 오늘 내 일(2026-11-28)
    api("GET", "/api/briefs").catch(() => []),
    role === "member" ? api("GET", "/api/me/getting-started").catch(() => null) : Promise.resolve(null),  // 첫날 점검표는 구성원에게
  ]);
  const count = type => inbox.filter(i => i.type === type).length;
  const todo = [
    ["approval_pending", "결재 대기", "approvals"],
    ["execution_failed", "AI 실행 실패", "inbox"],
    ["pipeline_failed", "파이프라인 실패·중단", "inbox"],
    ["low_gate_score", "품질 점검 미달", "inbox"],
    ["completed", "최근 완료", "inbox"],
  ].map(([type, name, tab]) =>
    '<button class="home-stat" data-go="' + tab + '"><b>' + count(type) + "</b><span>" + name + "</span></button>"
  ).join("");
  const today = _homeTodayBlock(myWork, briefs, count("approval_pending"));
  const ways = [
    { title: "빠르게 묻거나 초안 받기", when: "결재 없이 바로 답이 필요할 때 — 질문·요약·초안·코드 리뷰", how: "대화에서 그냥 묻거나 @Agent이름으로 부르기 — 어떤 Agent가 있는지는 아래 \"누구에게 맡길까?\"", tab: "conversations", go: "대화 열기", show: true, dir: true },
    { title: "정식 업무로 맡기기", when: "결과를 결재받고 이력·품질 점검 점수를 남겨야 할 때", how: "위 입력창에 적고 [추천 받기] — 정식 업무가 맞으면 업무 요청으로 등록돼 할 일·배정·결재로 이어집니다(대화에서 「업무 요청으로 전환」도 가능)", tab: "briefs", go: "진행 중인 업무 요청 보기", show: role !== "viewer" },
    { title: "여러 단계를 자동으로", when: "보고서·제안서·사업 기획·소프트웨어 개발처럼 조사→작성→검증이 필요한 일", how: "업무 요청 뒤 할 일의 협업 워크스페이스에서 기본 제공 파이프라인 실행 — 계획 단계에서 한 번만 승인", tab: "mywork", go: "내 업무 열기", show: role !== "viewer" },
    { title: "반복 업무 자동화", when: "매주 실적 보고처럼 정해진 때마다 도는 일, 외부 시스템에서 호출", how: "연동 설정 — 정기 실행(admin/manager)·API 키·외부 알림(admin)", tab: "integrations", go: "연동 설정 열기", show: canManage },  // 정기 실행·외부 알림은 admin·manager 기능(2026-11-28)
  ].filter(w => w.show).map(w =>
    '<div class="home-way"><h3>' + esc(w.title) + '</h3><p class="muted">' + esc(w.when) + "</p><p>" + esc(w.how) + "</p>" +
    '<button class="small" data-go="' + w.tab + '">' + esc(w.go) + "</button>" +
    (w.dir ? ' <button class="small" data-agent-dir>🤖 누구에게 맡길까?</button>' : "") + "</div>"
  ).join("");
  const checks = [];
  if (cfg && cfg.length) checks.push('기본 구성과 다른 곳 ' + cfg.length + '건 — <button class="small" data-go="admin">사용자 관리에서 점검</button>');
  if (notify && !notify.webhook_url) checks.push('외부 알림 웹훅이 설정되지 않았습니다 — 결재 대기를 메신저로 받으려면 <button class="small" data-go="integrations">연동 설정</button>');
  (worker && worker.warnings || []).forEach(w => checks.push("⚠ " + esc(w)));
  (health && health.warnings || []).forEach(w => checks.push((w.startsWith("아직") ? "" : "⚠ ") + esc(w) + ' <button class="small" data-go="integrations">연동 설정</button>'));  // 작업 프로세스(2026-11-26, P1-6 2단계)
  if (usage && usage.unpriced_models) checks.push("단가가 없는 모델 " + usage.unpriced_models + '개 — 실제 비용을 보려면 <button class="small" data-go="modelregistry">모델 레지스트리</button>에서 단가 입력');
  // 할 일·결재가 있으면 "오늘 내 일"을 맨 위에, 없으면 "AI에게 일 맡기기" 다음에(2026-11-28)
  const hasToday = (myWork || []).some(w => w.task_status !== "done") || count("approval_pending") > 0;
  const firstDay = _homeFirstDayBlock(started);
  content.innerHTML =
    // 맡은 일이 있으면 "오늘 내 일"이 먼저, 첫날 점검표는 그 다음(2026-11-28)
    "<h2>홈</h2>" + (hasToday ? today : "") + firstDay +
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
    (hasToday ? "" : today) +
    // AI 처리 현황(예전 "처리할 일") — 운영 지표라 admin·manager에게, 구성원에게는 실패가 있을 때만(2026-11-28).
    (canManage || count("execution_failed") + count("pipeline_failed") + count("low_gate_score")
      ? '<section class="card"><h3>' + (canManage ? "AI 처리 현황" : "확인이 필요한 AI 작업") + '</h3><div class="home-stats">' + todo + "</div></section>"
      : "") +
    '<section class="card"><h3>AI에게 일을 맡기는 방법</h3><div class="home-ways">' + ways + "</div>" +
    '<div id="home-agent-dir"></div>' +
    '<p class="muted">자세한 절차는 <button class="small" data-go="manual">이용 가이드</button>에 있습니다.</p></section>' +
    (role === "admin" ? '<section class="card"><h3>관리 점검</h3>' +
      (checks.length ? "<ul>" + checks.map(c => "<li>" + c + "</li>").join("") + "</ul>" : '<p class="muted">확인할 항목이 없습니다.</p>') +
      "</section>" : "");
  content.querySelectorAll("[data-go]").forEach(btn => btn.addEventListener("click", () => selectTab(btn.dataset.go)));
  content.querySelectorAll("[data-try-prompt]").forEach(b => b.addEventListener("click", () => {
    const box = document.getElementById("wizard-text");
    if (!box) return;
    box.value = b.dataset.tryPrompt;
    box.focus();
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  const fdClose = content.querySelector("[data-firstday-close]");
  if (fdClose) fdClose.onclick = () => { _lsSet("hair.firstday.hidden", "1"); fdClose.closest("section").remove(); };
  content.querySelectorAll("[data-agent-dir]").forEach(btn => btn.addEventListener("click", () => {
    const slot = document.getElementById("home-agent-dir");
    if (slot.innerHTML) { slot.innerHTML = ""; return; }
    _renderAgentDirectory(slot);
  }));
  content.querySelectorAll("[data-open-task]").forEach(btn => btn.addEventListener("click", async () => {
    await selectTab("mywork");
    renderCollabWorkspace(Number(btn.dataset.openTask));
  }));
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
  box.innerHTML = (rec.tip ? '<p class="wizard-tip">💡 ' + esc(rec.tip) + "</p>" : "") +
    '<div class="home-ways" style="margin-top:0.6rem">' + rec.recommendations.map((r, i) =>
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
  renderTaskList(brief.id);  // 방금 올린 업무의 진행 단계를 바로 보인다(2026-11-27, 사용성 평가 8번)
}



/* 오늘 내 일(2026-11-28, 일반 사용자·신입 관점 Phase A-3) — 홈 첫 화면이 AI 운영 지표(실패·품질 점검)만 보여 "오늘 내가 할 일"이
   없었다. 내게 배정된 끝나지 않은 일(기한 지남 → 가까운 기한 순), 내 결재 대기, 내가 낸 업무 요청의 진행을 모은다. */
function _homeTodayBlock(myWork, briefs, approvalsWaiting) {
  const todayStr = new Date().toISOString().slice(0, 10);
  const open = (myWork || []).filter(w => w.task_status !== "done");
  const seen = new Set();
  const tasks = open.filter(w => !seen.has(w.task_id) && seen.add(w.task_id)).sort((a, b) =>
    (a.due_date || "9999") < (b.due_date || "9999") ? -1 : (a.due_date || "9999") > (b.due_date || "9999") ? 1 : a.task_id - b.task_id);
  const overdue = tasks.filter(w => w.due_date && w.due_date < todayStr).length;
  const taskRows = tasks.slice(0, 6).map(w => "<li>" + esc(w.role_name) + " " + _dueCell(w.due_date, w.estimate_hours).replace(/<div[^>]*>.*<\/div>/, "") +
    ' <button class="small" data-open-task="' + w.task_id + '">열기</button></li>').join("");
  const mine = (briefs || []).filter(b => b.submitted_by === ME.id).slice(0, 4);
  const briefRows = mine.map(b => "<li>" + esc((b.raw_text || "").slice(0, 40)) + ((b.raw_text || "").length > 40 ? "…" : "") +
    ' <span class="muted">— ' + esc(label(b.status)) + "</span></li>").join("");
  const parts = [];
  parts.push("<h4>내가 맡은 일 " + tasks.length + "건" + (overdue ? ' <span class="gate-badge gate-warn">기한 지남 ' + overdue + "</span>" : "") + "</h4>" +
    (taskRows ? '<ul class="home-today-list">' + taskRows + "</ul>" + (tasks.length > 6 ? '<button class="small" data-go="mywork">전부 보기</button>' : "")
      : '<p class="muted">지금 맡은 일이 없습니다. 배정되면 기한과 함께 여기에 보입니다.</p>'));
  if (approvalsWaiting) parts.push('<h4>내 결재를 기다리는 건 ' + approvalsWaiting + '건</h4><button class="small primary" data-go="approvals">결재하러 가기</button>');
  if (mine.length) parts.push("<h4>내가 낸 업무 요청</h4><ul class=\"home-today-list\">" + briefRows + '</ul><button class="small" data-go="briefs">업무 요청 보기</button>');
  return '<section class="card home-today"><h3>오늘 내 일</h3>' + parts.join("") + "</section>";
}


/* 누구에게 맡길까(2026-11-28, 신입·일반 사용자 관점 Phase B-2) — 대화에서 @이름으로 부르려면 어떤 Agent가 무엇을 하는지
   알아야 하는데 Agent 목록은 admin·manager 전용이었다. 이름·하는 일·스킬로 찾고 바로 대화를 연다. 홈·대화 탭이 함께 쓴다. */
async function _renderAgentDirectory(slot) {
  slot.innerHTML = '<p class="muted">불러오는 중…</p>';
  let list;
  try { list = await api("GET", "/api/agent-directory"); } catch (e) { slot.innerHTML = '<p class="msg error">' + esc(e.message || "") + "</p>"; return; }
  slot.innerHTML = '<div class="card agent-dir"><div class="ab-head"><b>누구에게 맡길까? — Agent ' + list.filter(a => !a.dev).length + "명</b>" +
    ' <button class="small" data-agent-dir-close>닫기</button></div>' +
    '<input type="search" class="agent-dir-q" placeholder="하고 싶은 일이나 낱말로 찾기 — 예: 요약, 메일, 계약, 엑셀" aria-label="Agent 찾기">' +
    (list.some(a => a.dev) ? '<label class="muted"><input type="checkbox" class="agent-dir-dev"> 개발 Agent도 보기(' + list.filter(a => a.dev).length + ")</label>" : "") +
    '<div class="agent-dir-list"></div></div>';
  const listEl = slot.querySelector(".agent-dir-list");
  const draw = (q) => {
    const k = (q || "").trim().toLowerCase();
    const showDev = !!(slot.querySelector(".agent-dir-dev") || {}).checked;
    const rows = list.filter(a => (showDev || !a.dev) && (!k || (a.role_name + " " + a.does + " " + a.skills.join(" ")).toLowerCase().includes(k)));
    listEl.innerHTML = rows.length ? rows.map(a =>
      '<div class="agent-dir-item"><div><b>@' + esc(a.role_name) + "</b>" + (a.does ? '<div class="muted">' + esc(a.does) + "</div>" : "") +
      (a.skills.length ? '<div class="agent-dir-skills">' + a.skills.map(s => '<span class="chip">' + esc(s) + "</span>").join("") + "</div>" : "") +
      '</div><button class="small" data-agent-dir-open="' + a.id + '">대화 열기</button></div>').join("")
      : '<p class="muted">맞는 Agent가 없습니다 — 다른 낱말로 찾거나, 대화의 기본 AI에 그냥 물어보세요.</p>';
    listEl.querySelectorAll("[data-agent-dir-open]").forEach(b => b.onclick = async () => {
      const a = list.find(x => x.id === Number(b.dataset.agentDirOpen));
      b.disabled = true;
      try { await _startWizardPick({ kind: "agent", agent_id: a.id, role_name: a.role_name }, ""); }
      catch (e) { showMsg("global-msg", e.message, "error"); b.disabled = false; }
    });
  };
  draw("");
  slot.querySelector(".agent-dir-q").addEventListener("input", (ev) => draw(ev.target.value));
  const devBox = slot.querySelector(".agent-dir-dev");
  if (devBox) devBox.addEventListener("change", () => draw(slot.querySelector(".agent-dir-q").value));
  slot.querySelector("[data-agent-dir-close]").onclick = () => { slot.innerHTML = ""; };
}


/* 처음 써 보기(2026-11-28, 신입·일반 사용자 관점 Phase D-8) — 첫 화면에서 무엇부터 해 볼지 세 가지와 바로 넣어 볼 예시.
   1·2는 서버 기록으로, 3(Word로 받기·서식 복사)은 이 브라우저에 기억한다. 다 하거나 "그만 보기"면 숨긴다. */
function _lsGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* 저장 안 되는 브라우저 — 무시 */ } }

function _homeFirstDayBlock(started) {
  if (!started || _lsGet("hair.firstday.hidden")) return "";
  const saved = !!_lsGet("hair.firstday.saved");
  if (started.asked && started.delegated && saved) return "";
  const examples = ["이번 주 회의록을 정리해서 팀에 보낼 메일 초안을 써 줘", "엑셀에서 두 표를 사번으로 합치는 방법 알려 줘",
    "출장비 규정이 어떻게 돼?", "거래처 견적서 비교표를 만들어 줘"];
  const step = (done, title, how) => "<li" + (done ? ' class="done"' : "") + ">" + (done ? "✅ " : "⬜ ") + "<b>" + title + "</b> — " + how + "</li>";
  return '<section class="card home-firstday"><div class="ab-head"><h3>처음이라면 — 세 가지만 해 보세요</h3>' +
    ' <button class="small" data-firstday-close>그만 보기</button></div><ol>' +
    step(started.asked, "AI에게 물어보기", '대화를 열고 궁금한 것을 그냥 적어 보세요. <button class="small" data-go="conversations">대화 열기</button>') +
    step(started.delegated, "일 맡겨 보기", "아래 \"AI에게 일 맡기기\"에 할 일을 적고 [추천 받기] — 맞는 Agent·방법을 골라 줍니다.") +
    step(saved, "결과를 내 문서로", "AI 답변 옆 <b>Word로 받기</b>나 <b>복사</b>(서식째)로 메일·보고서에 바로 쓰세요.") +
    '</ol><p class="muted">예시를 누르면 입력창에 채워집니다: ' +
    examples.map(e => '<button class="small" data-try-prompt="' + escAttr(e) + '">' + esc(e) + "</button>").join(" ") + "</p></section>";
}

/* ---------- AI 에이전트 ---------- */

// 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
// 하는 기능을 구현해줘" → 명확화: 실제 LLM 가중치 재학습이 아니라 CrewAI
// Agent/LLM 동작 파라미터 조정) — 비워두면 CrewAI 기본값을 그대로 쓴다.
// 숫자 입력은 빈 칸이 "기본값 사용"이고, bool류는 체크박스 대신
// "기본값/켜기/끄기" 3단 select를 쓴다 — 체크박스로 하면 "체크 안 함"이
// "명시적으로 false로 저장"과 구분이 안 돼, CrewAI 기본값이 true인
// respect_context_window 같은 값을 의도치 않게 false로 덮어쓸 위험이
// 있다(실제로 설계 중 발견한 함정이라 기록해둔다).
// 추천 후보 한 줄(2026-11-27 — 성능 테스트 결과 반영). 이 Agent에 쓸 수 없는 모델(도구 호출 불가 등)은 고를 수 없게,
// 점검에서 문제가 나온 모델은 ⚠, 단계 1회 예상 시간을 함께 보인다. 두 추천 화면(Agent 설정·LLM 설정 최적화)이 같이 쓴다.
function _optimizeCandidateOption(c, i, selected) {
  let label = (c.tier_mapped ? "★ " : "") + c.provider + " / " + c.model;
  if (typeof c.estimated_stage_s === "number") label += " · 단계 " + Math.round(c.estimated_stage_s) + "초";
  if (c.excluded) label = "✗ " + label + " — " + c.excluded_reason;
  else if (c.issues && c.issues.length) label += " ⚠ " + c.issues.join(", ");
  else if (c.checked) label += " ✓ 점검 통과";
  return '<option value="' + i + '"' + (selected && !c.excluded ? " selected" : "") + (c.excluded ? " disabled" : "") +
    ' title="' + escAttr(c.excluded ? c.excluded_reason : (c.issues || []).join(", ")) + '">' + esc(label) + "</option>";
}

function _tuneTriStateHtml(id, current) {
  const value = current === true ? "true" : current === false ? "false" : "";
  return '<select id="' + id + '">' +
    '<option value=""' + (value === "" ? " selected" : "") + '>기본값 사용</option>' +
    '<option value="true"' + (value === "true" ? " selected" : "") + '>켜기</option>' +
    '<option value="false"' + (value === "false" ? " selected" : "") + '>끄기</option>' +
    '</select>';
}
// 사용자 요청(2026-10-18, "Agent의 목표, 기능, 입출력 등을 고려해 가용한
// LLM 모델에서 종류 및 설정 등을 최적화 setting 하는 admin 기능") — 규칙
// 기반 추천(비용 없음, `GET .../optimize-recommendation`)과 선택적 LLM
// 보정(비용 발생, `POST .../optimize-recommendation/refine`)의 결과를
// 같은 모양으로 렌더링한다. 전역 위임 클릭 리스너(아래, "최적화 추천"/
// "AI 의견 보기"/"추천값 입력란에 채우기" 버튼)와 `renderAgentOptimization()`
// 둘 다 재사용한다.
const _OPTIMIZE_TIER_LABELS = {
  deep_reasoning: "심층추론(기획/분석/설계)",
  precision_fast: "정밀·신속(구현/리뷰/QA)",
  standard: "표준(작성/조정)",
};
function _renderOptimizeRecommendation(agentId, rec, source) {
  const target = (source === "llm" && rec.llm_refined) ? rec.llm_refined : rec.recommended;
  const tuningText = Object.keys(target.tuning_config || {}).length
    ? Object.entries(target.tuning_config).map(([k, v]) => k + "=" + v).join(", ")
    : "(변경 없음)";
  let html = '<div class="msg ' + (rec.changed ? "warn" : "ok") + '" style="margin-top:0.5rem">' +
    '<div><span class="gate-badge">' + esc(_OPTIMIZE_TIER_LABELS[rec.tier] || rec.tier) + "</span> " +
    '<small class="muted">' + esc(rec.classification_source) + "</small></div>" +
    "<p>" + esc(rec.rationale) + "</p>" +
    "<p><b>추천:</b> " + esc(target.provider || "(현재 유지)") + " / " + esc(target.model || "(현재 유지)") +
    "<br><small>" + esc(tuningText) + "</small></p>";
  if (source === "llm" && rec.llm_rationale) {
    html += '<p class="muted"><b>AI 보정 근거:</b> ' + esc(rec.llm_rationale) + "</p>";
  }
  if (source === "llm" && rec.source === "rule_based_fallback") {
    html += '<p class="muted">⚠ AI 보정 실패 — 규칙 기반 추천을 그대로 유지합니다(' +
      esc(rec.llm_error || "") + ")</p>";
  }
  // 사용자 요청(2026-10-26, "모델 추천의 경우 모델 레지스트리에 등록된
  // 모델을 선택할 수 있도록 해줘") — 1위 추천만 고정해 채우지 않고,
  // `agent_optimizer.recommend_model()`이 함께 돌려준 "등록된 활성
  // 모델 전체(이 tier 기준 순위)" 후보 목록을 셀렉트로 보여줘 admin이
  // 2위/3위 등 다른 레지스트리 모델을 직접 골라 채울 수 있게 한다.
  const candidates = (rec.recommended && rec.recommended.candidates) || [];
  const pickSelId = "optimize-model-pick-" + agentId;
  if (candidates.length) {
    html += '<div style="margin-top:0.3rem"><label>등록된 모델 중 선택: ' +
      '<select id="' + pickSelId + '" data-candidates="' + escAttr(JSON.stringify(candidates)) + '">' +
      candidates.map((c, i) => _optimizeCandidateOption(c, i, c.provider === target.provider && c.model === target.model)).join("") +
      '</select> <span class="muted">(★ = 관리자가 이 tier에 매핑 · ✗ = 이 Agent에 쓸 수 없음 · ⚠ = 성능 테스트에서 문제)</span></label></div>' +
      (rec.needs && rec.needs.tools && rec.needs.tools.length
        ? '<p class="muted">이 Agent가 쓰는 도구: ' + esc(rec.needs.tools.join(", ")) + " — 도구 호출을 못 하는 모델은 고를 수 없습니다.</p>" : "") +
      (rec.current_fitness && rec.current_fitness.excluded
        ? '<p class="gate-warn">⚠ 현재 모델은 이 Agent에 쓸 수 없습니다: ' + esc(rec.current_fitness.excluded_reason) + "</p>" : "");
  }
  html += '<button class="small" data-fill-optimize="' + agentId + '" data-optimize-payload="' +
    escAttr(JSON.stringify(target)) + '" data-optimize-pick-sel="' +
    (candidates.length ? pickSelId : "") + '">추천값 입력란에 채우기</button> ';
  if (source !== "llm") {
    html += '<button class="small" data-refine-optimize="' + agentId + '">AI 의견 보기(비용 발생)</button>';
  }
  html += "</div>";
  return html;
}
// ---------- 에이전트 설정 창(2026-11-21) ----------
// 한 줄 = 이름(+기술명) | 입력 | 설명. 입력란 id는 그대로라 저장 핸들러는 바뀌지 않는다.
function _asRow(label, code, controlHtml, hint) {
  return '<div class="as-row"><div class="as-label">' + label + (code ? '<code>' + code + '</code>' : '') + '</div>' +
    '<div class="as-ctl">' + controlHtml + '</div>' +
    (hint ? '<div class="as-hint">' + hint + '</div>' : '') + '</div>';
}
function _asSection(title, scopeBadges, intro, body) {
  return '<section class="as-section"><h4>' + title + (scopeBadges || "") + '</h4>' +
    (intro ? '<p class="muted">' + intro + '</p>' : '') + body + '</section>';
}
const _AS_SCOPE_ALL = '<span class="as-scope" title="대화 @멘션·업무 실행·실행 버튼(직접 호출)과 Crew 실행 모두">모든 실행</span>';
const _AS_SCOPE_CREW = '<span class="as-scope" title="팀 Agent 실행·파이프라인·Crew 실행">Crew 실행 전용</span>';
const _AS_SCOPE_MENTION = '<span class="as-scope" title="대화에서 @멘션했을 때">대화 @멘션</span>';

function tuningFormHtml(a) {
  const t = a.tuning_config || {};
  const num = (id, value, placeholder, extra) =>
    '<input type="number" id="' + id + '" value="' + (value === undefined || value === null ? "" : value) +
    '" placeholder="' + placeholder + '" ' + (extra || "") + '>';
  const common =
    _asRow("창의성(온도)", "temperature",
      num("tune-temp-" + a.id, t.temperature, "모델 기본값", 'step="0.1" min="0" max="2"'),
      "낮을수록 일관된 답, 높을수록 다양한 답. Anthropic은 최대 1까지만 받아 1을 넘으면 1로 맞춰 보냅니다.") +
    _asRow("실행 시간 제한(초)", "max_execution_time",
      num("tune-maxtime-" + a.id, t.max_execution_time, "기본값", 'min="1"'),
      "한 번 실행에 쓸 수 있는 최대 시간입니다. 직접 호출에서는 LLM 응답 대기 한도로 쓰입니다.") +
    _asRow("최대 반복 횟수", "max_iter",
      num("tune-maxiter-" + a.id, t.max_iter, "기본값", 'min="1"'),
      "직접 호출에서는 도구 호출을 주고받는 최대 횟수, Crew 실행에서는 생각·행동 반복 상한입니다.") +
    _asRow("추론 모드", "reasoning",
      _tuneTriStateHtml("tune-reasoning-" + a.id, t.reasoning ?? null),
      "켜면 Crew 실행에서 먼저 계획을 세우고, 직접 호출에서는 시간 제한을 넉넉하게 늘립니다(실행 시간 제한을 따로 정하면 그 값이 우선).") +
    _asRow("품질 검토(자체 개선)", "self_review",
      _tuneTriStateHtml("tune-selfreview-" + a.id, t.self_review ?? null),
      "켜면 대화 @멘션·업무 실행에서 초안을 스킬 기준과 품질 기준으로 한 번 더 점검해 고친 최종본을 냅니다. LLM 호출이 한 번 더 들어가 응답이 그만큼 늦어집니다(Crew 실행에는 적용되지 않음). 보고서·제안서처럼 품질이 중요한 Agent에 권장합니다.");
  const crewOnly =
    _asRow("분당 최대 호출 수", "max_rpm", num("tune-maxrpm-" + a.id, t.max_rpm, "무제한", 'min="1"'), "") +
    _asRow("재시도 횟수", "max_retry_limit", num("tune-maxretry-" + a.id, t.max_retry_limit, "기본값", 'min="0"'), "오류가 났을 때 다시 시도하는 횟수입니다.") +
    _asRow("추론 시도 횟수", "max_reasoning_attempts", num("tune-maxreasonattempts-" + a.id, t.max_reasoning_attempts, "무제한", 'min="1"'), "추론 모드를 켰을 때만 의미가 있습니다.") +
    _asRow("코드 실행 허용", "allow_code_execution", _tuneTriStateHtml("tune-codeexec-" + a.id, t.allow_code_execution ?? null), "") +
    _asRow("컨텍스트 자동 축소", "respect_context_window", _tuneTriStateHtml("tune-respectctx-" + a.id, t.respect_context_window ?? null),
      "실행 도중 모델 한도를 넘으면 앞부분을 요약해 계속합니다. 보내기 전에 자르는 기준은 모델 레지스트리의 ‘컨텍스트 한도’입니다.");
  return _asSection("파인튜닝", "",
    "LLM 가중치 재학습이 아니라 실행 동작 설정입니다. 비워 두면 기본값을 씁니다. 같은 항목을 모델 레지스트리에서도 정했다면 여기(에이전트) 값이 우선합니다.",
    '<div class="as-sub">공통 설정' + _AS_SCOPE_ALL + '</div>' + common +
    '<div class="as-sub">Crew 실행 설정' + _AS_SCOPE_CREW + '</div>' + crewOnly +
    '<div class="as-save"><button class="small" data-set-tuning="' + a.id + '">파인튜닝 저장</button></div>');
}

// 역할/인격(2026-10-20 도입, 2026-11-21부터 직접 호출에도 적용) — 비워두면
// 역할명·스킬로 자동 생성한 값(placeholder로 보여줌)을 쓴다.
function personaFormHtml(a) {
  const field = (id, label, code, stored, effective, hint) =>
    _asRow(label, code,
      '<textarea id="' + id + '" rows="3" placeholder="비워두면 자동 생성: ' + escAttr(effective || "") + '">' + esc(stored || "") + '</textarea>', hint);
  return _asSection("역할/인격", _AS_SCOPE_ALL,
    "직접 적은 값은 대화 @멘션·업무 실행에서는 시스템 프롬프트에, Crew 실행에서는 Agent·Task 정의에 들어갑니다. 빈 칸으로 저장하면 그 항목만 자동 생성으로 돌아갑니다.",
    field("persona-goal-" + a.id, "목표", "goal", a.goal, a.effective_goal, "") +
    field("persona-backstory-" + a.id, "배경", "backstory", a.backstory, a.effective_backstory, "") +
    field("persona-expected-" + a.id, "기대 산출물", "expected_output", a.expected_output, a.effective_expected_output, "결과물의 형식·분량 지시(예: ‘HTML 발표자료로 작성’).") +
    '<div class="as-save"><button class="small" data-set-persona="' + a.id + '">역할/인격 저장</button></div>');
}

// 대화 컨텍스트 정책(2026-11-20) — 이 Agent를 대화에서 `@멘션`했을 때의
// 이전 대화/첨부 사용. "스킬 기본"이면 Agent 스킬들 중 가장 포괄적인 값.
function contextPolicyFormHtml(a) {
  const eff = a.effective_context_policy || { history: "full", attachments: true };
  const historyOptions = [["", "스킬 기본"], ["full", "전체(최근 10건+요약)"], ["recent", "최근 1~2턴"], ["none", "사용 안 함"]]
    .map(([v, t]) => '<option value="' + v + '"' + ((a.history_mode || "") === v ? " selected" : "") + '>' + t + '</option>').join("");
  const attachKey = a.use_attachments === null || a.use_attachments === undefined ? "" : (a.use_attachments ? "1" : "0");
  const attachOptions = [["", "스킬 기본"], ["1", "사용"], ["0", "사용 안 함"]]
    .map(([v, t]) => '<option value="' + v + '"' + (attachKey === v ? " selected" : "") + '>' + t + '</option>').join("");
  return _asSection("대화 컨텍스트", _AS_SCOPE_MENTION,
    '현재 적용: 이전 대화 <b>' + esc(_HISTORY_MODE_LABEL[eff.history]) + '</b> · 첨부 <b>' + (eff.attachments ? "사용" : "사용 안 함") + '</b>. ' +
    '"스킬 기본"이면 이 Agent 스킬들 중 맥락이 가장 많이 필요한 쪽을 따릅니다. 대화에서 메시지마다 "이전 대화" 칩으로 바꿀 수도 있습니다(그 값이 가장 우선).',
    _asRow("이전 대화", "", '<select id="ctx-history-' + a.id + '">' + historyOptions + '</select>', "") +
    _asRow("첨부 자료", "", '<select id="ctx-attach-' + a.id + '">' + attachOptions + '</select>', "") +
    '<div class="as-save"><button class="small" data-set-context-policy="' + a.id + '">대화 컨텍스트 저장</button></div>');
}

// 설정 창 열기 — 행 안에 숨겨 렌더링한 설정 본문(#agent-settings-<id>)을 창으로
// 옮긴다(버튼에 걸린 저장 핸들러가 그대로 따라온다). 저장 후 목록이 다시 그려지면
// `_reattachAgentSettings()`가 새 본문으로 바꿔 끼워 창과 입력값을 유지한다.
let _agentSettingsOpenId = null;
function openAgentSettings(agentId, title) {
  closeAgentSettings();
  const body = document.getElementById("agent-settings-" + agentId);
  if (!body) return;
  _agentSettingsOpenId = String(agentId);
  const overlay = document.createElement("div");
  overlay.className = "confirm-modal-overlay";
  overlay.id = "agent-settings-overlay";
  overlay.innerHTML =
    '<div class="confirm-modal-box as-box" role="dialog" aria-modal="true">' +
    '<div class="as-head"><h3></h3><button type="button" class="small" data-as-close>닫기</button></div>' +
    '<div class="as-msg" id="agent-settings-msg"></div>' +
    '<div class="as-body"></div></div>';
  overlay.querySelector("h3").textContent = "⚙ " + title + " 설정";
  overlay.querySelector(".as-body").appendChild(body);
  body.hidden = false;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay || ev.target.closest("[data-as-close]")) closeAgentSettings();
  });
  document.body.appendChild(overlay);
}
function closeAgentSettings() {
  const overlay = document.getElementById("agent-settings-overlay");
  if (overlay) overlay.remove();
  _agentSettingsOpenId = null;
}
function _reattachAgentSettings() {
  const overlay = document.getElementById("agent-settings-overlay");
  if (!overlay) return;
  const fresh = document.getElementById("agent-settings-" + _agentSettingsOpenId);
  const holder = overlay.querySelector(".as-body");
  if (!fresh || fresh.closest("#agent-settings-overlay")) { if (!fresh) closeAgentSettings(); return; }
  const scroll = holder.scrollTop;
  holder.innerHTML = "";
  holder.appendChild(fresh);
  fresh.hidden = false;
  holder.scrollTop = scroll;
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && document.getElementById("agent-settings-overlay")) closeAgentSettings();
});

async function renderAgentPool(content) {
  const agents = await api("GET", "/api/agent-pool");
  const isAdmin = ME.role === "admin";
  const canExecute = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = canExecute ? await fetchModelsByProvider() : {};
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") — 같은
  // 패턴: "Tool Pool ID" 숫자 입력도 "도구/스킬 Pool" 탭을 따로 열어
  // ID를 외워와야 했다. 도구 목록을 같이 받아 select로 바꾼다.
  const tools = canExecute ? await api("GET", "/api/tool-pool") : [];
  const toolNameById = {};
  tools.forEach(t => { toolNameById[t.id] = t.name; });
  content.innerHTML = '<section class="card"><h2>AI 에이전트 목록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"생성방식" 열은 "AI 에이전트 생성" 탭에서 어떤 옵션(stub/simple_llm/crewai)으로 태어났는지를 보여줍니다 — crewai로 만든 Agent만 그 시점에 스킬 매칭 도구를 갖고 태어납니다.</li>' +
    '<li>신규 Agent는 전부 pending_onboarding 상태로 생기고, <b>온보딩 승인</b>을 눌러야 active가 돼 "인력 검색"·매칭 후보로 노출됩니다.</li>' +
    '<li><b>오프보딩</b>(active→deprecated)은 이 화면에서 되돌릴 버튼이 없습니다 — 다시 쓰려면 관리자가 API/DB를 직접 손대야 합니다.</li>' +
    '<li><b>모델 설정</b>은 이 에이전트가 기본으로 쓸 LLM provider/model을 저장만 합니다(비용 없음).</li>' +
    '<li><b>실행</b>은 온보딩된(활성) 에이전트에게 실제로 업무를 맡깁니다 — 설정해둔 모델(없으면 전역 기본값)로 실제 API를 호출하므로 비용이 발생합니다. 스킬에 내부지식검색이 있으면 내부지식검색(RAG), 조직배정관계조회가 있으면 관계분석 방식으로 자동 동작합니다.</li>' +
    '<li>에이전트 결과물을 지식베이스에 남기려면 <b>대화 메시지</b>나 <b>승인된 산출물</b>(갤러리·업무 화면)에서 "지식베이스로 보내기"를 누르세요 — admin 결재 후 등록됩니다.</li>' +
    '<li><b>MCP 서버(JSON)</b>에 예: [{"url":"http://localhost:8000/mcp","transport":"streamable-http"}] 형태로 넣으면 "Crew 실행" 시 이 Agent에 실제 MCP 도구가 붙습니다 — 빈 칸으로 두고 저장하면 기존 설정을 그대로 둡니다.</li>' +
    '<li><b>도구 연결</b>은 아래에서 "도구/스킬 Pool"에 등록된 도구를 골라 연결하면, 스킬 매칭과 무관하게 그 Agent에 도구가 하나 더 붙습니다.</li>' +
    // 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... AI
    // 에이전트 목록 포함 AI 에이전트 지정 항목이나 호출시(채팅 포함)에도
    // 활용될 수 있도록 개선") — "역할"(생성 당시 역할, 예: "백엔드
    // 개발자 Agent")이 식별자 역할까지 겸해 와서 같은 역할의 Agent가
    // 여럿이면 ID로만 구분해야 했다. 선택 입력인 "이름"을 추가해 지정한
    // 이름이 매칭·Crew 선택·팀 멤버·결재·채팅 @멘션 전부에서 역할명보다
    // 우선 쓰이게 한다(지정 안 하면 기존처럼 역할명 그대로 — 100% 하위
    // 호환).
    '<li><b>이름</b>은 선택 입력입니다 — 지정하면 매칭 결과·Crew 실행·팀 멤버·결재·인박스·채팅 @멘션 등 이 Agent가 보이는 모든 화면에서 역할명 대신 그 이름이 먼저 보입니다. 비워서 저장하면 다시 역할명으로 돌아갑니다.</li>' +
    '<li><b>⚙ 설정</b>을 누르면 설정 창이 열립니다. 구역마다 적용 범위 표시가 있습니다 — <b>모든 실행</b>(대화 @멘션·업무 실행·실행 버튼과 Crew 실행), <b>Crew 실행 전용</b>, <b>대화 @멘션</b>.</li>' +
    '<li><b>파인튜닝</b>은 LLM 가중치 재학습이 아니라 실행 동작 설정입니다. 온도·실행 시간 제한·최대 반복 횟수·추론 모드는 모든 실행에, 분당 호출 수·재시도·코드 실행·컨텍스트 자동 축소는 Crew 실행에만 적용됩니다. 비워두면 기본값을 씁니다.</li>' +
    '<li><b>역할/인격(목표·배경·기대 산출물)</b>은 역할명·스킬로 자동 생성되던 값을 Agent별로 직접 덮어씁니다 — 모든 실행에 적용되고, 비워두면 자동 생성으로 돌아갑니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 레코드를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 막힙니다.</li>' +
    '</ul>'
    ) +
    // 사용자 보고(2026-10-14, 스크린샷 — 13개 열이 전부 끼어서 "성과
    // 점수(수동)" 같은 헤더가 한 글자씩 세로로 줄바꿈될 만큼 눌림) —
    // 식별·관리에 바로 필요한 "핵심 정보"(ID/이름/역할/스킬/상태/
    // 생성방식/동작)만 열로 남기고, "모델 실행 후에만 쌓이는 정보"
    // (소속팀/비용/성과점수/자동평가/최근 산출물)는 "상세정보"
    // 한 열의 접힌 패널로 묶는다.
    // 사용자 요청(2026-10-14, "Agent 목록에 LLM 정보는 기본으로 표시되도록
    // 해줘") — LLM(provider/model)만은 접지 않고 별도 열로 항상 보이게
    // 뺀다(실행에 어떤 모델이 쓰이는지는 "실행 후에만 쌓이는 정보"가
    // 아니라 실행 전에도 미리 확인해야 하는 설정이라 상세 패널에 묻혀
    // 있으면 안 됨 — "대화창 사용 모델 표시" 개선과 같은 취지).
    // 사용자 요청(2026-10-27, "노출되지 않도록 개선") — 정수 PK는
    // 화면에서 숨기고 코드만 보여준다(수정/삭제 등 동작은 a.id를 그대로
    // 쓰므로 기능은 안 바뀜).
    table(["코드", "이름", "역할", "스킬", "상태", "생성방식", "LLM", "상세정보", "동작"], agents.map(a => {
      // 사용자 보고(2026-10-14, 스크린샷 — "동작" 칸 하나에 10개 넘는
      // 버튼/입력이 전부 세로로 풀폭 스택돼 행 하나가 화면을 가득
      // 채움) — 자주 쓰는 상태변경/실행 버튼만 바로 보이게 남기고,
      // 나머지(이름·비용·점수·모델·도구연결·파인튜닝)는 "⚙ 설정"
      // 하나로 접는다.
      // 행에는 자주 쓰는 동작만(2026-11-27, 사용성 평가 6번) — 예전엔 행마다 빨간 [오프보딩][삭제]가 [실행] 바로 옆에 있어
      // 39행 화면에 빨간 버튼이 78개였다. 되돌리기 어려운 두 동작은 ⚙ 설정 창 맨 아래 "위험 구역"으로 옮긴다(확인 창은 그대로).
      let actions = "";
      let danger = "";
      if (isAdmin) {
        if (a.status === "pending_onboarding") actions += '<button class="small primary" data-onboard="' + a.id + '">온보딩 승인</button>';
        if (a.status === "active") danger += '<button class="small danger" data-deprecate="' + a.id + '">오프보딩(사용 중지)</button> ';
        danger += '<button class="small danger" data-delete-agent="' + a.id + '">완전히 삭제</button>';
      }
      if (canExecute && a.status === "active") {
        actions += '<button class="small primary" data-execute="' + a.id + '">실행(일 시키기)</button>';
        // 사용자 요청(2026-11-20, 지식베이스 등록 체계 개편) — 여기 있던
        // "지식베이스로 보내기"(Agent의 `last_output`을 결재 없이 즉시
        // 색인)는 삭제했다. 그 값은 어느 경로든 "마지막 출력 하나"라
        // 승인 여부·출처를 보장할 수 없었다 — Agent 결과물은 대화 메시지나
        // 승인된 산출물에서 "지식베이스로 보내기"(admin 결재) 경로로만
        // 지식이 된다.
      }
      // ⚙ 설정(2026-11-21 개편) — 구역별 한 줄씩 정리한 본문을 행 안에 숨겨 두고,
      // 버튼을 누르면 넓은 설정 창으로 연다(`openAgentSettings`).
      let settings = "";
      if (canExecute) {
        const providers = ["anthropic", "openai", "ollama"];
        settings += _asSection("기본", "", "",
          _asRow("이름", "", '<input class="as-wide" placeholder="비우면 역할명으로 표시" id="name-' + a.id + '" value="' + escAttr(a.name || "") + '">' +
            '<button class="small" data-set-name="' + a.id + '">이름 저장</button>', "") +
          _asRow("LLM", "provider / model",
            '<select id="provider-' + a.id + '">' +
            providers.map(p => '<option value="' + p + '"' + (p === a.llm_provider ? " selected" : "") + ">" + p + "</option>").join("") +
            "</select>" + modelSelectHtml("model-" + a.id),
            "현재: " + (a.llm_provider ? esc(a.llm_provider) + " / " + esc(a.llm_model || "") : "전역 기본값") +
            ". 생성 옵션(top_k·top_p·최대 출력)은 모델 레지스트리에서 모델별로 정합니다.") +
          _asRow("MCP 서버", "JSON",
            '<input class="as-wide" placeholder="예: [{&quot;url&quot;:&quot;http://localhost:8000/mcp&quot;,&quot;transport&quot;:&quot;streamable-http&quot;}]" id="mcp-' + a.id + '" value="' +
            esc(a.mcp_servers && a.mcp_servers.length ? JSON.stringify(a.mcp_servers) : "").replace(/"/g, "&quot;") + '">',
            "비우고 저장하면 기존 설정을 유지합니다. Crew 실행 때 이 Agent에 MCP 도구가 붙습니다.") +
          '<div class="as-save"><button class="small" data-set-model="' + a.id + '">모델 설정 저장</button></div>');
        const toolOptions = tools.length
          ? tools.map(t => '<option value="' + t.id + '">' + esc(t.name) + " (#" + t.id + ")" +
              (a.linked_tool_ids.includes(t.id) ? " — 연결됨" : "") + '</option>').join("")
          : '<option value="">(등록된 도구 없음)</option>';
        settings += _asSection("도구 연결", "", "",
          _asRow("도구", "",
            '<select id="linktool-' + a.id + '">' + toolOptions + '</select>' +
            '<button class="small" data-link-tool="' + a.id + '">연결</button>' +
            '<button class="small" data-unlink-tool="' + a.id + '">해제</button>',
            "연결됨: " + (a.linked_tool_ids.length
              ? a.linked_tool_ids.map(id => esc(toolNameById[id] || ("#" + id))).join(", ")
              : "없음(스킬 기반 자동 연결은 별도)")));
        settings += tuningFormHtml(a);
        settings += personaFormHtml(a);
        settings += contextPolicyFormHtml(a);
      }
      if (isAdmin) {
        settings += _asSection("운영 지표", '<span class="as-scope">관리자</span>', "",
          _asRow("비용", "", '<input type="number" step="0.01" placeholder="현재 ' + esc(String(a.cost_estimate ?? "-")) + '" id="cost-' + a.id + '">' +
            '<button class="small" data-set-cost="' + a.id + '">비용 입력</button>', "") +
          _asRow("성과점수(수동)", "0~1", '<input type="number" step="0.01" min="0" max="1" placeholder="현재 ' +
            (a.performance_score === null ? "-" : esc(String(a.performance_score))) + '" id="ascore-' + a.id + '">' +
            '<button class="small" data-set-ascore="' + a.id + '">점수 입력</button>', ""));
      }
      if (canExecute) {
        settings += _asSection("최적화 추천", "", "역할·스킬을 보고 등록된 모델 중 알맞은 모델과 파인튜닝 값을 추천합니다(규칙 기반, 비용 없음).",
          '<div><button class="small" data-recommend-optimize="' + a.id + '">⚡ 최적화 추천</button><div id="optimize-result-' + a.id + '"></div></div>');
      }
      if (danger) {
        settings += _asSection("위험 구역", "", "되돌리기 어려운 동작입니다. 오프보딩은 새 일에 배정·실행되지 않게 멈추고(기록은 남음), 삭제는 이 Agent 기록을 지웁니다.",
          '<div class="as-danger">' + danger + "</div>");
      }
      if (settings) {
        actions += '<button class="small" data-open-agent-settings="' + a.id + '" data-agent-title="' + escAttr(a.name || a.role_name || ("#" + a.id)) + '">⚙ 설정</button>' +
          '<div id="agent-settings-' + a.id + '" hidden>' + settings + '</div>';
      }
      // 사용자 보고(2026-10-14, "최근 산출물도 전체화면/복사 가능한
      // 형태로 개선") — `last_output`은 보통 수백~수천 자짜리 실제
      // LLM 마크다운 산출물(제목·목록·코드블록 포함)인데, 80자 평문
      // 자르기만 하면 줄바꿈이 사라지고 마크다운 기호가 그대로 노출돼
      // 미리보기로서 의미가 없었다. 다른 화면(산출물 상세보기 등)과
      // 똑같이 `renderContent()`(Claude Artifact 스타일 — 복사·전체화면
      // 버튼은 전역 위임으로 이미 연결돼 있어 추가 배선 불필요)로
      // 렌더링한다.
      // 완성된 HTML 문서(발표자료 등)는 마크다운이 아니라 샌드박스
      // iframe으로 렌더링한다 — 앱 DOM에 직접 넣으면 그 안의 `<style>`이
      // 이 목록 테이블 전체의 폰트/색을 덮어쓴다(2026-11-19 사용자 보고).
      const output = a.last_output
        ? renderContent(a.last_output, _looksLikeFullHtmlDocument(a.last_output) ? "html" : undefined)
        : '<span class="muted">-</span>';
      const llmInfo = a.llm_provider ? esc(a.llm_provider) + "/" + esc(a.llm_model || "") : "-";
      // Phase 3 aggregate_agent_score() — 관리자 수동 점수와 별도로,
      // 이 Agent가 만든 산출물들의 실측 Gate A-G/TCR 평균을 보여준다
      // (사용자 요청, 2026-09-28, "Phase1~4까지... 분석하고 개선해줘"
      // 리뷰 중 발견 — 이전까지 API에 있어도 화면 어디에도 안 보였다).
      const autoEval = a.auto_evaluation
        ? (a.auto_evaluation.avg_tcr === null ? "TCR 미측정" : "TCR " + a.auto_evaluation.avg_tcr.toFixed(0) + "%") +
          " (n=" + a.auto_evaluation.sample_count + ")"
        : "-";
      // 사용자 요청(2026-10-07, "현재 UI가 복수의 에이전트 그룹 생성 및
      // HAIR 관점의 연동에 적합한 경우는 아님... AI 에이전트 관리 노출
      // 검토") — 이 Agent가 어느 팀 소속인지 여기서 바로 보여준다.
      const teamNames = a.team_names && a.team_names.length
        ? '<button class="small" data-goto-teams="1" title="팀 관리로 이동">' + a.team_names.map(esc).join(", ") + '</button>'
        : '<span class="muted">-</span>';
      // 핵심 정보(식별·상태·동작)와 분리한 "모델 실행 후 쌓이는 정보"
      // 묶음 — 소속팀/비용/성과점수/자동평가/최근 산출물은 매번
      // 볼 필요는 없지만 완전히 없애면 안 되는 정보라 접어서 둔다.
      // LLM은 별도 열로 뺐으니 여기서는 중복 표시하지 않는다.
      const detail = '<details><summary>상세</summary>' +
        '<div style="text-align:left;white-space:normal">' +
        '<div>소속팀: ' + teamNames + '</div>' +
        '<div>비용: ' + a.cost_estimate + '</div>' +
        '<div>성과점수(수동): ' + (a.performance_score === null ? "-" : a.performance_score) + '</div>' +
        '<div>자동평가(Gate/TCR): ' + esc(autoEval) + '</div>' +
        '<div>최근 산출물:' + (a.last_output ? "" : " " + output) + '</div>' +
        (a.last_output ? output : "") +
        '</div></details>';
      return [esc(a.code || "-"), a.name ? esc(a.name) : '<span class="muted">(미지정)</span>', esc(a.role_name), esc(a.skill_tags.join(", ")),
        esc(label(a.status)), esc(label(a.framework)), llmInfo, detail, '<div class="actions">' + actions + "</div>"];
    })) + '<div id="execute-result"></div></section>';

  if (canExecute) {
    agents.forEach(a => wireProviderModelSelect("provider-" + a.id, "model-" + a.id, modelsByProvider, a.llm_model));
  }

  content.querySelectorAll("[data-goto-teams]").forEach(btn => {
    btn.onclick = () => selectTab("teams");
  });
  content.querySelectorAll("[data-onboard]").forEach(btn => {
    btn.onclick = async () => { try { await api("POST", "/api/agent-pool/" + btn.dataset.onboard + "/onboard"); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); } };
  });
  content.querySelectorAll("[data-deprecate]").forEach(btn => {
    // 실측으로 발견한 갭(사용자 요청, 2026-09-28, "UI/UX 내용 중... 분석
    // 하고 개선해줘") — 이 버튼만 확인 창이 없었다. 사람 쪽 "오프보딩"
    // (data-offboard)과 앱 안의 모든 삭제 버튼은 confirm을 거치는데,
    // 이 화면 안내문 자체가 "되돌릴 버튼이 없다"고 명시하는 되돌리기
    // 어려운 동작인데도 한 번의 클릭만으로 실행됐다.
    btn.onclick = async () => {
      if (!(await confirmModal("이 Agent를 오프보딩합니다(active→deprecated, 이 화면엔 되돌리는 버튼이 없습니다). 계속할까요?"))) return;
      closeAgentSettings();
      try {
        await api("POST", "/api/agent-pool/" + btn.dataset.deprecate + "/deprecate");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-agent]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 AI 에이전트 레코드를 완전히 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      closeAgentSettings();
      try {
        await api("DELETE", "/api/agent-pool/" + btn.dataset.deleteAgent);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-open-agent-settings]").forEach(btn => {
    btn.onclick = () => openAgentSettings(btn.dataset.openAgentSettings, btn.dataset.agentTitle);
  });
  content.querySelectorAll("[data-set-cost]").forEach(btn => {
    btn.onclick = async () => {
      const v = document.getElementById("cost-" + btn.dataset.setCost).value;
      try { await api("POST", "/api/agent-pool/" + btn.dataset.setCost + "/cost", { cost_estimate: Number(v) }); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-ascore]").forEach(btn => {
    btn.onclick = async () => {
      const v = document.getElementById("ascore-" + btn.dataset.setAscore).value;
      try { await api("POST", "/api/agent-pool/" + btn.dataset.setAscore + "/performance", { score: Number(v) }); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-name]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setName;
      const v = document.getElementById("name-" + id).value;
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/name", { name: v || null });
        showMsg("global-msg", v ? "이름을 저장했습니다." : "이름을 지웠습니다(역할명으로 표시됩니다).", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-tuning]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setTuning;
      const numOrNull = (elId) => {
        const v = document.getElementById(elId).value;
        return v === "" ? null : Number(v);
      };
      const triOrNull = (elId) => {
        const v = document.getElementById(elId).value;
        return v === "" ? null : v === "true";
      };
      const payload = {
        max_iter: numOrNull("tune-maxiter-" + id),
        max_rpm: numOrNull("tune-maxrpm-" + id),
        max_execution_time: numOrNull("tune-maxtime-" + id),
        max_retry_limit: numOrNull("tune-maxretry-" + id),
        temperature: numOrNull("tune-temp-" + id),
        reasoning: triOrNull("tune-reasoning-" + id),
        max_reasoning_attempts: numOrNull("tune-maxreasonattempts-" + id),
        allow_code_execution: triOrNull("tune-codeexec-" + id),
        respect_context_window: triOrNull("tune-respectctx-" + id),
        self_review: triOrNull("tune-selfreview-" + id),
      };
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/tuning", payload);
        showMsg("global-msg", "파인튜닝 설정을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-persona]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setPersona;
      const payload = {
        goal: document.getElementById("persona-goal-" + id).value,
        backstory: document.getElementById("persona-backstory-" + id).value,
        expected_output: document.getElementById("persona-expected-" + id).value,
      };
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/persona", payload);
        showMsg("global-msg", "역할/인격 설정을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-context-policy]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setContextPolicy;
      const history = document.getElementById("ctx-history-" + id).value;
      const attach = document.getElementById("ctx-attach-" + id).value;
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/context-policy", {
          history_mode: history || null,
          use_attachments: attach === "" ? null : attach === "1",
        });
        showMsg("global-msg", "대화 컨텍스트 설정을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-model]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setModel;
      const provider = document.getElementById("provider-" + id).value;
      const model = modelSelectValue("model-" + id);
      const mcpRaw = document.getElementById("mcp-" + id).value.trim();
      const payload = { provider, model };
      if (mcpRaw) {
        try {
          payload.mcp_servers = JSON.parse(mcpRaw);
        } catch (e) {
          showMsg("global-msg", "MCP 서버 설정이 올바른 JSON이 아닙니다: " + e.message, "error");
          return;
        }
      }
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/model", payload);
        showMsg("global-msg", "모델 설정을 저장했습니다 — 다음 실행부터 적용됩니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-execute]").forEach(btn => {
    btn.onclick = async () => {
      const taskPrompt = window.prompt("이 에이전트에게 시킬 업무를 입력하세요(실제 LLM API 호출 — 비용 발생):");
      if (!taskPrompt) return;
      try {
        const r = await withBusy(btn, "실행 중…", () => api("POST", "/api/agent-pool/" + btn.dataset.execute + "/execute", { prompt: taskPrompt }));
        document.getElementById("execute-result").innerHTML =
          '<div class="msg ok"><b>실행 결과</b><br>' + renderContent(r.output) + "</div>";
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-link-tool]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.linkTool;
      const toolId = document.getElementById("linktool-" + id).value;
      if (!toolId) { showMsg("global-msg", "연결할 도구를 선택하세요.", "error"); return; }
      try {
        await api("POST", "/api/agent-pool/" + id + "/tools/" + toolId);
        showMsg("global-msg", "도구를 연결했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-unlink-tool]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.unlinkTool;
      const toolId = document.getElementById("linktool-" + id).value;
      if (!toolId) { showMsg("global-msg", "해제할 도구를 선택하세요.", "error"); return; }
      try {
        await api("DELETE", "/api/agent-pool/" + id + "/tools/" + toolId);
        showMsg("global-msg", "도구 연결을 해제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  // 저장 핸들러를 모두 붙인 뒤에 열린 설정 창의 본문을 새 것으로 바꿔 끼운다.
  _reattachAgentSettings();
}


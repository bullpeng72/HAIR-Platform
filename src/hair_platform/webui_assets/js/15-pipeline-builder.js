/* ---------- 파이프라인 빌더(사용자 요청, 2026-10-18, "BMAD-METHOD와
   하네스 방법론을 참조해... 개발 Agent 팀을 구성(기존의 개발팀 갱신)...
   각 단계별 단독 또는 복수의 에이전트가 순차/병행 처리... 다른
   업무(시장조사-전략/기획 수립 등)에도 적용될 수 있도록 일반화") ----------
   엔진은 "단계 + 역할"만 안다(도메인 무관) — 그래서 이 화면 하나로
   SW개발 파이프라인도, 다른 업무 파이프라인도 똑같이 만들 수 있다. */

const PIPELINE_MODE_LABEL = {
  single: "단독(1명)",
  sequential_relay: "순차 릴레이(N명, 앞사람 결과를 이어받음)",
  // 계획 분담(2026-11-23, P2-9) — 첫 번째 역할이 리드: 계획 → 나머지가 분담 실행 → 리드가 병합
  plan_merge: "계획 분담(첫 역할=리드가 나눠 맡기고 결과를 합침)",
};

function pipelineStageAgentRowHtml(activeAgents) {
  return '<div class="inline pipeline-agent-row" style="margin-bottom:0.3rem">' +
    '<select class="pipeline-agent-role" style="min-width:200px"><option value="">— 역할 선택 —</option>' +
    activeAgents.map(a => '<option value="' + esc(a.role_name) + '">' + agentDisplayLabel(a) + '</option>').join("") +
    '</select>' +
    '<input class="pipeline-agent-instructions" placeholder="이 역할만의 구체 지시(선택)" style="flex:1;min-width:200px">' +
    '<button type="button" class="small danger pipeline-agent-row-remove">×</button>' +
    '</div>';
}

function wirePipelineAgentRowRemovers(scope) {
  scope.querySelectorAll(".pipeline-agent-row-remove").forEach(btn => {
    btn.onclick = () => {
      const rows = scope.querySelectorAll(".pipeline-agent-row");
      if (rows.length <= 1) { showMsg("global-msg", "최소 역할 1개는 있어야 합니다.", "error"); return; }
      btn.closest(".pipeline-agent-row").remove();
    };
  });
}

// 사용자 요청(2026-11-17, "Human Agency Scale 등 개념과 비교해 HAIR-
// Platform 개선이 필요한 Insight 정리" → "구현 시작") — 협업 수준
// (자동화↔증강)이 재시도 상한·Gate 임계값·승인 필요 여부에 흩어져
// 있어, 관리자가 이 단계가 실제로 어느 정도 사람 개입을 필요로
// 하는지 한눈에 보려면 세 숫자를 따로 읽고 조합해야 했다 — 이미
// 있는 세 값만 조합해 보여주는 순수 파생 라벨이다(새 저장값 없음).
// `requires_human_approval=false`여도 재시도를 다 소진하면 결국
// 사람에게 에스컬레이션된다는 사실(`pipeline_runtime.py`)까지
// 반영해, "완전 자동"이라 부를 수 있는 경우를 Gate 기준이 느슨할
// 때로 좁힌다 — 정확한 분류가 아니라 참고용 근사치임을 title로
// 명시한다.
function _pipelineStageCollaborationBadge(stage) {
  let label, cls, title;
  if (stage.requires_human_approval) {
    label = "사람 필수"; cls = "gate-warn";
    title = "Gate 통과 여부와 무관하게 항상 사람 결재를 거칩니다.";
  } else if (stage.min_gate_pass_ratio <= 0.3) {
    label = "자동화 중심"; cls = "gate-ok";
    title = "Gate 기준이 느슨해(" + Math.round(stage.min_gate_pass_ratio * 100) + "%) 대부분 사람 개입 없이 다음 단계로 넘어갑니다(단, 재시도를 다 소진하면 그때는 사람에게 올라갑니다).";
  } else {
    label = "증강 중심"; cls = "gate-na";
    title = "Gate 기준이 엄격해(" + Math.round(stage.min_gate_pass_ratio * 100) + "%) 실패가 잦으면 재시도 소진 후 사람에게 에스컬레이션됩니다.";
  }
  return '<span class="gate-badge ' + cls + '" title="' + escAttr(title) + ' (참고용 근사치 — HAS 척도 기반)">' + label + '</span>';
}

// 검토 실패 시 되돌아갈 단계(2026-11-22, 하드코딩·스텁 점검 4순위) — Gate 기준이 있는 단계에서만
// 의미가 있다. 이 단계보다 앞선 단계만 고를 수 있다.
// 필수 도구(2026-11-26, 개선 계획 1-2) — 이 단계에서 Agent가 실제로 불러야 하는 도구. 부르지 않으면 단계 미통과 →
// 다시 시도("필수 도구를 실제로 부르지 않았습니다"). 단계 Agent에 연결되지 않은 도구는 막지 않고 '측정 안 함'으로 알린다.
let _pipelineToolNames = [];
// 단계별 모델(2026-11-26, 개선 계획 3-2) — 비우면 대표 Agent의 모델. 요구사항 정리·형식 검토 같은 가벼운 단계를 작은
// 모델로 보내 큰 로컬 모델 대기를 줄인다. 목록은 모델 레지스트리(provider → 모델명).
let _pipelineModels = { anthropic: [], openai: [], ollama: [] };
function _stageModelHtml(stage) {
  const cur = stage.llm_provider ? stage.llm_provider + "|" + (stage.llm_model || "") : "";
  const opts = ['<option value="">대표 Agent의 모델</option>'];
  Object.entries(_pipelineModels).forEach(([prov, names]) => (names || []).forEach(n => {
    const v = prov + "|" + n;
    opts.push('<option value="' + escAttr(v) + '"' + (v === cur ? " selected" : "") + ">" + esc(prov + " · " + n) + "</option>");
  }));
  if (cur && !opts.some(o => o.includes('value="' + escAttr(cur) + '"'))) {
    opts.push('<option value="' + escAttr(cur) + '" selected>' + esc(cur.replace("|", " · ")) + "(레지스트리에 없음)</option>");
  }
  return '<p class="muted"><span title="이 단계만 다른 모델로 실행합니다. 여러 파이프라인이 같은 Agent를 쓰므로 Agent 설정을 바꾸지 않고 가벼운 단계(요구사항 정리·형식 검토)만 작은 모델로 보낼 때 씁니다.">이 단계 모델:</span> ' +
    '<select data-stage-model="' + stage.id + '">' + opts.join("") + "</select></p>";
}
function _stageRequiredToolsHtml(stage) {
  const req = stage.required_tools || [];
  const chips = req.map(t =>
    '<span class="chip">🔧 ' + esc(t) + ' <button class="small" type="button" data-remove-required-tool="' + stage.id + '" data-tool="' + escAttr(t) + '" title="필수 도구에서 빼기">×</button></span>'
  ).join(" ");
  const options = _pipelineToolNames.filter(n => !req.includes(n))
    .map(n => '<option value="' + escAttr(n) + '">' + esc(n) + "</option>").join("");
  return '<p class="muted" data-required-tools="' + escAttr(JSON.stringify(req)) + '" data-stage-id="' + stage.id + '">' +
    '<span title="이 단계에서 Agent가 반드시 실제로 불러야 하는 도구 — 부르지 않고 결과만 적으면 미통과로 다시 시도합니다(호출 기록으로 확인).">필수 도구:</span> ' +
    (chips || "없음") +
    (options ? ' <select data-add-required-tool="' + stage.id + '"><option value="">+ 필수 도구 추가</option>' + options + "</select>" : "") +
    "</p>";
}

function _stageReturnSelectHtml(stage, stages) {
  const earlier = (stages || []).filter(s => s.seq_order < stage.seq_order);
  if (!earlier.length || !(stage.min_gate_pass_ratio > 0)) return "";
  return '<label class="inline" style="gap:0.3rem" title="이 단계가 Gate 기준에 못 미치면, 같은 검토를 다시 돌리는 대신 고른 단계로 돌아가 검토 의견을 반영해 다시 쓰게 합니다">' +
    '검토 실패 시 <select data-stage-return="' + stage.id + '">' +
    '<option value="0">이 단계만 재시도</option>' +
    earlier.map(s => '<option value="' + s.id + '"' + (s.id === stage.on_fail_return_stage_id ? " selected" : "") + ">" +
      s.seq_order + ". " + esc(s.name) + "로 되돌림</option>").join("") +
    "</select></label>";
}

function pipelineStageCardHtml(templateId, stage, index, total, stages) {
  const agentsText = stage.agents.map(a =>
    esc(a.role_name) + (a.role_name === stage.lead_role_name ? " (리드)" : "")
  ).join(", ");
  return '<div class="card" style="margin-bottom:0.5rem" data-pipeline-stage-card="' + stage.id + '">' +
    '<div class="inline" style="justify-content:space-between">' +
    '<b>' + stage.seq_order + ". " + esc(stage.name) + "</b>" +
    '<span class="muted">' + (PIPELINE_MODE_LABEL[stage.mode] || stage.mode) +
    (stage.is_primary_deliverable ? ' <span class="gate-badge gate-ok">최종 결과물 단계</span>' : "") +
    (stage.is_requirements_source ? ' <span class="gate-badge">요구사항 정의</span>' : "") +
    (stage.checks_requirements_traceability ? ' <span class="gate-badge">추적성 검사</span>' : "") +
    "</span>" +
    "</div>" +
    (stage.instructions ? '<p class="muted">' + esc(stage.instructions) + "</p>" : "") +
    (stage.expected_output ? '<p class="muted"><b>기대 산출물:</b> ' + esc(stage.expected_output) + "</p>" : "") +
    "<p>참여 역할: " + agentsText + "</p>" +
    '<p class="muted">Gate 통과 기준 ' + Math.round(stage.min_gate_pass_ratio * 100) + "% · 최대 재시도 " +
    stage.max_retry + "회 · 사람 승인 " + (stage.requires_human_approval ? "필요" : "불필요") +
    " " + _pipelineStageCollaborationBadge(stage) + "</p>" +
    _stageReturnSelectHtml(stage, stages) +
    _stageRequiredToolsHtml(stage) +
    _stageModelHtml(stage) +
    '<div class="actions">' +
    (index > 0 ? '<button class="small" data-reorder-stage="' + stage.id + ':up">↑</button>' : "") +
    (index < total - 1 ? '<button class="small" data-reorder-stage="' + stage.id + ':down">↓</button>' : "") +
    '<button class="small" data-toggle-stage-flag="' + stage.id + ':is_requirements_source:' + (stage.is_requirements_source ? "0" : "1") + '">' +
      (stage.is_requirements_source ? "요구사항 정의 해제" : "요구사항 정의 단계로") + "</button>" +
    '<button class="small" data-toggle-stage-flag="' + stage.id + ':checks_requirements_traceability:' + (stage.checks_requirements_traceability ? "0" : "1") + '">' +
      (stage.checks_requirements_traceability ? "추적성 검사 끄기" : "추적성 검사 켜기") + "</button>" +
    // 엄격 검토(2026-11-25, P0-4) — 되돌아갈 단계가 정해진 검토 단계에서만 의미가 있다.
    (stage.on_fail_return_stage_id ? '<button class="small" data-toggle-stage-flag="' + stage.id + ':strict_review:' + (stage.strict_review ? "0" : "1") + '" title="켜면 검토자가 승인하면서 고칠 곳을 짚은 &quot;조건부 승인&quot;도 보완 요청처럼 작성 단계로 되돌려 고치게 합니다(재시도 상한 안에서, 시간이 더 듦). 끄면 노란 &quot;조건부 승인&quot; 배지로만 알립니다.">' +
      (stage.strict_review ? "엄격 검토 끄기" : "엄격 검토 켜기") + "</button>" : "") +
    '<button class="small danger" data-delete-stage="' + stage.id + '">단계 삭제</button>' +
    "</div></div>";
}

function pipelineAddStageFormHtml(templateId, activeAgents) {
  return '<form class="card" data-add-stage-form="' + templateId + '" style="margin-top:0.5rem">' +
    "<h4>+ 단계 추가</h4>" +
    '<div class="inline">' +
    '<input class="stage-name" placeholder="단계 이름" required style="min-width:140px">' +
    '<select class="stage-mode">' +
    Object.keys(PIPELINE_MODE_LABEL).map(m => '<option value="' + m + '">' + PIPELINE_MODE_LABEL[m] + "</option>").join("") +
    "</select>" +
    "</div>" +
    '<textarea class="stage-instructions" placeholder="이 단계 공통 지시문(선택 — 예: &quot;설계 산출물을 검증하세요&quot;)" style="width:100%;margin-top:0.3rem;min-height:2.2rem"></textarea>' +
    // 사용자 요청(2026-10-20, "고급 에이전트 관리") — 지금까지 모든
    // 단계가 똑같은 플랫폼 공통 Task.expected_output 문구를 썼다(예:
    // "QA 기능테스트" 단계도 "구현" 단계와 같은 기대 산출물 문구). 빈
    // 칸이면 그 공통 문구를 그대로 쓴다.
    '<textarea class="stage-expected-output" placeholder="이 단계의 기대 산출물(선택 — 예: &quot;실제로 실행 가능한 테스트 코드와 통과/실패 리포트&quot;, 비우면 공통 기본 문구 사용)" style="width:100%;margin-top:0.3rem;min-height:2.2rem"></textarea>' +
    '<p class="muted" style="margin:0.3rem 0 0">역할을 추가한 순서가 "순차 릴레이"의 처리 순서입니다. "계획 분담"에서는 <b>첫 번째로 추가한 역할이 리드(나누고 합치는 쪽)</b>가 됩니다.</p>' +
    '<div class="stage-agent-rows">' + pipelineStageAgentRowHtml(activeAgents) + "</div>" +
    '<button type="button" class="small stage-add-agent-row">+ 역할 추가</button>' +
    '<div class="inline" style="margin-top:0.4rem">' +
    '<label class="inline" style="gap:0.3rem"><input type="checkbox" class="stage-requires-approval"> 사람 승인 필요(계획 확정 체크포인트)</label>' +
    // 사용자 보고(2026-10-19, "최종 결과물에 코드가 아니라 품질
    // 리포트만 있다") — Task 워크스페이스의 "최종 결과물"은 이 단계
    // 산출물을 우선 집는다(체크 안 하면 기존처럼 "가장 최근 승인된
    // deliverable"로 폴백). 보통 "구현/작성" 성격의 단계 하나에만
    // 체크한다 — 검증/보고 단계는 보통 아니다.
    '<label class="inline" style="gap:0.3rem"><input type="checkbox" class="stage-is-primary"> 이 단계 산출물이 최종 결과물</label>' +
    // 요구사항 추적성(2026-11-22, 하드코딩·스텁 점검 4순위) — 어느 단계가 요구사항을 정의하고, 어느
    // 단계가 그 요구사항 대비 검증되는지 화면에서 지정한다(예전엔 1단계 고정, 검사 여부는 화면에 없었다).
    '<label class="inline" style="gap:0.3rem" title="요구사항 추적성의 기준 문서를 내는 단계 — 지정이 없으면 1단계"><input type="checkbox" class="stage-is-req-source"> 요구사항 정의 단계</label>' +
    '<label class="inline" style="gap:0.3rem" title="이 단계 산출물이 요구사항을 빠짐없이 반영했는지 LLM이 판정"><input type="checkbox" class="stage-checks-trace"> 요구사항 추적성 검사</label>' +
    '</div>' +
    '<div class="inline" style="margin-top:0.3rem">' +
    '<label>최대 재시도 <input type="number" class="stage-max-retry" value="2" min="1" max="10" style="width:60px"></label>' +
    '<label>Gate 통과 기준 <input type="number" class="stage-min-gate-ratio" value="0" min="0" max="100" style="width:60px">%</label>' +
    "</div>" +
    '<button class="primary small" type="submit" style="margin-top:0.4rem">이 단계 추가</button>' +
    "</form>";
}

// 요약 한 줄 + 하나씩 펼침(2026-11-27, 사용성 평가 5번) — 예전엔 템플릿 8개의 모든 단계·설정이 한 페이지에 다 펼쳐져
// 높이 14,895px·버튼 226개·입력칸 190개였다. 템플릿마다 요약(단계 수·사람 승인 단계·단계 흐름)만 보이고, [편집]을 누른
// 하나만 펼친다. 펼친 템플릿은 저장 뒤 새로 그려도 그대로 열려 있다.
let _plOpenTemplateId = null;

function pipelineTemplateCardHtml(t, detail, activeAgents) {
  const stages = detail ? detail.stages : [];
  const open = String(_plOpenTemplateId) === String(t.id);
  const approvals = stages.filter(s => s.requires_human_approval).length;
  const flow = stages.map(s => s.name).join(" → ");
  return '<section class="card pl-card' + (open ? " pl-open" : "") + '" data-pipeline-template-card="' + t.id + '">' +
    '<div class="pl-summary">' +
    '<div class="pl-title"><h3>' + esc(t.name) +
    (t.is_active ? "" : ' <span class="gate-badge gate-warn">비활성</span>') + "</h3>" +
    '<span class="muted">' + stages.length + "단계 · 사람 승인 " + approvals + "곳" + (t.domain && t.domain !== "general" ? " · " + esc(t.domain) : "") + "</span></div>" +
    '<button class="small' + (open ? "" : " primary") + '" data-pl-toggle="' + t.id + '">' + (open ? "닫기" : "편집") + "</button></div>" +
    (flow ? '<p class="pl-flow muted" title="' + escAttr(flow) + '">' + esc(flow) + "</p>" : "") +
    '<div class="pl-body"' + (open ? "" : " hidden") + ">" +
    (t.description ? '<p class="muted">' + esc(t.description) + "</p>" : "") +
    (stages.length
      ? stages.map((s, i) => pipelineStageCardHtml(t.id, s, i, stages.length, stages)).join("")
      : '<p class="muted">아직 단계가 없습니다 — 아래에서 추가하세요.</p>') +
    pipelineAddStageFormHtml(t.id, activeAgents) +
    '<div class="actions" style="margin-top:0.5rem">' +
    '<button class="small" data-toggle-pipeline-active="' + t.id + '" data-next-active="' + (t.is_active ? "0" : "1") + '">' +
    (t.is_active ? "비활성화" : "활성화") + "</button>" +
    (ME.role === "admin" ? '<button class="small danger" data-delete-pipeline-template="' + t.id + '">템플릿 삭제</button>' : "") +
    "</div></div></section>";
}

async function renderPipelines(content) {
  const [templates, agents, toolPool] = await Promise.all([
    api("GET", "/api/pipeline-templates"),
    api("GET", "/api/agent-pool"),
    api("GET", "/api/tool-pool").catch(() => []),  // 필수 도구 고르기(2026-11-26)
  ]);
  _pipelineToolNames = (toolPool || []).filter(t => t.is_active !== false && t.is_active !== 0).map(t => t.name);
  _pipelineModels = await fetchModelsByProvider();  // 단계별 모델(2026-11-26)
  const activeAgents = agents.filter(a => a.status === "active");
  const details = await Promise.all(
    templates.map(t => api("GET", "/api/pipeline-templates/" + t.id))
  );
  const detailById = {};
  templates.forEach((t, i) => { detailById[t.id] = details[i]; });

  content.innerHTML =
    '<section class="card"><h2>파이프라인 관리 — 단계형 개발/업무 Agent 팀</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"단계(stage)"를 순서대로 쌓아 파이프라인을 만듭니다 — 각 단계는 <b>단독(1명)</b>, <b>순차 릴레이(N명이 순서대로 이어받음)</b>, <b>계획 분담(리드가 팀원별 하위 작업을 나누고, 팀원이 각자 맡은 뒤, 리드가 하나로 합침)</b> 중 하나로 동작합니다.</li>' +
      '<li>단계별로 통과 판정에 쓸 Gate 목록·통과 비율·최대 재시도·"사람 승인 필요" 여부를 정할 수 있습니다 — 승인이 필요 없는 단계는 기준만 통과하면 자동으로 다음 단계로 넘어갑니다(최소 HITL). 반복해도 기준을 못 넘기면 그때만 결재함으로 올라갑니다(최소 HOTL). 기본 템플릿의 검증 단계는 Gate A와 요구사항 추적성(둘 다 필수), 구현 단계는 실행 가능성 Gate로 판정합니다 — C·D·F·G는 내용과 무관하게 비슷한 값이 나와 기록만 합니다.</li>' +
      '<li>단계 카드에서 <b>요구사항 정의 단계</b>(요구사항 추적성의 기준 — 지정이 없으면 1단계)와 <b>요구사항 추적성 검사</b>를 켜고 끌 수 있고, Gate 기준이 있는 단계는 <b>검토 실패 시</b> 같은 검토를 다시 돌리는 대신 앞 단계(작성·구현)로 되돌려 검토 의견을 반영해 다시 쓰게 할 수 있습니다.</li>' +
      '<li>예전의 <b>병행 위임</b>(리드가 도구로 그때그때 위임)은 리드가 실제로 위임하지 않고 혼자 처리하는 문제가 반복돼 2026-11-26에 없앴습니다 — 남아 있던 단계는 같은 리드·팀원의 계획 분담으로 자동 전환됩니다.</li>' +
      '<li>실행은 할 일(Task)의 협업 워크스페이스에서 "파이프라인 실행"으로 시작합니다 — 이 화면은 템플릿(설계도)만 관리합니다. 실행할 때 "신규 실행엔진(LangGraph)"을 고를 수 있습니다.</li>' +
      '<li>SW 개발뿐 아니라 시장조사-분석(3C/SWOT)-전략/기획 같은 다른 업무도 같은 방식으로 만들 수 있습니다(역할만 다를 뿐 엔진은 동일).</li>' +
      "</ul>"
    ) +
    '<form class="inline" id="pipeline-template-create-form">' +
    '<input id="pipeline-template-name" placeholder="파이프라인 이름" required>' +
    '<input id="pipeline-template-description" placeholder="설명(선택)" style="flex:1;min-width:160px">' +
    '<input id="pipeline-template-domain" placeholder="도메인(선택, 예: software_dev)" style="width:180px">' +
    '<button class="primary" type="submit">파이프라인 등록</button></form>' +
    "</section>" +
    _plDraftCardHtml() +
    (templates.length
      ? templates.map(t => pipelineTemplateCardHtml(t, detailById[t.id], activeAgents)).join("")
      : '<p class="muted">등록된 파이프라인이 없습니다.</p>');

  _plBindDraft(content);
  content.querySelectorAll("[data-pl-toggle]").forEach(btn => {
    btn.onclick = () => {
      const id = btn.dataset.plToggle;
      _plOpenTemplateId = String(_plOpenTemplateId) === id ? null : id;
      content.querySelectorAll("[data-pipeline-template-card]").forEach(card => {
        const open = card.dataset.pipelineTemplateCard === String(_plOpenTemplateId);
        card.classList.toggle("pl-open", open);
        card.querySelector(".pl-body").hidden = !open;
        const tb = card.querySelector("[data-pl-toggle]");
        tb.textContent = open ? "닫기" : "편집";
        tb.classList.toggle("primary", !open);
      });
      if (_plOpenTemplateId) btn.closest(".pl-card").scrollIntoView({ block: "start", behavior: "smooth" });
    };
  });

  document.getElementById("pipeline-template-create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/pipeline-templates", {
        name: document.getElementById("pipeline-template-name").value,
        description: document.getElementById("pipeline-template-description").value,
        domain: document.getElementById("pipeline-template-domain").value || "general",
      });
      showMsg("global-msg", "파이프라인을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-add-stage-form]").forEach(form => {
    wirePipelineAgentRowRemovers(form);
    form.querySelector(".stage-add-agent-row").onclick = () => {
      form.querySelector(".stage-agent-rows").insertAdjacentHTML("beforeend", pipelineStageAgentRowHtml(activeAgents));
      wirePipelineAgentRowRemovers(form);
    };
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const templateId = form.dataset.addStageForm;
      const mode = form.querySelector(".stage-mode").value;
      const rows = Array.from(form.querySelectorAll(".pipeline-agent-row"));
      const roleAgents = rows.map(r => ({
        role_name: r.querySelector(".pipeline-agent-role").value,
        instructions: r.querySelector(".pipeline-agent-instructions").value,
      }));
      if (roleAgents.some(a => !a.role_name)) {
        showMsg("global-msg", "모든 역할 줄에서 역할을 선택하세요.", "error"); return;
      }
      if (mode === "single" && roleAgents.length !== 1) {
        showMsg("global-msg", "단독 모드는 역할이 정확히 1개여야 합니다.", "error"); return;
      }
      if (mode === "plan_merge" && roleAgents.length < 2) {
        showMsg("global-msg", "계획 분담 모드는 리드(첫 번째 역할) 포함 최소 2개 역할이 필요합니다.", "error"); return;
      }
      const payload = {
        name: form.querySelector(".stage-name").value,
        mode,
        agents: roleAgents,
        instructions: form.querySelector(".stage-instructions").value,
        max_retry: Number(form.querySelector(".stage-max-retry").value) || 2,
        min_gate_pass_ratio: (Number(form.querySelector(".stage-min-gate-ratio").value) || 0) / 100,
        requires_human_approval: form.querySelector(".stage-requires-approval").checked,
        is_primary_deliverable: form.querySelector(".stage-is-primary").checked,
        is_requirements_source: form.querySelector(".stage-is-req-source").checked,
        checks_requirements_traceability: form.querySelector(".stage-checks-trace").checked,
        expected_output: form.querySelector(".stage-expected-output").value,
      };
      if (mode === "plan_merge") payload.lead_role_name = roleAgents[0].role_name;
      try {
        await api("POST", "/api/pipeline-templates/" + templateId + "/stages", payload);
        showMsg("global-msg", "단계를 추가했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  content.querySelectorAll("[data-reorder-stage]").forEach(btn => {
    btn.onclick = async () => {
      const [stageId, direction] = btn.dataset.reorderStage.split(":");
      const templateId = btn.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      try {
        await api("POST", "/api/pipeline-templates/" + templateId + "/stages/" + stageId + "/reorder", { direction });
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-stage-return]").forEach(sel => {
    sel.onchange = async () => {
      const templateId = sel.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      try {
        await api("PATCH", "/api/pipeline-templates/" + templateId + "/stages/" + sel.dataset.stageReturn,
          { on_fail_return_stage_id: Number(sel.value) });
        showMsg("global-msg", "검토 실패 시 동작을 바꿨습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-toggle-stage-flag]").forEach(btn => {
    btn.onclick = async () => {
      const [stageId, field, value] = btn.dataset.toggleStageFlag.split(":");
      const templateId = btn.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      try {
        await api("PATCH", "/api/pipeline-templates/" + templateId + "/stages/" + stageId, { [field]: value === "1" });
        showMsg("global-msg", "단계 설정을 바꿨습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  const _saveRequiredTools = async (el, next) => {
    const templateId = el.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
    const stageId = el.closest("[data-required-tools]").dataset.stageId;
    try {
      await api("PATCH", "/api/pipeline-templates/" + templateId + "/stages/" + stageId, { required_tools: next });
      showMsg("global-msg", "필수 도구를 바꿨습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };
  content.querySelectorAll("[data-stage-model]").forEach(sel => {
    sel.onchange = async () => {
      const templateId = sel.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      const [prov, model] = sel.value ? sel.value.split("|") : ["", ""];
      try {
        await api("PATCH", "/api/pipeline-templates/" + templateId + "/stages/" + sel.dataset.stageModel, { llm_provider: prov, llm_model: model });
        showMsg("global-msg", sel.value ? "이 단계 모델을 " + prov + " · " + model + "(으)로 바꿨습니다." : "이 단계는 대표 Agent의 모델을 씁니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-add-required-tool]").forEach(sel => {
    sel.onchange = () => {
      if (!sel.value) return;
      const cur = JSON.parse(sel.closest("[data-required-tools]").dataset.requiredTools || "[]");
      _saveRequiredTools(sel, cur.concat([sel.value]));
    };
  });
  content.querySelectorAll("[data-remove-required-tool]").forEach(btn => {
    btn.onclick = () => {
      const cur = JSON.parse(btn.closest("[data-required-tools]").dataset.requiredTools || "[]");
      _saveRequiredTools(btn, cur.filter(t => t !== btn.dataset.tool));
    };
  });
  content.querySelectorAll("[data-delete-stage]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 단계를 지웁니다. 계속할까요?"))) return;
      const stageId = btn.dataset.deleteStage;
      const templateId = btn.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      try {
        await api("DELETE", "/api/pipeline-templates/" + templateId + "/stages/" + stageId);
        showMsg("global-msg", "단계를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-toggle-pipeline-active]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/pipeline-templates/" + btn.dataset.togglePipelineActive + "/active", {
          is_active: btn.dataset.nextActive === "1",
        });
        showMsg("global-msg", "변경했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-delete-pipeline-template]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 파이프라인 템플릿을 완전히 지웁니다(실행 이력이 있으면 지울 수 없습니다). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/pipeline-templates/" + btn.dataset.deletePipelineTemplate);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 업무 설명으로 초안 만들기(2026-11-27, AI Native 분석 3단계 — 3번 자연어 프로세스 설계) ---------- */
// 업무를 글로 설명하면 플랫폼 기본 AI가 단계 초안을 제안하고(등록된 Agent 역할·도구만), 사람이 고친 뒤 만든다.
// AI를 못 쓰면 기본 초안(작성 → 검토 → 최종 확인). 초안 단계에서는 아무것도 저장되지 않는다.
let _plDraft = null;

function _plDraftCardHtml() {
  return '<section class="card" id="pl-draft-card"><h2>업무 설명으로 초안 만들기</h2>' +
    '<p class="muted">하는 일을 글로 적으면 AI가 단계·담당 Agent·검토 단계·사람 승인 위치를 제안합니다. 고친 뒤 <b>이대로 만들기</b>를 눌러야 저장됩니다(마지막 단계는 기본으로 사람 승인).</p>' +
    '<label for="pl-draft-desc">업무 설명</label>' +
    '<textarea id="pl-draft-desc" rows="4" placeholder="예: 거래처에서 계약서 초안을 받으면 위험 조항(위약금·해지·책임 제한)을 찾아 표로 정리하고, 수정 제안 문구와 함께 법무 담당자에게 검토 의견서를 보낸다."></textarea>' +
    '<details><summary class="muted">예시 결과물 붙이기(선택)</summary><textarea id="pl-draft-example" rows="4" placeholder="이런 형태의 결과가 나와야 한다는 예시"></textarea></details>' +
    '<p><button class="primary" id="pl-draft-btn">초안 만들기</button> <span class="muted" id="pl-draft-msg"></span></p>' +
    '<div id="pl-draft-result"></div></section>';
}

function _plDraftResultHtml(res) {
  const d = res.draft;
  const roleOpts = (sel) => '<option value="">— 역할 고르기 —</option>' + res.available_roles.map(r =>
    '<option' + (r === sel ? " selected" : "") + ">" + esc(r) + "</option>").join("");
  const domains = ["general", "software_dev", "business_planning", "proposal_writing", "performance_reporting", "finance", "sales", "legal", "hr"];
  let html = "";
  if (res.note) html += '<p class="msg warn">' + esc(res.note) + "</p>";
  if (res.similar.length) {
    html += '<p class="muted">비슷한 기존 파이프라인: ' + res.similar.map(s => "<b>" + esc(s.name) + "</b>").join(", ") +
      " — 새로 만들기 전에 이걸 써도 되는지 확인하세요.</p>";
  }
  html += '<div class="pl-draft-meta"><label>이름 <input id="pl-dr-name" value="' + escAttr(d.name) + '"></label> ' +
    '<label>업무 영역 <select id="pl-dr-domain">' + domains.map(x => "<option" + (x === d.domain ? " selected" : "") + ">" + x + "</option>").join("") + "</select></label></div>";
  html += '<ol class="pl-draft-stages">' + d.stages.map((s, i) =>
    '<li class="pl-draft-stage" data-i="' + i + '"><div class="pl-draft-row"><input class="pl-dr-sname" value="' + escAttr(s.name) + '" aria-label="단계 이름"> ' +
    s.roles.map((r, j) => '<select class="pl-dr-role" data-j="' + j + '" aria-label="담당 역할">' + roleOpts(r) + "</select>").join(" ") +
    (s.mode === "sequential_relay" ? ' <span class="muted">(순서대로 이어받기)</span>' : "") +
    (s.missing_roles && s.missing_roles.length ? ' <span class="gate-badge gate-warn">등록 안 된 역할 "' + esc(s.missing_roles.join(", ")) + '" — 골라 주세요</span>' : "") +
    "</div>" +
    '<textarea class="pl-dr-inst" rows="2" aria-label="지시문">' + esc(s.instructions) + "</textarea>" +
    '<div class="pl-draft-row muted"><label><input type="checkbox" class="pl-dr-human"' + (s.requires_human_approval ? " checked" : "") + "> 사람 승인</label>" +
    (s.why_human ? " — " + esc(s.why_human) : "") +
    (s.return_to !== null && s.return_to !== undefined ? " · 검토 미달이면 " + (s.return_to + 1) + "단계로 되돌림" : "") +
    (s.required_tools.length ? " · 필수 도구: " + esc(s.required_tools.join(", ")) : "") +
    " · 결과 형식 " + esc(s.output_content_type) + "</div></li>").join("") + "</ol>";
  html += '<p><button class="primary" id="pl-draft-create">이대로 만들기</button> <span class="muted" id="pl-draft-create-msg"></span></p>';
  return html;
}

function _plBindDraft(content) {
  const btn = content.querySelector("#pl-draft-btn");
  if (!btn) return;
  btn.onclick = async () => {
    const desc = content.querySelector("#pl-draft-desc").value.trim();
    const msg = content.querySelector("#pl-draft-msg");
    if (desc.length < 10) { msg.textContent = "업무를 열 글자 이상으로 설명해 주세요."; return; }
    btn.disabled = true;
    msg.textContent = "AI가 초안을 만드는 중입니다(모델에 따라 1~2분)…";
    try {
      _plDraft = await api("POST", "/api/pipeline-templates/draft", { description: desc, example_output: content.querySelector("#pl-draft-example").value });
      msg.textContent = _plDraft.source === "llm" ? "초안이 나왔습니다. 고친 뒤 만드세요." : "";
      content.querySelector("#pl-draft-result").innerHTML = _plDraftResultHtml(_plDraft);
      _plBindDraftCreate(content);
    } catch (e) {
      msg.textContent = "초안을 만들지 못했습니다: " + (e.message || e);
    } finally { btn.disabled = false; }
  };
}

function _plBindDraftCreate(content) {
  const create = content.querySelector("#pl-draft-create");
  if (!create) return;
  create.onclick = async () => {
    const d = JSON.parse(JSON.stringify(_plDraft.draft));
    d.name = content.querySelector("#pl-dr-name").value.trim();
    d.domain = content.querySelector("#pl-dr-domain").value;
    content.querySelectorAll(".pl-draft-stage").forEach(li => {
      const s = d.stages[Number(li.dataset.i)];
      s.name = li.querySelector(".pl-dr-sname").value.trim();
      s.roles = Array.from(li.querySelectorAll(".pl-dr-role")).map(x => x.value);
      s.instructions = li.querySelector(".pl-dr-inst").value;
      s.requires_human_approval = li.querySelector(".pl-dr-human").checked;
    });
    const msg = content.querySelector("#pl-draft-create-msg");
    if (!d.name) { msg.textContent = "이름을 적어 주세요."; return; }
    if (d.stages.some(s => !s.name || s.roles.some(r => !r))) { msg.textContent = "모든 단계에 이름과 담당 역할을 정해 주세요."; return; }
    try {
      const r = await api("POST", "/api/pipeline-templates/from-draft", d);
      _plDraft = null;
      _plOpenTemplateId = String(r.id);
      showMsg("global-msg", "파이프라인을 만들었습니다 — 아래에서 단계를 더 다듬을 수 있습니다.", "ok");
      refresh();
    } catch (e) { msg.textContent = "만들지 못했습니다: " + (e.message || e); }
  };
}


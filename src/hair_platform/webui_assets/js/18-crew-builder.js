/* ---------- Crew 빌더(Agent 협업) ---------- */

// 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고, 에이전트
// 그룹(crew 구성)을 직관적으로 하기 위한... 개편") — 지금까지 실제
// 업무에 여러 Agent를 묶어 투입하려면 "① 팀부터 만들고(사전 등록) →
// ② 배정+승인 → ③ 협업 워크스페이스에서 실행"의 3단계를 거쳐야 했고,
// 그 전에 미리 조합을 테스트해보고 싶으면 결과가 저장되지 않는 별도
// 샌드박스(아래 `renderCrew`)로 가야 했다. 이 화면이 그 둘을 하나로
// 묶는다 — "업무에 바로 실행"(신규, 팀 없이 즉석 실행+실제 결과물
// 저장)과 "즉석 테스트"(기존 `renderCrew`, 변경 없이 그대로 위임) 두
// 모드를 탭으로 전환한다.
let _pendingCrewBuilderTaskId = null;

async function renderCrewBuilder(content) {
  const presetTaskId = _pendingCrewBuilderTaskId;
  _pendingCrewBuilderTaskId = null;
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  content.innerHTML =
    '<section class="card"><h2>Crew 빌더 — 여러 Agent를 묶어 실제로 협업시키기</h2>' +
    '<div class="actions">' +
    '<button class="small" id="crewbuilder-tab-task" type="button">업무에 바로 실행</button>' +
    '<button class="small" id="crewbuilder-tab-sandbox" type="button">즉석 테스트(저장 안 됨)</button>' +
    // 사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행 가시성")
    // — 로드맵 3단계. `GET /api/crew-runs`가 admin/manager 전용이라
    // 이 탭도 같은 권한으로만 보여준다.
    (isSupervisor ? '<button class="small" id="crewbuilder-tab-history" type="button">실행 이력</button>' : "") +
    '</div></section>' +
    '<div id="crewbuilder-body"></div>';
  const tabTask = document.getElementById("crewbuilder-tab-task");
  const tabSandbox = document.getElementById("crewbuilder-tab-sandbox");
  const tabHistory = document.getElementById("crewbuilder-tab-history");
  const body = document.getElementById("crewbuilder-body");
  const setActive = (mode) => {
    tabTask.classList.toggle("primary", mode === "task");
    tabSandbox.classList.toggle("primary", mode === "sandbox");
    if (tabHistory) tabHistory.classList.toggle("primary", mode === "history");
  };
  tabTask.onclick = () => { setActive("task"); _renderAdhocCrewComposer(body, null); };
  tabSandbox.onclick = () => { setActive("sandbox"); renderCrew(body); };
  if (tabHistory) tabHistory.onclick = () => { setActive("history"); _renderCrewHistory(body); };
  setActive("task");
  await _renderAdhocCrewComposer(body, presetTaskId);
}

// 사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행 가시성") —
// 지금까지 팀 Crew·즉석 Crew 실행은 끝나는 순간 협업 다이어그램·토큰
// 사용량·Gate 점수가 전부 사라졌다(admin 즉석 테스트 샌드박스에서만
// 실행 직후 한 번 보여주고 끝). `crew_history.py`가 영구 기록한
// 데이터를 그 샌드박스가 이미 쓰는 `renderCrewResult()`/
// `crewDiagramSvg()`에 그대로 먹여 똑같이 보여준다(새 렌더 로직 불필요
// — `crew_history.get_run()`이 그 두 함수가 기대하는 모양 그대로
// `agents`/`delegations`/`gate_scores`/`tcr`/`token_usage`/
// `final_output`을 돌려준다).
// 사용자 요청(2026-10-21, "Layer 2 — 실행 가시성 강화") — "실행
// 이력" 테이블과 실패 상세 화면 양쪽이 각자 `expected`(= manager가
// 있으면 agent_ids에서 매니저 제외)를 중복 계산하던 걸 하나로
// 합친다. 숫자 계산 자체는 기존과 동일 — 표시만 텍스트에서 진행
// 바로 바뀐다.
function crewRunExpectedCount(run) {
  const expected = run.manager_agent_id !== null
    ? run.agent_ids.length - 1 : run.agent_ids.length;
  return Math.max(expected, 1);
}

function crewRunProgressHtml(run) {
  const total = crewRunExpectedCount(run);
  const completed = run.status === "failed"
    ? run.completed_agent_ids.length : run.progress.length;
  const pct = Math.round((completed / total) * 100);
  return '<div class="progress-bar" style="width:80px;display:inline-block;vertical-align:middle;margin-right:0.4rem">' +
    '<div class="progress-bar-fill' + (run.status === "failed" ? " failed" : "") + '" style="width:' + pct + '%"></div>' +
    '</div>' + completed + '/' + total;
}

async function _renderCrewHistory(container, teamId) {
  // 사용자 요청(2026-10-29, 개선 계획 Phase 1 "Team 실행 이력 필터") —
  // teamId가 주어지면 그 팀의 실행만 좁혀 보여준다("팀 관리" 탭의
  // "실행 이력 보기"가 씀). 안 주면 기존처럼 전체(Crew 빌더의
  // "실행 이력" 탭).
  const runs = await api("GET", "/api/crew-runs" + (teamId ? "?team_id=" + teamId : ""));
  container.innerHTML =
    '<section class="card">' +
    '<p class="muted">팀 Agent 실행·즉석 실행 둘 다 여기 기록됩니다(즉석 테스트 샌드박스는 지금도 저장되지 않습니다). 진행 중인 실행은 "진행" 열에서 지금까지 끝난 Agent 수를 볼 수 있습니다 — 새로고침하면 갱신됩니다.</p>' +
    (runs.length
      ? table(["ID", "업무", "종류", "process", "상태", "진행", "시작", "동작"], runs.map(r => {
          const progressText = (r.status === "running" || r.status === "failed")
            ? crewRunProgressHtml(r)
            : "-";
          return [
            r.id,
            '<a href="#" data-goto-task="' + r.task_id + '">할 일 #' + r.task_id + '</a>',
            r.kind === "team" ? "팀(#" + r.team_id + ")" : "즉석",
            r.process,
            esc(label(r.status)),
            progressText,
            esc(fmtTime(r.created_at)),
            '<button class="small" data-view-crew-run="' + r.id + '">보기</button>',
          ];
        }))
      : '<p class="muted">아직 기록된 실행이 없습니다.</p>') +
    '</section>' +
    '<div id="crew-result"></div>';
  container.querySelectorAll("[data-goto-task]").forEach(a => {
    a.onclick = (ev) => {
      ev.preventDefault();
      _pendingCrewBuilderTaskId = null; // 다른 화면으로 이동 — 빌더 상태 아님
      selectTab("briefs");
      showMsg("global-msg", '"업무 요청" 탭에서 해당 브리프를 찾아 할 일 #' + a.dataset.gotoTask + "의 상세를 확인하세요.", "ok");
    };
  });
  container.querySelectorAll("[data-view-crew-run]").forEach(btn => {
    btn.onclick = async () => {
      try {
        const run = await api("GET", "/api/crew-runs/" + btn.dataset.viewCrewRun);
        if (run.status === "running") {
          showMsg("global-msg", "아직 실행 중입니다(" + run.progress.length + "건 완료) — 끝난 뒤 다시 보기를 눌러주세요.", "ok");
          return;
        }
        if (run.status === "failed") {
          _renderFailedCrewRun(document.getElementById("crew-result"), run, container);
          return;
        }
        renderCrewResult(run);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

// 사용자 요청(2026-10-20, "실패 시 일부 재시도" — Crew 빌더 로드맵
// 5단계) — 실패한 실행도 "아무 것도 안 남았다"가 아니라, 이미 완료된
// Agent 수와 남은 Agent 수를 보여주고 재시도 버튼을 제공한다.
function _renderFailedCrewRun(resultBox, run, historyContainer) {
  const completedCount = run.completed_agent_ids.length;
  const remaining = crewRunExpectedCount(run) - completedCount;
  resultBox.innerHTML =
    '<section class="card">' +
    '<p class="msg error">이 실행은 실패했습니다: ' + esc(run.error || "원인 미상") + '</p>' +
    '<p class="muted">' + crewRunProgressHtml(run) + '명 완료 — ' +
    (completedCount > 0 ? '완료된 Agent의 결과물은 이미 이 업무의 결과물로 저장됐습니다.' : '아무도 완료하지 못했습니다.') +
    '</p>' +
    (remaining > 0
      ? '<button class="small" id="crew-run-retry-remaining">실패한 Agent만 재시도(' + remaining + '명)</button>'
      : "") +
    '</section>';
  const retryBtn = document.getElementById("crew-run-retry-remaining");
  if (!retryBtn) return;
  retryBtn.onclick = async () => {
    try {
      const queued = await withBusy(retryBtn, "재시도 중…(시간이 걸릴 수 있습니다)", () =>
        api("POST", "/api/crew-runs/" + run.id + "/retry-remaining", {}));
      const jobs = await Promise.all(queued.job_ids.map(id => pollExecutionJob(run.task_id, id)));
      if (jobs.every(j => j.status === "succeeded")) {
        showMsg("global-msg", "재시도가 완료됐습니다(" + jobs.length + "건) — 이 업무의 결과물로 쌓였습니다.", "ok");
      } else {
        const failed = jobs.filter(j => j.status === "failed").length;
        showMsg("global-msg", "재시도 중 " + failed + "건이 실패했습니다 — 실행 이력에서 다시 확인하세요.", "error");
      }
      await _renderCrewHistory(historyContainer);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };
}

// 팀 사전 등록 없이, Task 하나를 고르고 → Agent를 즉석으로 골라 →
// 바로 실행한다. 결과는 "팀 Agent 실행"과 똑같이 그 Task의
// deliverable+결재 체인으로 쌓인다(`POST .../execute-adhoc-crew`,
// `deliverables.start_adhoc_crew_execution()` 참고) — 다른 점은 팀이라는
// 전제조건이 없고, Agent별로 서로 다른 지시를 줄 수 있다는 것뿐이다.
async function _renderAdhocCrewComposer(container, presetTaskId) {
  const groups = await _fetchAllTasksGroupedByBrief();
  const hasTasks = groups.some(g => g.tasks.length);
  container.innerHTML =
    '<section class="card">' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>팀을 미리 등록·배정·승인하지 않아도, 이 업무의 참가자라면 Agent를 즉석으로 골라 바로 실행할 수 있습니다 — 결과는 이 업무의 결과물+결재 체인으로 그대로 쌓입니다.</li>' +
      '<li>Agent별로 서로 다른 지시를 줄 수 있습니다("팀 Agent 실행"은 참여 전원에게 같은 지시를 줍니다).</li>' +
      '<li>온보딩(active) 안 된 Agent는 선택할 수 없습니다.</li>' +
      '<li>실제 LLM API를 호출하므로 비용이 발생합니다(참여 Agent 수만큼 커질 수 있음).</li>' +
      '</ul>'
    ) +
    (hasTasks
      ? '<select id="crewbuilder-task-id" style="min-width:280px"><option value="">— 할 일 선택 —</option>' +
        _taskPickerOptionsHtml(groups) + '</select>'
      : '<p class="msg error">아직 할 일이 없습니다 — "업무 요청" 탭에서 먼저 만드세요.</p>') +
    '</section>' +
    '<div id="crewbuilder-task-form"></div>';
  const picker = document.getElementById("crewbuilder-task-id");
  if (!picker) return;
  const formBox = document.getElementById("crewbuilder-task-form");
  const renderForm = async () => {
    const taskId = Number(picker.value);
    if (!taskId) { formBox.innerHTML = ""; return; }
    await _renderAdhocCrewForm(formBox, taskId);
  };
  picker.addEventListener("change", renderForm);
  if (presetTaskId) {
    picker.value = String(presetTaskId);
    await renderForm();
  }
}

// 사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움") — 추천 Agent가
// 있으면 빈 행 1개 대신 추천된 조합으로 미리 채운 행 N개로 시작한다.
// 역할명을 그대로 쓴 기본 지시문을 붙여주되 자유롭게 수정할 수 있다.
async function _suggestedCrewRowsHtml(taskId, active, task) {
  const suggestions = await api("GET", "/api/tasks/" + taskId + "/suggested-agents").catch(() => []);
  if (!suggestions.length) return { html: crewRowHtml(active), suggested: false };
  const html = suggestions.map(s =>
    crewRowHtml(active, s.id, "'" + task.role_name + "' 업무를 수행하세요.")
  ).join("");
  return { html, suggested: true };
}

async function _renderAdhocCrewForm(formBox, taskId) {
  const agents = await api("GET", "/api/agent-pool");
  const active = agents.filter(a => a.status === "active");
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = isSupervisor ? await fetchModelsByProvider() : {};
  const task = await api("GET", "/api/tasks/" + taskId);
  const { html: rowsHtml, suggested } = await _suggestedCrewRowsHtml(taskId, active, task);
  formBox.innerHTML = active.length
    ? '<section class="card">' +
      (suggested
        ? '<p class="muted">이 업무에 적합해 보이는 Agent 조합을 미리 채웠습니다 — 자유롭게 수정/삭제/추가하세요. ' +
          '<button type="button" class="small" id="crewbuilder-adhoc-clear-suggestion">추천 비우고 새로 시작</button></p>'
        : "") +
      '<form id="crewbuilder-adhoc-form">' +
      '<div id="crewbuilder-adhoc-rows">' + rowsHtml + '</div>' +
      '<button type="button" class="small" id="crewbuilder-adhoc-add-row">+ Agent 추가</button>' +
      '<div class="inline" style="margin-top:0.5rem">' +
      '<select id="crewbuilder-adhoc-process"><option value="sequential">sequential(순서대로 전달)</option>' +
      '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
      '<select id="crewbuilder-adhoc-manager"><option value="">매니저 자동 배정(hierarchical 전용)</option>' +
      crewAgentOptionsHtml(active) + '</select>' +
      (isSupervisor
        ? '<select id="crewbuilder-adhoc-provider"><option value="">provider 자동 판별</option>' +
          '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
          '<option value="ollama">Ollama(로컬)</option></select>' + modelSelectHtml("crewbuilder-adhoc-model")
        : '<span class="muted">provider/model은 매니저·관리자만 선택할 수 있습니다(기본값 사용).</span>') +
      '<button class="primary" type="submit">이 업무에 바로 실행(비용 발생)</button>' +
      '</div>' + crewAdvancedOptionsHtml("crewbuilder-adhoc", isSupervisor, true) +
      '</form><div id="crewbuilder-adhoc-promotion"></div></section>'
    : '<p class="muted">온보딩된(활성) 에이전트가 없습니다 — "AI 에이전트 생성"·"AI 에이전트 관리" 탭에서 먼저 준비하세요.</p>';
  if (!active.length) return;
  if (isSupervisor) wireProviderModelSelect("crewbuilder-adhoc-provider", "crewbuilder-adhoc-model", modelsByProvider);
  wireCrewAdvancedOptions("crewbuilder-adhoc", modelsByProvider, isSupervisor);
  wireCrewRowRemovers();
  wireCrewProcessToggle("crewbuilder-adhoc-process", "crewbuilder-adhoc-rows", "crewbuilder-adhoc-self-review");
  document.getElementById("crewbuilder-adhoc-add-row").onclick = () => {
    document.getElementById("crewbuilder-adhoc-rows").insertAdjacentHTML("beforeend", crewRowHtml(active));
    wireCrewRowRemovers();
    wireCrewProcessToggle("crewbuilder-adhoc-process", "crewbuilder-adhoc-rows", "crewbuilder-adhoc-self-review");
  };
  const clearBtn = document.getElementById("crewbuilder-adhoc-clear-suggestion");
  if (clearBtn) {
    clearBtn.onclick = () => {
      document.getElementById("crewbuilder-adhoc-rows").innerHTML = crewRowHtml(active);
      wireCrewRowRemovers();
      wireCrewProcessToggle("crewbuilder-adhoc-process", "crewbuilder-adhoc-rows", "crewbuilder-adhoc-self-review");
      clearBtn.closest("p").remove();
    };
  }
  document.getElementById("crewbuilder-adhoc-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showMsg("global-msg", "", "");
    const rows = Array.from(formBox.querySelectorAll(".crew-row"));
    const agentIds = [];
    const taskDescriptions = [];
    const parallelAgentIds = [];
    for (const row of rows) {
      const agentId = Number(row.querySelector(".crew-row-agent").value);
      const taskDesc = row.querySelector(".crew-row-task").value.trim();
      if (!agentId) { showMsg("global-msg", "모든 줄에서 Agent를 선택하세요.", "error"); return; }
      if (!taskDesc) { showMsg("global-msg", "모든 줄에서 할 일을 입력하세요.", "error"); return; }
      agentIds.push(agentId);
      taskDescriptions.push(taskDesc);
      if (row.querySelector(".crew-row-parallel").checked) parallelAgentIds.push(agentId);
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 Agent는 선택해야 합니다.", "error"); return; }
    if (!(await confirmModal(agentIds.length + "개 Agent로 이 업무에 바로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?"))) return;
    const managerVal = document.getElementById("crewbuilder-adhoc-manager").value;
    const payload = Object.assign({
      agent_ids: agentIds,
      task_descriptions: taskDescriptions,
      process: document.getElementById("crewbuilder-adhoc-process").value,
      manager_agent_id: managerVal ? Number(managerVal) : null,
      parallel_agent_ids: parallelAgentIds,
    }, crewAdvancedOptionsPayload("crewbuilder-adhoc", isSupervisor, true));
    if (isSupervisor) {
      const provider = document.getElementById("crewbuilder-adhoc-provider").value;
      const model = modelSelectValue("crewbuilder-adhoc-model");
      if (provider) payload.provider = provider;
      if (model) payload.model = model;
    }
    const submitBtn = ev.submitter || formBox.querySelector("#crewbuilder-adhoc-form button[type=submit]");
    try {
      const queued = await withBusy(submitBtn, "실행 중…(시간이 걸릴 수 있습니다)", () =>
        api("POST", "/api/tasks/" + taskId + "/execute-adhoc-crew", payload));
      const jobs = await Promise.all(queued.job_ids.map(id => pollExecutionJob(taskId, id)));
      if (jobs.every(j => j.status === "succeeded")) {
        showMsg("global-msg", "Crew 실행이 완료됐습니다(" + jobs.length + "건) — 이 업무의 결과물로 쌓였습니다.", "ok");
        // 사용자 요청(2026-10-20, "Crew 빌더... 이 조합을 저장") — 잘
        // 돌아간 조합을 매번 처음부터 다시 고르지 않도록, 방금 실행한
        // Agent 구성 그대로 팀/파이프라인 단계로 저장할 수 있게 한다.
        await _renderCrewPromotionPanel(
          document.getElementById("crewbuilder-adhoc-promotion"),
          { agentIds, taskDescriptions, process: payload.process, managerAgentId: payload.manager_agent_id, active }
        );
      } else {
        const failed = jobs.filter(j => j.status === "failed").length;
        showMsg("global-msg", "Crew 실행 중 " + failed + "건이 실패했습니다 — 그 업무의 워크스페이스에서 진행 이력을 확인하세요.", "error");
      }
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// 사용자 요청(2026-10-20, "Crew 빌더... 이 조합을 저장") — 로드맵
// 2단계. 방금 실행한 Agent 구성(+process/매니저)을 팀 또는 파이프라인
// 단계로 승격한다 — 새 백엔드 없이 기존 API만 조합한다(팀: `teams.
// create_team`+`add_team_member`+`update_team_defaults`, 파이프라인:
// `pipeline_runtime.create_pipeline_template`+`add_stage`).
async function _renderCrewPromotionPanel(container, { agentIds, taskDescriptions, process, managerAgentId, active }) {
  // 팀/파이프라인 등록(POST /api/teams, /api/pipeline-templates 등)은
  // 전부 admin/manager 전용 엔드포인트다 — member는 애초에 저장할
  // 수단이 없으므로 패널 자체를 안 보여준다(눌러보고서야 403을 받는
  // 것보다 처음부터 안 보이는 쪽이 정직하다).
  if (!["admin", "manager"].includes(ME.role)) { container.innerHTML = ""; return; }
  const roleNameById = {};
  active.forEach(a => { roleNameById[a.id] = a.role_name; });
  const templates = await api("GET", "/api/pipeline-templates").catch(() => []);
  container.innerHTML =
    '<section class="card" style="margin-top:0.5rem"><h4>이 조합을 저장해 다음에 재사용</h4>' +
    '<form class="inline" id="crewbuilder-save-team-form">' +
    '<input id="crewbuilder-save-team-name" placeholder="팀 이름" required style="min-width:160px">' +
    '<button class="small" type="submit">이 조합을 팀으로 저장</button>' +
    '</form>' +
    '<form class="inline" id="crewbuilder-save-pipeline-form" style="margin-top:0.4rem;flex-wrap:wrap">' +
    '<select id="crewbuilder-save-pipeline-target">' +
    '<option value="">— 새 파이프라인 만들기 —</option>' +
    templates.map(t => '<option value="' + t.id + '">' + esc(t.name) + " (" + t.stage_count + "단계)</option>").join("") +
    '</select>' +
    '<input id="crewbuilder-save-pipeline-new-name" placeholder="새 파이프라인 이름" style="min-width:140px">' +
    '<input id="crewbuilder-save-pipeline-stage-name" placeholder="단계 이름" required style="min-width:140px">' +
    '<button class="small" type="submit">이 구성을 파이프라인 단계로 저장</button>' +
    '</form>' +
    (process === "hierarchical" && !managerAgentId
      ? '<p class="muted">매니저를 자동 배정으로 두고 실행했습니다 — 파이프라인 단계(계획 분담)는 리드가 될 매니저를 반드시 지정해야 해서, 이 조합은 파이프라인으로 저장할 수 없습니다. 매니저를 직접 골라 다시 실행하면 저장할 수 있습니다.</p>'
      : "") +
    "</section>";

  document.getElementById("crewbuilder-save-team-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = document.getElementById("crewbuilder-save-team-name").value.trim();
    if (!name) return;
    try {
      const team = await api("POST", "/api/teams", { name });
      for (const agentId of agentIds) {
        await api("POST", "/api/teams/" + team.id + "/members", { member_type: "agent", member_id: agentId });
      }
      await api("PATCH", "/api/teams/" + team.id, {
        default_process: process,
        default_manager_agent_id: managerAgentId || null,
      });
      showMsg("global-msg", '팀 "' + esc(name) + '"으로 저장했습니다 — "팀 관리" 탭에서 확인·배정할 수 있습니다.', "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const pipelineForm = document.getElementById("crewbuilder-save-pipeline-form");
  if (!pipelineForm) return;
  const targetSelect = document.getElementById("crewbuilder-save-pipeline-target");
  const newNameInput = document.getElementById("crewbuilder-save-pipeline-new-name");
  const syncNewNameVisibility = () => { newNameInput.style.display = targetSelect.value ? "none" : ""; };
  targetSelect.addEventListener("change", syncNewNameVisibility);
  syncNewNameVisibility();

  pipelineForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (process === "hierarchical" && !managerAgentId) {
      showMsg("global-msg", "매니저를 지정하지 않고 실행한 조합은 파이프라인 단계로 저장할 수 없습니다(계획 분담은 리드가 될 매니저가 필수).", "error");
      return;
    }
    const stageName = document.getElementById("crewbuilder-save-pipeline-stage-name").value.trim();
    if (!stageName) return;
    try {
      let templateId = Number(targetSelect.value) || null;
      if (!templateId) {
        const newName = newNameInput.value.trim();
        if (!newName) { showMsg("global-msg", "새 파이프라인 이름을 입력하세요.", "error"); return; }
        const tpl = await api("POST", "/api/pipeline-templates", { name: newName });
        templateId = tpl.id;
      }
      // 위임형(hierarchical) Crew는 계획 분담 단계로 저장한다(2026-11-26 — 병행 위임 모드 제거, 매니저가 리드).
      const mode = agentIds.length === 1 ? "single" : (process === "hierarchical" ? "plan_merge" : "sequential_relay");
      const stageAgents = agentIds.map((id, i) => ({
        role_name: roleNameById[id],
        instructions: taskDescriptions[i] || "",
      }));
      const body = { name: stageName, mode, agents: stageAgents };
      if (mode === "plan_merge") body.lead_role_name = roleNameById[managerAgentId];
      await api("POST", "/api/pipeline-templates/" + templateId + "/stages", body);
      showMsg("global-msg", '파이프라인 단계 "' + esc(stageName) + '"로 저장했습니다 — "파이프라인 관리" 탭에서 확인·조정할 수 있습니다.', "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// 사용자 보고(2026-10-06, "crew 실행 UI/UX를 개선해줘 — 에이전트 ID를
// 알 수 없음 등") — 이전엔 위 표에서 ID를 직접 읽어 textarea에
// "1: 할 일" 식으로 손으로 옮겨 적어야 했다(오타·형식 실수 위험, 이
// 세션에서 반복 고친 "직접 입력 대신 실제 데이터에서 선택" 패턴과 같은
// 문제). Agent별로 드롭다운+할 일 입력 행을 두고 "+ Agent 추가"로 늘리는
// 방식으로 바꾼다 — 드롭다운 자체가 "#id 역할명(스킬)"을 보여줘 ID를
// 몰라도 된다.
// 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... AI 에이전트
// 지정 항목이나 호출시에도 활용될 수 있도록") — Agent를 고르는 모든
// 드롭다운/라벨이 지정한 이름을 역할명보다 우선 보여주도록 한곳에
// 모은다(지정 안 했으면 역할명 그대로 — 호출부 변경 없이 자동 적용).
function agentDisplayLabel(a) {
  return a.name ? esc(a.name) + " (" + esc(a.role_name) + ")" : esc(a.role_name);
}

// 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고..." — Crew
// 빌더 로드맵 4단계, "Crew 레벨 고급 옵션") — 세 Crew 실행 폼(즉석
// 테스트/업무에 바로 실행/팀 Agent 실행)이 전부 같은 고급 설정(메모리·
// planning·매니저 전용 LLM)을 쓰므로 한곳에 모아 중복 없이 재사용한다.
// `memory`는 비용을 늘리는 방향이 아니라(OPENAI_API_KEY 없이 쓰기 위한
// 방어적 toggle) 모두에게 열어두고, `planning`/매니저 전용 LLM은
// provider/model과 같은 이유로 admin/manager 전용이다(백엔드도 똑같이
// 막는다 — `app.py`의 "팀원은... 직접 선택할 수 없습니다" 검사 참고).
// `enableSelfReview`(사용자 요청, 2026-11-04, "Agent 자가비평" 설계) —
// `self_review`는 `AdhocCrewExecuteRequest`에만 있는 필드라(team-crew·
// 샌드박스는 지원 안 함), 이 공유 헬퍼 3개에 opt-in 파라미터로만
// 추가한다 — 호출부가 안 넘기면(undefined, falsy) 기존 두 호출부는
// 렌더링·payload 모두 한 글자도 안 바뀐다.
function crewAdvancedOptionsHtml(prefix, isSupervisor, enableSelfReview) {
  const supervisorFields = isSupervisor
    ? '<div class="inline" style="margin-top:0.3rem">' +
      '<label><input type="checkbox" id="' + prefix + '-planning"> 사전 계획(planning, 추가 LLM 호출 비용 발생)</label>' +
      '</div>' +
      '<div class="inline" style="margin-top:0.3rem">' +
      '<span class="muted">매니저 전용 LLM(매니저 자동 배정 시에만 적용) — </span>' +
      '<select id="' + prefix + '-manager-provider"><option value="">워커와 동일</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      modelSelectHtml(prefix + "-manager-model") +
      "</div>"
    : "";
  // self_review는 provider/model 같은 비용 선택권이 없는 단순 토글이라
  // supervisorFields(관리자 전용)가 아니라 항상 보이는 블록에 둔다 —
  // member도 설정할 수 있어야 한다(백엔드 RBAC과 일치).
  const selfReviewField = enableSelfReview
    ? '<div class="inline" style="margin-top:0.3rem">' +
      '<label><input type="checkbox" id="' + prefix + '-self-review"> 자기 검토(완료 후 같은 Agent가 자신의 초안을 요구사항과 대조해 한 번 더 수정 — sequential·비병렬 전용)</label>' +
      '</div>'
    : "";
  return '<details class="muted" style="margin-top:0.4rem">' +
    "<summary>고급 설정(메모리" + (isSupervisor ? "·사전 계획·매니저 전용 LLM" : "") + ")</summary>" +
    '<div class="inline" style="margin-top:0.3rem">' +
    '<label><input type="checkbox" id="' + prefix + '-memory" checked> 메모리 사용(끄면 provider가 openai가 아니어도 OPENAI_API_KEY 없이 실행할 수 있습니다)</label>' +
    "</div>" +
    selfReviewField +
    supervisorFields +
    "</details>";
}

function wireCrewAdvancedOptions(prefix, modelsByProvider, isSupervisor) {
  if (isSupervisor) {
    wireProviderModelSelect(prefix + "-manager-provider", prefix + "-manager-model", modelsByProvider);
  }
}

function crewAdvancedOptionsPayload(prefix, isSupervisor, enableSelfReview) {
  const payload = { memory: document.getElementById(prefix + "-memory").checked };
  if (isSupervisor) {
    payload.planning = document.getElementById(prefix + "-planning").checked;
    const managerProvider = document.getElementById(prefix + "-manager-provider").value;
    const managerModel = modelSelectValue(prefix + "-manager-model");
    if (managerProvider) payload.manager_provider = managerProvider;
    if (managerModel) payload.manager_model = managerModel;
  }
  if (enableSelfReview) {
    payload.self_review = document.getElementById(prefix + "-self-review").checked;
  }
  return payload;
}

function crewAgentOptionsHtml(active) {
  // 사용자 요청(2026-10-27, "Crew 빌더 - 즉석 테스트... ID 대신 agent
  // code가, 추가로 에이전트명 표시되도록") — 드롭다운 라벨의 "#id"도
  // 같은 화면에서 노출되는 정수 PK라 코드로 바꾼다(value는 그대로 id —
  // 제출 payload는 안 바뀜).
  return active.map(a =>
    '<option value="' + a.id + '">' + esc(a.code || ("#" + a.id)) + " " + agentDisplayLabel(a) +
    (a.skill_tags.length ? " (" + esc(a.skill_tags.join(", ")) + ")" : "") + "</option>"
  ).join("");
}

// 사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움" — Crew 빌더 로드맵
// 5단계) — `presetAgentId`/`presetTaskDesc`가 주어지면(추천 결과로
// 채울 때) 행이 그 값으로 미리 선택/입력된 채로 시작한다. 생략하면
// 기존처럼 빈 행(수동 흐름 그대로 보존).
//
// 사용자 요청(2026-10-20, "독립 작업 병렬 실행") — 체크박스 1개 추가.
// `process="sequential"`에서만 의미가 있어 hierarchical로 바뀌면
// `wireCrewProcessToggle()`이 비활성화한다(아래 참고).
function crewRowHtml(active, presetAgentId, presetTaskDesc) {
  const agentOptions = active.map(a =>
    '<option value="' + a.id + '"' + (presetAgentId === a.id ? " selected" : "") + '>' + esc(a.code || ("#" + a.id)) + " " +
    agentDisplayLabel(a) + (a.skill_tags.length ? " (" + esc(a.skill_tags.join(", ")) + ")" : "") + "</option>"
  ).join("");
  return '<div class="inline crew-row" style="margin-bottom:0.3rem">' +
    '<select class="crew-row-agent" style="min-width:220px"><option value="">— Agent 선택 —</option>' +
    agentOptions + "</select>" +
    '<input class="crew-row-task" placeholder="이 Agent가 할 일" style="flex:1;min-width:200px"' +
    (presetTaskDesc ? ' value="' + escAttr(presetTaskDesc) + '"' : "") + ">" +
    '<label class="muted" style="white-space:nowrap"><input type="checkbox" class="crew-row-parallel"> 병렬(독립 작업)</label>' +
    '<button type="button" class="small danger crew-row-remove">×</button>' +
    "</div>";
}

// hierarchical(매니저 조율)에서는 "병렬" 지정이 적용될 지점이 없다
// (`crew_runtime.run_crew()` 참고) — process select가 바뀔 때마다
// 체크박스를 비활성화/활성화해 혼동을 막는다.
// `selfReviewCheckboxId`(사용자 요청, 2026-11-04, "Agent 자가비평" 설계) —
// 백엔드가 `self_review`를 `process='sequential'`이고 어느 행도 "병렬"
// 체크 안 돼 있을 때만 허용하므로(`app.py`의 execute-adhoc-crew 400
// 체크와 동일), 그 조건이 깨지면 체크박스를 미리 끄고 비활성화해
// 사용자가 400을 직접 맞닥뜨리기 전에 UI에서 막는다. 생략하면
// (team-crew/샌드박스 호출부) 기존 동작과 동일.
function wireCrewProcessToggle(processSelectId, rowsContainerId, selfReviewCheckboxId) {
  const select = document.getElementById(processSelectId);
  const container = document.getElementById(rowsContainerId);
  if (!select || !container) return;
  const selfReviewCb = selfReviewCheckboxId ? document.getElementById(selfReviewCheckboxId) : null;
  const sync = () => {
    const isSequential = select.value === "sequential";
    container.querySelectorAll(".crew-row-parallel").forEach(cb => {
      cb.disabled = !isSequential;
      if (!isSequential) cb.checked = false;
    });
    if (selfReviewCb) {
      const anyParallel = !!container.querySelector(".crew-row-parallel:checked");
      selfReviewCb.disabled = !isSequential || anyParallel;
      if (selfReviewCb.disabled) selfReviewCb.checked = false;
    }
  };
  select.addEventListener("change", sync);
  if (selfReviewCb) container.addEventListener("change", sync);
  sync();
}

function wireCrewRowRemovers() {
  document.querySelectorAll(".crew-row-remove").forEach(btn => {
    btn.onclick = () => {
      const rows = document.querySelectorAll(".crew-row");
      if (rows.length <= 1) { showMsg("global-msg", "최소 한 Agent는 있어야 합니다.", "error"); return; }
      btn.closest(".crew-row").remove();
    };
  });
}

async function renderCrew(content) {
  const agents = await api("GET", "/api/agent-pool");
  const active = agents.filter(a => a.status === "active");
  const modelsByProvider = await fetchModelsByProvider();
  content.innerHTML =
    '<section class="card"><h2>Crew 실행(즉석 테스트) — 여러 Agent를 묶어 실제로 협업시키기</h2>' +
    '<p class="muted"><b>이 화면은 업무(할 일)와 연결되지 않는 즉석 실험용입니다</b> — 결과가 어떤 할 일의 결과물로도 저장되지 않고, 끝나면 그걸로 끝입니다.</p>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>실제 업무에 여러 Agent를 역할별로 묶어 투입하려면: ① "AI 에이전트 생성"에서 한 브리프의 여러 할 일을 같이 선택해 역할별 Agent를 만들고 그 결과 화면에서 "팀으로 묶기"를 누르거나, 이미 있는 Agent들을 "팀 관리"에서 팀으로 등록한 뒤 ② "업무 요청"의 할 일 상세에서 그 팀을 배정 → 결재까지 마치면 ③ 협업 워크스페이스의 "팀 Agent 실행(Crew)" 버튼으로 실행하세요 — 그 경로는 결과가 그 할 일의 결과물+결재 체인으로 그대로 쌓입니다.</li>' +
      '<li>아래는 그 전에 조합·프롬프트를 미리 테스트해보고 싶을 때만 쓰세요.</li>' +
      '<li>Agent를 드롭다운으로 고르고 그 옆에 할 일을 입력하세요 — "+ Agent 추가"로 참여 인원을 늘릴 수 있습니다.</li>' +
      '<li><b>sequential</b>이면 앞줄 에이전트의 산출물이 다음 에이전트에게 자동으로 전달됩니다(순서대로 릴레이).</li>' +
      '<li><b>sequential</b>에서 Agent 행의 <b>"병렬(독립 작업)"</b>을 체크하면 그 Agent는 앞 Agent 결과를 기다리지 않고 동시에 시작합니다(앞 사람 결과를 받지 않는 독립 작업에만 쓰세요). hierarchical에서는 체크할 수 없습니다.</li>' +
    '<li><b>hierarchical</b>은 "매니저 Agent"를 지정하면 그 매니저가 작업을 조율·위임만 하고 직접 산출물을 내지는 않습니다(비워두면 CrewAI가 자동으로 매니저 LLM을 하나 배정) — 아래 결과의 "Agent별 산출물"에서 매니저 행은 항상 "(없음)"으로 나옵니다. 매니저가 실제로 위임하지 않고 끝나는 경우가 많아("실제 위임/협업 발생 내역"이 0건) sequential을 권장합니다.</li>' +
      '<li>모든 Agent는 위임(allow_delegation)이 켜져 있어 필요하면 서로에게 실제로 질문·재위임할 수 있고, 그 내역이 "실제 위임/협업 발생 내역"에 기록됩니다.</li>' +
      '<li>온보딩(active) 안 된 Agent는 드롭다운에 안 뜨고 지정할 수도 없습니다.</li>' +
      '<li>실제 LLM API를 호출하므로 비용이 발생합니다(참여 Agent 수만큼 커질 수 있음).</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "Crew 빌더 - 즉석 테스트에서 테이블에 ID
    // 표시됨, ID 대신 agent code가, 추가로 에이전트명 표시되도록") —
    // "AI 에이전트 관리" 테이블과 같은 표기(코드+이름)로 맞춘다. 정수
    // id는 드롭다운(`crewAgentOptionsHtml`)에서 여전히 내부적으로
    // 쓰이므로 기능은 안 바뀐다.
    (active.length
      ? table(["코드", "이름", "역할", "스킬"], active.map(a => [
          esc(a.code || "-"),
          a.name ? esc(a.name) : '<span class="muted">(미지정)</span>',
          esc(a.role_name), esc(a.skill_tags.join(", ")),
        ]))
      : '<p class="muted">온보딩된(활성) 에이전트가 없습니다 — 먼저 "AI 에이전트 생성"·"AI 에이전트 관리" 탭에서 준비하세요.</p>') +
    '<form id="crew-form">' +
    '<div id="crew-rows">' + (active.length ? crewRowHtml(active) : "") + "</div>" +
    (active.length ? '<button type="button" class="small" id="crew-add-row">+ Agent 추가</button>' : "") +
    '<div class="inline" style="margin-top:0.5rem">' +
    '<select id="crew-process"><option value="sequential">sequential(순서대로 전달)</option>' +
    '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
    '<select id="crew-manager"><option value="">매니저 자동 배정(hierarchical 전용)</option>' +
    crewAgentOptionsHtml(active) + "</select>" +
    '<select id="crew-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    modelSelectHtml("crew-model") +
    '<button class="primary" type="submit"' + (active.length ? "" : " disabled") + '>Crew 실행(비용 발생)</button>' +
    "</div></form>" +
    crewAdvancedOptionsHtml("crew", true) +
    '<div id="crew-result"></div></section>';

  wireProviderModelSelect("crew-provider", "crew-model", modelsByProvider);
  wireCrewAdvancedOptions("crew", modelsByProvider, true);
  wireCrewRowRemovers();
  wireCrewProcessToggle("crew-process", "crew-rows");
  const addRowBtn = document.getElementById("crew-add-row");
  if (addRowBtn) {
    addRowBtn.onclick = () => {
      document.getElementById("crew-rows").insertAdjacentHTML("beforeend", crewRowHtml(active));
      wireCrewRowRemovers();
      wireCrewProcessToggle("crew-process", "crew-rows");
    };
  }

  document.getElementById("crew-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showMsg("global-msg", "", "");  // 이전 시도의 에러 배너가 남아 새 결과와 헷갈리지 않도록 지운다
    const rows = Array.from(document.querySelectorAll(".crew-row"));
    const agentIds = [];
    const taskDescriptions = [];
    const parallelAgentIds = [];
    for (const row of rows) {
      const agentId = Number(row.querySelector(".crew-row-agent").value);
      const taskDesc = row.querySelector(".crew-row-task").value.trim();
      if (!agentId) { showMsg("global-msg", "모든 줄에서 Agent를 선택하세요.", "error"); return; }
      if (!taskDesc) { showMsg("global-msg", "모든 줄에서 할 일을 입력하세요.", "error"); return; }
      agentIds.push(agentId);
      taskDescriptions.push(taskDesc);
      if (row.querySelector(".crew-row-parallel").checked) parallelAgentIds.push(agentId);
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 Agent는 선택해야 합니다.", "error"); return; }
    const managerVal = document.getElementById("crew-manager").value;
    if (!(await confirmModal(agentIds.length + "개 Agent로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?"))) return;

    const submitBtn = ev.submitter || document.querySelector("#crew-form button[type=submit]");
    try {
      const result = await withBusy(submitBtn, "실행 중…(시간이 걸릴 수 있습니다)", () => api("POST", "/api/crew/run", Object.assign({
        agent_ids: agentIds,
        task_descriptions: taskDescriptions,
        process: document.getElementById("crew-process").value,
        manager_agent_id: managerVal ? Number(managerVal) : null,
        provider: document.getElementById("crew-provider").value || null,
        model: modelSelectValue("crew-model"),
        parallel_agent_ids: parallelAgentIds,
      }, crewAdvancedOptionsPayload("crew", true))));
      renderCrewResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

/* 사용자 요청(2026-10-06, "crew 실행 UI/UX를 개선해줘 — 묶인 Agent간
   협업 관계를 다이어그램으로 표시") — 지금까지는 "Agent별 산출물"·"위임
   내역" 표만 있어, 몇 명이 어떤 구조(순차 릴레이 vs 매니저 조율)로
   묶였는지, 실제로 누가 누구에게 위임했는지를 표를 읽어 머릿속으로
   재구성해야 했다. 실선(회색)은 요청 시점에 이미 정해진 구조(sequential
   순서 전달 / hierarchical 조율 관계) — 항상 그린다. 점선(주황)은
   `result.delegations`에 실제로 기록된 위임만 그린다(추측 금지,
   "실제 발동 증거를 남긴다" 원칙과 같은 정신) — 대상이
   확인되지 않으면(알 수 없는 동료를 호출해 CrewAI가 에러를 반환한
   경우 등) 화살표 대신 아래 "확인 안 된 위임" 목록에 원문을 남긴다.
   외부 차트 라이브러리 없이 순수 inline SVG로 그린다(이 앱의 기존
   무의존성 원칙과 동일). */
// 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — crewDiagramSvg()
// 의 노드 좌표 계산(구조선 — 실행 결과와 무관하게 process+매니저+멤버
// 목록만으로 계산 가능한 부분)을 떼어내 "팀 관리" 화면의 실행 전
// 미리보기 캔버스와 공유한다. `agentList`는 `role_name`(필수, 위치를
// 찾는 키 — CrewAI가 실제로 보고하는 coworker 문자열과 일치해야 해서
// 건드리면 안 됨)을 가진 객체 배열. `managerMember`는 `agentList`의
// 원소 중 하나(명시적 매니저) 또는 null/undefined(hierarchical인데
// 매니저 미지정 — CrewAI 자동 배정, 합성 노드로 표시). 이 함수는
// 순수 좌표 계산만 하고 SVG 문자열은 만들지 않는다 — 호출자(아래
// crewDiagramSvg, 새 팀 캔버스)가 각자 자신에게 맞는 방식(raw SVG
// `<line>` vs Drawflow `addConnection()`)으로 그린다.
function _crewStructuralLayout(agentList, process, managerMember) {
  const isHierarchical = process === "hierarchical";
  const nodeW = 150, nodeH = 44, gapX = 190;
  const positions = {};
  let managerNode = null;
  let workers = agentList;
  let svgWidth, svgHeight;

  if (isHierarchical) {
    workers = agentList.filter(a => a !== managerMember);
    svgWidth = Math.max(workers.length, 1) * gapX + 20;
    svgHeight = 170;
    managerNode = managerMember || { role_name: "매니저(LLM 자동 배정)", synthetic: true };
    positions[managerNode.role_name] = { x: svgWidth / 2 - nodeW / 2, y: 16 };
    workers.forEach((a, i) => { positions[a.role_name] = { x: i * gapX + 20, y: 112 }; });
  } else {
    svgWidth = Math.max(agentList.length, 1) * gapX + 20;
    svgHeight = 90;
    agentList.forEach((a, i) => { positions[a.role_name] = { x: i * gapX + 20, y: 23 }; });
  }
  return { positions, svgWidth, svgHeight, nodeW, nodeH, gapX, isHierarchical, managerNode, workers };
}

// 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — "팀 관리"
// 화면에서 `_crewStructuralLayout()`이 계산한 좌표를 Drawflow 캔버스에
// 그린다. 캔버스는 서버 상태(process/매니저/순서)의 투영이다 — 연결선은
// 항상 자동으로 그려지고, 사용자가 직접 선을 긋거나 지우지 않는다.
// 사용자가 하는 일은 노드를 드래그해 순서를 바꾸거나(sequential에서만
// 의미 있음) "매니저로 지정"을 누르는 것뿐 — 그 결과가 서버에 반영되면
// `refresh()`가 전체를 다시 그려 항상 정돈된 모습으로 돌아온다.
// Drawflow CDN 로드에 실패해도(오프라인 등) 조용히 아무 것도 안 그리고
// 끝난다 — 기존 멤버 표 CRUD는 전혀 영향받지 않는다(이 설계의 핵심
// 안전장치).
function _initTeamWorkflowCanvas(container, team, activeAgentMembers) {
  if (!container || typeof Drawflow === "undefined") return null;
  container.innerHTML = "";
  const editor = new Drawflow(container);
  // 사용자 보고(2026-10-21, "노드 크기 변경 기능... 지금 노드 크기
  // 조절은 안되고 너무 커 모든 노드가 표시되지 않음") — _crewStructuralLayout
  // 의 nodeW=150/gapX=190은 원래 SVG(viewBox로 컨테이너 폭에 자동
  // 맞춰짐)용 상수라, Drawflow의 실제 DOM 좌표에 그대로 쓰면 멤버가
  // 몇 명만 넘어도 컨테이너 밖으로 넘친다. zoom_min을 넉넉히 낮게 둬
  // 아래 fitToContainer()가 멤버 수가 많아도 전부 보이게 축소할 수
  // 있게 한다.
  editor.zoom_min = 0.2;
  editor.zoom_max = 1.6;
  editor.zoom_value = 0.1;
  editor.start();

  const process = team.default_process || "sequential";
  const managerMember = activeAgentMembers.find(a => a.id === team.default_manager_agent_id) || null;
  const layout = _crewStructuralLayout(activeAgentMembers, process, managerMember);
  const nodeIdByRoleName = {};

  const nodeHtml = (a, isManager) =>
    '<div class="team-canvas-node' + (isManager ? " team-canvas-node-manager" : "") + '">' +
    '<div class="team-canvas-node-label">' + esc(agentDisplayLabel(a)) + '</div>' +
    (process === "hierarchical" && !isManager
      ? '<button type="button" class="small" data-set-manager="' + a.id + '">매니저로 지정</button>'
      : "") +
    "</div>";

  const allNodes = layout.isHierarchical ? [layout.managerNode].concat(layout.workers) : activeAgentMembers;
  allNodes.forEach(a => {
    if (a.synthetic) return;  // CrewAI 자동 배정 매니저 — 실제 Agent가 아니라 노드로 안 그림
    const pos = layout.positions[a.role_name];
    const isManager = a === layout.managerNode;
    const nodeId = editor.addNode(
      "agent", 1, 1, pos.x, pos.y,
      isManager ? "team-canvas-manager" : "team-canvas-worker",
      { agentId: a.id }, nodeHtml(a, isManager)
    );
    nodeIdByRoleName[a.role_name] = nodeId;
  });

  if (layout.isHierarchical && !layout.managerNode.synthetic) {
    layout.workers.forEach(a => {
      editor.addConnection(
        nodeIdByRoleName[layout.managerNode.role_name], nodeIdByRoleName[a.role_name],
        "output_1", "input_1"
      );
    });
  } else if (!layout.isHierarchical) {
    for (let i = 0; i < activeAgentMembers.length - 1; i++) {
      editor.addConnection(
        nodeIdByRoleName[activeAgentMembers[i].role_name],
        nodeIdByRoleName[activeAgentMembers[i + 1].role_name],
        "output_1", "input_1"
      );
    }
  }

  container.querySelectorAll("[data-set-manager]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/teams/" + team.id, {
          default_manager_agent_id: Number(btn.dataset.setManager),
        });
        showMsg("global-msg", "매니저를 지정했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  if (!layout.isHierarchical && activeAgentMembers.length > 1) {
    editor.on("nodeMoved", async () => {
      let exported;
      try {
        exported = editor.export().drawflow.Home.data;
      } catch (e) { return; }  // Drawflow 내부 구조가 예상과 다르면 조용히 무시(기존 순서 유지)
      const nodes = Object.values(exported);
      nodes.sort((a, b) => a.pos_x - b.pos_x);
      const orderedRowIds = nodes
        .map(n => {
          const agent = activeAgentMembers.find(x => x.id === n.data.agentId);
          return agent ? agent.teamMemberRowId : null;
        })
        .filter(Boolean);
      if (orderedRowIds.length !== activeAgentMembers.length) return;  // 안전장치 — 집합이 안 맞으면 저장 안 함
      try {
        await api("PATCH", "/api/teams/" + team.id + "/members/order", {
          member_row_ids: orderedRowIds,
        });
        showMsg("global-msg", "순서를 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }

  // Drawflow는 zoom_in()/zoom_out()(0.1 단위)만 공개 API로 제공하고
  // 현재 배율을 직접 읽는 API는 없다 — 시작값은 항상 1.0(Drawflow
  // 기본값)이므로 이후 변화는 이 클로저에서 직접 추적한다.
  let currentZoom = 1.0;
  const applyZoomSteps = (targetZoom) => {
    const clamped = Math.max(editor.zoom_min, Math.min(editor.zoom_max, targetZoom));
    const steps = Math.round((clamped - currentZoom) / editor.zoom_value);
    for (let i = 0; i < Math.abs(steps); i++) {
      if (steps < 0) editor.zoom_out(); else editor.zoom_in();
    }
    currentZoom = clamped;
  };
  const fitToContainer = () => {
    const w = container.clientWidth || 600;
    const h = container.clientHeight || 260;
    applyZoomSteps(Math.min(1, w / layout.svgWidth, h / layout.svgHeight));
  };
  fitToContainer();  // 최초 렌더부터 전체 노드가 보이게

  return {
    zoomIn: () => applyZoomSteps(currentZoom + editor.zoom_value),
    zoomOut: () => applyZoomSteps(currentZoom - editor.zoom_value),
    fit: fitToContainer,
  };
}

function crewDiagramSvg(result) {
  const agents = result.agents;
  const manager = agents.find(a => a.output === null) || null;
  const layout = _crewStructuralLayout(agents, result.process, manager);
  const { positions, svgWidth, nodeW, nodeH, isHierarchical, managerNode, workers } = layout;
  let maxY = layout.svgHeight;  // 위임 곡선이 마지막 행 아래로 처지면 viewBox가 그만큼 커져야 잘리지 않는다.

  let body = "";

  if (isHierarchical) {
    const mPos = positions[managerNode.role_name];
    workers.forEach(a => {
      const wPos = positions[a.role_name];
      body += '<line x1="' + (mPos.x + nodeW / 2) + '" y1="' + (mPos.y + nodeH) + '" x2="' + (wPos.x + nodeW / 2) + '" y2="' + wPos.y +
        '" stroke="#999" stroke-width="2" marker-end="url(#crew-arrow-structural)" />';
    });
  } else {
    for (let i = 0; i < agents.length - 1; i++) {
      const p1 = positions[agents[i].role_name], p2 = positions[agents[i + 1].role_name];
      body += '<line x1="' + (p1.x + nodeW) + '" y1="' + (p1.y + nodeH / 2) + '" x2="' + p2.x + '" y2="' + (p2.y + nodeH / 2) +
        '" stroke="#999" stroke-width="2" marker-end="url(#crew-arrow-structural)" />';
    }
  }

  const unresolved = [];
  const pairs = {};
  (result.delegations || []).forEach(d => {
    if (!d.to_agent_role || !positions[d.to_agent_role] || !positions[d.from_agent_role]) {
      unresolved.push(d);
      return;
    }
    const key = d.from_agent_role + "\u2192" + d.to_agent_role;
    if (!pairs[key]) pairs[key] = { from: d.from_agent_role, to: d.to_agent_role, tools: [] };
    if (!pairs[key].tools.includes(d.tool)) pairs[key].tools.push(d.tool);
  });
  Object.values(pairs).forEach(pair => {
    const p1 = positions[pair.from], p2 = positions[pair.to];
    const x1 = p1.x + nodeW / 2, y1 = p1.y + nodeH, x2 = p2.x + nodeW / 2, y2 = p2.y + nodeH;
    const midX = (x1 + x2) / 2, midY = Math.max(y1, y2) + 26;
    maxY = Math.max(maxY, midY + 14);  // 라벨 텍스트 한 줄 높이만큼 여유를 더 둔다.
    body += '<path d="M' + x1 + "," + y1 + " Q" + midX + "," + midY + " " + x2 + "," + y2 +
      '" stroke="#b45309" stroke-width="2" stroke-dasharray="4,3" fill="none" marker-end="url(#crew-arrow-delegation)" />' +
      '<text x="' + midX + '" y="' + (midY - 4) + '" font-size="10" fill="#b45309" text-anchor="middle">' + esc(pair.tools.join(", ")) + "</text>";
  });

  const allNodes = isHierarchical ? [managerNode].concat(workers) : agents;
  allNodes.forEach(a => {
    const pos = positions[a.role_name];
    const isManagerNode = a === managerNode;
    // 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정... 호출시(채팅
    // 포함)에도 활용될 수 있도록") — 다이어그램 라벨만 이름을 우선
    // 보여준다. `positions`/위임 매칭 키는 여전히 role_name 그대로다
    // (CrewAI가 실제로 보고하는 coworker 문자열과 일치해야 해서 건드리면
    // 위임 선 자체가 안 그려진다).
    const rawLabel = a.name || a.role_name;
    const label = rawLabel.length > 18 ? rawLabel.slice(0, 17) + "…" : rawLabel;
    body += '<rect x="' + pos.x + '" y="' + pos.y + '" width="' + nodeW + '" height="' + nodeH + '" rx="8" ' +
      'fill="' + (isManagerNode ? "#fef3c7" : "#eef1f6") + '" stroke="' + (isManagerNode ? "#b45309" : "#888") + '" stroke-width="1.5" />' +
      '<text x="' + (pos.x + nodeW / 2) + '" y="' + (pos.y + nodeH / 2 + 4) + '" font-size="12" text-anchor="middle" fill="#222">' +
      esc(label) + "</text>";
  });

  const svg = '<svg viewBox="0 0 ' + svgWidth + " " + maxY + '" style="width:100%;max-width:' + svgWidth + 'px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg">' +
    "<defs>" +
    '<marker id="crew-arrow-structural" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#999"/></marker>' +
    '<marker id="crew-arrow-delegation" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#b45309"/></marker>' +
    "</defs>" + body + "</svg>";
  return { svg, unresolved };
}

async function renderCrewResult(result) {
  const box = document.getElementById("crew-result");
  const diagram = crewDiagramSvg(result);
  // 사용자 요청(2026-11-05, 로드맵 8번 "Crew 빌더 diff 모드") — 이
  // Task의 지난 Crew 실행들을 가져와 "바로 이전 실행"을 찾는다.
  // 샌드박스(`/api/crew/run`)로 돌린 결과는 Task에 안 묶여 `task_id`
  // 자체가 없으므로(기존에도 그랬다) 그 경우는 비교 기능 없이 기존
  // 그대로 렌더한다 — 새로 뭔가 깨지는 게 없다.
  let prevRun = null;
  if (result.task_id) {
    try {
      const pastRuns = await api("GET", "/api/tasks/" + result.task_id + "/crew-runs");
      const earlier = pastRuns.filter(r => result.id != null && r.id < result.id);
      if (earlier.length) {
        prevRun = earlier.reduce((a, b) => (a.id > b.id ? a : b));
      }
    } catch (e) { /* 이력 조회 실패해도 diff 없이 기존처럼 보여준다 */ }
  }

  let html = "<h3>협업 관계 다이어그램</h3>" +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>실선(회색)은 요청 시점에 정해진 구조(순차 전달 또는 매니저 조율)입니다.</li>' +
      '<li>점선(주황)은 실제로 발생한 위임/질의입니다.</li>' +
      '</ul>'
    ) +
    diagram.svg +
    (diagram.unresolved.length
      ? '<details class="muted"><summary>확인 안 된 위임(' + diagram.unresolved.length + '건) — 대상 Agent를 다이어그램에서 특정하지 못함</summary>' +
        table(["위임한 Agent", "LLM이 지목한 대상(원문)", "도구"], diagram.unresolved.map(d => [esc(d.from_agent_role || "-"), esc(d.to_agent_role || "(응답에 coworker 없음)"), esc(d.tool)])) +
        "</details>"
      : "");
  html += '<h3>최종 산출물' +
    (prevRun
      ? ' <button class="small" type="button" data-diff-final="1">이전 실행과 비교</button>'
      : ' <button class="small" type="button" disabled title="비교할 이전 실행이 없습니다">이전 실행과 비교</button>') +
    '</h3><div class="msg ok">' + renderContent(result.final_output) + "</div>" +
    '<div id="crew-diff-final" style="display:none"></div>';
  // 사용자 요청(2026-10-12, "UI 개선 잔여 작업 진행해줘") — CrewAI가
  // 이미 집계해주는 토큰 사용량을 `run_crew()`가 이제 반환한다(크루
  // 완료 전엔 비어있을 수
  // 없다 — crew.kickoff() 성공 이후에만 이 함수가 반환되므로 항상 값이
  // 있다). 기존 대시보드의 `.stat-row` 패턴(새 CSS 불필요)을 그대로
  // 재사용한다. 비용/실행시간은 CrewAI가 집계해주지 않아(UsageMetrics에
  // 없음) 보여주지 않는다 — 없는 값을 지어내지 않는다.
  const tu = result.token_usage;
  html += "<h3>토큰 사용량(이 Crew 실행 전체)</h3>" +
    '<div class="card">' +
    '<div class="stat-row"><span>총 토큰</span><span>' + tu.total_tokens + '</span></div>' +
    '<div class="stat-row"><span>입력(prompt)</span><span>' + tu.prompt_tokens + '</span></div>' +
    '<div class="stat-row"><span>출력(completion)</span><span>' + tu.completion_tokens + '</span></div>' +
    '<div class="stat-row"><span>LLM 호출 수</span><span>' + tu.successful_requests + '</span></div>' +
    '</div>';
  // 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
  // 연결해줘") — run_crew()가 이제 gate_scores/tcr을 반환한다. 단건
  // deliverable 카드의 gateBadge와 같은 "측정 없음" 구분 원칙을 그대로
  // 따른다.
  const gates = Object.values(result.gate_scores);
  const measured = gates.filter(g => !g.not_measured);
  html += "<h3>Gate A-G 채점(이 Crew 실행 전체)</h3>";
  html += measured.length === 0
    ? '<p class="muted">Gate 측정 없음</p>'
    : '<p>' + measured.filter(g => g.passed).length + "/" + measured.length + " 통과" +
      (result.tcr === null ? "" : " · TCR " + result.tcr.toFixed(0) + "%") + '</p>';
  // 사용자 요청(2026-10-27, "다른 UI에도 ID가 남아 있는지 확인해줘") —
  // agent_pool은 이미 code가 있으니 raw agent_id 대신 쓴다.
  // 사용자 요청(2026-11-05, 로드맵 8번) — "비교" 열 추가, 이전 실행에
  // 같은 agent_id의 산출물이 있을 때만 버튼을 활성화한다.
  html += "<h3>Agent별 산출물</h3>" + table(["Agent 코드", "역할", "산출물", "비교"], result.agents.map(a => {
    const prevAgent = prevRun ? prevRun.agents.find(pa => pa.agent_id === a.agent_id && pa.output !== null) : null;
    const compareCell = a.output === null
      ? ""
      : prevAgent
        ? '<button class="small" type="button" data-diff-agent="' + a.agent_id + '">비교</button>'
        : '<span class="muted">(이전 실행에 없음)</span>';
    return [
      esc(a.code || ("#" + a.agent_id)), agentDisplayLabel(a),
      a.output === null ? "(없음 — 매니저는 조율만 하고 직접 산출물을 내지 않습니다)"
        : esc(a.output).slice(0, 200) + (a.output.length > 200 ? "…" : ""),
      compareCell,
    ];
  }));
  html += '<div id="crew-diff-agent" style="display:none"></div>';
  html += "<h3>실제 위임/협업 발생 내역(" + result.delegations.length + "건)</h3>";
  html += result.delegations.length
    ? table(["위임한 Agent 역할", "대상 Agent 역할", "사용한 도구"], result.delegations.map(d => [esc(d.from_agent_role || "-"), esc(d.to_agent_role || "(확인 안 됨)"), esc(d.tool)]))
    : '<p class="muted">이번 실행에서는 Agent 간 실제 위임이 발생하지 않았습니다(각자 독립적으로 완수).</p>';
  box.innerHTML = html;

  if (prevRun) {
    const finalDiffBox = document.getElementById("crew-diff-final");
    const finalBtn = box.querySelector("[data-diff-final]");
    if (finalBtn) {
      finalBtn.onclick = () => {
        if (finalDiffBox.style.display === "none") {
          finalDiffBox.innerHTML = renderTextDiff(prevRun.final_output, result.final_output);
          finalDiffBox.style.display = "block";
        } else {
          finalDiffBox.style.display = "none";
        }
      };
    }
    const agentDiffBox = document.getElementById("crew-diff-agent");
    box.querySelectorAll("[data-diff-agent]").forEach(btn => {
      btn.onclick = () => {
        const agentId = Number(btn.dataset.diffAgent);
        const a = result.agents.find(x => x.agent_id === agentId);
        const prevAgent = prevRun.agents.find(pa => pa.agent_id === agentId && pa.output !== null);
        agentDiffBox.innerHTML = "<h4>" + esc(agentDisplayLabel(a)) + " — 이전 실행과 비교</h4>" +
          renderTextDiff(prevAgent.output, a.output);
        agentDiffBox.style.display = "block";
      };
    });
  }
}


/* ---------- AI 에이전트 생성 ---------- */

async function renderJobs(content) {
  // 사용자 보고(2026-10-06, "업무 요청 ID 등 불러올 수 있는 정보들을
  // 찾아 오입력되지 않도록 개선") — 이전엔 "업무 요청 ID"·"할 일 ID"를
  // 숫자로 직접 타이핑해야 해서, 존재하지 않거나 엉뚱한 ID를 입력해도
  // 제출 전엔 알 방법이 없었다(404는 제출 후에야 뜸). 이미 서버가 갖고
  // 있는 업무 요청/할 일 목록을 그대로 불러와 고르는 방식으로 바꾼다.
  const briefs = await api("GET", "/api/briefs");
  const briefLabel = (b) => {
    const text = (b.raw_text || "").trim();
    const snippet = text.length > 24 ? text.slice(0, 24) + "…" : text;
    return "#" + b.id + " " + snippet;
  };
  const briefOptionsHtml = briefs.length
    ? briefs.map(b => '<option value="' + b.id + '">' + esc(briefLabel(b)) + '</option>').join("")
    : '<option value="">(등록된 업무 요청이 없습니다)</option>';
  const modelsByProvider = await fetchModelsByProvider();
  // 사용자 보고(2026-10-27, "Agent 직접 생성 스킬이 도구/스킬 Pool의
  // 스킬인지 확인" → "skill pool에 등록된 스킬명을 보여주고 선택할 수
  // 있도록 개선") — `skillPickerHtml()`의 입력창이 `list="skill-
  // datalist"`를 참조하는데, 이 화면(renderJobs)은 지금까지 그
  // `<datalist id="skill-datalist">` 자체를 한 번도 안 만들어 넣었다
  // (renderBriefs/renderHumanPool은 만든다) — 그래서 "Agent 직접
  // 생성"의 스킬 입력창은 타이핑해도 자동완성이 전혀 안 뜨는, 그냥
  // 빈 자유 입력창이었다. 다른 두 화면과 같은 패턴으로 끼워 넣는다.
  const skillDatalist = await skillDatalistHtml();

  content.innerHTML =
    '<section class="card"><h2>AI 에이전트 생성</h2>' +
    '<form class="inline" id="job-form">' +
    '<select id="job-brief-id" required style="width:220px">' + briefOptionsHtml + '</select>' +
    // 기본 선택(2026-11-21, 하드코딩 점검 5번) — 예전엔 첫 항목인 stub(자리표시자만
    // 만들고 아무것도 검증하지 않음)이 기본이었다. 실제로 쓰는 방식(crewai)을 기본으로.
    '<select id="job-framework"><option value="crewai" selected>crewai(실제 CrewAI Agent)</option>' +
    '<option value="simple_llm">simple_llm(1회 LLM 호출)</option>' +
    '<option value="stub">stub(등록만 — LLM 호출·검증 없음)</option></select>' +
    '<select id="job-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    modelSelectHtml("job-model") +
    '<button class="primary" type="submit">생성 실행</button></form>' +
    '<div id="job-tasks-picker" class="muted" style="margin-bottom:0.75rem">업무 요청을 선택하면 그 아래 할 일 목록이 뜹니다.</div>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>crewai</b>(기본 선택, 비용 발생, 보통 더 큼): 실제 CrewAI Agent+Task+Crew를 구성해 실행합니다 — 필요 스킬에 내부지식검색/조직배정관계조회 같은 게 있으면 Skill Pool이 매칭해주는 실제 도구를 생성 시점부터 붙여서 태어나므로, 이후 "Crew 빌더"·팀·파이프라인에서 바로 협업시킬 Agent를 만들 때 씁니다.</li>' +
      '<li><b>simple_llm</b>(비용 발생): 실제 LLM API를 딱 한 번만 호출해 "이 역할이 처음 맡을 산출물 하나"를 받아옵니다. CrewAI 같은 프레임워크 오버헤드 없이 가장 가벼운 실제 호출입니다.</li>' +
      '<li><b>stub</b>(무료): LLM을 전혀 호출하지 않는 규칙 기반 스텁입니다 — 역할명·스킬로 자리표시자 산출물만 만들고 즉시 끝납니다(실제로 일하지는 못함). 조직도/배정 흐름만 비용 없이 채워보고 싶을 때 씁니다.</li>' +
      '<li>만든 Agent는 이 탭이 아니라 "AI 에이전트 관리" 탭에서 온보딩 승인(pending_onboarding → active) 후에야 실제 업무 배정·실행에 쓸 수 있습니다.</li>' +
      '<li><b>중복 방지</b>: 같은 할 일(역할)에 이미 생성된 Agent가 있으면 새 Agent를 만들지 않고 기존 것을 재사용합니다 — 이 경우 "AI 에이전트 관리"에 새 행이 안 생기는데, 결과에 "재사용" 배지와 안내가 표시됩니다. 새 Agent를 만들려면 다른 할 일을 고르세요.</li>' +
      '</ul>'
    ) +
    '<div id="job-result"></div></section>' +
    // 사용자 요청(2026-10-21, "Agent 직접 생성" — CrewAI Studio UI
    // 참조) — 위 Task 기반 생성과 달리 Brief/Task 선택 없이 역할명·
    // goal·backstory·expected_output·스킬을 바로 입력해 즉시 등록한다.
    // skillPickerHtml()/initSkillPicker()는 이미 Task 생성·사람 인력
    // 등록에서 쓰는 컴포넌트를 그대로 재사용(새로 안 만듦).
    '<section class="card"><h2>Agent 직접 생성(Brief/Task 없이 즉시 등록)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>역할명만 필수입니다 — goal/backstory/expected_output을 비워두면 실행 시점에 역할명·스킬 기반으로 자동 생성됩니다("AI 에이전트 관리"에서 나중에 덮어쓸 수 있는 것과 동일한 규칙).</li>' +
      '<li>이렇게 만든 Agent도 똑같이 <b>pending_onboarding</b> 상태로 시작합니다 — "AI 에이전트 관리" 탭에서 온보딩 승인해야 실제 업무에 쓸 수 있습니다. 도구 연결도 그 탭에서(이 Agent가 실제로 생긴 뒤에만 가능).</li>' +
      '</ul>'
    ) +
    '<form id="direct-agent-form">' +
    '<label>역할명(필수)<br><input id="direct-agent-role-name" required style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<label style="display:block;margin-top:0.5rem">스킬<br>' + skillPickerHtml("direct-agent-skills-picker", "direct-agent-skills") + skillDatalist + '</label>' +
    '<label style="display:block;margin-top:0.5rem">Goal(목표)<br><textarea id="direct-agent-goal" rows="2" style="width:100%;box-sizing:border-box;margin-top:0.2rem" placeholder="비워두면 역할명·스킬 기반으로 자동 생성"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">Backstory(배경)<br><textarea id="direct-agent-backstory" rows="2" style="width:100%;box-sizing:border-box;margin-top:0.2rem" placeholder="비워두면 역할명·스킬 기반으로 자동 생성"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">Expected Output(기대 산출물)<br><textarea id="direct-agent-expected" rows="2" style="width:100%;box-sizing:border-box;margin-top:0.2rem" placeholder="비워두면 플랫폼 공통 기본 문구 사용"></textarea></label>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">Agent 생성</button>' +
    '</form>' +
    '<div id="direct-agent-result"></div></section>';

  const tasksPicker = document.getElementById("job-tasks-picker");
  const loadTasksForBrief = async (briefId) => {
    if (!briefId) {
      tasksPicker.innerHTML = '<span class="muted">업무 요청을 선택하면 그 아래 할 일 목록이 뜹니다.</span>';
      return;
    }
    tasksPicker.innerHTML = '<span class="muted">불러오는 중...</span>';
    const tasks = await api("GET", "/api/briefs/" + briefId + "/tasks");
    tasksPicker.innerHTML = tasks.length
      ? '<b>할 일 선택(여러 개 가능):</b> ' +
        tasks.map(t =>
          '<label style="margin-right:0.75rem"><input type="checkbox" class="job-task-checkbox" value="' + t.id + '"> ' +
          "#" + t.id + " " + esc(t.role_name) +
          (t.required_skills.length ? " (" + esc(t.required_skills.join(", ")) + ")" : "") +
          "</label>"
        ).join("")
      : '<span class="muted">이 업무 요청엔 할 일이 없습니다 — "업무 요청" 탭의 "할 일로 나누기"에서 먼저 만드세요.</span>';
  };

  const briefSelect = document.getElementById("job-brief-id");
  briefSelect.addEventListener("change", () => loadTasksForBrief(briefSelect.value));
  if (briefSelect.value) await loadTasksForBrief(briefSelect.value);
  wireProviderModelSelect("job-provider", "job-model", modelsByProvider);

  document.getElementById("job-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const briefId = Number(briefSelect.value);
    if (!Number.isInteger(briefId) || briefId < 1) {
      showMsg("global-msg", "업무 요청을 선택하세요.", "error"); return;
    }
    const taskIds = Array.from(tasksPicker.querySelectorAll(".job-task-checkbox:checked")).map(cb => Number(cb.value));
    if (!taskIds.length) {
      showMsg("global-msg", "할 일을 하나 이상 선택하세요.", "error"); return;
    }
    const framework = document.getElementById("job-framework").value;
    const provider = document.getElementById("job-provider").value || null;
    const model = modelSelectValue("job-model");
    const submitBtn = ev.submitter || document.querySelector("#job-form button[type=submit]");
    const busyLabel = framework === "stub" ? "생성 중…" : "생성 중…(실제 API 호출, 시간이 걸릴 수 있습니다)";
    try {
      const result = await withBusy(submitBtn, busyLabel, () => api("POST", "/api/briefs/" + briefId + "/generate", {
        task_ids: taskIds, use_llm: framework !== "stub", framework, provider, model,
      }));
      renderJobResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-10-21, "Agent 직접 생성") — 위 Task 기반 생성과
  // 별개인 두 번째 폼. skillPickerHtml()가 이미 넣어둔 hidden input의
  // 값(쉼표로 join)을 flush() 호출 뒤에 읽는다(입력창에 남은 값을
  // "추가" 안 누르고 바로 제출해도 놓치지 않도록 — 기존 호출부 관례).
  const directSkillPicker = initSkillPicker("direct-agent-skills-picker", "direct-agent-skills", []);
  document.getElementById("direct-agent-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const roleName = document.getElementById("direct-agent-role-name").value.trim();
    if (!roleName) { showMsg("global-msg", "역할명을 입력하세요.", "error"); return; }
    directSkillPicker.flush();
    const skillsRaw = document.getElementById("direct-agent-skills").value;
    const skillTags = skillsRaw ? skillsRaw.split(",").filter(Boolean) : [];
    const submitBtn = ev.submitter || document.querySelector("#direct-agent-form button[type=submit]");
    try {
      const result = await withBusy(submitBtn, "생성 중…", () => api("POST", "/api/agent-pool", {
        role_name: roleName,
        skill_tags: skillTags,
        goal: document.getElementById("direct-agent-goal").value,
        backstory: document.getElementById("direct-agent-backstory").value,
        expected_output: document.getElementById("direct-agent-expected").value,
      }));
      document.getElementById("direct-agent-result").innerHTML =
        '<p class="msg ok">Agent #' + result.id + '을(를) 등록했습니다 — "AI 에이전트 관리" 탭에서 온보딩 승인 후, 필요하면 도구도 거기서 연결하세요.</p>';
      document.getElementById("direct-agent-form").reset();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function renderJobResult(result) {
  const box = document.getElementById("job-result");
  const reusedItems = (result.items || []).filter(it => it.reused);
  const reusedCount = result.reused_count != null ? result.reused_count : reusedItems.length;

  // dedup: 같은 역할(task)에 이미 생성된 에이전트가 있으면 새 에이전트를
  // 만들지 않고 기존 job_item을 재사용한다. 이 경우 "AI 에이전트 관리"에 새
  // 행이 안 생기는데, 화면이 "성공"으로만 뜨면 "생성했는데 안 보인다"는
  // 오해를 피할 수 없다 — 재사용을 명시적으로 알리고 관리 탭으로 이동을 돕는다.
  //
  // **정정(2026-10-06, 사용자 보고 "생성은 '성공'으로 떴는데 관리 탭에
  // 안 보임")**: 배너가 "재사용했다"고만 말하고 "그래서 몇 번 Agent를
  // 보라는 건지"는 안 알려줘서, 관리 탭에 가도 여러 행 중 어떤 게
  // 그건지 못 찾는 게 진짜 혼란의 원인이었다 — 이제 재사용된 기존
  // Agent의 ID·역할명을 배너와 표에 직접 명시한다.
  let banner = "";
  if (reusedCount > 0) {
    const refs = reusedItems
      .filter(it => it.agent_pool_id != null)
      .map(it => esc(it.agent_pool_code || ("#" + it.agent_pool_id)) + "(" + esc(it.agent_pool_role_name) + ")")
      .join(", ");
    banner =
      '<div class="msg warn"><b>새 에이전트가 생성되지 않았습니다.</b> ' +
      "같은 역할에 이미 생성된 에이전트가 있어 " + reusedCount + "건을 재사용했습니다(중복 방지)" +
      (refs ? " — 기존 Agent " + refs + "를 그대로 씁니다." : ".") +
      ' "AI 에이전트 관리" 탭에서 위 ID로 찾으세요. 새 에이전트를 만들려면 다른 할 일 ID로 생성하세요. ' +
      '<button class="small" id="goto-agentpool">AI 에이전트 관리로 이동</button></div>';
  }

  // 사용자 요청(2026-10-07, "현재 UI가 복수의 에이전트 그룹 생성 및
  // HAIR 관점의 연동에 적합한 경우는 아님... AI 에이전트 생성과 통합
  // 검토") — 한 브리프의 여러 할 일을 한 번에 골라 역할별 Agent를
  // 동시에 만드는 기능이 이미 있었으므로(CrewAI의 "역할 기반 그룹"
  // 장점이 여기서 나온다), 생성 직후 그 Agent들을 바로 "팀"으로
  // 묶을 수 있게 한다 — 지금까지는 "Crew 실행"이라는 업무와 무관한
  // 즉석 화면에서만 여러 Agent를 다룰 수 있었고, 그룹을 저장할 방법도
  // 없었다. "팀 관리"에서 하려면 Agent를 다른 할 일로 따로 만들어야
  // 했던 갭도 이걸로 없어진다 — 할 일을 처음부터 같이 고르면
  // 되므로.
  const agentItems = result.items.filter(it => it.agent_pool_id != null);
  const bundleCheckbox = (it) => it.agent_pool_id != null
    ? '<input type="checkbox" class="bundle-agent-checkbox" value="' + it.agent_pool_id + '" checked>'
    : "";
  const bundleFormHtml = agentItems.length > 1
    ? '<form id="bundle-team-form" class="inline" style="margin-top:0.75rem">' +
      '<input id="bundle-team-name" placeholder="팀 이름" required>' +
      '<button class="small" type="submit">체크한 Agent를 팀으로 묶기</button></form>' +
      '<p class="muted">역할별로 같이 만든 Agent를 팀으로 등록해두면, 나중에 "업무 요청"의 할 일 상세에서 팀 전체를 배정하고 "팀 Agent 실행"으로 CrewAI 협업을 시킬 수 있습니다 — "팀 관리" 탭에서 멤버를 더 추가/제외할 수도 있습니다.</p>'
    : "";

  box.innerHTML = banner +
    "<p>생성 결과 상태: <b>" + esc(label(result.status)) + "</b></p>" +
    table(["묶기", "ID", "할 일 ID", "상태", "연결된 Agent", "에러", "재시도", "동작"], result.items.map(it => [
      bundleCheckbox(it),
      it.id, it.task_id,
      esc(label(it.status)) + (it.reused ? ' <span class="badge-reused">재사용</span>' : ""),
      it.agent_pool_id != null ? esc(it.agent_pool_code || ("#" + it.agent_pool_id)) + " " + esc(it.agent_pool_role_name) : '<span class="muted">-</span>',
      esc(it.error || ""), it.retry_count,
      it.status === "failed" ? '<button class="small" data-retry-job="' + result.job_id + '" data-retry-item="' + it.id + '">재시도</button>' : "",
    ])) +
    bundleFormHtml;
  box.querySelectorAll("[data-retry-item]").forEach(btn => {
    btn.onclick = async () => {
      try {
        const r = await api("POST", "/api/jobs/" + btn.dataset.retryJob + "/items/" + btn.dataset.retryItem + "/retry");
        renderJobResult(r);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  const goto = document.getElementById("goto-agentpool");
  if (goto) goto.onclick = () => selectTab("agentpool");

  const bundleForm = document.getElementById("bundle-team-form");
  if (bundleForm) {
    bundleForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const agentIds = Array.from(box.querySelectorAll(".bundle-agent-checkbox:checked")).map(cb => Number(cb.value));
      if (agentIds.length < 1) { showMsg("global-msg", "팀으로 묶을 Agent를 하나 이상 체크하세요.", "error"); return; }
      const name = document.getElementById("bundle-team-name").value.trim();
      if (!name) { showMsg("global-msg", "팀 이름을 입력하세요.", "error"); return; }
      try {
        const team = await api("POST", "/api/teams", { name });
        for (const agentId of agentIds) {
          await api("POST", "/api/teams/" + team.id + "/members", { member_type: "agent", member_id: agentId });
        }
        showMsg("global-msg", "팀 \"" + name + "\"을(를) 만들고 Agent " + agentIds.length + "명을 추가했습니다 — \"팀 관리\" 탭에서 확인하세요.", "ok");
        bundleForm.reset();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
}


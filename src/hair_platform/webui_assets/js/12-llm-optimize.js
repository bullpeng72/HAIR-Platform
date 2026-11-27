/* ---------- LLM 설정 최적화(일괄) ---------- */

async function renderAgentOptimization(content) {
  content.innerHTML = '<section class="card"><h2>LLM 설정 최적화 추천</h2><p class="muted">불러오는 중…</p></section>';
  let recs, platformLlm;
  let registry, auxLlm;
  try {
    [recs, platformLlm, registry, auxLlm] = await Promise.all([
      api("GET", "/api/agent-pool-optimization/recommendations"),
      api("GET", "/api/platform-llm-config"),
      api("GET", "/api/model-registry"),
      api("GET", "/api/auxiliary-llm-config"),
    ]);
  } catch (e) {
    content.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }
  // 사용자 요청(2026-11-02, "LLM 설정에 HAIR-Platform 공통/기본 동작을
  // 위한 LLM을 지정할 수 있게") — `embedding-config` 폼(지식 베이스
  // 탭)과 같은 모양: 명시적으로 설정해둔 값이 있으면 그걸, 없으면 지금
  // 실제로 쓰이는 자동판별 결과를 보여준다. 설정하면 agent_optimizer.
  // refine_with_llm()·대화 "기본 AI"/요약처럼 Agent별 설정이 따로
  // 없는 공통 동작에서 환경변수보다 먼저 적용된다(Agent 실행 자체의
  // provider/model 해석은 그대로 — 범위 밖).
  //
  // 사용자 요청(2026-11-02, "모델 레지스트리에 등록된 모델에 한해
  // 선택할 수 있도록 개선") — 자유 텍스트 provider/모델명 입력을
  // "모델 레지스트리" 탭에 등록+활성화된 모델 중에서 고르는 셀렉트로
  // 바꾼다(백엔드 `llm.set_platform_default_llm()`도 같은 조건으로
  // 다시 검증한다 — 여기 목록에 없는 값은 선택 자체가 안 되지만,
  // API를 직접 호출해도 막힌다).
  const activeModels = registry.filter(m => m.is_active);
  const platformLlmFormHtml = activeModels.length
    ? '<form class="inline" id="platform-llm-config-form">' +
      '<select id="plc-model" required>' +
      activeModels.map(m => {
        const value = m.provider + "|" + m.name;
        const selected = (m.provider === platformLlm.provider && m.name === platformLlm.model) ? " selected" : "";
        return '<option value="' + escAttr(value) + '"' + selected + '>' + esc(m.provider) + " / " + esc(m.name) + '</option>';
      }).join("") +
      '</select>' +
      '<button type="submit" class="small primary">저장</button>' +
      '</form>'
    : '<p class="msg error">등록·활성화된 모델이 없습니다 — "모델 레지스트리" 탭에서 먼저 모델을 등록하세요.</p>';
  const platformLlmHtml =
    '<section class="card"><h2>플랫폼 공통 기본 LLM</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>Agent별 설정이 없는 공통 동작(LLM 설정 최적화 추천 보정, 대화창 "기본 AI"/요약 등)이 쓰는 기본값입니다 — 설정하면 환경변수보다 먼저 적용됩니다. 각 Agent 자체의 모델 설정과는 별개입니다.</li>' +
      '<li>"모델 레지스트리" 탭에 등록되고 활성화된 모델만 고를 수 있습니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (platformLlm.is_explicit ? '<b>설정됨</b>' : '<span class="muted">자동판별 중(아직 명시적으로 설정 안 함)</span>') +
    ' — provider: <b>' + esc(platformLlm.provider) + '</b>, 모델: <b>' + esc(platformLlm.model) + '</b></p>' +
    platformLlmFormHtml +
    "</section>";

  // 보조 작업 모델(2026-11-24, P0-2 응답 속도) — 언어 교정·수치 문장 교정만 따로 보낼 작은 모델.
  const auxConfigured = auxLlm.provider && auxLlm.model;
  const auxLlmHtml =
    '<section class="card"><h2>보조 작업 모델</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>결과물의 한자·외국 문자 교정과 근거 없는 수치 문장 교정처럼 <b>짧고 기계적인 보조 호출</b>만 이 모델로 보냅니다. 결과물 작성 자체는 각 Agent의 모델을 그대로 씁니다.</li>' +
      '<li>로컬 대형 모델 하나를 모든 작업이 순서대로 기다리면 짧은 교정도 앞 작업이 끝날 때까지 밀립니다. <b>대기가 실제로 줄어드는 경우는 보조 모델이 본 작업 모델과 동시에 돌 수 있을 때뿐</b>입니다 — 클라우드 모델(예: OpenAI의 소형 모델)이거나, 메모리가 두 로컬 모델을 함께 올릴 만큼 넉넉할 때.</li>' +
      '<li>⚠️ 메모리가 부족한 PC에서 로컬 모델 두 개를 쓰면 Ollama가 모델을 번갈아 내렸다 올리며 하나씩 처리해 오히려 느려질 수 있습니다(실측: 32GB Mac에서 35B 본 모델 + 7.8B 보조 모델 → 교정 대기 시간 변화 없음).</li>' +
      '<li>비워 두면 예전처럼 본 작업과 같은 모델을 씁니다. "모델 레지스트리"에 등록·활성화된 모델만 고를 수 있습니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (auxConfigured
      ? '<b>설정됨</b> — ' + esc(auxLlm.provider) + ' / <b>' + esc(auxLlm.model) + '</b>' +
        (auxLlm.in_use ? '' : ' <span class="msg error">레지스트리에서 비활성화돼 지금은 쓰이지 않습니다</span>')
      : '<span class="muted">설정 안 함 — 보조 호출도 본 작업 모델을 씁니다</span>') + '</p>' +
    (activeModels.length
      ? '<form class="inline" id="aux-llm-config-form">' +
        '<select id="aux-model"><option value="">(사용 안 함 — 본 작업 모델 사용)</option>' +
        activeModels.map(m => {
          const value = m.provider + "|" + m.name;
          const selected = (m.provider === auxLlm.provider && m.name === auxLlm.model) ? " selected" : "";
          return '<option value="' + escAttr(value) + '"' + selected + '>' + esc(m.provider) + " / " + esc(m.name) + '</option>';
        }).join("") +
        '</select>' +
        '<button type="submit" class="small primary">저장</button>' +
        '</form>'
      : '') +
    "</section>";

  const changedCount = recs.filter(r => r.changed).length;
  content.innerHTML = platformLlmHtml + auxLlmHtml + '<section class="card"><h2>LLM 설정 최적화 추천</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>모델 순위: ① 이 Agent에 <b>쓸 수 없는 모델은 제외</b>(✗ — Agent가 도구를 쓰는데 성능 테스트에서 도구 호출을 못 한 모델) → ② 관리자가 "모델 레지스트리"에서 그 tier에 매핑한 모델(★) → ③ 성능 테스트 점검에서 문제가 적은 모델(⚠ 수치 변형·한국어 혼입·추론 끔 무시) → ④ 성능 테스트가 그 tier를 추천한 모델 → ⑤ 모델 이름 추정. 성능 테스트(전체 점검)를 하지 않은 모델은 능력을 판단하지 않습니다.</li>' +
    '<li>이 화면은 <b>일괄 적용</b> 화면입니다 — 체크한 Agent는 "선택 적용"을 누르면 미리보기 없이 바로 모델/파인튜닝 설정이 저장됩니다. 하나씩 검토하려면 "AI 에이전트 관리" 화면의 "⚡ 최적화 추천" 버튼을 쓰세요(그쪽은 입력란에 채우기만 하고 저장은 admin이 직접 합니다).</li>' +
    // 사용자 요청(2026-10-26, "llm 설정 최적화에는 에이전트를 추천
    // 설정된 에이전트도 재선택하고 다시 추천 모델을 적용할 수 있도록
    // UI/기능 개선") — 이전엔 `r.changed`(1위 추천이 이미 현재 설정과
    // 같음)인 Agent는 체크박스/모델 선택 자체가 없어서, "이미 적용함"
    // 또는 "이미 최적"인 Agent를 다시 선택해 다른 등록 모델로 바꾸거나
    // 재적용할 방법이 없었다. 체크박스·모델 선택은 이제 전체 Agent에
    // 항상 뜬다 — "변경 필요"는 그냥 참고 표시일 뿐, 체크 자체를
    // 막지 않는다.
    '<li>"변경 필요"가 아니어도(이미 추천과 같거나 한 번 적용한 Agent) 체크하고 "추천 Provider/Model"의 셀렉트에서 다른 등록 모델을 골라 다시 적용할 수 있습니다.</li>' +
    "</ul>"
    ) +
    '<button id="select-changed-optimize" class="small">변경 필요만 선택</button> ' +
    '<button id="apply-selected-optimize" class="small primary">선택 적용</button> ' +
    '<span class="muted">변경 필요 ' + changedCount + "/" + recs.length + "건</span>" +
    table(["적용", "역할", "분류", "현재 Provider/Model", "추천 Provider/Model", "tuning 변경 필드", "근거"], recs.map(r => {
      // 사용자 요청(2026-10-26, "모델 추천의 경우 모델 레지스트리에
      // 등록된 모델을 선택할 수 있도록 해줘") — 일괄 적용 화면에서도
      // 1위 추천 고정이 아니라, 레지스트리 활성 모델 후보 중 다른 걸
      // 골라 적용할 수 있게 셀렉트로 보여준다(`r.changed` 여부와
      // 무관하게 항상 — 바로 위 help-list 항목 참고).
      const candidates = r.recommended.candidates || [];
      const modelCell = candidates.length
        ? '<select class="optimize-model-pick" data-agent-id="' + r.agent_id +
          '" data-candidates="' + escAttr(JSON.stringify(candidates)) + '">' +
          candidates.map((c, i) => _optimizeCandidateOption(c, i, c.provider === r.recommended.provider && c.model === r.recommended.model)).join("") +
          '</select>'
        : esc(r.recommended.provider || "(기본값)") + " / " + esc(r.recommended.model || "(기본값)");
      return [
        '<input type="checkbox" class="optimize-apply-check" data-agent-id="' + r.agent_id +
          '" data-changed="' + (r.changed ? "1" : "0") + '" data-payload="' + escAttr(JSON.stringify({
            provider: r.recommended.provider,
            model: r.recommended.model,
            tuning_config: r.recommended.tuning_config,
          })) + '">',
        esc(r.role_name),
        esc(_OPTIMIZE_TIER_LABELS[r.tier] || r.tier),
        esc(r.current.provider || "(기본값)") + " / " + esc(r.current.model || "(기본값)") +
          // 현재 모델을 이 Agent에 쓸 수 없으면(도구 호출 불가 등, 2026-11-27) 바로 보이게
          (r.current_fitness && r.current_fitness.excluded
            ? '<br><span class="gate-badge gate-warn" title="' + escAttr(r.current_fitness.excluded_reason) + '">✗ 쓸 수 없음</span>'
            : (r.current_fitness && r.current_fitness.issues && r.current_fitness.issues.length
              ? '<br><span class="gate-badge gate-warn">⚠ ' + esc(r.current_fitness.issues.join(", ")) + "</span>" : "")),
        modelCell,
        Object.keys(r.tuning_diff).length ? esc(Object.keys(r.tuning_diff).join(", ")) : "(없음)",
        // 근거는 두 줄까지만, 전체는 마우스를 올리면(2026-11-27, 사용성 평가 5번 — 39행마다 긴 회색 글이 화면의 73%였다).
        '<div class="muted clamp-2" title="' + escAttr(r.rationale) + '">' + esc(r.rationale) + "</div>",
      ];
    })) +
    "</section>";

  const platformLlmForm = document.getElementById("platform-llm-config-form");
  if (platformLlmForm) platformLlmForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const [provider, model] = document.getElementById("plc-model").value.split("|");
    try {
      await api("PUT", "/api/platform-llm-config", { provider, model });
      showMsg("global-msg", "플랫폼 공통 기본 LLM을 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const auxLlmForm = document.getElementById("aux-llm-config-form");
  if (auxLlmForm) auxLlmForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const picked = document.getElementById("aux-model").value;
    const [provider, model] = picked ? picked.split("|") : [null, null];
    try {
      await api("PUT", "/api/auxiliary-llm-config", { provider, model });
      showMsg("global-msg", picked ? "보조 작업 모델을 저장했습니다." : "보조 작업 모델을 해제했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("select-changed-optimize").onclick = () => {
    Array.from(content.querySelectorAll(".optimize-apply-check")).forEach(chk => {
      chk.checked = chk.dataset.changed === "1";
    });
  };

  document.getElementById("apply-selected-optimize").onclick = async () => {
    const checks = Array.from(content.querySelectorAll(".optimize-apply-check:checked"));
    if (!checks.length) { showMsg("global-msg", "적용할 Agent를 선택하세요.", "error"); return; }
    if (!(await confirmModal(checks.length + "개 Agent에 추천 설정을 적용합니다. 계속할까요?"))) return;
    let ok = 0, fail = 0;
    for (const chk of checks) {
      const id = chk.dataset.agentId;
      const payload = JSON.parse(chk.dataset.payload);
      const pickSel = content.querySelector('.optimize-model-pick[data-agent-id="' + id + '"]');
      if (pickSel) {
        const cands = JSON.parse(pickSel.dataset.candidates || "[]");
        const chosen = cands[Number(pickSel.value)];
        if (chosen) {
          payload.provider = chosen.provider;
          payload.model = chosen.model;
        }
      }
      try {
        if (payload.provider) {
          await api("PATCH", "/api/agent-pool/" + id + "/model", { provider: payload.provider, model: payload.model });
        }
        await api("PATCH", "/api/agent-pool/" + id + "/tuning", Object.assign({
          max_iter: null, max_rpm: null, max_execution_time: null, max_retry_limit: null,
          temperature: null, reasoning: null, max_reasoning_attempts: null,
          allow_code_execution: null, respect_context_window: null,
        }, payload.tuning_config || {}));
        ok++;
      } catch (e) { fail++; }
    }
    showMsg("global-msg", ok + "개 적용 완료" + (fail ? ", " + fail + "개 실패" : "") + ".", fail ? "error" : "ok");
    renderAgentOptimization(content);
  };
}


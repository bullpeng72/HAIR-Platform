/* ---------- 모델 레지스트리(사용자 요청, 2026-10-06, "운영 관리에 사용
   가능한 ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고
   등록된 모델을 불러오는 방식으로 UI/UX를 개선") ---------- */

// 등록 직후 추천 기본값 저장(2026-11-26) — "등록하면서 추천 기본값도 함께 저장"이 켜져 있을 때만. 실패해도 등록은 유지.
async function _applyRecommendationAfterRegister(modelId, name) {
  const box = document.getElementById("mr-apply-rec");
  if (!box || !box.checked || !modelId) return "";
  try {
    const r = await api("POST", "/api/model-registry/" + modelId + "/apply-recommendation");
    return name + ": 컨텍스트 " + Number(r.context_window).toLocaleString() + "토큰 등 추천값 저장.";
  } catch (e) {
    return name + ": 추천값 없음(" + e.message + ") — 표에서 직접 입력하세요.";
  }
}

async function renderModelRegistry(content) {
  const isAdmin = ME.role === "admin";
  const models = await api("GET", "/api/model-registry");
  const byProvider = (p) => models.filter(m => m.provider === p);
  const providerLabel = { anthropic: "Anthropic", openai: "OpenAI", ollama: "Ollama(로컬)" };
  const providerOptionsHtml = Object.keys(providerLabel)
    .map(p => '<option value="' + p + '">' + providerLabel[p] + "</option>").join("");
  // 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화 각
  // tier에 적용되는 모델을 매핑하는 기능을 제공... 향후 새로운 모델
  // 출시되거나 모델 프로바이더 변경시에도 각 Tier별 모델 맵핑 변경
  // 하여 관리, 일단 설정된 후 변경할 수 있는 기능이 제공되지 않아") —
  // "tier 직접 매핑으로 변경"(premium/balanced/economy 등급 간접
  // 매핑 대신, agent_optimizer의 tier 이름 자체에 모델을 직접 매핑).
  // 체크박스 — 모델 하나가 여러 tier에 동시 매핑될 수 있다. 전부
  // 체크 해제한 뒤 저장하면 그 모델은 다시 이름 패턴 추정으로
  // 돌아간다(초기화) — 몇 번이든 다시 바꿀 수 있다.
  const _OPTIMIZATION_TIER_LABELS = {
    deep_reasoning: "심층추론", precision_fast: "정밀·신속", standard: "표준",
  };
  const tierMappingHtml = (m) =>
    Object.keys(_OPTIMIZATION_TIER_LABELS).map(t =>
      '<label style="white-space:nowrap;margin-right:0.5rem"><input type="checkbox" class="mr-tier-pick" data-model-id="' + m.id +
      '" value="' + t + '"' + ((m.optimization_tiers || []).includes(t) ? " checked" : "") + '> ' +
      esc(_OPTIMIZATION_TIER_LABELS[t]) + "</label>"
    ).join("") +
    '<button class="small" data-save-optimization-tiers="' + m.id + '">저장</button>';
  // 사용자 요청(2026-11-18, "...엔터프라이즈 환경 적용이 가능한 수준의
  // 확실한 개선 방안을 도출해줘" 개선축 B) — 이 모델이 실제로 받아들이는
  // 토큰 한도를 admin이 직접 등록한다(`token_budget.py`가 `_build_
  // transcript()`에서 이 값을 읽어 프롬프트가 한도를 넘기기 전에 미리
  // 잘라낸다). Ollama는 로컬 서버 실행 플래그(`-c 32768` 등)로 결정되는
  // 값이라 플랫폼이 자동으로 알 수 없다 — 비워두면 provider별 보수적
  // 기본값으로 폴백한다(Anthropic 200K/OpenAI 128K/Ollama 8K).
  const contextWindowHtml = (m) =>
    '<input type="number" min="1" step="1" class="mr-context-window" ' +
    'data-model-id="' + m.id + '" placeholder="미등록(기본값 사용)" ' +
    'value="' + (m.context_window || "") + '" style="width:8rem">' +
    '<button class="small" data-save-context-window="' + m.id + '">저장</button>';
  // 모델별 생성 옵션(2026-11-21, "특정 모델만을 위한 하드코딩은 없도록") —
  // top_k/top_p를 모델마다 등록한다. 비우면 보내지 않는다(그 모델의 기본값).
  // OpenAI는 top_k를 지원하지 않아 입력란을 두지 않는다.
  const samplingOptionsHtml = (m) => {
    const o = m.sampling_options || {};
    const topK = m.provider === "openai" ? "" :
      '<input type="number" min="1" step="1" class="mr-top-k" data-model-id="' + m.id + '" placeholder="top_k" ' +
      'value="' + (o.top_k ?? "") + '" style="width:5.5rem" title="top_k — 비우면 모델 기본값">';
    return topK +
      '<input type="number" min="0.01" max="1" step="0.01" class="mr-top-p" data-model-id="' + m.id + '" placeholder="top_p" ' +
      'value="' + (o.top_p ?? "") + '" style="width:5.5rem" title="top_p(0~1) — 비우면 모델 기본값">' +
      (m.provider === "anthropic" ? "" :
        '<input type="number" min="256" step="256" class="mr-max-out" data-model-id="' + m.id + '" placeholder="최대 출력" ' +
        'value="' + (o.max_output_tokens ?? "") + '" style="width:6.5rem" title="한 번 응답의 최대 출력 토큰(사고 토큰 포함) — ' +
        (m.provider === "ollama" ? "비우면 32768(사고 토큰 포함 — Ollama 기본은 무제한이라 반복 루프에 빠지면 끝나지 않음)" : "비우면 provider 기본값") + '">') +
      '<button class="small" data-save-sampling-options="' + m.id + '">저장</button>' +
      // 추천값(2026-11-26) — 컨텍스트 한도·생성 옵션 칸을 모델 정보로 채운다(저장은 각 "저장" 버튼으로).
      ' <button class="small" data-recommend-model="' + m.id + '" title="모델 정보(Ollama: 학습 최대 컨텍스트·구조·제작자 권장값, 클라우드: 계열별 공개 한도)와 이 PC 메모리로 컨텍스트 한도·생성 옵션을 추천해 칸에 채웁니다 — 저장은 직접 누르세요.">추천값</button>';
  };
  // 사용자 요청(2026-11-12, "ollama, openai 등 프로바이더의 모델 성능
  // Test하고 결과 표시, 티어 추천 기능") — 고정 테스트 프롬프트 1건을
  // 실제로 호출해 속도(글자/초)를 재고, 그 속도만으로 tier를
  // 추천한다(품질 평가 아님 — `model_benchmark.py` 모듈 docstring
  // 참고). "저장"은 직접 눌러야 기존 "최적화 Tier 매핑"에 반영된다
  // (추천이 자동 적용되지 않음 — ⚡ 최적화 추천과 같은 관례).
  // 성능 테스트 결과(2026-11-27 정교화 — `model_benchmark.py`). 빠른 측정: 예열 후 3회 중앙값, 생성 속도·첫 응답·첫 로드를
  // 나눠 보임. 전체 점검: 능력 점검(도구·JSON·수치·한국어·생각 끄기) + 긴 문서 읽기 → 단계 1회 예상 시간으로 tier 추천.
  const _BENCH_CHECK_LABELS = { tools: "도구 호출", json: "JSON 형식", numbers: "수치 보존", korean: "한국어", think_off: "생각 끄기", long_doc: "긴 문서" };
  const _fmtNum = (v, digits) => (typeof v === "number" ? v.toFixed(digits) : "?");
  const _benchDetailHtml = (b) => {
    const d = b.details;
    if (!d) {  // 예전 방식(짧은 질문 1회, 글자/초)
      return b.chars_per_second.toFixed(1) + '자/초 · ' + b.latency_seconds.toFixed(1) + '초 <span class="muted" title="모델 로드 시간이 섞인 1회 측정입니다 — 다시 측정하세요">(예전 방식)</span>';
    }
    const def = d.default || {};
    const off = d.think_off;
    let html = '<span title="생각(추론) 토큰 포함, 예열 후 ' + (def.runs || 3) + '회 중앙값">생성 ' + _fmtNum(def.gen_tps, 1) + ' tok/s</span>' +
      ' · 응답 ' + _fmtNum(def.wall_s, 1) + '초';
    if (off) html += ' · <span title="생각 끔일 때(같은 질문)">생각 끔 ' + _fmtNum(off.wall_s, 1) + '초</span>';
    if (typeof d.load_s === "number" && d.load_s > 0.5) html += '<br><span class="muted" title="모델을 메모리에 올리는 시간 — 측정에서 뺐습니다">첫 로드 ' + _fmtNum(d.load_s, 1) + '초</span>';
    if (typeof d.estimated_stage_s === "number") {
      html += '<br><span title="' + escAttr(d.estimate_basis || "") + '">단계 1회 예상 ' + Math.round(d.estimated_stage_s) + '초</span>';
    }
    if (d.checks) {
      html += '<br>' + Object.entries(d.checks).map(([k, c]) =>
        '<span class="gate-badge ' + (c.passed ? "gate-ok" : "gate-warn") + '" title="' + escAttr(c.reason || "") +
        (c.prefill_tps ? " — 입력 처리 " + c.prefill_tps + " tok/s" : "") + '">' + (c.passed ? "✓ " : "✗ ") + esc(_BENCH_CHECK_LABELS[k] || k) + "</span>"
      ).join(" ");
    }
    if (d.warnings && d.warnings.length) {
      html += '<br><span class="gate-warn" style="font-size:0.85em">⚠ ' + d.warnings.map(esc).join("<br>⚠ ") + "</span>";
    }
    return html;
  };
  const benchmarkHtml = (m) => {
    const b = m.latest_benchmark;
    let resultHtml;
    if (!b) {
      resultHtml = '<span class="muted">아직 테스트 안 함</span>';
    } else if (b.status === "failed") {
      resultHtml = '<span class="gate-badge gate-warn" title="' + escAttr(b.error || "") + '">실패</span> ' +
        '<span class="muted">' + esc(fmtTime(b.tested_at)) + '</span>';
    } else {
      const tiers = (b.details && b.details.recommended_tiers) || (b.recommended_tier ? [b.recommended_tier] : []);
      const modeLabel = b.details ? (b.details.mode === "full" ? "전체 점검" : "빠른 측정") : "";
      resultHtml = _benchDetailHtml(b) + "<br>" +
        (tiers.length
          ? '<span class="gate-badge" title="' + (b.details && b.details.mode === "full" ? "단계 예상 시간과 능력 점검으로 정한 추천" : "생성 속도만 본 추천 — 심층추론은 전체 점검이 필요합니다") + '">추천: ' +
            tiers.map(t => esc(_OPTIMIZATION_TIER_LABELS[t] || t)).join(" · ") + '</span> ' +
            '<button class="small" data-apply-benchmark-tier="' + m.id + '" data-tiers="' + escAttr(tiers.join(",")) + '">추천 tier 저장</button> '
          : '<span class="muted">추천 tier 없음</span> ') +
        '<span class="muted">(' + esc(modeLabel) + (modeLabel ? " " : "") + esc(fmtTime(b.tested_at)) + ')</span>';
      if (b.latest_full) {
        const f = b.latest_full.details || {};
        resultHtml += '<br><span class="muted" title="가장 최근 전체 점검의 추천과 경고">최근 전체 점검: ' +
          ((f.recommended_tiers || []).map(t => esc(_OPTIMIZATION_TIER_LABELS[t] || t)).join(" · ") || "추천 없음") +
          ((f.warnings || []).length ? " · ⚠ " + f.warnings.map(esc).join(" / ") : "") + "</span>";
      }
    }
    return resultHtml + '<br><button class="small" data-run-benchmark="' + m.id + '" data-mode="quick" title="예열 후 짧은 질문 3회 — 속도만(로컬 약 10~30초)">빠른 측정</button> ' +
      '<button class="small" data-run-benchmark="' + m.id + '" data-mode="full" title="빠른 측정 + 도구 호출·JSON·수치 보존·한국어·생각 끄기 점검 + 긴 문서 읽기(로컬 약 30초~2분)">전체 점검</button>';
  };
  // 모델별 단가(2026-11-22, 하드코딩·스텁 점검 3순위) — 100만 토큰당 입력/출력. 비우면 금액을
  // 계산하지 않고 토큰 수만 보인다(단가표를 코드에 넣지 않는다). 로컬 모델은 0으로 둘 수 있다.
  const pricingHtml = (m) =>
    '<input type="number" min="0" step="0.01" class="mr-price-in" data-model-id="' + m.id + '" placeholder="입력" ' +
    'value="' + (m.price_input_per_1m ?? "") + '" style="width:5.5rem" title="100만 토큰당 입력 단가">' +
    '<input type="number" min="0" step="0.01" class="mr-price-out" data-model-id="' + m.id + '" placeholder="출력" ' +
    'value="' + (m.price_output_per_1m ?? "") + '" style="width:5.5rem" title="100만 토큰당 출력 단가">' +
    '<button class="small" data-save-pricing="' + m.id + '">저장</button>';
  const providerTableHtml = (provider) => {
    const rows = byProvider(provider);
    return "<h3>" + providerLabel[provider] + "</h3>" +
      table(["모델명", "상태", "최적화 Tier 매핑", "컨텍스트 한도(토큰)", "생성 옵션", "단가(100만 토큰당 입력/출력)", "성능 테스트", "동작"], rows.map(m => [
        esc(m.name),
        m.is_active ? "활성" : "비활성",
        tierMappingHtml(m),
        contextWindowHtml(m),
        samplingOptionsHtml(m),
        pricingHtml(m),
        benchmarkHtml(m),
        '<div class="actions">' +
          '<button class="small" data-toggle-model-active="' + m.id + '" data-active="' + (m.is_active ? "0" : "1") + '">' +
          (m.is_active ? "비활성화" : "활성화") + "</button>" +
          (isAdmin ? '<button class="small danger" data-delete-model="' + m.id + '">삭제</button>' : "") +
          "</div>",
      ]));
  };

  content.innerHTML =
    '<section class="card"><h2>모델 레지스트리</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>여기 등록한 모델이 "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"·협업 워크스페이스의 "모델명" 입력창 자동완성에 뜹니다 — 목록에 없는 이름도 그대로 입력할 수 있어 자유 입력 자체는 막지 않습니다(참고용 자동완성).</li>' +
      '<li><b>불러오기</b>는 Ollama면 로컬 서버(OLLAMA_BASE_URL)에서, OpenAI/Anthropic이면 그 계정의 API 키로 실제 보유 중인 모델 목록을 조회합니다(토큰을 생성하지 않는 카탈로그 조회라 과금되지 않습니다) — 조회만으로는 아무것도 저장되지 않고, 선택 후 "선택한 모델 등록"을 눌러야 저장됩니다.</li>' +
      '<li><b>비활성화</b>는 자동완성에서만 빼고 등록 자체는 유지합니다.</li>' +
      '<li><b>삭제</b>는 레지스트리 항목만 지웁니다 — 이미 Agent에 저장된 모델 설정 자체는 안 바뀝니다.</li>' +
      '<li><b>최적화 Tier 매핑</b>은 "LLM 설정 최적화"(Agent 관리 화면의 "⚡ 최적화 추천")가 심층추론/정밀·신속/표준 역할에 이 모델을 최우선으로 추천할지 직접 지정합니다(모델 하나가 여러 tier에 동시 매핑될 수 있습니다) — 아무것도 체크하지 않으면 모델명 패턴 추정으로 폴백합니다. 새 모델 출시·provider 교체 시 여기 매핑만 바꾸면 되고, 몇 번이든 다시 바꿀 수 있습니다.</li>' +
      '<li><b>성능 테스트</b>는 고정된 짧은 프롬프트 1건을 이 모델에 실제로 보내 응답 속도(글자/초)를 재고, 그 속도만으로 tier를 추천합니다 — <b>속도만 보는 참고용이지 품질 평가가 아닙니다</b>(얼마나 똑똑한지는 측정하지 않습니다). "이 tier로 저장"을 눌러야 위 "최적화 Tier 매핑"에 실제로 반영됩니다. 클라우드 provider는 실제 API 비용이 발생합니다(Ollama는 로컬 연산이라 무료).</li>' +
      '<li>이 탭 전체가 admin 전용입니다 — manager를 포함한 다른 역할에는 메뉴 자체가 보이지 않습니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="mr-fetch-form">' +
    '<select id="mr-fetch-provider">' + providerOptionsHtml + "</select>" +
    '<button type="submit" class="small">불러오기</button>' +
    "</form>" +
    '<div id="mr-fetch-result"></div>' +
    // 등록하면서 추천 기본값 저장(2026-11-26) — 비워 두면 보수적 기본값(Ollama 8K 등)이라 긴 문서가 잘리기 쉽다.
    '<label class="muted" style="display:block;margin:0.4rem 0" title="Ollama는 모델 정보와 이 PC 메모리로, 클라우드는 계열별 공개 한도로 컨텍스트 한도·최대 출력·제작자 권장 top_k/top_p를 정해 저장합니다. 나중에 표에서 바꿀 수 있습니다.">' +
    '<input type="checkbox" id="mr-apply-rec" checked> 등록하면서 추천 기본값(컨텍스트 한도·생성 옵션)도 함께 저장</label>' +
    '<form class="inline" id="mr-manual-form">' +
    '<select id="mr-manual-provider">' + providerOptionsHtml + "</select>" +
    '<input id="mr-manual-name" placeholder="모델명 직접 입력(불러오기 없이 바로 등록)" style="flex:1;min-width:200px" required>' +
    '<button type="submit" class="small">수동 등록</button>' +
    "</form>" +
    providerTableHtml("anthropic") +
    providerTableHtml("openai") +
    providerTableHtml("ollama") +
    '<h3>실제 사용량(최근 30일)</h3><div id="mr-usage"><p class="muted">불러오는 중…</p></div>' +
    "</section>";
  // 실제 토큰 사용량(2026-11-22) — 모든 LLM 호출(대화·Agent 실행·파이프라인·판정)을 provider·모델별로.
  api("GET", "/api/llm-usage?days=30").then(u => {
    const box = document.getElementById("mr-usage");
    if (!box) return;
    if (!u.models.length) { box.innerHTML = '<p class="muted">아직 기록된 사용량이 없습니다.</p>'; return; }
    const fmt = n => Number(n || 0).toLocaleString();
    box.innerHTML = table(["provider", "모델", "호출", "입력 토큰", "출력 토큰", "금액"], u.models.map(r => [
      esc(r.provider), esc(r.model), fmt(r.calls), fmt(r.prompt_tokens), fmt(r.completion_tokens),
      r.cost === null ? '<span class="muted">단가 미입력</span>' : r.cost.toLocaleString(undefined, { maximumFractionDigits: 4 }),
    ])) + '<p class="muted">금액 합계(단가가 입력된 모델만): ' + u.total_cost.toLocaleString(undefined, { maximumFractionDigits: 4 }) +
      (u.unpriced_models ? ' · 단가 미입력 모델 ' + u.unpriced_models + '개는 토큰 수만 표시' : "") + "</p>";
  }).catch(() => {});

  document.getElementById("mr-fetch-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const provider = document.getElementById("mr-fetch-provider").value;
    const resultBox = document.getElementById("mr-fetch-result");
    resultBox.innerHTML = '<p class="muted">불러오는 중...</p>';
    let resp;
    try {
      resp = await api("GET", qs("/api/model-registry/fetch", { provider }));
    } catch (e) {
      resultBox.innerHTML = "";
      showMsg("global-msg", e.message, "error");
      return;
    }
    const registeredNames = new Set(byProvider(provider).map(m => m.name));
    resultBox.innerHTML = resp.models.length
      ? '<div style="max-height:220px;overflow-y:auto;border:1px solid #ddd;padding:0.5rem;margin:0.5rem 0">' +
        resp.models.map(name =>
          '<label style="display:block"><input type="checkbox" class="mr-candidate" value="' + esc(name) + '"' +
          (registeredNames.has(name) ? " disabled checked" : "") + "> " + esc(name) +
          (registeredNames.has(name) ? ' <span class="muted">(이미 등록됨)</span>' : "") + "</label>"
        ).join("") +
        "</div>" +
        '<button type="button" class="small primary" id="mr-register-selected">선택한 모델 등록</button>'
      : '<p class="muted">불러올 수 있는 모델이 없습니다.</p>';
    const registerBtn = document.getElementById("mr-register-selected");
    if (registerBtn) {
      registerBtn.onclick = async () => {
        const names = Array.from(resultBox.querySelectorAll(".mr-candidate:checked:not(:disabled)")).map(cb => cb.value);
        if (!names.length) { showMsg("global-msg", "등록할 모델을 선택하세요.", "error"); return; }
        try {
          const notes = [];
          for (const name of names) {
            const created = await api("POST", "/api/model-registry", { provider, name });
            notes.push(await _applyRecommendationAfterRegister(created.id, name));
          }
          showMsg("global-msg", names.length + "개 모델을 등록했습니다." + notes.filter(Boolean).join(" "), "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      };
    }
  });

  document.getElementById("mr-manual-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const manualName = document.getElementById("mr-manual-name").value;
      const created = await api("POST", "/api/model-registry", {
        provider: document.getElementById("mr-manual-provider").value,
        name: manualName,
      });
      const note = await _applyRecommendationAfterRegister(created.id, manualName);
      showMsg("global-msg", "모델을 등록했습니다." + (note ? " " + note : ""), "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-toggle-model-active]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/model-registry/" + btn.dataset.toggleModelActive + "/active", {
          is_active: btn.dataset.active === "1",
        });
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-model]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 모델을 레지스트리에서 지웁니다(되돌릴 수 없음, 이미 저장된 Agent의 모델 설정 자체는 안 바뀝니다). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/model-registry/" + btn.dataset.deleteModel);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-save-optimization-tiers]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.saveOptimizationTiers;
      const checks = content.querySelectorAll('.mr-tier-pick[data-model-id="' + modelId + '"]:checked');
      try {
        await api("PATCH", "/api/model-registry/" + modelId + "/optimization-tiers", {
          optimization_tiers: Array.from(checks).map(c => c.value),
        });
        showMsg("global-msg", "최적화 Tier 매핑을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-save-context-window]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.saveContextWindow;
      const input = content.querySelector('.mr-context-window[data-model-id="' + modelId + '"]');
      const raw = input.value.trim();
      try {
        await api("PATCH", "/api/model-registry/" + modelId + "/context-window", {
          context_window: raw === "" ? null : Number(raw),
        });
        showMsg("global-msg", "컨텍스트 한도를 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-save-pricing]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.savePricing;
      const val = (cls) => {
        const el = content.querySelector("." + cls + '[data-model-id="' + id + '"]');
        const raw = el ? el.value.trim() : "";
        return raw === "" ? null : Number(raw);
      };
      try {
        await api("PATCH", "/api/model-registry/" + id + "/pricing", {
          price_input_per_1m: val("mr-price-in"), price_output_per_1m: val("mr-price-out"),
        });
        showMsg("global-msg", "단가를 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-recommend-model]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.recommendModel;
      try {
        const r = await api("GET", "/api/model-registry/" + modelId + "/recommendation");
        if (!r.available) { showMsg("global-msg", (r.reasons || []).join(" "), "error"); return; }
        const setv = (cls, v) => {
          const el = content.querySelector("." + cls + '[data-model-id="' + modelId + '"]');
          if (el && v !== null && v !== undefined) { el.value = v; el.classList.add("recommended"); }
        };
        setv("mr-context-window", r.context_window);
        setv("mr-top-k", r.top_k);
        setv("mr-top-p", r.top_p);
        setv("mr-max-out", r.max_output_tokens);
        showMsg("global-msg", "추천값을 채웠습니다(" + r.source + ") — 확인 후 각 \"저장\"을 누르세요. 근거: " + (r.reasons || []).join(" · "), "ok");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-save-sampling-options]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.saveSamplingOptions;
      const k = content.querySelector('.mr-top-k[data-model-id="' + modelId + '"]');
      const p = content.querySelector('.mr-top-p[data-model-id="' + modelId + '"]');
      const mo = content.querySelector('.mr-max-out[data-model-id="' + modelId + '"]');
      const kRaw = k ? k.value.trim() : "";
      const pRaw = p ? p.value.trim() : "";
      const moRaw = mo ? mo.value.trim() : "";
      try {
        await api("PATCH", "/api/model-registry/" + modelId + "/sampling-options", {
          top_k: kRaw === "" ? null : Number(kRaw),
          top_p: pRaw === "" ? null : Number(pRaw),
          max_output_tokens: moRaw === "" ? null : Number(moRaw),
        });
        showMsg("global-msg", "생성 옵션을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-run-benchmark]").forEach(btn => {
    btn.onclick = async () => {
      const full = btn.dataset.mode === "full";
      const what = full
        ? "예열 1회 + 짧은 질문 3회(생각 기능이 있으면 생각 끔 3회 더) + 능력 점검 4건 + 긴 문서(약 7천 토큰) 1건"
        : "예열 1회 + 짧은 질문 3회(생각 기능이 있으면 생각 끔 3회 더)";
      if (!(await confirmModal("이 모델에 실제로 " + what + "을 보내 측정합니다 — 클라우드 provider는 실제 API 비용이 발생합니다(Ollama는 로컬 연산이라 무료). 계속할까요?"))) return;
      try {
        await withBusy(btn, full ? "점검 중…" : "측정 중…", () =>
          api("POST", "/api/model-registry/" + btn.dataset.runBenchmark + "/benchmark?mode=" + (full ? "full" : "quick"), {})
        );
        showMsg("global-msg", "성능 테스트가 끝났습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-apply-benchmark-tier]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.applyBenchmarkTier;
      const tiers = (btn.dataset.tiers || "").split(",").filter(Boolean);
      const existing = models.find(m => m.id === Number(modelId));
      const nextTiers = Array.from(new Set([...(existing.optimization_tiers || []), ...tiers]));
      try {
        await api("PATCH", "/api/model-registry/" + modelId + "/optimization-tiers", {
          optimization_tiers: nextTiers,
        });
        showMsg("global-msg", "추천 tier를 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}


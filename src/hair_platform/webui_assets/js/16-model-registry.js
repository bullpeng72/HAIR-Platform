/* ---------- 모델 레지스트리(사용자 요청, 2026-10-06, "운영 관리에 사용
   가능한 ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고
   등록된 모델을 불러오는 방식으로 UI/UX를 개선") ---------- */

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
      '<button class="small" data-save-sampling-options="' + m.id + '">저장</button>';
  };
  // 사용자 요청(2026-11-12, "ollama, openai 등 프로바이더의 모델 성능
  // Test하고 결과 표시, 티어 추천 기능") — 고정 테스트 프롬프트 1건을
  // 실제로 호출해 속도(글자/초)를 재고, 그 속도만으로 tier를
  // 추천한다(품질 평가 아님 — `model_benchmark.py` 모듈 docstring
  // 참고). "저장"은 직접 눌러야 기존 "최적화 Tier 매핑"에 반영된다
  // (추천이 자동 적용되지 않음 — ⚡ 최적화 추천과 같은 관례).
  const benchmarkHtml = (m) => {
    const b = m.latest_benchmark;
    let resultHtml;
    if (!b) {
      resultHtml = '<span class="muted">아직 테스트 안 함</span>';
    } else if (b.status === "failed") {
      resultHtml = '<span class="gate-badge gate-warn" title="' + esc(b.error || "") + '">실패</span> ' +
        '<span class="muted">' + esc(fmtTime(b.tested_at)) + '</span>';
    } else {
      resultHtml = b.chars_per_second.toFixed(1) + '자/초 · ' + b.latency_seconds.toFixed(1) + '초 소요 · ' +
        '<span class="gate-badge" title="속도만 반영한 참고용 추천입니다 — 실제 응답 품질은 직접 확인하세요">추천: ' +
        esc(_OPTIMIZATION_TIER_LABELS[b.recommended_tier] || b.recommended_tier) + '</span> ' +
        '<button class="small" data-apply-benchmark-tier="' + m.id + '" data-tier="' + b.recommended_tier + '">이 tier로 저장</button> ' +
        '<span class="muted">(' + esc(fmtTime(b.tested_at)) + ')</span>';
    }
    return resultHtml + '<br><button class="small" data-run-benchmark="' + m.id + '">성능 테스트</button>';
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
          for (const name of names) {
            await api("POST", "/api/model-registry", { provider, name });
          }
          showMsg("global-msg", names.length + "개 모델을 등록했습니다.", "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      };
    }
  });

  document.getElementById("mr-manual-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/model-registry", {
        provider: document.getElementById("mr-manual-provider").value,
        name: document.getElementById("mr-manual-name").value,
      });
      showMsg("global-msg", "모델을 등록했습니다.", "ok");
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
      if (!(await confirmModal("이 모델에 실제로 테스트 프롬프트 1건을 보내 응답 속도를 측정합니다 — 클라우드 provider는 실제 API 비용이 발생합니다(Ollama는 로컬 연산이라 무료). 계속할까요?"))) return;
      try {
        await withBusy(btn, "테스트 중…", () =>
          api("POST", "/api/model-registry/" + btn.dataset.runBenchmark + "/benchmark", {})
        );
        showMsg("global-msg", "성능 테스트가 끝났습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-apply-benchmark-tier]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.applyBenchmarkTier;
      const tier = btn.dataset.tier;
      const existing = models.find(m => m.id === Number(modelId));
      const nextTiers = Array.from(new Set([...(existing.optimization_tiers || []), tier]));
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


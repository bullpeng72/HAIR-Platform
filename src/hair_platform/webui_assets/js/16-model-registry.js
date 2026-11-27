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

// 펼쳐 둔 설정 패널(2026-11-27) — 저장·측정 뒤 다시 그려도 같은 모델을 펼친 채로 둔다.
let _mrOpenModelId = null;

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
  // ---- 화면 구성(2026-11-27 개선, "모델 설정 UI도 개선해줘") ----
  // 예전: 한 줄에 8칸(Tier·컨텍스트·생성 옵션·단가마다 "저장" 버튼 4개, 이름 없는 입력칸, 매우 긴 성능 테스트 칸).
  // 지금: 요약 한 줄 + "설정" 패널(이름 붙은 입력칸, 저장 한 번에 바뀐 항목만 저장, 성능 테스트 상세·이력).
  const _DEFAULT_CTX = { anthropic: 200000, openai: 128000, ollama: 8192 };
  const _fmtInt = (n) => Number(n).toLocaleString();
  const tierChipsHtml = (m) => (m.optimization_tiers || []).length
    ? (m.optimization_tiers || []).map(t => '<span class="gate-badge gate-ok">' + esc(_OPTIMIZATION_TIER_LABELS[t] || t) + "</span>").join(" ")
    : '<span class="muted" title="매핑하지 않으면 ⚡ 최적화 추천이 모델 이름으로 추정합니다">이름으로 추정</span>';
  const contextSummaryHtml = (m) => {
    const o = m.sampling_options || {};
    const ctx = m.context_window
      ? _fmtInt(m.context_window)
      : '<span class="muted" title="비어 있으면 provider 기본값을 씁니다">기본 ' + _fmtInt(_DEFAULT_CTX[m.provider] || 8192) + "</span>";
    return ctx + (o.max_output_tokens ? '<br><span class="muted">출력 ' + _fmtInt(o.max_output_tokens) + "</span>" : "");
  };
  const benchSummaryHtml = (m) => {
    const b = m.latest_benchmark;
    if (!b) return '<span class="muted">미측정</span>';
    if (b.status === "failed") return '<span class="gate-badge gate-warn" title="' + escAttr(b.error || "") + '">측정 실패</span>';
    const d = b.details;
    if (!d) return '<span class="muted" title="모델 로드 시간이 섞인 예전 방식 1회 측정 — 다시 측정하세요">' + b.chars_per_second.toFixed(1) + "자/초(예전 방식)</span>";
    const def = d.default || {};
    let html = _fmtNum(def.gen_tps, 1) + " tok/s";
    if (typeof d.estimated_stage_s === "number") html += ' · <span title="' + escAttr(d.estimate_basis || "") + '">단계 ' + Math.round(d.estimated_stage_s) + "초</span>";
    const full = d.checks ? d : (b.latest_full && b.latest_full.details);
    if (full && full.checks) {
      const entries = Object.entries(full.checks);
      const failed = entries.filter(([, c]) => !c.passed).map(([k]) => _BENCH_CHECK_LABELS[k] || k);
      html += "<br>" + (failed.length
        ? '<span class="gate-badge gate-warn" title="' + escAttr((full.warnings || []).join(" / ")) + '">점검 ' + (entries.length - failed.length) + "/" + entries.length + " · ✗ " + esc(failed.join(", ")) + "</span>"
        : '<span class="gate-badge gate-ok">점검 ' + entries.length + "/" + entries.length + " 통과</span>");
    } else {
      html += '<br><span class="muted">능력 점검 전</span>';
    }
    return html;
  };
  // 설정 패널 — 이름 붙은 입력칸. 저장 한 번에 바뀐 묶음만 각 API로 보낸다.
  const field = (label, inputHtml, hint) =>
    '<label class="mr-field"><span class="mr-field-label">' + esc(label) + "</span>" + inputHtml +
    (hint ? '<span class="mr-field-hint">' + hint + "</span>" : "") + "</label>";
  const numInput = (m, key, value, attrs) =>
    '<input type="number" data-mr-field="' + key + '" data-model-id="' + m.id + '" value="' + (value ?? "") + '" ' + attrs + ">";
  const settingsPanelHtml = (m) => {
    const o = m.sampling_options || {};
    const b = m.latest_benchmark;
    const recTiers = (b && b.details && b.details.recommended_tiers) || [];
    const tierBoxes = Object.keys(_OPTIMIZATION_TIER_LABELS).map(t =>
      '<label class="mr-check"><input type="checkbox" data-mr-tier value="' + t + '" data-model-id="' + m.id + '"' +
      ((m.optimization_tiers || []).includes(t) ? " checked" : "") + "> " + esc(_OPTIMIZATION_TIER_LABELS[t]) + "</label>").join("");
    let benchHtml;
    if (!b) benchHtml = '<p class="muted">아직 측정하지 않았습니다. 빠른 측정은 속도만, 전체 점검은 도구 호출·수치 보존·한국어 등 능력까지 봅니다.</p>';
    else if (b.status === "failed") benchHtml = '<p><span class="gate-badge gate-warn">측정 실패</span> ' + esc(b.error || "") + ' <span class="muted">(' + esc(fmtTime(b.tested_at)) + ")</span></p>";
    else {
      const modeLabel = b.details ? (b.details.mode === "full" ? "전체 점검" : "빠른 측정") : "예전 방식";
      benchHtml = "<p>" + _benchDetailHtml(b) + '<br><span class="muted">' + esc(modeLabel) + " · " + esc(fmtTime(b.tested_at)) + "</span></p>";
      if (b.latest_full) {
        const f = b.latest_full.details || {};
        benchHtml += '<p class="muted">최근 전체 점검(' + esc(fmtTime(b.latest_full.tested_at)) + "): 추천 " +
          ((f.recommended_tiers || []).map(t => esc(_OPTIMIZATION_TIER_LABELS[t] || t)).join(" · ") || "없음") +
          ((f.warnings || []).length ? " · ⚠ " + f.warnings.map(esc).join(" / ") : "") + "</p>";
      }
    }
    return '<div class="mr-panel" data-mr-panel="' + m.id + '">' +
      '<div class="mr-panel-grid">' +
        '<fieldset><legend>최적화 Tier</legend>' + tierBoxes +
          (recTiers.length
            ? '<p class="mr-field-hint">성능 테스트 추천: ' + recTiers.map(t => esc(_OPTIMIZATION_TIER_LABELS[t] || t)).join(" · ") +
              ' <button type="button" class="small" data-apply-benchmark-tier="' + m.id + '" data-tiers="' + escAttr(recTiers.join(",")) + '">추천대로 체크</button></p>'
            : "") +
          '<p class="mr-field-hint">⚡ 최적화 추천이 이 tier에 이 모델을 먼저 고릅니다. 모두 끄면 이름으로 추정합니다.</p>' +
        "</fieldset>" +
        '<fieldset><legend>컨텍스트·출력</legend>' +
          field("컨텍스트 한도(토큰)", numInput(m, "context_window", m.context_window, 'min="1" step="1" placeholder="기본 ' + _fmtInt(_DEFAULT_CTX[m.provider] || 8192) + '"'),
            m.provider === "ollama" ? "실제 컨텍스트 창(num_ctx)으로 전달 — 클수록 메모리를 더 씀" : "프롬프트를 이 한도 안으로 미리 줄임") +
          (m.provider === "anthropic" ? "" :
            field("최대 출력(토큰)", numInput(m, "max_output_tokens", o.max_output_tokens, 'min="256" step="256" placeholder="' + (m.provider === "ollama" ? "기본 32,768" : "provider 기본") + '"'),
              "한 번 응답의 상한(생각 토큰 포함)")) +
          // 동시 실행 상한(2026-11-27, 개선 계획 3-1) — 넘는 호출은 기다리고, 대화 응답이 파이프라인보다 먼저 자리를 받는다.
          field("동시 실행 상한", numInput(m, "max_concurrent", m.max_concurrent, 'min="1" max="64" step="1" placeholder="' + (m.provider === "ollama" ? "기본 1" : "상한 없음") + '"'),
            m.provider === "ollama"
              ? "이 모델에 한 번에 보내는 호출 수 — Ollama는 기본적으로 하나씩 처리하므로 1. OLLAMA_NUM_PARALLEL을 올렸다면 그 값으로"
              : "비우면 상한 없음 — API 속도 제한(분당 호출)에 걸리면 낮추세요") +
        "</fieldset>" +
        '<fieldset><legend>샘플링</legend>' +
          (m.provider === "openai" ? "" : field("top_k", numInput(m, "top_k", o.top_k, 'min="1" step="1" placeholder="모델 기본"'), "후보 단어 수 — 비우면 모델 기본값")) +
          field("top_p", numInput(m, "top_p", o.top_p, 'min="0.01" max="1" step="0.01" placeholder="모델 기본"'), "0~1 — 비우면 모델 기본값") +
        "</fieldset>" +
        '<fieldset><legend>단가(100만 토큰당)</legend>' +
          field("입력", numInput(m, "price_input_per_1m", m.price_input_per_1m, 'min="0" step="0.01" placeholder="미입력"'), "") +
          field("출력", numInput(m, "price_output_per_1m", m.price_output_per_1m, 'min="0" step="0.01" placeholder="미입력"'),
            "비우면 사용량 화면에 토큰 수만 표시 — 로컬 모델은 0") +
        "</fieldset>" +
      "</div>" +
      '<div class="actions mr-panel-actions">' +
        '<button type="button" class="small primary" data-mr-save="' + m.id + '">저장</button>' +
        '<button type="button" class="small" data-recommend-model="' + m.id + '" title="모델 정보(Ollama: 학습 최대 컨텍스트·구조·제작자 권장값, 클라우드: 계열별 공개 한도)와 이 PC 메모리로 컨텍스트 한도·최대 출력·top_k/top_p를 채웁니다 — 저장은 직접 누르세요.">추천값 채우기</button>' +
        '<button type="button" class="small" data-mr-close="' + m.id + '">닫기</button>' +
        '<span class="muted" data-mr-dirty="' + m.id + '"></span>' +
      "</div>" +
      '<fieldset class="mr-bench"><legend>성능 테스트</legend>' + benchHtml +
        '<div class="actions">' +
          '<button type="button" class="small" data-run-benchmark="' + m.id + '" data-mode="quick" title="예열 후 짧은 질문 3회 — 속도만(로컬 약 10~30초)">빠른 측정</button>' +
          '<button type="button" class="small" data-run-benchmark="' + m.id + '" data-mode="full" title="빠른 측정 + 도구 호출·JSON·수치 보존·한국어·생각 끄기 점검 + 긴 문서 읽기(로컬 약 30초~2분)">전체 점검</button>' +
          '<button type="button" class="small" data-mr-history="' + m.id + '">이력 보기</button>' +
        "</div>" +
        '<div data-mr-history-box="' + m.id + '"></div>' +
      "</fieldset>" +
    "</div>";
  };
  const providerTableHtml = (provider) => {
    const rows = byProvider(provider);
    const head = '<h3 class="mr-provider">' + providerLabel[provider] + ' <span class="muted">' + rows.length + "개</span></h3>";
    if (!rows.length) return head + '<p class="muted">등록된 모델이 없습니다 — 위 "모델 추가"에서 불러오거나 이름을 입력하세요.</p>';
    const filter = rows.length > 8
      ? '<input type="search" class="mr-filter" data-mr-filter="' + provider + '" placeholder="모델 이름 검색…" aria-label="모델 이름 검색">' : "";
    return head + filter + '<div class="table-wrap"><table class="mr-table"><thead><tr>' +
      "<th>모델</th><th>최적화 Tier</th><th>컨텍스트(토큰)</th><th>성능</th><th>동작</th></tr></thead><tbody>" +
      rows.map(m => {
        const open = _mrOpenModelId === m.id;
        return '<tr class="mr-row' + (m.is_active ? "" : " mr-inactive") + '" data-mr-row="' + m.id + '" data-mr-provider="' + provider + '" data-mr-name="' + escAttr(m.name.toLowerCase()) + '">' +
          '<td class="mr-name">' + esc(m.name) + (m.is_active ? "" : ' <span class="gate-badge gate-na" title="자동완성에서 빠짐">비활성</span>') + "</td>" +
          "<td>" + tierChipsHtml(m) + "</td>" +
          "<td>" + contextSummaryHtml(m) + "</td>" +
          "<td>" + benchSummaryHtml(m) + "</td>" +
          '<td><div class="actions">' +
            '<button type="button" class="small' + (open ? " primary" : "") + '" data-mr-toggle="' + m.id + '" aria-expanded="' + open + '">' + (open ? "설정 ▴" : "설정 ▾") + "</button>" +
            '<button type="button" class="small" data-toggle-model-active="' + m.id + '" data-active="' + (m.is_active ? "0" : "1") + '">' + (m.is_active ? "비활성화" : "활성화") + "</button>" +
            (isAdmin ? '<button type="button" class="small danger" data-delete-model="' + m.id + '">삭제</button>' : "") +
          "</div></td></tr>" +
          '<tr class="mr-detail" data-mr-detail="' + m.id + '"' + (open ? "" : " hidden") + '><td colspan="5">' + settingsPanelHtml(m) + "</td></tr>";
      }).join("") +
      "</tbody></table></div>";
  };

  content.innerHTML =
    '<section class="card"><h2>모델 레지스트리</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>여기 등록한 모델이 "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"·협업 워크스페이스의 "모델명" 입력창 자동완성에 뜹니다 — 목록에 없는 이름도 그대로 입력할 수 있습니다(참고용 자동완성).</li>' +
      '<li><b>목록 불러오기</b>는 Ollama면 로컬 서버(OLLAMA_BASE_URL)에서, OpenAI/Anthropic이면 그 계정의 API 키로 실제 보유 중인 모델 목록을 조회합니다(과금 없음) — 고른 뒤 "선택한 모델 등록"을 눌러야 저장됩니다.</li>' +
      '<li>모델마다 <b>설정 ▾</b>을 누르면 최적화 Tier·컨텍스트 한도·최대 출력·샘플링·단가를 한 화면에서 고치고 <b>저장</b> 한 번으로 바뀐 항목만 저장합니다. 저장하지 않은 변경이 있으면 표시됩니다. <b>추천값 채우기</b>는 모델 정보로 칸을 채웁니다(저장은 직접).</li>' +
      '<li><b>최적화 Tier</b>는 "⚡ 최적화 추천"이 심층추론/정밀·신속/표준 역할에 이 모델을 먼저 고를지 정합니다(여러 개 가능). 모두 끄면 모델 이름으로 추정합니다.</li>' +
      '<li><b>성능 테스트</b> — 빠른 측정(예열 후 3회: 생성 tok/s·응답 시간·생각 끔 시간)과 전체 점검(도구 호출·JSON·수치 보존·한국어·생각 끄기·긴 문서 → 단계 1회 예상 시간으로 tier 추천). 클라우드 provider는 실제 API 비용이 발생합니다(Ollama는 무료).</li>' +
      '<li><b>비활성화</b>는 자동완성에서만 빼고 등록은 유지합니다. <b>삭제</b>는 레지스트리 항목만 지웁니다 — Agent에 저장된 모델 설정은 안 바뀝니다. 이 탭은 admin 전용입니다.</li>' +
      '</ul>'
    ) +
    // 모델 추가(2026-11-27) — provider 선택 하나로 두 가지 방법(목록 불러오기 / 이름 직접 입력).
    '<fieldset class="mr-add"><legend>모델 추가</legend>' +
      '<form class="inline" id="mr-add-form">' +
        '<select id="mr-provider" aria-label="provider">' + providerOptionsHtml + "</select>" +
        '<button type="button" class="small" id="mr-fetch-btn" title="그 provider가 실제로 가진 모델 목록 조회(과금 없음)">목록 불러오기</button>' +
        '<span class="muted">또는</span>' +
        '<input id="mr-manual-name" placeholder="모델 이름 직접 입력" style="flex:1;min-width:200px">' +
        '<button type="submit" class="small">직접 등록</button>' +
      "</form>" +
      '<div id="mr-fetch-result"></div>' +
      // 등록하면서 추천 기본값 저장(2026-11-26) — 비워 두면 보수적 기본값(Ollama 8K 등)이라 긴 문서가 잘리기 쉽다.
      '<label class="muted" style="display:block;margin-top:0.4rem" title="Ollama는 모델 정보와 이 PC 메모리로, 클라우드는 계열별 공개 한도로 컨텍스트 한도·최대 출력·제작자 권장 top_k/top_p를 정해 저장합니다. 나중에 설정에서 바꿀 수 있습니다.">' +
      '<input type="checkbox" id="mr-apply-rec" checked> 등록하면서 추천 기본값(컨텍스트 한도·생성 옵션)도 함께 저장</label>' +
    "</fieldset>" +
    providerTableHtml("ollama") +
    providerTableHtml("anthropic") +
    providerTableHtml("openai") +
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

  const providerSel = document.getElementById("mr-provider");
  document.getElementById("mr-fetch-btn").addEventListener("click", async () => {
    const provider = providerSel.value;
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
      ? '<div class="mr-candidates">' +
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

  document.getElementById("mr-add-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const manualName = document.getElementById("mr-manual-name").value.trim();
    if (!manualName) { showMsg("global-msg", "모델 이름을 입력하거나 \"목록 불러오기\"를 누르세요.", "error"); return; }
    try {
      const created = await api("POST", "/api/model-registry", { provider: providerSel.value, name: manualName });
      const note = await _applyRecommendationAfterRegister(created.id, manualName);
      _mrOpenModelId = created.id;  // 등록한 모델의 설정을 바로 펼쳐 보여 준다
      showMsg("global-msg", "모델을 등록했습니다." + (note ? " " + note : ""), "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-mr-filter]").forEach(inp => {
    inp.addEventListener("input", () => {
      const q = inp.value.trim().toLowerCase();
      content.querySelectorAll('[data-mr-provider="' + inp.dataset.mrFilter + '"]').forEach(row => {
        const hide = q && !row.dataset.mrName.includes(q);
        row.hidden = !!hide;
        const detail = content.querySelector('[data-mr-detail="' + row.dataset.mrRow + '"]');
        if (detail && hide) detail.hidden = true;
      });
    });
  });

  // 설정 패널 펼치기·접기 — 펼친 모델은 새로 그려도(저장·측정 후) 펼친 채로 둔다.
  const setOpen = (id, open) => {
    const detail = content.querySelector('[data-mr-detail="' + id + '"]');
    const btn = content.querySelector('[data-mr-toggle="' + id + '"]');
    if (!detail || !btn) return;
    detail.hidden = !open;
    btn.textContent = open ? "설정 ▴" : "설정 ▾";
    btn.classList.toggle("primary", open);
    btn.setAttribute("aria-expanded", String(open));
    _mrOpenModelId = open ? Number(id) : (_mrOpenModelId === Number(id) ? null : _mrOpenModelId);
  };
  content.querySelectorAll("[data-mr-toggle]").forEach(btn => {
    btn.onclick = () => setOpen(btn.dataset.mrToggle, btn.getAttribute("aria-expanded") !== "true");
  });
  content.querySelectorAll("[data-mr-close]").forEach(btn => { btn.onclick = () => setOpen(btn.dataset.mrClose, false); });

  // 저장 안 한 변경 표시
  const markDirty = (id) => {
    const el = content.querySelector('[data-mr-dirty="' + id + '"]');
    if (el) el.textContent = "저장하지 않은 변경이 있습니다";
  };
  content.querySelectorAll("[data-mr-panel]").forEach(panel => {
    panel.addEventListener("input", (ev) => {
      if (ev.target.matches("[data-mr-field], [data-mr-tier]")) markDirty(panel.dataset.mrPanel);
    });
  });

  const panelValue = (id, key) => {
    const el = content.querySelector('[data-mr-field="' + key + '"][data-model-id="' + id + '"]');
    if (!el) return undefined;  // 그 provider에 없는 칸
    const raw = el.value.trim();
    return raw === "" ? null : Number(raw);
  };
  const same = (a, b) => (a ?? null) === (b ?? null);
  content.querySelectorAll("[data-mr-save]").forEach(btn => {
    btn.onclick = async () => {
      const id = Number(btn.dataset.mrSave);
      const m = models.find(x => x.id === id);
      const o = m.sampling_options || {};
      const saved = [];
      try {
        await withBusy(btn, "저장 중…", async () => {
          const tiers = Array.from(content.querySelectorAll('[data-mr-tier][data-model-id="' + id + '"]:checked')).map(c => c.value);
          if (tiers.slice().sort().join() !== (m.optimization_tiers || []).slice().sort().join()) {
            await api("PATCH", "/api/model-registry/" + id + "/optimization-tiers", { optimization_tiers: tiers });
            saved.push("최적화 Tier");
          }
          const ctx = panelValue(id, "context_window");
          if (!same(ctx, m.context_window)) {
            await api("PATCH", "/api/model-registry/" + id + "/context-window", { context_window: ctx });
            saved.push("컨텍스트 한도");
          }
          const next = { top_k: panelValue(id, "top_k"), top_p: panelValue(id, "top_p"), max_output_tokens: panelValue(id, "max_output_tokens") };
          if (Object.entries(next).some(([k, v]) => v !== undefined && !same(v, o[k]))) {
            const body = {};
            Object.entries(next).forEach(([k, v]) => { body[k] = v === undefined ? (o[k] ?? null) : v; });
            await api("PATCH", "/api/model-registry/" + id + "/sampling-options", body);
            saved.push("생성 옵션");
          }
          const maxc = panelValue(id, "max_concurrent");
          if (!same(maxc, m.max_concurrent)) {
            await api("PATCH", "/api/model-registry/" + id + "/max-concurrent", { max_concurrent: maxc });
            saved.push("동시 실행 상한");
          }
          const pin = panelValue(id, "price_input_per_1m"), pout = panelValue(id, "price_output_per_1m");
          if (!same(pin, m.price_input_per_1m) || !same(pout, m.price_output_per_1m)) {
            await api("PATCH", "/api/model-registry/" + id + "/pricing", { price_input_per_1m: pin, price_output_per_1m: pout });
            saved.push("단가");
          }
        });
        showMsg("global-msg", saved.length ? saved.join("·") + "을(를) 저장했습니다." : "바뀐 항목이 없습니다.", "ok");
        if (saved.length) refresh();
      } catch (e) {
        showMsg("global-msg", (saved.length ? saved.join("·") + "은(는) 저장됐고, 나머지에서 오류: " : "") + e.message, "error");
        if (saved.length) refresh();
      }
    };
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
  content.querySelectorAll("[data-recommend-model]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.recommendModel;
      try {
        const r = await api("GET", "/api/model-registry/" + modelId + "/recommendation");
        if (!r.available) { showMsg("global-msg", (r.reasons || []).join(" "), "error"); return; }
        const setv = (key, v) => {
          const el = content.querySelector('[data-mr-field="' + key + '"][data-model-id="' + modelId + '"]');
          if (el && v !== null && v !== undefined) { el.value = v; el.classList.add("recommended"); }
        };
        setv("context_window", r.context_window);
        setv("top_k", r.top_k);
        setv("top_p", r.top_p);
        setv("max_output_tokens", r.max_output_tokens);
        markDirty(modelId);
        showMsg("global-msg", "추천값을 채웠습니다(" + r.source + ") — 확인 후 \"저장\"을 누르세요. 근거: " + (r.reasons || []).join(" · "), "ok");
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
      _mrOpenModelId = Number(btn.dataset.runBenchmark);
      try {
        await withBusy(btn, full ? "점검 중…" : "측정 중…", () =>
          api("POST", "/api/model-registry/" + btn.dataset.runBenchmark + "/benchmark?mode=" + (full ? "full" : "quick"), {})
        );
        showMsg("global-msg", "성능 테스트가 끝났습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  // 성능 테스트 추천 tier — 체크만 바꾸고 저장은 "저장"으로(다른 칸과 같은 방식).
  content.querySelectorAll("[data-apply-benchmark-tier]").forEach(btn => {
    btn.onclick = () => {
      const modelId = btn.dataset.applyBenchmarkTier;
      const tiers = (btn.dataset.tiers || "").split(",").filter(Boolean);
      content.querySelectorAll('[data-mr-tier][data-model-id="' + modelId + '"]').forEach(cb => {
        if (tiers.includes(cb.value)) cb.checked = true;
      });
      markDirty(modelId);
    };
  });
  content.querySelectorAll("[data-mr-history]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.mrHistory;
      const box = content.querySelector('[data-mr-history-box="' + id + '"]');
      try {
        const rows = await api("GET", "/api/model-registry/" + id + "/benchmarks");
        box.innerHTML = rows.length ? table(["측정 시각", "방식", "생성 tok/s", "응답(초)", "단계 예상(초)", "추천", "점검 실패"], rows.slice(0, 20).map(r => {
          const d = r.details || {};
          const failed = Object.entries(d.checks || {}).filter(([, c]) => !c.passed).map(([k]) => _BENCH_CHECK_LABELS[k] || k);
          return [
            esc(fmtTime(r.tested_at)),
            r.status === "failed" ? '<span class="gate-badge gate-warn">실패</span>' : esc(r.details ? (d.mode === "full" ? "전체 점검" : "빠른 측정") : "예전 방식"),
            r.details ? _fmtNum((d.default || {}).gen_tps, 1) : '<span class="muted">' + _fmtNum(r.chars_per_second, 1) + "자/초</span>",
            _fmtNum(r.latency_seconds, 1),
            typeof d.estimated_stage_s === "number" ? String(Math.round(d.estimated_stage_s)) : "",
            esc(((d.recommended_tiers || (r.recommended_tier ? [r.recommended_tier] : [])).map(t => _OPTIMIZATION_TIER_LABELS[t] || t)).join(" · ")),
            esc(failed.join(", ")),
          ];
        })) : '<p class="muted">이력이 없습니다.</p>';
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}


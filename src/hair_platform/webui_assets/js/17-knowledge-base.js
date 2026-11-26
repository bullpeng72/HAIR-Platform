/* ---------- 지식 베이스(VectorDB/GraphDB 관리) ---------- */

// 사용자 요청(2026-10-26, "공통의 지식을 관리하기 위해 Admin에 VectorDB,
// GraphDB 관리 기능을 추가해줘") — 페이지네이션 offset을 탭 밖(모듈
// 스코프)에 둔다. `refresh()`는 `selectTab()`으로 이 함수를 처음부터
// 다시 부르므로(Crew 빌더의 `_pendingCrewBuilderTaskId`와 같은 이유),
// "다음" 버튼을 눌렀을 때 이 값이 0으로 되돌아가면 페이지 이동이 전혀
// 안 되는 것처럼 보인다.
let _kbChunkOffset = 0;
const _KB_CHUNK_LIMIT = 20;

// 지식 등록 요청 목록의 상태 필터(2026-11-20) — 화면을 다시 그려도 유지.
let _kbSubmissionFilter = "pending";
const _KB_SUBMISSION_FILTERS = [
  ["pending", "승인 대기"], ["registering", "등록 중"], ["registered", "등록됨"],
  ["rejected", "반려"], ["failed", "실패"], ["withdrawn", "회수"], ["", "전체"],
];

function _knowledgeSubmissionsSection(submissions) {
  const counts = {};
  submissions.forEach(s => { counts[s.status] = (counts[s.status] || 0) + 1; });
  const rows = submissions.filter(s => !_kbSubmissionFilter || s.status === _kbSubmissionFilter);
  const isAdmin = ME.role === "admin";
  const filterBar = '<div class="inline" style="margin-bottom:0.5rem;gap:0.3rem;flex-wrap:wrap">' +
    _KB_SUBMISSION_FILTERS.map(([value, text]) => {
      const n = value ? (counts[value] || 0) : submissions.length;
      return '<button type="button" class="small' + (_kbSubmissionFilter === value ? " primary" : "") +
        '" data-kb-sub-filter="' + value + '">' + text + " " + n + '</button>';
    }).join("") + '</div>';
  const actionsFor = (s) => {
    let html = "";
    if (s.status === "pending" && isAdmin) html += '<button class="small" data-goto-approvals>결재 화면으로</button>';
    if (s.status === "pending" && s.submitted_by === ME.id) html += '<button class="small" data-kb-sub-withdraw="' + s.id + '">회수</button>';
    if (s.status === "failed" && isAdmin) html += '<button class="small" data-kb-sub-retry="' + s.id + '">재시도</button>';
    return html ? '<div class="actions">' + html + '</div>' : '<span class="muted">-</span>';
  };
  const decisionCell = (s) => {
    if (s.status === "rejected") return esc(s.decided_by_username || "") + " · 사유: " + esc(s.reject_reason || "");
    if (s.status === "failed") return '<span class="muted">' + esc(s.error || "") + '</span>';
    if (s.decided_by_username) return esc(s.decided_by_username) + " · " + esc(fmtTime(s.decided_at));
    return '<span class="muted">-</span>';
  };
  return '<section class="card"><h2>지식 등록 요청</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>대화 메시지·승인된 산출물의 "지식베이스로 보내기"로 들어온 요청입니다 — admin이 <b>결재</b> 탭에서 승인해야 지식베이스에 등록(임베딩)됩니다. 반려할 때는 사유가 필요합니다.</li>' +
      '<li>등록되는 내용은 <b>제출 시점의 스냅샷</b>입니다 — 원본 메시지/산출물을 나중에 고치거나 지워도 등록된 지식은 바뀌지 않습니다. 바뀐 내용을 반영하려면 원본에서 다시 보내면 "갱신 요청"이 되고, 승인 시 기존 지식을 대체합니다.</li>' +
      '<li>admin이 직접 보낸 건과 아래 "지식 자료실"/"텍스트 직접 입력"은 결재 없이 바로 등록됩니다.</li>' +
      '</ul>'
    ) +
    filterBar +
    table(["ID", "제목", "출처", "제출자", "상태", "제출일", "처리", "동작"], rows.map(s => [
      s.id,
      esc(s.title) + (s.supersedes_id ? ' <span class="gate-badge gate-warn">갱신</span>' : ''),
      esc(s.source_label),
      esc(s.submitted_by_username || ("#" + s.submitted_by)),
      esc(label(s.status)),
      esc(fmtTime(s.created_at)),
      decisionCell(s),
      actionsFor(s),
    ])) +
    '</section>';
}

// 지식 문서 공개 범위 선택(2026-11-25, P1-5) — manager는 "admin만"을 고르거나 그 상태를 바꿀 수 없다(서버도 막음).
function _kbVisibilitySelectHtml(d, teams) {
  const v = d.visibility || "all";
  const locked = ME.role !== "admin" && v === "admin";
  const opts = [["all", "전체"], ["manager", "manager 이상"]];
  if (ME.role === "admin" || v === "admin") opts.push(["admin", "admin만"]);
  (teams || []).forEach(t => opts.push(["team:" + t.id, "팀: " + t.name]));
  if (!opts.some(o => o[0] === v)) opts.push([v, v]);
  return '<select data-kb-visibility="' + d.id + '"' + (locked ? " disabled" : "") + '>' +
    opts.map(o => '<option value="' + escAttr(o[0]) + '"' + (o[0] === v ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") +
    "</select>";
}

async function renderKnowledgeBase(content) {
  const [embeddingConfig, ragSearchConfig, knowledgeDocs, chunkStats, chunkPage, graphStats, relationships, knowledgeSubmissions, kbTeams] = await Promise.all([
    api("GET", "/api/embedding-config"),
    api("GET", "/api/rag-search-config"),
    api("GET", "/api/documents"),
    api("GET", "/api/knowledge-chunks/stats"),
    api("GET", qs("/api/knowledge-chunks", { limit: _KB_CHUNK_LIMIT, offset: _kbChunkOffset })),
    api("GET", "/api/graph/stats"),
    api("GET", "/api/graph/relationships"),
    api("GET", "/api/knowledge-submissions"),
    api("GET", "/api/teams").catch(() => []),  // 공개 범위 "팀" 선택지(2026-11-25, P1-5)
  ]);

  const modelBreakdown = chunkStats.by_model.length
    ? chunkStats.by_model.map(m => esc(m.embedding_provider) + "/" + esc(m.embedding_model) + ": " + m.count + "건").join(", ")
    : "없음";
  const chunkRows = chunkPage.items.map(c => [
    c.source_document_filename ? ("문서: " + esc(c.source_document_filename))
      : (c.source_agent_role_name ? ("Agent: " + esc(c.source_agent_role_name)) : "기타"),
    esc(c.text.length > 80 ? c.text.slice(0, 80) + "…" : c.text),
    String(c.text.length),
    esc(c.embedding_provider) + "/" + esc(c.embedding_model),
    esc(fmtTime(c.created_at)),
    ME.role === "admin"
      ? '<button class="small danger" data-delete-chunk="' + c.id + '">삭제</button>'
      : '<span class="muted">-</span>',
  ]);
  const pageStart = chunkPage.total ? _kbChunkOffset + 1 : 0;
  const pageEnd = Math.min(_kbChunkOffset + _KB_CHUNK_LIMIT, chunkPage.total);

  content.innerHTML =
    _knowledgeSubmissionsSection(knowledgeSubmissions) +
    // 사용자 요청(2026-10-26, "embedding 모델 설정 기능을 구현해줘
    // (하드코딩 하지않도록)") — 원래 "모델 레지스트리" 탭에 있었으나,
    // VectorDB/GraphDB 관리 기능이 이 탭으로 모이면서 함께 옮겼다
    // (코드는 그대로, 위치만 이동).
    '<section class="card"><h2>임베딩 모델 설정(VectorDB 검색용)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"내부지식검색" 스킬을 가진 Agent가 과거 산출물을 검색할 때 쓰는 임베딩 provider/모델입니다 — 채팅용 모델과는 별개입니다.</li>' +
      '<li>서로 다른 모델로 만든 벡터는 비교하지 않으므로(의미 공간이 다름) 시스템 전체에 하나만 설정합니다. 바꾸면 그 이전에 다른 모델로 색인된 과거 산출물은 더 이상 검색되지 않습니다(다시 색인해야 함).</li>' +
      '<li>설정한 적이 없으면 자동판별(OPENAI_API_KEY가 있으면 openai, 없으면 로컬 ollama) 결과를 보여줍니다 — 아래 폼으로 저장하면 그걸 명시적으로 고정합니다.</li>' +
      '<li>provider를 고르면 그 provider가 실제로 보유한 임베딩 모델 목록을 자동으로 불러와 "모델 선택" 드롭다운에 채워줍니다(Ollama는 로컬 서버, OpenAI는 API 키로 조회 — 과금 없는 카탈로그 조회입니다). 드롭다운에서 고르면 아래 모델명 칸에 그대로 채워집니다 — 목록에 없는 이름이 필요하면 그 칸에 직접 입력해도 됩니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (embeddingConfig.is_explicit ? '<b>설정됨</b>' : '<span class="muted">자동판별 중(아직 명시적으로 설정 안 함)</span>') +
    ' — provider: <b>' + esc(embeddingConfig.provider) + '</b>, 모델: <b>' + esc(embeddingConfig.model) + '</b></p>' +
    '<form class="inline" id="embedding-config-form">' +
    '<select id="ec-provider">' +
    '<option value="openai"' + (embeddingConfig.provider === "openai" ? " selected" : "") + '>OpenAI</option>' +
    '<option value="ollama"' + (embeddingConfig.provider === "ollama" ? " selected" : "") + '>Ollama(로컬)</option>' +
    '</select>' +
    '<select id="ec-model-pick" style="min-width:220px"><option value="">모델 목록 불러오는 중…</option></select>' +
    '<input id="ec-model" placeholder="모델명(예: mxbai-embed-large:latest)" value="' + escAttr(embeddingConfig.model) + '" style="flex:1;min-width:220px" required>' +
    '<button type="submit" class="small primary">저장</button>' +
    '<span id="ec-fetch-status" class="muted"></span>' +
    '</form>' +
    "</section>" +
    // 사용자 요청(2026-11-03, "RAG 검색 품질 향상... 우선순위를 정해
    // 진행해줘") — top_k(검색 결과 개수)와 min_score(관련성 하한,
    // 코사인 유사도 기준)를 admin/manager가 조정할 수 있게 한다.
    '<section class="card"><h2>RAG 검색 설정</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>top_k: 검색 1회당 가져올 결과 개수(기본 3개).</li>' +
      '<li>min_score: 관련성 하한(코사인 유사도, 0.0~1.0) — 이보다 낮은 결과는 무관한 것으로 보고 제외합니다(기본 0.2). 너무 높이면 관련 있는 결과도 걸러질 수 있습니다.</li>' +
      '<li>대화·단일 Agent 실행에서 "내부지식검색" 결과를 요청 앞에 자동으로 넣을 때는 한 단계를 더 거칩니다 — 코사인이 아주 높거나(0.9 이상) 요청과 단어가 실제로 겹치는 결과만 넣습니다(임베딩 모델에 따라 무관한 자료도 코사인이 높게 나와, 요청과 상관없는 지식이 매번 끼어드는 것을 막기 위함). 걸러져 남는 게 없으면 "관련 자료를 찾지 못했다"고 알립니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (ragSearchConfig.is_explicit ? '<b>설정됨</b>' : '<span class="muted">기본값 사용 중(아직 명시적으로 설정 안 함)</span>') +
    ' — top_k: <b>' + esc(ragSearchConfig.top_k) + '</b>, min_score: <b>' + esc(ragSearchConfig.min_score) + '</b></p>' +
    '<form class="inline" id="rag-search-config-form">' +
    '<input id="rsc-top-k" type="number" min="1" step="1" placeholder="top_k" value="' + escAttr(ragSearchConfig.top_k) + '" style="width:100px" required>' +
    '<input id="rsc-min-score" type="number" min="0" max="1" step="0.05" placeholder="min_score" value="' + escAttr(ragSearchConfig.min_score) + '" style="width:120px" required>' +
    '<button type="submit" class="small primary">저장</button>' +
    '</form>' +
    "</section>" +
    // 사용자 요청(2026-10-26, "대화창 및 에이전트로 구성된 업무에서
    // 외부 자료(마크다운, pdf, html)를 입력받을 수 있도록") — 역시
    // 코드는 그대로, 위치만 이동.
    '<section class="card"><h2>지식 자료실(RAG 참고 자료 — 파일 업로드)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>여기 올린 자료는 대화와 무관하게 전체 시스템에서 공유됩니다 — "내부지식검색" 스킬을 가진 모든 Agent가 검색할 수 있습니다(대화창·AI 에이전트 관리에서 올린 파일도 똑같은 곳에 쌓입니다).</li>' +
      '<li>지원 형식: .md / .markdown / .txt / .html / .htm / .pdf / .csv / .xlsx / .docx (각 최대 10MB) / .pptx(최대 25MB). 엑셀은 시트당 5,000행까지만 읽습니다(초과분은 "생략" 표시).</li>' +
      '</ul>'
    ) +
    // 직접 등록(업로드·텍스트 입력·삭제)은 admin 전용(2026-11-20 개편) —
    // manager는 조회만 하고, 지식은 "지식베이스로 보내기"(결재)로 넣는다.
    (ME.role === "admin"
      ? '<form class="inline" id="kd-upload-form">' +
        '<input type="file" id="kd-file-input" accept=".md,.markdown,.txt,.html,.htm,.pdf,.csv,.xlsx,.docx,.pptx" required>' +
        '<button type="submit" class="small primary">업로드·색인</button>' +
        '</form>'
      : '<p class="muted">자료 직접 등록·삭제는 admin만 할 수 있습니다 — 지식을 추가하려면 대화 메시지나 승인된 산출물에서 "지식베이스로 보내기"를 쓰세요(admin 결재 후 등록).</p>') +
    '<p class="muted">🔒 <b>공개 범위</b> — 그 문서를 검색 근거로 쓸 수 있는 사람(요청한 사람 기준)입니다. 기본은 전체 공개이고, admin은 늘 전부 봅니다. "admin만"은 admin만 정할 수 있습니다.</p>' +
    table(["파일명", "출처", "업로더", "글자수", "조각수", "업로드일", "공개 범위", "동작"], knowledgeDocs.map(d => [
      esc(d.filename),
      d.source_type === "submission"
        ? "결재 등록(요청 #" + esc(d.submission_id) + ")"
        : "직접 등록",
      esc(d.uploaded_by_username),
      String(d.char_count),
      String(d.chunk_count),
      esc(fmtTime(d.created_at)),
      _kbVisibilitySelectHtml(d, kbTeams),
      ME.role === "admin"
        ? '<button class="small danger" data-delete-document="' + d.id + '">삭제</button>'
        : '<span class="muted">-</span>',
    ])) +
    (knowledgeDocs.length ? "" : '<p class="muted">아직 올린 자료가 없습니다.</p>') +
    "</section>" +
    // 신규 — 파일 없이 텍스트를 바로 붙여넣어 색인(admin 전용, 위와 같은 이유).
    (ME.role === "admin"
      ? '<section class="card"><h2>텍스트 직접 입력</h2>' +
        '<p class="muted">파일을 만들기 번거로운 짧은 메모·정책 요약 등을 그대로 붙여넣어 색인합니다. 파일 업로드와 같은 전역 VectorDB에 들어갑니다.</p>' +
        '<form id="kb-text-form">' +
        '<input id="kb-text-label" placeholder="라벨(예: 2026 휴가 정책 요약 — 비우면 &quot;직접 입력&quot;)" style="width:100%;box-sizing:border-box;margin-bottom:0.4rem">' +
        '<textarea id="kb-text-body" placeholder="색인할 텍스트를 붙여넣으세요" rows="4" style="width:100%;box-sizing:border-box" required></textarea>' +
        '<button type="submit" class="small primary" style="margin-top:0.4rem">저장·색인</button>' +
        '</form>' +
        "</section>"
      : "") +
    // 신규 — 낱개 조각 조회/삭제 + 통계.
    '<section class="card"><h2>저장된 지식 조각(Chunk)</h2>' +
    '<p class="muted">전체 ' + chunkStats.total_chunks + '개 조각 · ' + chunkStats.total_characters + '자 · 모델별 ' + modelBreakdown + ' · 검색 엔진 ' + esc(chunkStats.vector_backend || 'sqlite') + (chunkStats.vector_backend === 'chroma' ? '(벡터 DB)' : '(전체 비교)') + '</p>' +
    '<p class="muted">출처 — Agent 산출물: ' + chunkStats.by_source.agent_output + '건, 업로드/직접입력: ' + chunkStats.by_source.uploaded_document + '건, 기타: ' + chunkStats.by_source.other + '건</p>' +
    table(["출처", "내용(미리보기)", "글자수", "모델", "생성일", "동작"], chunkRows) +
    (chunkRows.length ? "" : '<p class="muted">저장된 조각이 없습니다.</p>') +
    '<div class="inline">' +
    '<button type="button" class="small" id="kb-chunks-prev"' + (_kbChunkOffset <= 0 ? " disabled" : "") + '>이전</button>' +
    '<span class="muted">' + pageStart + '–' + pageEnd + ' / ' + chunkPage.total + '</span>' +
    '<button type="button" class="small" id="kb-chunks-next"' + (_kbChunkOffset + _KB_CHUNK_LIMIT >= chunkPage.total ? " disabled" : "") + '>다음</button>' +
    '</div>' +
    "</section>" +
    // 신규 — GraphDB는 별도 저장소가 없어 조회+통계만 제공.
    '<section class="card"><h2>GraphDB 관계 조회</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>GraphDB는 별도 저장소가 없습니다 — "사람 인력 관리"(상사 지정)·"결재"(배정 승인) 데이터를 관계로 읽어 보여주는 것뿐이라, 여기서 입력/삭제할 대상이 없습니다.</li>' +
      '<li>관계 자체를 바꾸려면 "사람 인력 관리"에서 상사를 지정하거나, 배정을 승인/반려하세요.</li>' +
      '</ul>'
    ) +
    '<p class="muted">상사가 지정된 사용자 ' + graphStats.users_with_manager_count + '명 · 직속 관리자 ' + graphStats.distinct_managers_count + '명 · 확정된 배정 ' + graphStats.confirmed_assignments_count + '건</p>' +
    // 사용자 요청(2026-11-03, "요청자ID, 리소스, Task ID만으로는 관계를
    // 파악하기 어려워... 가독성 높고 의미있는 정보전달이 되기 위한
    // 노출 항목을 제안해줘" → "그렇게 수정해줘") — 원시 ID 대신
    // 사람이 읽을 이름/상태로 바꾼다. Task ID는 업무명 옆 괄호로
    // 축소 표기(별도 컬럼으로 안 둠 — 매니저가 "몇 번"보다 "뭘 하는
    // 업무인지"를 먼저 읽는 게 자연스러움).
    table(["요청자", "업무명", "배정된 리소스", "업무 상태"], relationships.map(r => [
      esc(r.requested_by_username) + " (user#" + r.requested_by + ")",
      esc(r.role_name) + " (#" + r.task_id + ")",
      esc(LABELS[r.resource_type] || r.resource_type) + ": " + esc(r.resource_name || (r.resource_type + " #" + r.resource_id)),
      esc(LABELS[r.task_status] || r.task_status),
    ])) +
    (relationships.length ? "" : '<p class="muted">확정된 배정 관계가 없습니다.</p>') +
    "</section>";

  document.getElementById("embedding-config-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("PUT", "/api/embedding-config", {
        provider: document.getElementById("ec-provider").value,
        model: document.getElementById("ec-model").value,
      });
      showMsg("global-msg", "임베딩 모델 설정을 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-11-10, "ollama, openai 선택시 가용한 모델이 자동
  // 표시되도록") — provider를 고르는 순간(초기 렌더 포함) 그 provider가
  // 실제로 보유한 모델을 조회해 <select id="ec-model-pick">에 채운다.
  // <input list>+<datalist> 콤보박스로 먼저 만들었으나(사용자 보고,
  // "openai, ollama 선택해도 임베딩 모델이 선택이 안됨") 브라우저마다
  // 드롭다운 화살표가 아예 안 보이거나 클릭으로 못 고르는 경우가
  // 있어 — 명확하게 클릭해서 고르는 <select>로 바꿨다. 고르면 그
  // 값을 실제 저장 대상인 `#ec-model` 입력란에 그대로 채워 넣고,
  // 그 입력란은 계속 자유 텍스트로 직접 고칠 수 있게 살려둔다(조회
  // 실패해도 수동 입력으로 저장 가능 — model_registry "불러오기"와
  // 같은 원칙, 핵심 기능은 절대 안 깨짐).
  async function _refreshEmbeddingModelCandidates() {
    const provider = document.getElementById("ec-provider").value;
    const statusEl = document.getElementById("ec-fetch-status");
    const pickEl = document.getElementById("ec-model-pick");
    const currentModel = document.getElementById("ec-model").value;
    pickEl.innerHTML = '<option value="">불러오는 중…</option>';
    statusEl.textContent = "";
    try {
      const resp = await api("GET", qs("/api/embedding-config/fetch", { provider }));
      if (!resp.models.length) {
        pickEl.innerHTML = '<option value="">(불러올 모델 없음 — 아래에 직접 입력)</option>';
        statusEl.textContent = "";
        return;
      }
      pickEl.innerHTML = '<option value="">모델 선택…(' + resp.models.length + '개)</option>' +
        resp.models.map(name =>
          '<option value="' + escAttr(name) + '"' + (name === currentModel ? " selected" : "") + '>' + esc(name) + '</option>'
        ).join("");
      statusEl.textContent = "";
    } catch (e) {
      pickEl.innerHTML = '<option value="">(목록을 못 불러옴 — 아래에 직접 입력)</option>';
      statusEl.textContent = e.message;
    }
  }
  document.getElementById("ec-provider").addEventListener("change", _refreshEmbeddingModelCandidates);
  document.getElementById("ec-model-pick").addEventListener("change", (ev) => {
    if (ev.target.value) document.getElementById("ec-model").value = ev.target.value;
  });
  _refreshEmbeddingModelCandidates();

  document.getElementById("rag-search-config-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("PUT", "/api/rag-search-config", {
        top_k: Number(document.getElementById("rsc-top-k").value),
        min_score: Number(document.getElementById("rsc-min-score").value),
      });
      showMsg("global-msg", "RAG 검색 설정을 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("kd-upload-form")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fileInput = document.getElementById("kd-file-input");
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const result = await apiUpload("/api/documents", formData);
      showMsg("global-msg", result.filename + " 업로드 완료(" + result.char_count + "자, " + result.chunk_count + "개 조각으로 색인됨).", "ok");
      _kbChunkOffset = 0;
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-kb-visibility]").forEach(sel => {
    sel.onchange = async () => {
      try {
        await api("PATCH", "/api/documents/" + sel.dataset.kbVisibility + "/visibility", { visibility: sel.value });
        showMsg("global-msg", "공개 범위를 바꿨습니다.", "ok");
      } catch (e) { showMsg("global-msg", e.message, "error"); refresh(); }
    };
  });
  content.querySelectorAll("[data-delete-document]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await confirmModal("이 자료를 삭제합니다(색인된 조각도 함께 지워집니다). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/documents/" + btn.getAttribute("data-delete-document"));
        showMsg("global-msg", "삭제했습니다.", "ok");
        _kbChunkOffset = 0;
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  // 지식 등록 요청(2026-11-20) — 상태 필터/회수/재시도/결재 화면 이동.
  content.querySelectorAll("[data-kb-sub-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      _kbSubmissionFilter = btn.dataset.kbSubFilter;
      refresh();
    });
  });
  content.querySelectorAll("[data-goto-approvals]").forEach(btn => {
    btn.addEventListener("click", () => selectTab("approvals"));
  });
  content.querySelectorAll("[data-kb-sub-withdraw]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await confirmModal("이 지식 등록 요청을 회수합니다. 계속할까요?"))) return;
      try {
        await api("POST", "/api/knowledge-submissions/" + btn.dataset.kbSubWithdraw + "/withdraw");
        showMsg("global-msg", "회수했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });
  content.querySelectorAll("[data-kb-sub-retry]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        await withBusy(btn, "재시도 중…", () =>
          api("POST", "/api/knowledge-submissions/" + btn.dataset.kbSubRetry + "/retry"));
        showMsg("global-msg", "다시 등록을 시작했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  document.getElementById("kb-text-form")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = document.getElementById("kb-text-body").value;
    const label = document.getElementById("kb-text-label").value;
    try {
      const result = await api("POST", "/api/documents/text", label ? { text, label } : { text });
      showMsg("global-msg", "저장 완료(" + result.char_count + "자, " + result.chunk_count + "개 조각으로 색인됨).", "ok");
      _kbChunkOffset = 0;
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-delete-chunk]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await confirmModal("이 조각을 삭제합니다. 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/knowledge-chunks/" + btn.getAttribute("data-delete-chunk"));
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  const prevBtn = document.getElementById("kb-chunks-prev");
  if (prevBtn) prevBtn.addEventListener("click", () => {
    _kbChunkOffset = Math.max(0, _kbChunkOffset - _KB_CHUNK_LIMIT);
    refresh();
  });
  const nextBtn = document.getElementById("kb-chunks-next");
  if (nextBtn) nextBtn.addEventListener("click", () => {
    _kbChunkOffset += _KB_CHUNK_LIMIT;
    refresh();
  });
}


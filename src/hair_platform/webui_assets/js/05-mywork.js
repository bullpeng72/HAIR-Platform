/* ---------- 내 업무(협업 워크스페이스, 사용자 요청 2026-09-28) ---------- */

async function renderMyWork(content) {
  const rows = await api("GET", "/api/my-work");
  content.innerHTML =
    '<section class="card"><h2>내 업무</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>확정된 배정 중 내가 직접 할 일(사람 배정 당사자)이거나 내가 AI에게 맡긴 일(Agent 배정 요청자)이거나, 내가 속한 팀이 배정된 일(팀 리더/팀원)만 보입니다.</li>' +
      '<li>"열기"를 누르면 그 업무의 협업 워크스페이스(결과물 타임라인)가 아래에 펼쳐집니다.</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
    // 분석하고 부족한 기능을 개선해줘") — 이전엔 "결과물 수"·"최근
    // 결과물" 시각만 있어 지금 상태가 뭔지, 최종 결과물(승인)이 나왔는지
    // 업무를 직접 열어보기 전엔 알 수 없었다. "상태"·"최종 결과물"
    // 열을 추가한다.
    // 머리글을 일반 사용자 말로(2026-11-28) — "할 일 ID·유형·관계·결과물 수"는 내부 이름이었다.
    table(["번호", "할 일(역할)", "기한", "맡은 쪽", "나와의 관계", "상태", "최종 결과물", "결과물", "최근 갱신", "동작"], rows.map(r => [
      r.task_id, esc(r.role_name), _dueCell(r.due_date, r.estimate_hours), esc(label(r.resource_type)),
      // 2026-10-07 — "팀 Crew 그룹" 배정만 있고 개별 사람/Agent 배정이
      // 없는 task도 "내 업무"에 뜨게 된 뒤(list_my_work() 수정) 추가.
      ({
        assignee: "내가 할 일",
        requester: "내가 맡긴 일",
        team_leader: "내 팀이 할 일(팀 리더)",
        team_member: "내 팀이 할 일(팀원)",
      })[r.my_relationship] || "내가 맡긴 일",
      // 사용자 요청(2026-11-12, "진행과정과 결과물 확인이 어려워(숨겨져
      // 있는 느낌)") — 기존엔 이 컬럼이 deliverable 상태만 봐서 "아직
      // 실행 안 함"과 "방금 실행시켰는데 아직 안 끝남"이 똑같이 "아직
      // 없음"으로 보였다. `task_completion_summary()`가 내려주는
      // `active_job`(agent_execution_jobs/crew_runs/pipeline_runs 중
      // running·paused_for_approval)을 deliverable 상태보다 먼저
      // 보여준다 — 지금 뭔가 돌고 있다는 신호가 최우선.
      r.active_job === "running" ? '<span class="gate-badge gate-warn">⏳ 실행 중</span>'
        : r.active_job === "paused_for_approval" ? '<span class="gate-badge gate-warn">🕐 승인 대기 중</span>'
        : r.latest_status ? esc(label(r.latest_status)) : '<span class="muted">아직 없음</span>',
      r.has_final_result ? '<span class="gate-badge gate-ok">✓ 승인됨</span>' : '<span class="muted">-</span>',
      r.deliverable_count,
      r.latest_deliverable_at ? esc(fmtTime(r.latest_deliverable_at)) : "-",
      '<button class="small" data-open-workspace="' + r.task_id + '">열기</button>',
    ]), { empty: "아직 맡은 일이 없습니다. 내가 맡긴 일과 나·내 팀에 배정된 일이 여기에 모입니다. <button class=\"small\" data-go-tab=\"home\">홈에서 AI에게 일 맡기기</button>" }) +
    '<div id="mywork-detail"></div></section>' + '<div id="myabs-slot"></div>';

  content.querySelectorAll("[data-open-workspace]").forEach(btn => {
    btn.onclick = () => renderCollabWorkspace(Number(btn.dataset.openWorkspace));
  });
  if (ME.role !== "viewer") _renderMyAbsences(content.querySelector("#myabs-slot"));
}

// 사용자 요청(2026-11-10, "claude의 artifact 처럼... 최종 결과물을
// 모아보는 갤러리 기능(결과물 삭제시 갤러리에서도 삭제)") — 여러
// Task를 가로지르는 결과물 모아보기. `GET /api/deliverables`는 캐시가
// 아니라 매번 실시간 조회라, 삭제하면 다음 새로고침부터 자동으로
// 빠진다(별도 동기화 코드 없음).
const CONTENT_TYPE_LABEL = { text: "텍스트", markdown: "마크다운", html: "HTML", code: "코드" };
const GALLERY_STATUS_BADGE = {
  draft: "gate-warn", pending_review: "gate-warn", approved: "gate-ok", rejected: "gate-warn",
};
const GALLERY_SORT_LABEL = { created_desc: "최신순", created_asc: "오래된순", status: "상태순" };
let _galleryOffset = 0;
const _GALLERY_LIMIT = 24;

async function renderGallery(content) {
  _galleryOffset = 0;
  await _renderGalleryFiltered(content, {});
}

// 사용자 요청(2026-11-10, "컨텐츠간 경계 구분, 필터링, 소팅 기능 등을
// 좀 더 다양하게 강화") — 유형 필터에 상태 필터·정렬·페이지네이션을
// 더한다("저장된 지식 조각" 화면의 {items,total,limit,offset} 규약을
// 그대로 재사용 — 새 페이지네이션 패턴을 만들지 않음). 카드 경계가
// 흐릿해 보인다는 보고의 실제 원인은 `.gallery-card`가 `<div>`인데
// 카드 배경/테두리는 `section.card`(태그+클래스 조합) 선택자로만
// 걸려 있어 전혀 안 먹고 있었다 — `.gallery-card` 자체에 명시적으로
// 테두리/배경/그림자를 준다(아래 CSS).
async function _renderGalleryFiltered(content, filters) {
  const canSeeAll = ["admin", "manager"].includes(ME.role);
  const sort = filters.sort || "created_desc";
  const params = new URLSearchParams();
  if (filters.content_type) params.set("content_type", filters.content_type);
  if (filters.status) params.set("status", filters.status);
  params.set("sort", sort);
  params.set("limit", _GALLERY_LIMIT);
  params.set("offset", _galleryOffset);
  const page = await api("GET", "/api/deliverables?" + params.toString());
  const rows = page.items;

  const typeOptions = ["", "text", "markdown", "html", "code"].map(ct =>
    '<option value="' + ct + '"' + (filters.content_type === ct ? " selected" : "") + '>' +
    (ct ? CONTENT_TYPE_LABEL[ct] : "전체 유형") + '</option>'
  ).join("");
  const statusOptions = ["", "draft", "pending_review", "approved", "rejected"].map(st =>
    '<option value="' + st + '"' + (filters.status === st ? " selected" : "") + '>' +
    (st ? esc(label(st)) : "전체 상태") + '</option>'
  ).join("");
  const sortOptions = Object.keys(GALLERY_SORT_LABEL).map(s =>
    '<option value="' + s + '"' + (sort === s ? " selected" : "") + '>' + GALLERY_SORT_LABEL[s] + '</option>'
  ).join("");
  const pageStart = page.total ? _galleryOffset + 1 : 0;
  const pageEnd = Math.min(_galleryOffset + _GALLERY_LIMIT, page.total);

  content.innerHTML =
    '<section class="card"><h2>갤러리</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>' + (canSeeAll ? "조직 전체" : "내가 참가한 업무") + '의 결과물을 모아 보여줍니다.</li>' +
      '<li>"새 탭에서 보기"는 그 결과물 하나만 담은 독립 페이지를 엽니다(로그인 세션 필요, 외부 비공개 공유는 아닙니다).</li>' +
      '</ul>'
    ) +
    '<div class="inline" style="margin-bottom:12px;flex-wrap:wrap">' +
    '유형: <select id="gallery-type-filter">' + typeOptions + '</select>' +
    '상태: <select id="gallery-status-filter">' + statusOptions + '</select>' +
    '정렬: <select id="gallery-sort-select">' + sortOptions + '</select>' +
    '</div>' +
    (rows.length === 0 ? '<p class="muted">결과물이 없습니다.</p>' :
    '<div class="gallery-grid">' + rows.map(r => {
      const canDelete = canSeeAll || (r.author_type === "human" && r.author_id === ME.id && r.status === "draft");
      return '<div class="card gallery-card" data-gallery-id="' + r.id + '">' +
        '<div class="gallery-card-head">' +
        '<span class="gate-badge gate-ok">' + esc(CONTENT_TYPE_LABEL[r.content_type] || r.content_type) + '</span>' +
        '<span class="gate-badge ' + (GALLERY_STATUS_BADGE[r.status] || "gate-warn") + '">' + esc(label(r.status)) + '</span>' +
        '<span class="muted">#' + r.id + '</span>' +
        '</div>' +
        '<p><b>' + esc(r.task_role_name) + '</b> (할 일 #' + r.task_id + ')</p>' +
        '<p class="muted">' + esc(fmtTime(r.created_at)) + '</p>' +
        '<p>' + esc(r.preview || "(내용 없음)") + '</p>' +
        '<div class="gallery-card-actions">' +
        '<a href="/deliverables/' + r.id + '/view" target="_blank" rel="noopener">새 탭에서 보기</a>' +
        // 승인된 산출물만 지식베이스 후보(2026-11-20) — 업무 결재와 지식
        // 결재는 별개라, 업무에서 승인 안 된 초안은 보내지 못한다.
        (r.status === "approved" && ME.role !== "viewer" ? " " + knowledgeSubmitButton("deliverable", r.id) : "") +
        (canDelete ? ' <button class="small danger" data-gallery-delete="' + r.id + '">삭제</button>' : '') +
        '</div>' +
        '</div>';
    }).join("") + '</div>') +
    (page.total > _GALLERY_LIMIT
      ? '<div class="inline" style="margin-top:12px">' +
        '<button type="button" class="small" id="gallery-prev"' + (_galleryOffset <= 0 ? " disabled" : "") + '>이전</button>' +
        '<span class="muted">' + pageStart + '–' + pageEnd + ' / ' + page.total + '</span>' +
        '<button type="button" class="small" id="gallery-next"' + (_galleryOffset + _GALLERY_LIMIT >= page.total ? " disabled" : "") + '>다음</button>' +
        '</div>'
      : '');

  const rerender = (next) => {
    _galleryOffset = 0;
    _renderGalleryFiltered(content, Object.assign({}, filters, next));
  };
  content.querySelector("#gallery-type-filter").onchange = (e) => rerender({ content_type: e.target.value || null });
  content.querySelector("#gallery-status-filter").onchange = (e) => rerender({ status: e.target.value || null });
  content.querySelector("#gallery-sort-select").onchange = (e) => rerender({ sort: e.target.value });
  const prevBtn = content.querySelector("#gallery-prev");
  if (prevBtn) prevBtn.onclick = () => {
    _galleryOffset = Math.max(0, _galleryOffset - _GALLERY_LIMIT);
    _renderGalleryFiltered(content, filters);
  };
  const nextBtn = content.querySelector("#gallery-next");
  if (nextBtn) nextBtn.onclick = () => {
    _galleryOffset += _GALLERY_LIMIT;
    _renderGalleryFiltered(content, filters);
  };
  content.querySelectorAll("[data-gallery-delete]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 결과물을 삭제할까요? 되돌릴 수 없습니다."))) return;
      try {
        await api("DELETE", "/api/deliverables/" + btn.dataset.galleryDelete);
        _renderGalleryFiltered(content, filters);
      } catch (e) {
        showMsg("global-msg", e.message, "error");
      }
    };
  });
}

// 2026-10-08, Phase 4 — "할 일 보드"
// 4칼럼 칸반(backlog/queued/in_progress/done). 드래그 없이 업무를 열어
// select로 상태를 바꾸는 식으로 단순화했다(설계 문서 UI 설계 그대로).
//
// 2026-10-12, "Phase 1 (A)(B)
// (H1)(H2)(I5)부터 구현 시작해줘" — "전체 조직 범위는 범위 밖"이라던
// 원래 주석을 뒤집는다: admin/manager에겐 "전체 조직" 범위 선택지를
// 추가한다(`/api/all-work`). 카드 템플릿(STATUS_COLUMNS)은 그대로
// 재사용하고 데이터 소스만 바꾼다("같은 카드 템플릿, 데이터 소스만
// 교체" 원칙) — 일반 사용자는 기존과 동일하게 "내 업무"만 보인다.
const STATUS_COLUMNS = [
  { key: "backlog", label: "백로그" },
  { key: "queued", label: "대기" },
  { key: "in_progress", label: "진행중" },
  { key: "done", label: "완료" },
];
const BOARD_PRIORITY_BADGE = { urgent: "gate-warn", high: "gate-warn", medium: "gate-ok", low: "gate-ok" };

async function renderBoard(content) {
  const canSeeAll = ["admin", "manager"].includes(ME.role);
  await _renderBoardScope(content, "mine", canSeeAll);
}

async function _renderBoardScope(content, scope, canSeeAll) {
  // 2026-10-14 — 여기서 즉시 content를 "불러오는 중…" 한 줄로 비우면
  // selectTab()의 같은 블랭크와 중복이고(탭 전환 시), `refresh()`로 이
  // 탭을 제자리에서 다시 그릴 때는 스크롤이 맨 위로 튀는 원인이 된다
  // (긴 보드를 스크롤해 본 상태에서 내용이 한 줄로 줄어들면 브라우저가
  // 스크롤 위치를 강제로 잘라낸다) — 아래에서 데이터를 다 가져온 뒤
  // 한 번에 교체한다.
  const endpoint = scope === "all" ? "/api/all-work" : "/api/my-work";
  const rows = await api("GET", endpoint);
  const details = await Promise.all(
    rows.map(r => api("GET", "/api/tasks/" + r.task_id).catch(() => null))
  );
  const tasks = rows
    .map((r, i) => Object.assign({}, r, details[i]))
    .filter(t => t.status !== undefined);

  const columnsHtml = STATUS_COLUMNS.map(col => {
    const colTasks = tasks.filter(t => t.status === col.key);
    // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
    // 결과물 확인이 어려워") — 카드에 역할명·우선순위뿐이라 실행 중인지
    // 결과물이 나왔는지 전혀 안 보여, 칸반을 보고도 열어봐야만 알 수
    // 있었다. "내 업무"와 같은 active_job/has_final_result(`/api/my-
    // work`·`/api/all-work`가 이미 내려줌)를 그대로 재사용한다.
    const progressBadgeHtml = (t) => t.active_job === "running"
      ? '<span class="gate-badge gate-warn">⏳ 실행 중</span>'
      : t.active_job === "paused_for_approval"
      ? '<span class="gate-badge gate-warn">🕐 승인 대기 중</span>'
      : t.has_final_result
      ? '<span class="gate-badge gate-ok">✓ 승인됨</span>'
      : "";
    const cards = colTasks.map(t =>
      '<div class="deliverable-card">' +
      '<div><strong>' + esc(t.role_name) + '</strong></div>' +
      '<div><span class="gate-badge ' + (BOARD_PRIORITY_BADGE[t.priority] || "") + '">' + esc(label(t.priority)) + '</span> ' + progressBadgeHtml(t) + '</div>' +
      '<div class="actions"><button class="small" data-board-open="' + t.task_id + '">열기</button></div>' +
      '</div>'
    ).join("");
    return '<div class="card" style="flex:1;min-width:200px">' +
      '<h4>' + esc(col.label) + ' (' + colTasks.length + ')</h4>' +
      (cards || '<p class="muted">없음</p>') +
      '</div>';
  }).join("");

  const scopeSelectHtml = canSeeAll
    ? '<label>범위: <select id="board-scope">' +
      '<option value="mine"' + (scope === "mine" ? " selected" : "") + '>내 업무</option>' +
      '<option value="all"' + (scope === "all" ? " selected" : "") + '>전체 조직</option>' +
      '</select></label>'
    : "";

  content.innerHTML =
    '<section class="card"><h2>할 일 보드</h2>' +
    (scopeSelectHtml ? '<div class="inline">' + scopeSelectHtml + '</div>' : "") +
    helpBox(
      '<ul class="muted help-list">' +
      (scope === "all"
        ? '<li>조직 전체의 확정된 업무를 보여줍니다(admin/manager 전용 범위).</li>'
        : '<li>"내 업무"에 뜨는, 내가 참가 중인 업무만 보여줍니다' +
          (canSeeAll ? '(위 범위를 "전체 조직"으로 바꾸면 조직 전체를 봅니다).</li>' : '(조직 전체 업무는 "업무 요청" 탭에서 브리프별로 확인하세요).</li>')) +
      '<li>카드의 "열기"로 워크스페이스를 펼쳐 상태/우선순위를 바꾸면 이 보드에도 반영됩니다(새로고침 필요).</li>' +
      '</ul>'
    ) +
    '<div class="inline" style="align-items:flex-start;flex-wrap:wrap">' + columnsHtml + '</div>' +
    '<div id="board-detail"></div>' +
    '</section>';

  content.querySelectorAll("[data-board-open]").forEach(btn => {
    btn.onclick = () => renderCollabWorkspace(Number(btn.dataset.boardOpen), "board-detail");
  });
  const scopeSelect = document.getElementById("board-scope");
  if (scopeSelect) {
    scopeSelect.onchange = () => _renderBoardScope(content, scopeSelect.value, canSeeAll);
  }
}

/* Phase 4 — execute-agent는 202+job_id만 즉시 돌려준다(사용자 요청,
   2026-09-28, "Phase 4 진행해줘"). 실제 완료는 여기서 폴링으로 확인한다
   (실시간 WebSocket은 1차 범위 밖으로 명시적으로 뺐다).
   사용자 결정(2026-11-12, "대화 및 업무에 같은 기준이 적용되도록") —
   백엔드(agent_runtime.py/crew_runtime.py)는 단일 Agent 실행·팀
   Crew 실행·파이프라인 단계 전부 `pollConversationJob()`과 똑같은
   타임아웃 체계(최대 90분, Ollama reasoning/Crew 경로)를 공유하는데,
   이 함수의 포기 시한만 2분으로 훨씬 짧게 남아 있었다 — 백엔드는
   멀쩡히 돌고 있는데 여기만 먼저 포기해 사용자에게 실패처럼 보이는,
   대화 쪽에서 이미 여러 번 겪은 바로 그 문제가 업무 쪽엔 반영이 안
   돼 있었다. `pollConversationJob()`과 같은 40분으로 맞춘다. */
async function pollExecutionJob(taskId, jobId, intervalMs, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 2400000);
  while (Date.now() < deadline) {
    const jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    const job = jobs.find(j => j.id === jobId);
    if (job && job.status !== "running") return job;
    // 진행 단계·작성 중 미리보기(2026-11-24, P0-2) — 워크스페이스의 "생성 중…" 카드에 갱신.
    const card = job && document.getElementById("ws-running-job-" + job.id);
    if (card) {
      const step = card.querySelector(".job-progress-step");
      if (step) step.textContent = job.progress ? "— " + job.progress : "";
      _showPartialOutput(card, job.partial_output);
    }
    await new Promise(r => setTimeout(r, intervalMs || 1500));
  }
  throw new Error("실행 완료 확인 시간이 초과됐습니다 — 잠시 후 새로고침해 주세요.");
}

// 2026-10-08, Phase 4 — **사용자 결정**:
// 선행 작업 "목록"이 아니라 Riido 수준의 그래프 시각화로 바로 간다.
// 외부 그래프 라이브러리 없이(이 프로젝트 전체가 CDN 의존 없는 순수
// vanilla JS 단일 파일이라는 기존 관례 그대로 유지) 순수 SVG로 그린다
// — depth(의존 사슬 길이)별로 열을 나눠 왼쪽에서 오른쪽으로 흐르게
// 배치하는 가장 단순한 레이어 레이아웃(순환이 없는 DAG라는 서버
// 보장 덕에 depth가 항상 유한하게 정의된다).
function _dependencyGraphDepths(nodes, edges) {
  const dependsOn = {};
  nodes.forEach(n => { dependsOn[n.id] = []; });
  edges.forEach(e => {
    if (dependsOn[e.task_id]) dependsOn[e.task_id].push(e.depends_on_task_id);
  });
  const depth = {};
  function compute(id, seen) {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0; // 서버가 순환을 막지만 방어적으로 무한루프만 피한다
    seen.add(id);
    const deps = dependsOn[id] || [];
    const d = deps.length ? 1 + Math.max(...deps.map(dep => compute(dep, seen))) : 0;
    depth[id] = d;
    return d;
  }
  nodes.forEach(n => compute(n.id, new Set()));
  return depth;
}

function renderDependencyGraphSvg(graph, currentTaskId) {
  const { nodes, edges } = graph;
  if (!nodes.length) return '<p class="muted">연결된 의존관계가 없습니다.</p>';
  const depth = _dependencyGraphDepths(nodes, edges);
  const columns = {};
  nodes.forEach(n => {
    const d = depth[n.id];
    (columns[d] = columns[d] || []).push(n);
  });
  const colWidth = 170, rowHeight = 56, nodeW = 150, nodeH = 42, padding = 16;
  const pos = {};
  Object.keys(columns).forEach(d => {
    columns[d].forEach((n, i) => {
      pos[n.id] = { x: padding + Number(d) * colWidth, y: padding + i * rowHeight };
    });
  });
  const maxDepth = Math.max(...Object.values(depth));
  const maxRows = Math.max(...Object.values(columns).map(c => c.length));
  const width = padding * 2 + (maxDepth + 1) * colWidth - (colWidth - nodeW);
  const height = padding * 2 + maxRows * rowHeight - (rowHeight - nodeH);
  const priorityColor = { low: "#9aa0a6", medium: "#4a90d9", high: "#e0a030", urgent: "#d9534f" };

  const edgesSvg = edges.map(e => {
    const from = pos[e.depends_on_task_id], to = pos[e.task_id];
    if (!from || !to) return "";
    const x1 = from.x + nodeW, y1 = from.y + nodeH / 2;
    const x2 = to.x, y2 = to.y + nodeH / 2;
    return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="#aaa" stroke-width="1.5" marker-end="url(#dep-arrow)"/>';
  }).join("");

  const nodesSvg = nodes.map(n => {
    const p = pos[n.id];
    const isCurrent = n.id === currentTaskId;
    const label20 = n.role_name.length > 16 ? n.role_name.slice(0, 15) + "…" : n.role_name;
    return '<g data-graph-node="' + n.id + '" style="cursor:pointer">' +
      '<rect x="' + p.x + '" y="' + p.y + '" width="' + nodeW + '" height="' + nodeH +
      '" rx="6" fill="' + (isCurrent ? "#eef6ff" : "#fff") + '" stroke="' +
      (priorityColor[n.priority] || "#999") + '" stroke-width="' + (isCurrent ? 3 : 1.5) + '"/>' +
      '<text x="' + (p.x + nodeW / 2) + '" y="' + (p.y + 17) +
      '" text-anchor="middle" font-size="11" font-weight="bold">' + esc(label20) + '</text>' +
      '<text x="' + (p.x + nodeW / 2) + '" y="' + (p.y + 32) +
      '" text-anchor="middle" font-size="10" fill="#666">' +
      esc(label(n.status)) + " · " + esc(label(n.priority)) + '</text>' +
      '</g>';
  }).join("");

  return '<svg width="' + width + '" height="' + height + '" style="max-width:100%">' +
    '<defs><marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" ' +
    'markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#aaa"/></marker></defs>' +
    edgesSvg + nodesSvg + '</svg>';
}

// containerId: 사용자 요청(2026-10-06, "팀 구성과 협업 프로세스를 하나의
// 화면으로 합치는 방안" → 옵션 C 채택)으로 일반화했다 — 원래 "내 업무"
// 탭의 #mywork-detail에만 그릴 수 있었는데, "업무 요청" 탭의 할 일 상세
// 패널에도 같은 워크스페이스를 그대로 재사용하기 위함(중복 구현 금지).
// 기본값을 기존 id로 둬서 "내 업무" 쪽 기존 호출부는 전혀 안 바꿔도 된다.
// 파이프라인 진행률(2026-11-24, P0-2 "파이프라인 진행률") — 실행 중 단계 행은 단계가 끝나야 생겨,
// 지금까지는 진행 중인 단계가 무엇을 하는지·얼마나 됐는지 알 수 없었다. 실행 행의 진행 정보로 보여 준다.
function _utcElapsed(ts) {
  const t = Date.parse(String(ts || "").replace(" ", "T") + "Z");
  if (isNaN(t)) return "";
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  return sec >= 3600 ? Math.floor(sec / 3600) + "시간 " + Math.floor((sec % 3600) / 60) + "분"
    : sec >= 60 ? Math.floor(sec / 60) + "분 " + (sec % 60) + "초" : sec + "초";
}
// 예상 남은 시간(2026-11-25, P0-3) — 같은 파이프라인 같은 단계의 최근 실행(최대 5회) 평균. 기록이 없는 단계는 빼고 센다.
function _pipelineEtaHtml(eta) {
  if (!eta || !(eta.known_stages > 0)) return "";
  const min = Math.max(1, Math.round(eta.remaining_seconds / 60));
  const text = min >= 60 ? Math.floor(min / 60) + "시간 " + (min % 60) + "분" : min + "분";
  return '<div class="muted" title="같은 파이프라인의 최근 실행(단계별 최대 ' + eta.basis + '회) 평균 소요 시간으로 계산했습니다. 보완 요청으로 다시 쓰면 더 걸립니다.">⏱ 예상 남은 시간 약 ' + text +
    (eta.unknown_stages ? " + 기록 없는 단계 " + eta.unknown_stages + "개" : "") + "</div>";
}
function _pipelineLiveHtml(run) {
  if (!run || run.status !== "running" || !run.total_stages) return "";
  const idx = run.current_stage_index || 1;
  const pct = Math.round(((idx - 1) / run.total_stages) * 100);
  const elapsed = _utcElapsed(run.stage_started_at);
  const preview = run.partial_output
    ? '<div class="job-partial-output"><div class="muted">작성 중…</div><pre>' +
      esc(run.partial_output.length > 2000 ? "…" + run.partial_output.slice(-2000) : run.partial_output) + "</pre></div>"
    : "";
  return '<div class="pipeline-live">' +
    '<div><b>▶ ' + idx + "/" + run.total_stages + " 단계 · " + esc(run.current_stage_name || "") + "</b>" +
    ' <span class="muted">— ' + esc(run.progress || "진행 중") + (elapsed ? " · 이 단계 " + elapsed + " 경과" : "") + "</span></div>" +
    _pipelineEtaHtml(run.eta) +
    '<div class="progress-bar" title="완료한 단계 ' + (idx - 1) + "/" + run.total_stages + '"><div class="progress-bar-fill" style="width:' + pct + '%"></div></div>' +
    preview + "</div>";
}
let _pipelineLiveToken = 0;  // 화면을 다시 그릴 때마다 새 폴러가 생기므로, 가장 최근 것만 돈다
async function _pollPipelineLive(taskId, containerId, runId) {
  const token = ++_pipelineLiveToken;
  while (isViewingWorkspace(taskId, containerId)) {
    await new Promise(r => setTimeout(r, 5000));
    if (token !== _pipelineLiveToken || !isViewingWorkspace(taskId, containerId)) return;
    let runs;
    try { runs = await api("GET", "/api/tasks/" + taskId + "/pipeline-runs"); } catch (e) { return; }
    const run = runs.find(r => r.id === runId);
    if (!run || run.status !== "running") { renderCollabWorkspace(taskId, containerId); return; }
    const box = document.getElementById("pipeline-live-" + runId);
    if (!box) return;
    box.innerHTML = _pipelineLiveHtml(run);
    const pre = box.querySelector("pre");
    if (pre) pre.scrollTop = pre.scrollHeight;
  }
}

async function renderCollabWorkspace(taskId, containerId = "mywork-detail") {
  const detail = document.getElementById(containerId);
  detail.dataset.taskId = String(taskId);
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = isSupervisor ? await fetchModelsByProvider() : {};
  let items, jobs, permission, confirmedAgents, confirmedTeams, taskDetail, activity, depGraph, siblingTasks;
  let pipelineTemplates, pipelineRuns, budget;
  try {
    items = await api("GET", "/api/tasks/" + taskId + "/deliverables");
    jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    // 메모/통합 활동 타임라인/
    // 상태·우선순위·의존관계(Phase 4),
    // 2026-10-08 — 전부 워크스페이스를 열 때 같이 불러온다.
    // 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자 기능 삭제해줘") —
    // 참가자 초대(Phase 2)는 Task 워크스페이스 전체에서 제거했다(아래
    // 참가자 카드·ws-invite-input·댓글 @멘션 자동완성도 같이 제거 —
    // 전부 이 `participants` 하나에서 갈라져 나갔던 기능). 백엔드
    // `task_participants` 테이블/API(`deliverables.py`)는 그대로 남아
    // 있지만, 이제 초대할 방법이 없어 `detect_mentioned_agents()`
    // (댓글 @Agent 호출)도 사실상 동작하지 않게 된다 — 의도된 부수
    // 효과(요청 범위 밖이라 백엔드까지 지우진 않았다).
    taskDetail = await api("GET", "/api/tasks/" + taskId);
    activity = await api("GET", "/api/tasks/" + taskId + "/activity");
    depGraph = await api("GET", "/api/tasks/" + taskId + "/dependencies");
    // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") —
    // "선행 작업 ID" 입력도 같은 문제라 같은 브리프의 다른 할 일
    // 목록으로 고를 수 있게 한다(선행 작업은 브리프를 넘어서도 걸 수
    // 있게 백엔드가 허용하지만, 실제로는 거의 항상 같은 브리프 내
    // 작업이라 이 목록만으로 충분 — 드문 브리프 간 선행 작업은 범위
    // 밖으로 남긴다).
    siblingTasks = await api("GET", "/api/briefs/" + taskDetail.brief_id + "/tasks").catch(() => []);
    // 사용자가 스크린샷으로 보고한 갭(2026-09-29) — 참가자기만 하면
    // 무조건 실행 버튼을 보여줘, human assignee가 눌러야만 "권한이
    // 없습니다" 403으로 뒤늦게 알게 됐다. 버튼을 그리기 전에 먼저 확인한다.
    permission = await api("GET", "/api/tasks/" + taskId + "/execution-permission");
    // 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 5 실행 중) — "다중
    // confirmed 허용" 이후 한 할 일에 confirmed agent가 2개 이상이면
    // 이 버튼이 어느 쪽을 실행하는지 전혀 알려주지 않았다(그냥 "먼저
    // 걸리는 하나"가 조용히 실행됨) — 의도와 다른 Agent가 실행돼
    // 실비용만 낭비될 위험이 있었다. 확정된 Agent 배정을 미리 조회해
    // 2개 이상이면 선택 드롭다운을 보여준다(`ExecuteAgentRequest.
    // agent_id`는 이미 추가돼 있었는데 이 UI만 안 쓰고 있었다).
    const assignments = await api("GET", "/api/tasks/" + taskId + "/assignments");
    confirmedAgents = assignments.filter(a => a.resource_type === "agent" && a.status === "confirmed");
    // 사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
    // 개선... 사람-에이전트 연동이 가능한 구조인지 확인") — Phase 3.
    // 이 할 일에 confirmed로 배정된 팀이 있으면 그 팀의 Agent 멤버를 실제 Crew로
    // 실행하는 섹션을 보여준다(팀이 여러 개면 드롭다운으로 하나를
    // 고른다 — 사용자 결정, 전부 동시 실행 아님).
    confirmedTeams = assignments.filter(a => a.resource_type === "team" && a.status === "confirmed");
    // 사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
    // 최소 HITL/HOTL... 단계별 단독 또는 복수의 에이전트가 순차/병행
    // 처리") — 팀 Crew 실행과 같은 자리에, 단계형 파이프라인 실행
    // 섹션을 추가한다. 권한은 위 permission 조회와 동일한 Task이므로
    // 별도 403 분기 없이 그대로 보여준다(서버가 다시 검사한다).
    pipelineTemplates = (await api("GET", "/api/pipeline-templates").catch(() => []))
      .filter(t => t.is_active);
    pipelineRuns = await api("GET", "/api/tasks/" + taskId + "/pipeline-runs").catch(() => []);
    // 사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도 예산
    // 추적") — 실패해도(권한 밖 등) 워크스페이스 전체가 깨지지 않게
    // catch로 감싼다(기존 pipelineRuns 등과 같은 방어).
    budget = await api("GET", "/api/tasks/" + taskId + "/budget").catch(() => null);
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  // 최신 job이 아직 안 끝났거나(생성 중) 실패했으면, 아직 timeline에
  // 안 나오는 그 실행을 별도 카드로 보여준다(성공하면 곧 deliverable로
  // 나타나므로 여기서 다시 보여줄 필요 없음).
  // 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현 가능한
  // 감사로그) — Gate 점수가 낮거나 실행이 실패했을 때 "왜 이렇게
  // 나왔는지"를 사후에 확인할 수 있게, 그 시점 system prompt·증강된
  // 프롬프트·provider/model을 펼쳐 보여준다(관리자/매니저만 — 기술적
  // 세부 정보라 일반 참가자 화면을 어지럽히지 않는다).
  const promptSnapshotDetails = (job) => (isSupervisor && job.prompt_snapshot)
    ? '<details class="muted"><summary>실행에 쓰인 프롬프트 보기(재현용)</summary>' +
      '<p>provider/model: ' + esc(job.prompt_snapshot.llm_provider || "-") + " / " + esc(job.prompt_snapshot.llm_model || "-") + '</p>' +
      '<p>system prompt:</p><pre style="white-space:pre-wrap">' + esc(job.prompt_snapshot.system_prompt || "") + '</pre>' +
      '<p>프롬프트(증강 후):</p><pre style="white-space:pre-wrap">' + esc(job.prompt_snapshot.augmented_prompt || "") + '</pre>' +
      '</details>'
    : "";
  const latestJob = jobs[0];
  const jobCard = (latestJob && latestJob.status === "running")
    ? '<div class="deliverable-card chat-agent" id="ws-running-job-' + latestJob.id + '"><div class="muted">AI 에이전트 · 생성 중… <span class="job-progress-step">' + esc(latestJob.progress ? "— " + latestJob.progress : "") + '</span></div></div>'
    : (latestJob && latestJob.status === "failed"
      ? '<div class="deliverable-card chat-agent"><div class="muted">AI 에이전트 · 실행 실패(' + esc(fmtTime(latestJob.finished_at)) + ')</div>' +
        '<div>' + esc(latestJob.error || "알 수 없는 오류") + '</div>' +
        promptSnapshotDetails(latestJob) + '</div>'
      : '');

  // Phase 5(사용자 요청, 2026-09-28, "협업 워크스페이스 UX 개선") — 공유
  // 스레드/채팅형 타임라인: 작성자 타입별로 카드를 좌우로 구분해 "누가
  // 쓴 건지" 한눈에 보이게 한다. Gate 요약 배지도 여기서 처음 노출한다
  // — d.evaluation(Phase 3부터 API엔 있었지만 이전까지 화면 어디에도
  // 안 나오고 있었다).
  const deliverableCards = items.map(d => {
    // 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 3 실행 중) — 결재자가
    // 그 task의 참가자가 아니면(흔한 경우 — 결재 체인은 조직도 기준이지
    // task 참가 여부와 무관) 반려해도 "왜"를 댓글로 남길 방법이 전혀
    // 없었다(댓글은 참가자 전용). 승인/반려에 선택적 사유를 붙여 그
    // 사유를 결재자 자격으로(참가자 여부와 무관하게) 타임라인에 남긴다.
    const decideActions = (isSupervisor && d.approval_id)
      ? '<div class="actions">' +
        '<input placeholder="사유(선택)" data-decide-reason="' + d.approval_id + '" style="flex:1;min-width:120px">' +
        '<button class="small" data-decide-deliverable="' + d.approval_id + '" data-approve="1">승인</button>' +
        '<button class="small danger" data-decide-deliverable="' + d.approval_id + '" data-approve="0">반려</button></div>'
      : (d.approval_id ? '<p class="muted">결재 대기 중 — "결재" 탭 또는 담당 매니저/관리자가 처리합니다.</p>' : '');
    // Phase 2 후속(라이브 시나리오 실행에서 발견한 갭 보완) — 사람
    // draft는 본인이 명시적으로 "검토 요청"을 눌러야만
    // 결재 체인이 붙는다(Agent처럼 자동 제출되지 않음). 본인 draft에만
    // 노출한다(대리 제출 금지).
    const submitAction = (d.author_type === "human" && d.status === "draft" && d.author_id === ME.id)
      ? '<div class="actions"><button class="small" data-submit-deliverable="' + d.id + '">검토 요청</button></div>'
      : '';
    // 실측으로 발견한 갭(사용자 요청, 2026-09-28, "UI/UX 내용 중... 분석
    // 하고 개선해줘") — measured.length가 0일 때 passed===measured.length
    // (0===0)가 참이 돼 "Gate 0/0 통과"를 녹색(gate-ok)으로 보여줬다.
    // "아무것도 측정 안 됨"이 "전부 통과"처럼 보이는 오해의 소지가 있는
    // 표시라, 측정된 Gate가 하나도 없으면 별도 문구로 구분한다.
    const gateBadge = d.evaluation
      ? (() => {
          const gates = Object.values(d.evaluation.gate_scores);
          const measured = gates.filter(g => !g.not_measured && !g.advisory);
          if (measured.length === 0) {
            return '<span class="gate-badge gate-warn" title="이 결과물에는 자동 품질 점검(Gate A-G) 결과가 없습니다">품질 점검 없음</span>';
          }
          const passed = measured.filter(g => g.passed).length;
          const cls = passed === measured.length ? "gate-ok" : "gate-warn";
          // 낱말확인(참고용, 2026-11-25) — 통과 집계와 따로, 찾은 낱말이 있으면 노란 배지로 보인다.
          const lex = d.evaluation.gate_scores.lexicon_check;
          const lexBadge = lex && lex.words && lex.words.length
            ? ' <span class="gate-badge gate-warn" title="' + escAttr("사전에 없는 낱말(참고용 — 지어낸 낱말일 수 있으니 확인): " + lex.words.map(w => w.word + " (…" + w.context + "…)").join(" / ")) + '">⚠ 낱말 확인 ' + lex.words.length + "</span>"
            : "";
          return '<span class="gate-badge ' + cls + '" title="자동 품질 점검(Gate A-G 등) 통과 수 · 완료율(TCR — 요청한 항목을 끝낸 비율)">품질 점검 ' + passed + "/" + measured.length +
            " 통과" + (d.evaluation.tcr === null ? "" : " · 완료율 " + d.evaluation.tcr.toFixed(0) + "%") + '</span>' + lexBadge;
        })()
      : "";
    const side = d.author_type === "agent" ? "chat-agent" : "chat-human";
    // 사용자 요청(2026-09-29, "Tool, Skill을 적극 활용하는 시나리오를
    // 기획해 진행해줘") 라이브 실행에서 발견한 갭 — VectorDB/GraphDB
    // 그라운딩이 실제로 뭘 검색해 답변에 반영했는지 결재자가 확인할
    // 방법이 없었다. 이 deliverable을 만든 job의 retrieved_context를
    // 찾아 접을 수 있는 "참고한 근거" 블록으로 보여준다.
    const sourceJob = jobs.find(j => j.deliverable_id === d.id && j.retrieved_context);
    const evidenceBlock = sourceJob
      ? '<details class="muted"><summary>참고한 근거(Tool/Skill 검색 결과)</summary>' +
        '<div>' + renderContent(sourceJob.retrieved_context) + '</div></details>'
      : '';
    // 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현 가능한
    // 감사로그) — 위 sourceJob 패턴과 같은 방식으로, 이 deliverable을
    // 만든 job의 prompt_snapshot을 찾아 보여준다(Gate 점수가 왜 이렇게
    // 나왔는지 사후에 재현해볼 수 있게).
    const promptJob = jobs.find(j => j.deliverable_id === d.id && j.prompt_snapshot);
    const promptBlock = promptJob ? promptSnapshotDetails(promptJob) : "";
    // 권한은 삭제 버튼과 동일한 경계(`deliverables.update_deliverable_
    // content()`가 서버에서 같은 규칙으로 재확인) — admin/manager는
    // 항상, 그 외엔 본인이 작성한 draft 상태 산출물만.
    const canEditDeliverable = isSupervisor ||
      (d.author_type === "human" && d.status === "draft" && d.author_id === ME.id);
    const deliverableEditTarget = canEditDeliverable ? { kind: "deliverable", id: d.id } : undefined;
    return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted">' + esc(label(d.author_type)) + " · " + esc(label(d.status)) + " · " + esc(fmtTime(d.created_at)) + " " + gateBadge + '</div>' +
    '<div>' + renderContent(d.content, d.content_type, deliverableEditTarget) + '</div>' +
    '<div class="actions"><button class="small" data-download-deliverable="' + d.id + '">다운로드</button>' +
    // 버전 이력(2026-11-23, P1-6) — 같은 작성자가 다시 낸 버전·직접 고친 기록을 현재본과 비교.
    '<button class="small" data-deliverable-history="' + d.id + '">버전 이력</button>' +
    // 부분 수정 요청(2026-11-24, P1-6) — 섹션·구간만 AI가 다시 쓰고, 비교를 보고 수락·거절. 권한은 직접 편집과 같다.
    (canEditDeliverable && d.content_type !== "html" ? '<button class="small" data-edit-proposal="' + d.id + '">부분 수정 요청</button>' : "") +
    // Word 내보내기(2026-11-23, P1-7) — 코드 결과물은 원본 다운로드가 더 맞아 버튼을 두지 않는다.
    (d.content_type !== "code" ? '<button class="small" data-export-docx="' + d.id + '" data-export-fmt="docx">Word로 내보내기</button>' : "") +
    // Excel·PowerPoint(2026-11-24, P1-7) — 표가 있으면 Excel(표마다 시트, 숫자는 숫자로), 제목마다 슬라이드인 PowerPoint.
    (d.content_type !== "code" && _hasTable(d.content)
      ? '<button class="small" data-export-docx="' + d.id + '" data-export-fmt="xlsx">Excel로</button>' : "") +
    (d.content_type !== "code" ? '<button class="small" data-export-docx="' + d.id + '" data-export-fmt="pptx">PowerPoint로</button>' : "") +
    // 평가 리포트(2026-11-25) — Agent-Evaluator SDK가 만든 Gate A-G 종합 HTML 리포트를 새 탭으로(격리된 출처에서 열림).
    (d.evaluation && d.evaluation.has_report
      ? '<a class="small" href="/api/deliverables/' + d.id + '/evaluation-report" target="_blank" rel="noopener" title="Agent-Evaluator가 만든 Gate A-G 종합 리포트(점수·지표·원인 진단·개선 제안)">평가 리포트</a>'
      : "") +
    (d.status === "approved" && ME.role !== "viewer" ? knowledgeSubmitButton("deliverable", d.id) : "") +
    // 저장소에 커밋(2026-11-24, P0-3 2단계) — 결재를 마친 결과물만, admin만(저장소 등록과 같은 경계).
    (d.status === "approved" && ME.role === "admin" ? '<button class="small" data-repo-commit="' + d.id + '">저장소에 커밋</button>' : "") +
    '</div>' +
    '<div class="deliverable-history" id="history-' + d.id + '" hidden></div>' +
    '<div class="edit-proposal-panel" id="edit-proposal-' + d.id + '" hidden></div>' +
    '<div class="repo-commit-panel" id="repo-commit-' + d.id + '" hidden></div>' +
    evidenceBlock +
    promptBlock +
    decideActions +
    submitAction +
    '<div id="comments-' + d.id + '"></div>' +
    '<form class="inline" data-comment-form="' + d.id + '">' +
    // UI 설계(2026-10-08)로 추가했던 "@ 입력 시 참가자 목록 자동완성
    // 드롭다운"은 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자
    // 기능 삭제해줘")으로 참가자 기능 자체와 함께 제거했다 — 평범한
    // 입력창으로 되돌린다.
    '<input placeholder="댓글" id="comment-input-' + d.id +
    '" style="flex:1;box-sizing:border-box" autocomplete="off">' +
    '<button class="small" type="submit">댓글</button></form>' +
    '</div>';
  }).join("") + jobCard;

  const timeline = deliverableCards || '<p class="muted">아직 결과물이 없습니다.</p>';

  // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
  // 분석하고 부족한 기능을 개선해줘") — 전체 타임라인(중간 과정 포함)과
  // "지금 업무에 실제로 쓸 수 있는 최종 결과물"을 구분해 보여준다.
  // 이전엔 승인된 결과물도 다른 초안/반려 건과 뒤섞인 채팅형 목록 안에
  // 묻혀 있어, 여러 차례 주고받은 뒤엔 어느 게 "확정본"인지 스크롤해서
  // 직접 판단해야 했다. 승인된 deliverable 중 가장 최신 것을 상단에
  // 고정 노출한다(같은 task에 여러 건이 승인된 드문 경우, 최신 것을
  // "현재" 최종본으로 간주 — revises_id 기반 버전 체계는 스코프 밖).
  const approvedItems = items.filter(d => d.status === "approved");

  // 사용자 보고(2026-10-19, "'할 일 목록(Todo) 앱 개발'의 최종 결과물에
  // 코드가 포함되어야 하는데 품질 리포트만 있어") — 파이프라인 업무는
  // 품질 판정/검증 같은 "검토" 단계가 항상 맨 나중에 돌고, 그 단계도
  // HITL 없이 바로 'approved'로 올라가므로 위 "가장 최근 승인 건" 규칙이
  // 구현 결과물이 아니라 마지막 리뷰 단계의 판정문을 집어왔다. 파이프라인
  // 템플릿 단계에 `is_primary_deliverable`로 "이 단계 산출물이 진짜
  // 최종 결과물"이라고 명시적으로 표시해두면, 생성 시각과 무관하게 그
  // 단계의 결과물을 우선한다 — 일반(비파이프라인) 업무는 영향 없음.
  let primaryDeliverable = null;
  for (const run of pipelineRuns) {
    const candidates = (run.stage_results || [])
      .filter(sr => sr.is_primary_deliverable && sr.deliverable_id);
    if (!candidates.length) continue;
    const best = candidates.reduce((a, b) => (a.attempt > b.attempt ? a : b));
    primaryDeliverable = items.find(d => d.id === best.deliverable_id) || null;
    if (primaryDeliverable) break;
  }

  const finalResult = primaryDeliverable
    ? primaryDeliverable
    : (approvedItems.length
        ? approvedItems.reduce((a, b) => (a.created_at > b.created_at ? a : b))
        : null);
  const finalResultLabel = finalResult === primaryDeliverable
    ? "✓ 최종 결과물(파이프라인 핵심 산출물)"
    : "✓ 최종 결과물(승인됨)";
  const finalResultEditTarget = finalResult && (
    isSupervisor ||
    (finalResult.author_type === "human" && finalResult.status === "draft" && finalResult.author_id === ME.id)
  ) ? { kind: "deliverable", id: finalResult.id } : undefined;
  const finalResultCard = finalResult
    ? '<div class="final-result-card">' +
      '<div class="muted">' + finalResultLabel + ' · ' + esc(label(finalResult.author_type)) + ' · ' + esc(fmtTime(finalResult.created_at)) + '</div>' +
      '<div>' + renderContent(finalResult.content, finalResult.content_type, finalResultEditTarget) + '</div>' +
      '<div class="actions"><button class="small" data-download-deliverable="' + finalResult.id + '">다운로드</button></div>' +
      '</div>'
    : '<p class="muted">아직 승인된 최종 결과물이 없습니다 — 아래는 진행 중인 이력입니다.</p>';

  const modelFields = isSupervisor
    ? '<select id="ws-provider"><option value="">provider 자동 판별</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      modelSelectHtml("ws-model")
    : '';  // 구성원에게 "매니저·관리자만 고를 수 있다"는 안내는 소음이었다(2026-11-28) — 기본 모델을 쓴다

  // Phase 4 — 이미 생성 중인 job이 있으면 실행 버튼을 막는다(중복
  // 트리거로 같은 task에 2개의 Agent 실행이 동시에 도는 것을 방지 —
  // "실행 중임을 알려주는 UI" 관례를 여기도 적용). 사용자가 스크린샷으로
  // 보고한 갭(2026-09-29) — permission.eligible이 false면(human
  // assignee지 Agent 요청자가 아니거나, 확정된 Agent 배정 자체가 없음)
  // 버튼을 비활성화하고 이유를 바로 보여준다 — 눌러봐야만 403으로 알게
  // 하지 않는다.
  const EXECUTION_BLOCK_REASONS = {
    not_requester: "이 업무에 AI 에이전트 실행을 요청한 사람 또는 매니저/관리자만 실행할 수 있습니다.",
    no_confirmed_agent: "이 업무에 확정된 AI 에이전트 배정이 없습니다.",
  };
  const hasRunningJob = !!(latestJob && latestJob.status === "running");
  const executeDisabled = hasRunningJob || !permission.eligible;
  const executeBtnLabel = hasRunningJob ? "생성 중…" : "이 업무의 AI 에이전트 실행(비용 발생)";
  // confirmed agent가 2개 이상이면 어느 Agent를 실행할지 명시적으로
  // 고르게 한다 — 1개뿐이면(기존 동작) 선택 없이 바로 실행된다.
  const agentPickerHtml = confirmedAgents.length > 1
    ? '<select id="ws-agent-picker" style="margin-right:0.5rem">' +
      confirmedAgents.map(a => '<option value="' + a.resource_id + '">' +
        esc(a.resource_name || ("Agent #" + a.resource_id)) + '</option>').join("") +
      '</select>'
    : '';
  // 사용자 요청(2026-10-26, "A안 + B안" — 업무 실행에 쓸 참고자료는
  // 저장 없이 그 1회 실행에만 첨부) — "AI 에이전트 관리" 화면에 있던
  // 전역 "자료 업로드" 버튼을 폐지하고, 업무 맥락이 실제로 있는 이
  // 자리로 옮겼다. 선택 사항이라 안 붙여도 기존처럼 그냥 실행된다.
  const executeBtnHtml =
    agentPickerHtml +
    '<input type="file" id="ws-execute-attachment" accept=".md,.markdown,.txt,.html,.htm,.pdf,.csv,.xlsx,.docx,.pptx" style="display:none">' +
    '<button type="button" class="small" id="ws-execute-attach-btn" title="이번 실행에만 쓸 참고자료 첨부(선택, 저장 안 됨)">📎 참고자료</button>' +
    '<span class="muted" id="ws-execute-attachment-name"></span> ' +
    '<button class="small" id="ws-execute-agent-btn"' + (executeDisabled ? " disabled" : "") + '>' +
    esc(executeBtnLabel) + '</button>' +
    (!hasRunningJob && !permission.eligible
      ? '<p class="muted">' + esc(EXECUTION_BLOCK_REASONS[permission.reason] || "실행할 수 없습니다.") + '</p>'
      : '') +
    (!hasRunningJob && confirmedAgents.length > 1
      ? '<p class="muted">이 업무에 확정된 Agent가 ' + confirmedAgents.length + '개입니다 — 실행할 Agent를 위에서 선택하세요.</p>'
      : '');

  // 사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
  // 개선... 사람-에이전트 연동이 가능한 구조인지 확인") —
  // Phase 3. 이 할 일에
  // confirmed로 배정된 팀이 있으면 그 팀의 Agent 멤버 전원을 실제
  // CrewAI Crew로 실행하는 섹션을 보여준다. 팀이 여러 개 배정돼
  // 있어도 한 번에 "팀 하나"만 실행한다(사용자 결정) — 전부 동시
  // 실행하면 비용이 팀 수만큼 자동으로 커지고, 의도치 않은 팀이 섞여
  // 실행될 위험도 있다(위 Agent 다중 확정과 같은 이유).
  const teamCrewPickerHtml = confirmedTeams.length > 1
    ? '<select id="ws-team-picker" style="margin-right:0.5rem">' +
      confirmedTeams.map(t => '<option value="' + t.resource_id + '">' +
        esc(t.resource_name || ("팀 #" + t.resource_id)) + '</option>').join("") +
      '</select>'
    : '';
  const teamCrewModelFields = isSupervisor
    ? '<select id="ws-team-crew-provider"><option value="">provider 자동 판별</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      modelSelectHtml("ws-team-crew-model")
    : '';  // 구성원에게 "매니저·관리자만 고를 수 있다"는 안내는 소음이었다(2026-11-28) — 기본 모델을 쓴다
  const teamCrewBtnHtml = confirmedTeams.length
    ? teamCrewPickerHtml +
      '<select id="ws-team-crew-process" style="margin-right:0.5rem">' +
      // 사용자 요청(2026-10-20, "고급 에이전트 관리") — "팀 관리"에서
      // 저장한 기본값을 쓰고 싶으면 아무것도 고르지 않아도 되게
      // 기본 선택지로 둔다(서버가 team.default_process로 폴백,
      // `null`을 보내야 그 폴백이 동작한다 — 명시적으로 "sequential"을
      // 고정 전송하면 기본값을 영영 못 쓴다).
      '<option value="">(팀 기본값 사용)</option>' +
      '<option value="sequential">sequential(순서대로 전달)</option>' +
      '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
      teamCrewModelFields +
      '<button class="small" id="ws-execute-team-crew-btn"' + (hasRunningJob ? " disabled" : "") + '>' +
      esc(hasRunningJob ? "생성 중…" : "팀 Agent 실행(Crew, 비용 발생)") + '</button>' +
      crewAdvancedOptionsHtml("ws-team-crew", isSupervisor) +
      (confirmedTeams.length > 1
        ? '<p class="muted">이 업무에 확정된 팀이 ' + confirmedTeams.length + '개입니다 — 실행할 팀을 위에서 선택하세요(그 팀만 실행됩니다).</p>'
        : '<p class="muted">팀의 온보딩된(active) Agent 멤버 전원이 CrewAI로 함께 실행되고, 각자의 산출물이 이 할 일의 결과물로 쌓입니다.</p>')
    : '';

  // 사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
  // 최소 HITL/HOTL... 단계별 단독 또는 복수의 에이전트가 순차/병행
  // 처리") — 파이프라인 실행 섹션. 실행 중인 run이 있으면 새로 시작하는
  // 버튼은 숨기고(서버도 같은 이유로 409를 던진다) 진행상황 타임라인만
  // 보여준다 — 승인 대기 중이면 결재함으로 가라는 안내를 덧붙인다.
  const PIPELINE_RUN_STATUS_LABEL = {
    running: "진행 중", paused_for_approval: "승인 대기", completed: "완료",
    failed: "실패", cancelled: "취소",
  };
  const PIPELINE_STAGE_BADGE_CLASS = {
    running: "running", passed: "succeeded", approved: "succeeded",
    failed_retrying: "running", escalated: "failed", rejected: "failed",
  };
  const PIPELINE_STAGE_LABEL = {
    running: "진행 중", passed: "통과", approved: "승인됨",
    failed_retrying: "재시도 중", escalated: "승인 대기(반복 실패)", rejected: "반려됨",
  };
  const latestPipelineRun = pipelineRuns[0];
  const pipelineRunActive = latestPipelineRun &&
    ["running", "paused_for_approval"].includes(latestPipelineRun.status);
  // 사용자 보고(2026-10-19, "A,C,D,F,G만 표시되고 B,E는 표시 안 됨 —
  // 평가되지 않은 Gate는 미평가로 표시 필요 / 색 표시 의미가 안 보임")
  // — 이전엔 `not_measured`인 Gate(B·E는 이 플랫폼 휴리스틱이 애초에
  // 측정하지 않는다, `_has_failing_gate`와 같은 전제)를 아예 목록에서
  // 빼버려 "B·E가 실패해서 숨겨진 건가" 오해를 샀다. 이제 A-G 7개를
  // 항상 다 보여주되, 미평가는 중립(회색) 배지로 구분하고 범례를 덧붙인다.
  const GATE_LEGEND_HTML =
    '<p class="muted" style="margin:0.2rem 0 0.4rem">' +
    '<span class="gate-badge gate-ok">목표 달성</span> 통과 · ' +
    '<span class="gate-badge gate-warn">목표 달성</span> 미통과 · ' +
    '<span class="gate-badge gate-na">목표 달성</span> 미평가(이 실행에서 재지 않은 항목) — 배지에 마우스를 올리면 점수와 이유가 보입니다.' +
    '</p>';
  // 사용자 요청(2026-10-21, "Layer 2 — 실행 가시성 강화") — 기존
  // 상세 목록(Gate 배지·시도 횟수·반려 이유)은 그대로 두고, 그 위에
  // 한눈에 훑을 수 있는 브레드크럼 요약을 추가한다(교체 아님 — 상세
  // 정보를 요약이 대신할 수 없어서).
  const pipelineBreadcrumbHtml = (run) => {
    const currentIdx = run.stage_results.findIndex(sr =>
      ["running", "failed_retrying", "escalated"].includes(sr.status));
    const resolvedCurrentIdx = currentIdx === -1 ? run.stage_results.length - 1 : currentIdx;
    return '<div class="pipeline-breadcrumb">' +
      run.stage_results.map((sr, i) => {
        const cls = PIPELINE_STAGE_BADGE_CLASS[sr.status] || "";
        const isCurrent = i === resolvedCurrentIdx;
        return '<span class="pipeline-breadcrumb-step job-status-badge ' + cls +
          (isCurrent ? ' pipeline-breadcrumb-current' : '') + '">' +
          (i + 1) + '. ' + esc(sr.stage_name) + '</span>';
      }).join('<span class="pipeline-breadcrumb-arrow">→</span>') +
      '</div>';
  };

  // 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사 + 요구사항 추적성
  // 자동 채점") — `gate_scores`는 A-G 7개 외에 "구현"/"코드 검증" 단계에
  // 한해 `code_syntax`/`requirements_traceability` 키가 추가로 들어올 수
  // 있다. 아래 렌더링은 원래 제네릭하게 모든 키를 순회하므로 자동으로
  // 배지가 뜨지만, 긴 영문 키를 그대로 배지 글자로 쓰면 어색해 짧은
  // 한글 라벨로 바꿔 보여준다(A-G는 그대로 글자 자체가 식별자).
  const GATE_EXTRA_LABELS = { code_syntax: "구문", requirements_traceability: "추적성", delegation_check: "위임확인", numeric_grounding: "수치근거", deliverable_runnable: "실행가능", test_execution: "테스트실행", browser_execution: "화면실행", quote_grounding: "인용근거", term_consistency: "용어확인", checker_crosscheck: "점검대조", review_verdict: "판정", lexicon_check: "낱말확인", plan_coverage: "배정확인", required_tools: "필수도구", external_data: "외부데이터" };
  const _stageToolsHtml = (tools) => {
    if (!tools) return "";
    if (!tools.length) return ' <span class="muted" title="이 시도에서 Agent가 부른 도구가 없습니다">· 도구 사용 없음</span>';
    return ' <span class="stage-tools" title="이 시도에서 Agent가 실제로 부른 도구(호출 기록)">🔧 ' +
      tools.map(t => esc(t.tool) + (t.calls > 1 ? " ×" + t.calls : "") + (t.failed ? ' <span class="gate-warn">실패 ' + t.failed + "</span>" : "")).join(", ") +
      "</span>";
  };
  const pipelineTimelineHtml = (run) => {
    if (!run) return "";
    const rows = run.stage_results.map(sr => {
      const gateSummary = sr.gate_scores
        ? Object.entries(sr.gate_scores)
            .filter(([gid]) => !gid.startsWith("_"))  // 내부 항목(평가 리포트 토큰 등, 2026-11-25)은 배지로 안 보임
            .map(([gid, g]) => {
              const label = (gid === "review_verdict" && g.conditional) ? "조건부 승인" : (GATE_EXTRA_LABELS[gid] || GATE_SHORT_NAMES[gid] || gid);
              const gname = GATE_SHORT_NAMES[gid] ? label + "(Gate " + gid + ")" : label;
              // 참고용 검사(advisory — 낱말확인)는 통과 여부와 별개로 찾은 게 있으면 노란색(2026-11-25).
              // 조건부 승인(2026-11-25, P0-4) — 검토자가 승인하면서 고칠 곳을 짚었다: 통과여도 노란색, 라벨도 바꾼다.
              const cls = g.not_measured ? "gate-na" : (g.advisory ? (g.status === "warn" ? "gate-warn" : "gate-ok") : (g.conditional ? "gate-warn" : (g.passed ? "gate-ok" : "gate-warn")));
              const title = g.not_measured
                ? gname + " — 미평가" + (g.reason ? " (" + g.reason + ")" : "")
                : gname + " — " + (g.passed ? "통과" : "미통과") +
                  (typeof g.score === "number" ? " (점수 " + g.score + ")" : "") +
                  // 무엇이 걸렸는지 첫 몇 건(2026-11-24 — 수치근거·인용근거·용어확인).
                  (!g.passed && g.unsupported && g.unsupported.length ? " — 근거 없음: " + g.unsupported.slice(0, 3).map(u => u.text).join(" / ") : "") +
                  (!g.passed && g.suspects && g.suspects.length ? " — 확인: " + g.suspects.slice(0, 3).map(u => "'" + u.found + "'(근거: '" + u.expected + "')").join(", ") : "") +
                  (g.words && g.words.length ? " — 사전에 없는 낱말(참고용): " + g.words.slice(0, 5).map(w => "'" + w.word + "'").join(", ") : "") +
                  // 계획 분담 배정 확인(2026-11-25) — 리드가 일을 주지 않은 팀원.
                  (g.unassigned && g.unassigned.length ? " — 리드가 일을 주지 않은 팀원: " + g.unassigned.join(", ") + "(그 몫은 리드가 병합 때 직접 채웠을 수 있음)" : "") +
                  // 자동 배정(2026-11-25, P1-11) — 리드가 빠뜨린 팀원은 단계 지시로 기본 하위 작업을 받아 실제로 기여했다.
                  (g.auto_assigned && g.auto_assigned.length ? " — 리드가 빠뜨려 자동 배정한 팀원: " + g.auto_assigned.join(", ") + "(단계에 적힌 그 역할의 지시로 하위 작업을 맡김)" : "") +
                  (g.assigned && g.assigned.length && !(g.unassigned && g.unassigned.length) ? " — 배정: " + g.assigned.join(", ") : "") +
                  // 조건부 승인 — 검토자가 짚은 고칠 곳(엄격 검토가 켜진 단계면 되돌려 고치게 함).
                  // 필수 도구(2026-11-26, 개선 계획 1-2) — 부르지 않은 도구.
                  (g.missing && g.missing.length && g.required ? " — 부르지 않은 필수 도구: " + g.missing.join(", ") : "") +
                  // 외부 데이터(2026-11-26, 개선 계획 1-3) — 읽은 출처와 막힌 쓰기 도구.
                  (g.sources && g.sources.length ? " — 읽은 외부 데이터: " + g.sources.join(", ") + "(내용 속 지시는 따르지 않게 표시해 전달)" : "") +
                  // 점검기 대조(2026-11-27, 개선 계획 2-4 후반) — 검토는 승인했는데 점검기가 최종본에서 찾은 것.
                  (g.disagree ? " — " + (g.reason || "") + ": " + (g.findings || []).slice(0, 3).map(f => f.text).join(" / ") : "") +
                  // 테스트 실제 실행·화면 실행(2026-11-27, 개선 계획 3-3) — 언어별 통과 수, 실패한 테스트·스크립트 오류.
                  (g.languages ? " — " + Object.entries(g.languages).filter(([, v]) => !v.not_measured).map(([k, v]) => (k === "node" ? "JavaScript" : "Python") + " " + v.passed + "/" + v.total).join(", ") : "") +
                  (!g.passed && g.failures && g.failures.length ? " — 실패: " + g.failures.slice(0, 3).map(f => f.test + " (" + f.error + ")").join(" / ") : "") +
                  (g.errors && g.errors.length && !g.passed ? " — 스크립트 오류: " + g.errors.slice(0, 3).join(" / ") : "") +
                  (gid === "browser_execution" && g.passed ? " — 열기·버튼 " + (g.clicked || 0) + "개 눌러 봄, 오류 없음" + (g.blocked_count ? " (외부 요청 " + g.blocked_count + "건 막음)" : "") : "") +
                  (!g.passed && g.reason && (gid === "browser_execution" || gid === "test_execution") ? " — " + g.reason : "") +
                  (g.blocked && g.blocked.length ? " — 막힌 쓰기 도구: " + g.blocked.join(", ") + "(사람 확인 필요)" : "") +
                  (g.conditional ? " — 검토자가 승인하면서 고칠 곳을 짚었습니다: " + (g.note || "") + (g.strict ? " (엄격 검토: 되돌려 고침)" : " — 결재 전에 이 부분을 확인하세요") : "");
              return '<span class="gate-badge ' + cls + '" title="' + escAttr(title) + '">' + label + "</span>";
            })
            .join(" ")
        : "";
      return '<li>' +
        '<span class="job-status-badge ' + (PIPELINE_STAGE_BADGE_CLASS[sr.status] || "") + '">' +
        esc(PIPELINE_STAGE_LABEL[sr.status] || sr.status) + "</span> " +
        esc(sr.stage_seq_order + ". " + sr.stage_name) +
        (sr.attempt > 1 ? " (시도 " + sr.attempt + ")" : "") + " " + gateSummary +
        // 실제로 부른 도구(2026-11-26, 개선 계획 1-1) — 결과물에 "점검했다"고만 적고 부르지 않았는지 사람이 확인할 수 있게.
        _stageToolsHtml(sr.tools) +
        "</li>";
    }).join("");
    return '<details class="card" open><summary>' +
      esc(run.pipeline_template_name || "파이프라인") + " — " +
      esc(PIPELINE_RUN_STATUS_LABEL[run.status] || run.status) +
      (run.engine === "graph" ? ' <span class="muted" title="LangGraph 실행엔진(신규) — 서버 재시작에도 중단 지점부터 정확히 재개됩니다">⚡신규엔진</span>' : "") +
      "</summary>" +
      pipelineBreadcrumbHtml(run) +
      // 실행 중 단계 진행률(2026-11-24, P0-2) — 아래 폴링이 이 블록만 갱신한다.
      '<div id="pipeline-live-' + run.id + '">' + _pipelineLiveHtml(run) + '</div>' +
      GATE_LEGEND_HTML +
      '<ul class="job-progress-list">' + rows + "</ul>" +
      (run.status === "paused_for_approval"
        ? '<p class="muted">사람 승인이 필요한 단계입니다 — "결재/승인" 메뉴에서 처리하세요.</p>'
        : "") +
      "</details>";
  };
  const pipelineExecuteHtml = (pipelineTemplates.length && !pipelineRunActive)
    ? '<select id="ws-pipeline-template-picker" style="margin-right:0.5rem">' +
      pipelineTemplates.map(t => '<option value="' + t.id + '">' + esc(t.name) + " (" + t.stage_count + "단계)</option>").join("") +
      '</select>' +
      '<button class="small" id="ws-execute-pipeline-btn">파이프라인 실행(비용 발생)</button>' +
      // 사용자 요청(2026-11-18, "langgraph... 다음 개선 진행해줘") —
      // 새 LangGraph 실행엔진(opt-in, `use_graph_engine`)을 API
      // 호출 없이 화면에서 직접 켤 수 있게 한다. 단계 중 하나라도
      // `parallel_delegated`(위임형)면 백엔드가 즉시 400으로 거부
      // 하므로 — 어떤 템플릿이 해당하는지 미리 가려내는 대신, 그
      // 경우의 에러 메시지를 그대로 보여준다(클라이언트 쪽에
      // 단계 모드 데이터를 새로 안 끌어와도 됨 — 과설계 방지).
      '<label class="muted" style="display:block;margin-top:0.3rem">' +
      '<input type="checkbox" id="ws-pipeline-use-graph-engine"> ' +
      '신규 실행엔진(LangGraph) 사용 — 서버 재시작에도 중단 지점부터 ' +
      '정확히 재개됩니다(모든 단계 모드 지원).</label>' +
      '<p class="muted">선택한 파이프라인의 1단계부터 순서대로 진행됩니다 — "사람 승인 필요"로 표시된 단계가 아니면 Gate 기준 통과 시 자동으로 다음 단계로 넘어갑니다.</p>'
    // 사용자 요청(2026-11-17, "Human Agency Scale... 코파일럿... 실행
    // 중단 요청" → "구현 시작") — 진행 중인 실행에 "중단 요청" 버튼을
    // 둔다. 이미 요청해둔 상태(`cancel_requested`)면 버튼 대신 안내
    // 문구로 바꾼다(중복 클릭 방지).
    : (pipelineRunActive
        ? (latestPipelineRun.cancel_requested
            ? '<p class="muted">⏳ 중단 요청됨 — 다음 안전한 지점(다음 단계 시작 전)에서 멈춥니다.</p>'
            : '<button class="small danger" data-cancel-pipeline-run="' + latestPipelineRun.id + '">실행 중단 요청</button>' +
              '<p class="muted">진행 중인 단계가 끝나는 즉시 멈춥니다 — 이미 시작된 Crew 실행 자체를 바로 끊지는 않습니다.</p>')
        : '<p class="muted">등록된(활성) 파이프라인이 없습니다 — "파이프라인 관리" 탭에서 먼저 만드세요.</p>');
  // 서버 재시작으로 중단된 실행(2026-11-25, P1-6) — 끝난 단계는 다시 하지 않고 진행 중이던 단계부터 이어 간다.
  const interruptedHtml = (latestPipelineRun && latestPipelineRun.status === "failed" && latestPipelineRun.interrupted_at)
    ? '<div class="msg error">⏸ 서버가 다시 시작되면서 이 실행이 ' + esc(fmtTime(latestPipelineRun.interrupted_at)) + "에 중단됐습니다(" +
      esc(String(latestPipelineRun.current_stage_seq)) + "단계 진행 중). " +
      (["admin", "manager"].includes(ME.role)
        ? '<button class="small primary" data-resume-pipeline-run="' + latestPipelineRun.id + '" title="끝난 단계는 다시 하지 않고, 진행 중이던 단계부터 다시 실행합니다(그 단계의 AI 호출은 다시 듭니다).">이어서 실행</button>'
        : "admin·manager가 이어서 실행할 수 있습니다.") + "</div>"
    : "";
  const pipelineSectionHtml =
    '<section class="card"><h4>파이프라인 실행</h4>' +
    pipelineExecuteHtml +
    interruptedHtml +
    pipelineTimelineHtml(latestPipelineRun) +
    '</section>';

  // 2026-10-08 — 결과물이 아직 없는
  // 단계에서도 참가자들이 맥락/의사결정을 적어두는 자유 텍스트 공간.
  // 마크다운으로 렌더링(결과물 content_type='markdown'과 같은 렌더러
  // 재사용)하고, 저장 버튼을 누르기 전까진 textarea 그대로 둔다(실시간
  // 공동편집은 스코프 밖 — 미결정 사항).
  const notesCard =
    '<section class="card">' +
    '<h4>메모</h4>' +
    (taskDetail.notes
      ? '<div class="muted" id="ws-notes-view">' + renderContent(taskDetail.notes, "markdown") + '</div>'
      : '<p class="muted" id="ws-notes-view">아직 메모가 없습니다.</p>') +
    '<form id="ws-notes-form">' +
    '<textarea id="ws-notes-input" rows="3" placeholder="이 업무에 대한 메모(마크다운)" ' +
    'style="width:100%;box-sizing:border-box">' + esc(taskDetail.notes || "") + '</textarea>' +
    '<div class="actions"><button class="small" type="submit">메모 저장</button></div>' +
    '</form>' +
    '</section>';

  // 배정·결과물·댓글·Agent 실행·결재·Task 변경을 한 타임라인으로
  // 합친 통합 활동 기록. 기본은 접혀 있다(평소엔 "진행 이력"만으로
  // 충분하고, 이건 "누가 언제 뭘 했는지"를 감사하듯 들여다볼 때만 연다).
  const activityRows = activity.map(a =>
    '<tr><td>' + esc(fmtTime(a.created_at)) + '</td>' +
    '<td>사용자 #' + esc(a.actor_id) + '</td>' +
    '<td>' + esc(a.action) + '</td>' +
    '<td class="muted">' + esc(label(a.subject_type)) + (a.subject_id === null ? "" : " #" + esc(a.subject_id)) + '</td></tr>'
  ).join("");
  const activitySection =
    '<details class="card"><summary>활동 타임라인(' + activity.length + '건)</summary>' +
    (activity.length
      ? '<table><thead><tr><th>시각</th><th>행위자</th><th>동작</th><th>대상</th></tr></thead>' +
        '<tbody>' + activityRows + '</tbody></table>'
      : '<p class="muted">아직 기록된 활동이 없습니다.</p>') +
    '</details>';

  // 2026-10-08, Phase 2+3에서 만들었던 "참가자" 카드(결재 없는 가벼운
  // 초대 + ws-invite-input 검색창)는 사용자 요청(2026-10-14, "내 업무
  // 메뉴에서 참가자 기능 삭제해줘")으로 Task 워크스페이스 전체에서
  // 제거했다 — 위 participants fetch 제거 지점 주석 참고.

  // 2026-10-08, Phase 4 — 상태/우선순위는
  // select 변경 즉시 저장(드래그 없이 UI를 단순화하는 설계 의도를
  // select onchange로 구현 — "버튼"이 아니라 "select"지만 드래그앤드롭을
  // 피한다는 본질은 같다).
  const STATUS_OPTIONS = ["backlog", "queued", "in_progress", "done"];
  const PRIORITY_OPTIONS = ["low", "medium", "high", "urgent"];
  const statusPriorityBar =
    '<div class="inline" style="margin:0.25rem 0 0.75rem">' +
    '<label>상태 <select id="ws-status-select">' +
    STATUS_OPTIONS.map(s => '<option value="' + s + '"' +
      (s === taskDetail.status ? " selected" : "") + '>' + esc(label(s)) + '</option>').join("") +
    '</select></label>' +
    '<label>우선순위 <select id="ws-priority-select">' +
    PRIORITY_OPTIONS.map(p => '<option value="' + p + '"' +
      (p === taskDetail.priority ? " selected" : "") + '>' + esc(label(p)) + '</option>').join("") +
    '</select></label>' +
    '</div>';

  // 사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도 예산
  // 추적") — 파이프라인 단계 시도 + Crew 재실행을 합산한 총 재시도
  // 수와, 확정 배정 단가 기반 추정 비용 + 실제 토큰 사용량을 보여준다.
  // admin/manager에게만 "재시도 예산" 입력을 보여준다(거버넌스성
  // 설정이라 `priority`/`status`와 달리 참가자 전체에 안 열어둠).
  // 재시도·단가 비용·토큰은 운영 지표라 admin·manager에게만(2026-11-28 — 일을 하는 사람에게는 소음이었다)
  const budgetCardHtml = budget && isSupervisor
    ? '<div class="card" style="margin:0.25rem 0 0.75rem">' +
      '<div class="stat-row"><span>총 재시도</span><span>' +
      budget.retries.total_retries + '회(파이프라인 ' + budget.retries.pipeline_stage_attempts +
      ' + Crew ' + budget.retries.crew_run_count + ')</span></div>' +
      '<div class="stat-row"><span>추정 비용(단가 기반)</span><span>' +
      budget.cost.estimated_cost.toFixed(2) + '</span></div>' +
      '<div class="stat-row"><span>실제 토큰 사용량</span><span>' +
      budget.cost.token_usage.total_tokens + '</span></div>' +
      (isSupervisor
        ? '<div class="inline" style="margin-top:0.4rem">' +
          '<label>재시도 예산(빈 값=무제한) <input type="number" min="0" id="ws-retry-budget-input" ' +
          'value="' + (taskDetail.max_retry_budget == null ? "" : taskDetail.max_retry_budget) + '" style="width:5rem"></label>' +
          '<button class="small" type="button" id="ws-retry-budget-save">저장</button>' +
          '</div>'
        : '') +
      '</div>'
    : "";

  // 사용자 요청(2026-11-17, "Human Agency Scale... 인간 고유 능력을
  // 보호하는 Task는 Gate 점수와 무관하게 항상 사람에게 올라가야
  // 한다" → "구현 시작") — `budgetCardHtml`과 달리 `budget` 조회
  // 실패(403 등)에 묶이지 않도록 별도 블록으로 둔다. admin/manager
  // 전용(거버넌스성 설정 — `max_retry_budget`과 같은 권한 범위).
  const humanJudgmentCardHtml = isSupervisor
    ? '<div class="card" style="margin:0.25rem 0 0.75rem">' +
      '<label><input type="checkbox" id="ws-human-judgment-toggle"' +
      (taskDetail.requires_human_judgment ? " checked" : "") + '> ' +
      '사람 판단 필수 — 켜면 이 할 일이 타는 모든 파이프라인 단계는 ' +
      'Gate 점수와 무관하게 항상 사람 결재를 거칩니다(협상·민감한 ' +
      '커뮤니케이션 등 AI가 대신 판단해선 안 되는 업무용)</label>' +
      '</div>'
    : "";

  // 의존관계 그래프(사용자 결정: Riido 수준 시각화). 제거는
  // SVG 안에서 처리하기보다(클릭 판별이 번거롭다) 그래프 아래 평범한
  // 목록에 "제거" 버튼을 둬서 접근성·구현 단순성을 둘 다 잡는다 —
  // 이 task가 의존하는 쪽(`e.task_id === taskId`)만 여기서 지울 수
  // 있다(다른 task가 이 task에 의존하는 edge는 그 task 화면에서 지운다).
  const dependencyListRows = depGraph.edges.map(e => {
    const fromNode = depGraph.nodes.find(n => n.id === e.task_id);
    const toNode = depGraph.nodes.find(n => n.id === e.depends_on_task_id);
    return '<li>' + esc(fromNode ? fromNode.role_name : "#" + e.task_id) +
      ' → ' + esc(toNode ? toNode.role_name : "#" + e.depends_on_task_id) +
      ' <span class="muted">완료를 기다림</span>' +
      (e.task_id === taskId
        ? ' <button class="small danger" data-remove-dependency="' + e.depends_on_task_id + '">제거</button>'
        : '')
      + '</li>';
  }).join("");
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") —
  // 같은 브리프의 다른 할 일(이미 선행으로 걸린 것·자기 자신 제외)만
  // 골라 select로 보여준다. 다른 브리프 작업을 선행으로 걸고 싶으면
  // (백엔드는 허용하지만 드문 경우) API를 직접 써야 한다 — 그 희소
  // 케이스 때문에 모두가 쓰는 폼을 다시 "ID 기억하기"로 되돌리지 않는다.
  const existingDepIds = new Set(
    depGraph.edges.filter(e => e.task_id === taskId).map(e => e.depends_on_task_id)
  );
  const depCandidates = siblingTasks.filter(t => t.id !== taskId && !existingDepIds.has(t.id));
  const dependencyForm = depCandidates.length
    ? '<form class="inline" id="ws-dependency-form">' +
      '<select id="ws-dependency-input" required style="min-width:220px"><option value="">— 선행 작업 선택 —</option>' +
      depCandidates.map(t =>
        '<option value="' + t.id + '">#' + t.id + " " + esc(t.role_name) + " (" + esc(label(t.status)) + ")</option>"
      ).join("") +
      '</select>' +
      '<button class="small" type="submit">선행 작업 추가</button>' +
      '</form>'
    : '<p class="muted">같은 브리프에 선행 작업으로 추가할 수 있는 다른 할 일이 없습니다.</p>';
  // 선행·후행 관계도 없고 같은 요청의 다른 할 일도 없으면 네모 하나뿐인 그림이라 숨긴다(2026-11-28)
  const dependencySection = (!depGraph.edges.length && !depCandidates.length) ? "" :
    '<section class="card">' +
    '<h4>의존관계</h4>' +
    '<p class="muted">이 업무와 연결된 선행/후행 작업을 그래프로 보여줍니다 — 테두리 색은 우선순위(빨강=긴급·주황=높음·파랑=보통·회색=낮음), 네모를 클릭하면 그 업무로 이동합니다.</p>' +
    '<div style="overflow-x:auto">' + renderDependencyGraphSvg(depGraph, taskId) + '</div>' +
    (dependencyListRows ? '<ul>' + dependencyListRows + '</ul>' : '') +
    dependencyForm +
    '</section>';

  // 사용자 요청(2026-09-29, "UI/UX를 개선해줘") — 원래 textarea·provider/
  // model·"LLM으로 도와줘"·"결과물 제출"이 한 줄(.inline flex-wrap)에 전부
  // 뒤섞여 있어 좁은 화면에서 텍스트가 select 옆에 끼어 줄바꿈이 어색했다.
  // textarea를 독립된 줄로 분리하고, "결과물 작성"과 "AI 에이전트 실행"을
  // 서로 다른 카드로 나눠 두 행동의 성격이 다르다는 걸 시각적으로 구분한다.
  // 요청 내용(2026-11-28, 신입·일반 사용자 관점) — 일을 맡은 사람이 열어도 무엇을 해 달라는 건지·누가 요청했는지가 없었다.
  const briefText = (taskDetail.brief_text || "").trim();
  const requestCard = briefText
    ? '<section class="card ws-request"><h4>📄 요청 내용</h4>' +
      '<p class="muted">' + (taskDetail.requested_by_name ? "요청: " + esc(taskDetail.requested_by_name) : "") +
      (taskDetail.assigned_by_name ? " · 배정: " + esc(taskDetail.assigned_by_name) : "") + " · 업무 요청 #" + esc(taskDetail.brief_id) + "</p>" +
      (briefText.length > 600
        ? '<details><summary>' + esc(briefText.slice(0, 200)) + '… <b>전체 보기</b></summary><div class="ws-request-body">' + _renderMarkdown(briefText) + "</div></details>"
        : '<div class="ws-request-body">' + _renderMarkdown(briefText) + "</div>") +
      "</section>"
    : "";
  detail.innerHTML =
    '<h3>협업 워크스페이스 — 할 일 #' + esc(taskId) + ' · ' + esc(taskDetail.role_name || "") + '</h3>' +
    requestCard +
    statusPriorityBar +
    '<div id="ws-schedule-slot"></div>' +
    budgetCardHtml +
    humanJudgmentCardHtml +
    finalResultCard +
    notesCard +
    // 2026-10-08 — 이
    // Task 전용 대화(`conversations.linked_task_id`)를 여기에
    // 임베드한다. 결과물 유무와 무관하게 항상 노출(그때 발견한
    // "결과물 0건이면 대화 자체가 불가능했던" 구조적 한계를 여기서
    // 해소 — 대화는 더 이상 deliverable에 종속되지 않는다).
    '<div id="ws-conversation-' + taskId + '"><p class="muted">대화를 불러오는 중…</p></div>' +
    dependencySection +
    // 사용자 요청(2026-10-19, "각각이 모두 필요하며 동작하는 기능인지
    // 확인하고... 합리적인 방식으로 순서를 조정... 진행이력, 활동
    // 타임라인이 중간에 보이는 것이 맞는지") — 실측 점검 결과 10개
    // 섹션 전부 실제로 동작하는 기능이었다(미사용/죽은 코드 없음).
    // 다만 순서가 "상태·메모·대화·의존관계(맥락 파악) → 진행
    // 이력/활동 타임라인(과거 기록) → 결과물 작성/AI 실행/파이프라인
    // 실행(할 일)"이라, 지금 업무를 "하러" 들어온 사람이 액션 버튼에
    // 닿기 전에 길어질 수 있는 과거 이력 목록부터 지나가야 했다.
    // "맥락 파악 → 지금 할 일(액션) → (필요하면 펼쳐보는) 과거 기록"
    // 순서로 바꾼다 — 진행 이력·활동 타임라인은 둘 다 "기록 열람"
    // 범주라 맨 끝으로, 액션 3종(결과물 작성/AI 에이전트 실행/파이프라인
    // 실행)은 의존관계 바로 다음으로 옮긴다.
    '<section class="card">' +
    '<h4>결과물 작성</h4>' +
    '<form id="ws-submit-form">' +
    '<textarea id="ws-content" rows="3" placeholder="결과물 내용을 입력하세요" style="width:100%;box-sizing:border-box"></textarea>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    contentTypeSelectHtml("ws-content-type") +
    modelFields +
    '<button class="small" type="button" id="ws-assist-btn">LLM으로 도와줘(비용 발생)</button>' +
    '<button class="primary" type="submit">결과물 제출</button>' +
    '</div>' +
    '</form>' +
    '</section>' +
    // 실행할 수 없는 구성원에게는 숨긴다(2026-11-28) — 비활성 버튼과 권한 안내만 보였다
    (isSupervisor || permission.eligible || hasRunningJob
      ? '<section class="card">' +
        '<h4>AI 에이전트 실행</h4>' +
        contentTypeSelectHtml("ws-execute-content-type") + ' ' +
        executeBtnHtml +
        '</section>'
      : '') +
    // 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고...
    // 에이전트 그룹(crew 구성)을 직관적으로") — 지금까지 여러 Agent를
    // 묶어 이 업무에 투입하려면 팀을 미리 등록·배정·승인까지 마쳐야만
    // 가능했다("팀 Agent 실행" 섹션이 confirmedTeams가 있을 때만
    // 보이는 이유). 팀 전제조건 없이 즉석으로 묶어 바로 실행하려면
    // "Crew 빌더"로 — 팀 등록 여부와 무관하게 항상 노출한다.
    // Crew 빌더는 admin·manager 화면이라 구성원에게는 열리지 않는 단추였다(2026-11-28)
    (isSupervisor
      ? '<section class="card"><h4>여러 Agent를 즉석으로 묶어 실행</h4>' +
        '<p class="muted">팀을 미리 등록하지 않아도 Agent를 그 자리에서 골라 바로 실행할 수 있습니다 — 결과는 이 업무의 결과물로 그대로 쌓입니다.</p>' +
        '<button class="small" id="ws-open-crew-builder-btn">Crew 빌더에서 구성하기</button>' +
        '</section>'
      : '') +
    (confirmedTeams.length
      ? '<section class="card"><h4>팀 Agent 실행(Crew)</h4>' +
        contentTypeSelectHtml("ws-team-crew-content-type") + ' ' + teamCrewBtnHtml + '</section>'
      : '') +
    (isSupervisor || latestPipelineRun ? pipelineSectionHtml : '') +  // 구성원은 실행이 있을 때 진행만 본다(2026-11-28)
    '<details class="workspace-timeline-section">' +
    '<summary class="muted" style="cursor:pointer">진행 이력(중간 과정 포함)</summary>' +
    '<div id="workspace-timeline">' + timeline + '</div>' +
    '</details>' +
    activitySection +
    '<div id="ws-result"></div>';

  if (isSupervisor) wireProviderModelSelect("ws-provider", "ws-model", modelsByProvider);
  if (confirmedTeams.length) {
    if (isSupervisor) {
      wireProviderModelSelect("ws-team-crew-provider", "ws-team-crew-model", modelsByProvider);
    }
    wireCrewAdvancedOptions("ws-team-crew", modelsByProvider, isSupervisor);
  }
  const executePipelineBtn = document.getElementById("ws-execute-pipeline-btn");
  if (executePipelineBtn) {
    executePipelineBtn.onclick = async () => {
      const templateId = Number(document.getElementById("ws-pipeline-template-picker").value);
      // 사용자 요청(2026-11-12, "UI/UX 개선") — 단일 Agent 실행/Crew
      // 실행에는 이미 비용 확인이 있었는데, 가장 비용이 커질 수 있는
      // (여러 단계 × 참여자 수만큼 LLM 호출이 누적되는) 파이프라인
      // 실행에는 확인이 전혀 없었다 — 실측으로 발견한 비대칭 수정.
      if (!(await confirmModal(
        "이 파이프라인을 처음부터 끝까지 실행합니다 — 단계마다 실제 LLM API를 호출해 비용이 발생하고, 참여자·재시도가 많을수록 누적 비용이 커질 수 있습니다. 계속할까요?"
      ))) return;
      const useGraphEngineBox = document.getElementById("ws-pipeline-use-graph-engine");
      try {
        await api("POST", "/api/tasks/" + taskId + "/execute-pipeline", {
          pipeline_template_id: templateId,
          use_graph_engine: !!(useGraphEngineBox && useGraphEngineBox.checked),
        });
        showMsg("global-msg", "파이프라인 실행을 시작했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }
  const resumePipelineRunBtn = detail.querySelector("[data-resume-pipeline-run]");
  if (resumePipelineRunBtn) {
    resumePipelineRunBtn.onclick = async () => {
      resumePipelineRunBtn.disabled = true;
      try {
        await api("POST", "/api/pipeline-runs/" + resumePipelineRunBtn.dataset.resumePipelineRun + "/resume");
        showMsg("global-msg", "중단된 단계부터 이어서 실행합니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { resumePipelineRunBtn.disabled = false; showMsg("global-msg", e.message, "error"); }
    };
  }
  const cancelPipelineRunBtn = detail.querySelector("[data-cancel-pipeline-run]");
  if (cancelPipelineRunBtn) {
    cancelPipelineRunBtn.onclick = async () => {
      if (!(await confirmModal(
        "이 파이프라인 실행에 중단을 요청합니다 — 지금 바로 멈추는 게 아니라, 진행 중인 단계가 끝나는 다음 안전한 지점에서 멈춥니다. 계속할까요?"
      ))) return;
      try {
        await api("POST", "/api/pipeline-runs/" + cancelPipelineRunBtn.dataset.cancelPipelineRun + "/cancel");
        showMsg("global-msg", "중단을 요청했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }
  items.forEach(d => loadDeliverableComments(d.id, taskId, containerId));
  _loadTaskConversation(taskId);

  detail.querySelectorAll("[data-decide-deliverable]").forEach(btn => {
    btn.onclick = async () => {
      const reasonInput = detail.querySelector('[data-decide-reason="' + btn.dataset.decideDeliverable + '"]');
      const reason = reasonInput ? reasonInput.value.trim() : "";
      try {
        await api("POST", "/api/approvals/" + btn.dataset.decideDeliverable + "/decide", {
          approve: btn.dataset.approve === "1",
          reason: reason || null,
        });
        showMsg("global-msg", "처리했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  detail.querySelectorAll("[data-submit-deliverable]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/deliverables/" + btn.dataset.submitDeliverable + "/submit", {});
        showMsg("global-msg", "검토를 요청했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  // 부분 수정 요청(2026-11-24, P1-6) — 섹션을 고르거나 바꿀 부분을 붙여 넣고 요청을 적으면 AI가 그 부분만
  // 다시 쓴 수정안을 만든다(결과물은 아직 그대로). 비교를 보고 수락하면 반영되고(이전 내용은 버전 이력),
  // 거절하면 기록만 남는다.
  detail.querySelectorAll("[data-edit-proposal]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.editProposal;
      const box = document.getElementById("edit-proposal-" + id);
      if (!box.hidden) { box.hidden = true; return; }
      let secs, props;
      try {
        [secs, props] = await Promise.all([
          api("GET", "/api/deliverables/" + id + "/sections"),
          api("GET", "/api/deliverables/" + id + "/edit-proposals"),
        ]);
      } catch (e) { showMsg("global-msg", e.message, "error"); return; }
      const statusLabel = { pending: "검토 대기", accepted: "수락됨", rejected: "거절됨" };
      const proposalHtml = p =>
        '<div class="edit-proposal" data-proposal-id="' + p.id + '">' +
        '<div class="muted">#' + p.id + " · " + esc(p.target_label) + " · 요청: " + esc(p.instruction) + " · " +
        esc(statusLabel[p.status] || p.status) + (p.decided_by_name ? " (" + esc(p.decided_by_name) + ")" : "") + "</div>" +
        renderTextDiff(p.original_text, p.proposed_text) +
        ((p.warnings || []).length && p.status === "pending"
          ? '<p class="msg error">⚠ 수락 전에 확인하세요: ' + p.warnings.map(esc).join(" · ") + "</p>" : "") +
        (p.status === "pending"
          ? '<div class="inline"><button class="small primary" data-proposal-accept="' + p.id + '">수락(반영)</button>' +
            '<button class="small" data-proposal-reject="' + p.id + '">거절</button></div>'
          : "") + "</div>";
      box.innerHTML =
        '<div class="inline" style="flex-wrap:wrap">' +
        '<label class="muted">바꿀 섹션 <select class="ep-section">' +
        secs.map(s => '<option value="' + s.index + '">' + esc(s.title) + " (" + s.chars.toLocaleString() + "자)</option>").join("") +
        "</select></label></div>" +
        '<details><summary class="muted">또는 바꿀 부분을 직접 붙여 넣기(결과물의 글을 그대로 복사)</summary>' +
        '<textarea class="ep-selected" rows="3" style="width:100%;box-sizing:border-box" placeholder="여기에 붙여 넣으면 섹션 대신 이 부분만 고칩니다"></textarea></details>' +
        '<div class="inline"><input class="ep-instruction" maxlength="2000" style="flex:1;min-width:16rem" placeholder="예: 3문장으로 줄이고 결론을 앞에 / 표로 바꿔 줘 / 수치 근거를 덧붙여 줘">' +
        '<button class="small primary ep-create">수정안 만들기</button></div>' +
        '<p class="muted">AI는 고른 부분만 다시 씁니다 — 나머지는 그대로 유지됩니다. 수정안은 수락해야 반영됩니다.</p>' +
        '<div class="ep-list">' + props.map(proposalHtml).join("") + "</div>";
      box.hidden = false;
      const list = box.querySelector(".ep-list");
      const bindDecisions = () => {
        list.querySelectorAll("[data-proposal-accept],[data-proposal-reject]").forEach(b => {
          b.onclick = async () => {
            const pid = b.dataset.proposalAccept || b.dataset.proposalReject;
            const accept = Boolean(b.dataset.proposalAccept);
            b.disabled = true;
            try {
              await api("POST", "/api/edit-proposals/" + pid + "/" + (accept ? "accept" : "reject"));
              showMsg("global-msg", accept ? "수정안을 반영했습니다 — 이전 내용은 버전 이력에 남았습니다." : "수정안을 거절했습니다.", "ok");
              if (accept) { renderCollabWorkspace(taskId, containerId); return; }
              box.hidden = true; btn.click();
            } catch (e) { b.disabled = false; showMsg("global-msg", e.message, "error"); }
          };
        });
      };
      bindDecisions();
      box.querySelector(".ep-create").onclick = async ev => {
        const instruction = box.querySelector(".ep-instruction").value.trim();
        const selected = box.querySelector(".ep-selected").value;
        if (!instruction) { showMsg("global-msg", "어떻게 고칠지 요청을 입력하세요", "error"); return; }
        const payload = { instruction };
        if (selected.trim()) payload.selected_text = selected; else payload.section_index = Number(box.querySelector(".ep-section").value);
        ev.target.disabled = true;
        const old = ev.target.textContent;
        ev.target.textContent = "만드는 중…(AI 호출)";
        try {
          const p = await api("POST", "/api/deliverables/" + id + "/edit-proposals", payload);
          list.insertAdjacentHTML("afterbegin", proposalHtml(p));
          bindDecisions();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
        finally { ev.target.disabled = false; ev.target.textContent = old; }
      };
    };
  });

  // 버전 이력(2026-11-23, P1-6) — 펼칠 때 불러와, 고른 이전 버전(다시 낸 버전 또는 직접 고치기 전
  // 내용)과 현재본의 줄 단위 차이를 보여 준다.
  detail.querySelectorAll("[data-deliverable-history]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.deliverableHistory;
      const box = document.getElementById("history-" + id);
      if (!box) return;
      if (!box.hidden) { box.hidden = true; return; }
      try {
        const h = await api("GET", "/api/deliverables/" + id + "/history");
        const options = [];
        h.versions.filter(v => !v.is_current).forEach(v => options.push({
          label: v.version + "번째 버전 · " + label(v.status) + " · " + fmtTime(v.created_at), content: v.content }));
        h.edits.forEach((e, i) => options.push({
          label: "직접 고치기 전 " + (i + 1) + " · " + (e.edited_by || "") + " · " + fmtTime(e.created_at), content: e.content }));
        const current = h.versions.find(v => v.is_current);
        const head = '<p class="muted">이 작성자의 버전 ' + h.versions.length + "개 중 " + (current ? current.version : "?") +
          "번째 · 직접 고친 기록 " + h.edits.length + "건</p>";
        if (!options.length) {
          box.innerHTML = head + '<p class="muted">비교할 이전 버전이 없습니다.</p>';
        } else {
          box.innerHTML = head + '<label class="muted">비교할 이전 내용 <select data-history-select>' +
            options.map((o, i) => '<option value="' + i + '">' + esc(o.label) + "</option>").join("") +
            '</select></label><div data-history-diff style="margin-top:0.4rem"></div>';
          const sel = box.querySelector("[data-history-select]");
          const diffBox = box.querySelector("[data-history-diff]");
          const show = () => { diffBox.innerHTML = renderTextDiff(options[Number(sel.value)].content, h.current_content); };
          sel.onchange = show;
          sel.value = String(options.length - 1);
          show();
        }
        box.hidden = false;
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  // 저장소에 커밋(2026-11-24, P0-3 2단계) — 미리보기(아무것도 쓰지 않음)로 파일·점검을 보여 주고,
  // 점검을 모두 통과했을 때만 "main에 커밋"을 누를 수 있다. 서버가 커밋 직전에 점검을 다시 한다.
  detail.querySelectorAll("[data-repo-commit]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.repoCommit;
      const box = document.getElementById("repo-commit-" + id);
      if (!box.hidden) { box.hidden = true; return; }
      let repos = [];
      try { repos = (await api("GET", "/api/code-repositories")).filter(r => r.is_active); }
      catch (e) { showMsg("global-msg", e.message, "error"); return; }
      if (!repos.length) {
        box.innerHTML = '<p class="muted">등록된 코드 저장소가 없습니다 — "연동 설정"에서 먼저 저장소를 등록하세요.</p>';
        box.hidden = false;
        return;
      }
      box.innerHTML =
        '<div class="inline" style="flex-wrap:wrap">' +
        '<select class="rc-repo">' + repos.map(r => '<option value="' + escAttr(r.name) + '">' + esc(r.name) + "</option>").join("") + "</select>" +
        '<button class="small rc-preview">미리보기</button></div>' +
        '<p class="muted">결과물의 코드 블록 중 <b>경로가 적힌 것만</b>(블록 첫 줄 <code># src/app.py</code> 또는 블록 바로 위 줄 <code>`src/app.py`</code>) 저장소의 <b>main 브랜치에 로컬 커밋</b>합니다. push는 하지 않습니다.</p>' +
        '<div class="rc-result"></div>';
      box.hidden = false;
      const result = box.querySelector(".rc-result");
      box.querySelector(".rc-preview").onclick = async () => {
        const repository = box.querySelector(".rc-repo").value;
        try {
          const p = await api("POST", "/api/deliverables/" + id + "/repo-commit/preview", { repository });
          const checks = p.checks.map(c =>
            "<li>" + (c.ok ? (c.info ? "ℹ️ " : "✅ ") : "❌ ") + esc(c.name) + (c.detail ? ' <span class="muted">— ' + esc(c.detail) + "</span>" : "") + "</li>"
          ).join("");
          const files = p.files.map(f =>
            "<tr><td><code>" + esc(f.path) + "</code></td><td>" + (f.action === "new" ? "새 파일" : "수정") + "</td><td>" + f.lines + "줄</td><td>" +
            (f.problems.length ? '<span class="msg error">' + esc(f.problems.join(", ")) + "</span>" : "") + "</td></tr>"
          ).join("");
          result.innerHTML =
            "<ul>" + checks + "</ul>" +
            (files ? '<div class="table-wrap"><table><thead><tr><th>경로</th><th>구분</th><th>크기</th><th>문제</th></tr></thead><tbody>' + files + "</tbody></table></div>" : "") +
            (p.skipped_blocks.length ? '<p class="muted">경로가 없어 커밋하지 않는 블록: ' + p.skipped_blocks.map(s => "<code>" + esc(s) + "</code>").join(", ") + "</p>" : "") +
            (p.ok
              ? '<div class="inline"><input class="rc-message" maxlength="200" placeholder="커밋 메시지(비우면 결과물·업무 번호로)" style="min-width:18rem">' +
                '<button class="small primary rc-commit" data-mode="pr" title="main에서 새 브랜치(hair/deliverable-번호)를 만들어 커밋하고 origin에 push합니다. GitHub이고 서버에 HAIR_GITHUB_TOKEN이 있으면 PR을 열고, 없으면 PR 만들기 링크를 드립니다. main은 그대로입니다.">브랜치에 올려 PR 만들기</button>' +
                '<button class="small rc-commit" data-mode="main" title="main 브랜치에 바로 로컬 커밋합니다(push 없음).">' + esc(p.branch) + "에 바로 커밋</button></div>"
              : '<p class="msg error">통과하지 못한 점검이 있어 커밋할 수 없습니다.</p>');
          result.querySelectorAll(".rc-commit").forEach(commitBtn => {
            commitBtn.onclick = async () => {
              const mode = commitBtn.dataset.mode || "main";
              result.querySelectorAll(".rc-commit").forEach(b => { b.disabled = true; });
              try {
                const r = await api("POST", "/api/deliverables/" + id + "/repo-commit", {
                  repository, mode, message: result.querySelector(".rc-message").value.trim() || null,
                });
                if (mode === "pr") {
                  // PR 모드(2026-11-25, P1-8) — push·PR 결과를 패널에 남긴다(링크를 눌러 확인·병합).
                  const prLine = r.pr
                    ? (r.pr.created ? 'PR을 열었습니다: <a href="' + escAttr(r.pr.url) + '" target="_blank" rel="noopener">' + esc(r.pr.url) + "</a>"
                      : 'PR은 만들지 않았습니다(' + esc(r.pr.reason || "") + ') — <a href="' + escAttr(r.pr.url) + '" target="_blank" rel="noopener">여기서 PR 만들기</a>')
                    : (r.pushed ? "push했습니다(GitHub 저장소가 아니라 PR은 만들지 않음)." : esc(r.push_error || "push하지 못했습니다."));
                  result.innerHTML = '<p class="msg ' + (r.pushed ? "ok" : "error") + '">' + esc(r.repository) + " 브랜치 <code>" + esc(r.branch) +
                    "</code>에 커밋했습니다(" + esc(r.short) + ", 파일 " + r.files.length + "개). " + prLine + "</p>";
                  showMsg("global-msg", "브랜치 " + r.branch + "에 커밋했습니다.", r.pushed ? "ok" : "error");
                } else {
                  showMsg("global-msg", r.repository + " " + r.branch + "에 커밋했습니다(" + r.short + ", 파일 " + r.files.length + "개) — push는 하지 않았습니다.", "ok");
                  box.hidden = true;
                }
              } catch (e) {
                result.querySelectorAll(".rc-commit").forEach(b => { b.disabled = false; });
                showMsg("global-msg", e.message, "error");
              }
            };
          });
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      };
    };
  });
  // Word 내보내기(2026-11-23, P1-7) — api()와 같은 인증 헤더(탭별 토큰)로 받아 파일로 저장한다.
  detail.querySelectorAll("[data-export-docx]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.exportDocx;
      const fmt = btn.dataset.exportFmt || "docx";
      const headers = {};
      const token = sessionStorage.getItem("hair_token");
      if (token) headers["Authorization"] = "Bearer " + token;
      try {
        const resp = await fetch("/api/deliverables/" + id + "/export." + fmt, { headers, credentials: "same-origin" });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || ("내보내기 실패(" + resp.status + ")"));
        }
        const blob = await resp.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "deliverable-" + id + "." + fmt;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  detail.querySelectorAll("[data-download-deliverable]").forEach(btn => {
    btn.onclick = () => {
      const d = items.find(x => String(x.id) === btn.dataset.downloadDeliverable)
        || (finalResult && String(finalResult.id) === btn.dataset.downloadDeliverable ? finalResult : null);
      if (d) downloadDeliverable(d.content, d.content_type, d.id);
    };
  });

  document.getElementById("ws-notes-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const notesVal = document.getElementById("ws-notes-input").value;
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/notes", { notes: notesVal });
      showMsg("global-msg", "메모를 저장했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자 기능 삭제해줘")으로
  // ws-invite-input 검색-초대 배선을 제거했다(위 participants fetch
  // 제거 지점 주석 참고).

  document.getElementById("ws-status-select").addEventListener("change", async (ev) => {
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/status", { status: ev.target.value });
      showMsg("global-msg", "상태를 변경했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  _renderTaskSchedule(document.getElementById("ws-schedule-slot"), taskId);

  document.getElementById("ws-priority-select").addEventListener("change", async (ev) => {
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/priority", { priority: ev.target.value });
      showMsg("global-msg", "우선순위를 변경했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const retryBudgetSaveBtn = document.getElementById("ws-retry-budget-save");
  if (retryBudgetSaveBtn) retryBudgetSaveBtn.addEventListener("click", async () => {
    const raw = document.getElementById("ws-retry-budget-input").value.trim();
    const maxRetryBudget = raw === "" ? null : Number(raw);
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/retry-budget", { max_retry_budget: maxRetryBudget });
      showMsg("global-msg", "재시도 예산을 저장했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const humanJudgmentToggle = document.getElementById("ws-human-judgment-toggle");
  if (humanJudgmentToggle) humanJudgmentToggle.addEventListener("change", async () => {
    const requiresHumanJudgment = humanJudgmentToggle.checked;
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/human-judgment", { requires_human_judgment: requiresHumanJudgment });
      showMsg("global-msg", requiresHumanJudgment ? "이제 이 할 일은 항상 사람 결재를 거칩니다." : "사람 판단 필수를 껐습니다.", "ok");
    } catch (e) {
      humanJudgmentToggle.checked = !requiresHumanJudgment;  // 실패 시 되돌림
      showMsg("global-msg", e.message, "error");
    }
  });

  const dependencyFormEl = document.getElementById("ws-dependency-form");
  if (dependencyFormEl) dependencyFormEl.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const dependsOnId = Number(document.getElementById("ws-dependency-input").value);
    if (!dependsOnId) { showMsg("global-msg", "선행 작업을 선택하세요.", "error"); return; }
    try {
      await api("POST", "/api/tasks/" + taskId + "/dependencies", {
        depends_on_task_id: dependsOnId,
      });
      showMsg("global-msg", "선행 작업을 추가했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  detail.querySelectorAll("[data-graph-node]").forEach(node => {
    node.addEventListener("click", () => {
      const targetId = Number(node.dataset.graphNode);
      if (targetId !== taskId) renderCollabWorkspace(targetId, containerId);
    });
  });

  detail.querySelectorAll("[data-remove-dependency]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        await api(
          "DELETE",
          "/api/tasks/" + taskId + "/dependencies/" + btn.dataset.removeDependency
        );
        showMsg("global-msg", "선행 작업을 제거했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  document.getElementById("ws-submit-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const contentVal = document.getElementById("ws-content").value.trim();
    if (!contentVal) { showMsg("global-msg", "내용을 입력해야 합니다.", "error"); return; }
    try {
      await api("POST", "/api/tasks/" + taskId + "/deliverables", {
        content: contentVal,
        content_type: document.getElementById("ws-content-type").value,
      });
      showMsg("global-msg", "결과물을 제출했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const assistBtn = document.getElementById("ws-assist-btn");
  assistBtn.onclick = async () => {
    const draftContext = document.getElementById("ws-content").value.trim() || null;
    const payload = { draft_context: draftContext, content_type: document.getElementById("ws-content-type").value };
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = modelSelectValue("ws-model");
      if (provider) payload.provider = provider;
      if (model) payload.model = model;
    }
    try {
      const result = await withBusy(assistBtn, "생성 중…(실제 API 호출)", () => api("POST", "/api/tasks/" + taskId + "/assist", payload));
      document.getElementById("ws-content").value = result.draft;
      showMsg("global-msg", "LLM 초안을 받았습니다 — 검토·수정 후 제출하세요.", "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };

  const attachBtn = document.getElementById("ws-execute-attach-btn");
  const attachInput = document.getElementById("ws-execute-attachment");
  const attachNameEl = document.getElementById("ws-execute-attachment-name");
  if (attachBtn) {
    attachBtn.onclick = () => attachInput.click();
    attachInput.addEventListener("change", () => {
      const f = attachInput.files && attachInput.files[0];
      attachNameEl.textContent = f ? ("첨부됨: " + f.name) : "";
    });
  }

  const executeBtn = document.getElementById("ws-execute-agent-btn");
  if (executeBtn) executeBtn.onclick = async () => {  // 실행할 수 없는 구성원에게는 영역이 없다(2026-11-28)
    if (!(await confirmModal("이 업무에 배정된 AI 에이전트를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?"))) return;
    const formData = new FormData();
    formData.append("content_type", document.getElementById("ws-execute-content-type").value);
    const agentPicker = document.getElementById("ws-agent-picker");
    if (agentPicker) formData.append("agent_id", agentPicker.value);
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = modelSelectValue("ws-model");
      if (provider) formData.append("provider", provider);
      if (model) formData.append("model", model);
    }
    const attachedFile = attachInput && attachInput.files && attachInput.files[0];
    if (attachedFile) formData.append("attachment", attachedFile);
    try {
      const queued = await apiUpload("/api/tasks/" + taskId + "/execute-agent", formData);
      // 202 응답 직후 즉시 새로고침해 "생성 중…" 카드를 먼저 보여준다
      // (Phase 4 — 응답이 빨리 오는 게 이 비동기화의 핵심이라, 여기서
      // 폴링이 끝날 때까지 버튼을 막아두면 의미가 없다).
      renderCollabWorkspace(taskId, containerId);
      const job = await pollExecutionJob(taskId, queued.job_id);
      if (job.status === "succeeded") {
        showMsg("global-msg", "AI 에이전트 실행이 완료됐습니다.", "ok");
      } else {
        showMsg("global-msg", "AI 에이전트 실행이 실패했습니다: " + job.error, "error");
      }
      if (isViewingWorkspace(taskId, containerId)) renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };

  const openCrewBuilderBtn = document.getElementById("ws-open-crew-builder-btn");
  if (openCrewBuilderBtn) {
    openCrewBuilderBtn.onclick = () => {
      _pendingCrewBuilderTaskId = taskId;
      selectTab("crew");
    };
  }

  const executeTeamCrewBtn = document.getElementById("ws-execute-team-crew-btn");
  if (executeTeamCrewBtn) {
    executeTeamCrewBtn.onclick = async () => {
      const teamPicker = document.getElementById("ws-team-picker");
      const teamId = teamPicker ? Number(teamPicker.value) : confirmedTeams[0].resource_id;
      if (!(await confirmModal("이 팀의 Agent 멤버 전원을 Crew로 실행합니다(실제 LLM API 호출, 비용은 참여 Agent 수만큼 커질 수 있습니다). 계속할까요?"))) return;
      const processVal = document.getElementById("ws-team-crew-process").value;
      const payload = Object.assign({
        team_id: teamId,
        process: processVal || null,
        content_type: document.getElementById("ws-team-crew-content-type").value,
      }, crewAdvancedOptionsPayload("ws-team-crew", isSupervisor));
      if (isSupervisor) {
        const provider = document.getElementById("ws-team-crew-provider").value;
        const model = modelSelectValue("ws-team-crew-model");
        if (provider) payload.provider = provider;
        if (model) payload.model = model;
      }
      try {
        const queued = await api("POST", "/api/tasks/" + taskId + "/execute-team-crew", payload);
        renderCollabWorkspace(taskId, containerId);
        const jobs = await Promise.all(queued.job_ids.map(id => pollExecutionJob(taskId, id)));
        if (jobs.every(j => j.status === "succeeded")) {
          showMsg("global-msg", "팀 Agent 실행이 완료됐습니다(" + jobs.length + "건).", "ok");
        } else {
          const failed = jobs.filter(j => j.status === "failed").length;
          showMsg("global-msg", "팀 Agent 실행 중 " + failed + "건이 실패했습니다 — 진행 이력을 확인하세요.", "error");
        }
        if (isViewingWorkspace(taskId, containerId)) renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }

  // 페이지를 새로고침했거나 다른 탭에 갔다 왔을 때도, 이미 돌고 있던
  // job이 있으면 자동으로 이어서 폴링해 끝나는 대로 갱신한다 — 버튼을
  // 눌러야만 완료를 알 수 있는 건 아니다.
  // 실행 중인 파이프라인이면 진행률 블록을 주기적으로 갱신한다(2026-11-24, P0-2).
  if (latestPipelineRun && latestPipelineRun.status === "running") {
    _pollPipelineLive(taskId, containerId, latestPipelineRun.id);
  }
  if (hasRunningJob) {
    pollExecutionJob(taskId, latestJob.id).then(job => {
      if (!isViewingWorkspace(taskId, containerId)) return;
      if (job.status === "failed") {
        showMsg("global-msg", "AI 에이전트 실행이 실패했습니다: " + job.error, "error");
      }
      renderCollabWorkspace(taskId, containerId);
    }).catch(() => {});
  }
}

// 백그라운드 폴링이 끝났을 때, 사용자가 그사이 다른 업무/탭으로
// 이동했다면 지금 화면을 덮어쓰지 않기 위한 가드(Phase 4).
function isViewingWorkspace(taskId, containerId = "mywork-detail") {
  const detail = document.getElementById(containerId);
  return !!detail && detail.dataset.taskId === String(taskId);
}

/* 실측으로 발견한 심각한 버그(사용자 요청, 2026-09-28, "UI/UX 내용
   중... 분석하고 개선해줘") — 원래 이 함수 하나가 "댓글 목록 갱신"과
   "폼에 submit 리스너 붙이기"를 같이 했다. 그런데 submit 성공 콜백
   *안에서* 같은 함수를 다시 불러 목록을 갱신했는데, 그 재호출이 같은
   <form> DOM 노드에 리스너를 addEventListener로 "또" 붙였다(노드 자체는
   안 바뀌므로 이전 리스너가 안 사라짐) — 댓글을 한 번 달 때마다
   리스너가 1→2→4→8개로 누적돼, 다음 댓글부터는 같은 POST가 리스너
   개수만큼 중복 발사되는 지수적 중복 제출 버그였다. "목록 갱신"과
   "리스너를 한 번만 붙이기"를 분리해서 고쳤다. */
async function refreshComments(deliverableId) {
  const box = document.getElementById("comments-" + deliverableId);
  if (!box) return;
  try {
    const comments = await api("GET", "/api/deliverables/" + deliverableId + "/comments");
    // 2026-10-08, Phase 3 — Agent가
    // `@멘션` 트리거로 남긴 응답 댓글을 사람 댓글과 시각적으로 구분한다
    // (배경색 + "AI 에이전트" 배지, 설계 문서 UI 설계 그대로). 사용자
    // 결정(a) — 댓글 트리거 결과는 항상 댓글로만 남고, deliverable로
    // 승격하려면 사람이 명시적으로 "이걸 결과물로 제출"을 눌러야 한다.
    // **새 엔드포인트를 만들지 않는다** — 버튼은 그 댓글 내용을 기존
    // "결과물 작성" textarea(#ws-content)에 채워 넣기만 하고, 실제
    // 제출은 기존 `ws-submit-form`(→ 기존 결재 체인)을 그대로 탄다.
    box.innerHTML = comments.map(c =>
      '<div class="' + (c.author_type === "agent" ? "chat-agent" : "muted") + '">↳ ' +
      (c.author_type === "agent" ? '<span class="gate-badge gate-ok">AI 에이전트</span> ' : '') +
      esc(c.content) +
      (c.author_type === "agent"
        ? ' <button class="small" data-promote-comment="' + c.id +
          '" data-promote-content="' + escAttr(c.content) + '">이걸 결과물로 제출</button>'
        : '') +
      '</div>'
    ).join("");
  } catch (e) {
    // 참가자가 아니면 403 — 조용히 무시(타임라인 자체는 이미 조회 권한 확인됨).
  }
}

// UI 설계(2026-10-08) — "@" 입력 시
// 참가자 목록 자동완성 드롭다운. 1차 구현 때는 정적 힌트 텍스트로만
// 대체했는데, 실제 누락으로 확인돼("HAIR-Platform... 구현에 누락
// 부분이 없는지 확인해줘") 보완한다. 외부 라이브러리 없이 순수 JS로
// 커서 위치 기준 마지막 "@" 뒤 텍스트를 쿼리 삼아 필터링한다.
//
// 채팅 호출 UX 개선(2026-10-10, 사용자 요청 "채팅에서 @로 생성된
// AI Agent와 사용자를 호출하려면 미리 전체 이름을 알고 있어야 해") —
// `opts.search`가 주어지면(대화 메시지창만; Task 결과물 댓글은 기존과
// 완전히 같은 "로컬 참가자만" 동작을 유지한다 — `opts` 생략 시 이전
// 동작과 100% 동일) 조직 전체 검색(`/api/conversations/mentionable`,
// 역할/스킬까지 부분 일치 + 이 사용자가 최근 초대한 대상 우선)도 함께
// 조회해 "참가자"/"초대 후 멘션" 두 그룹으로 보여준다. 후자를 고르면
// `opts.onInvited`로 먼저 초대한 뒤 멘션을 끼워 넣는다 — 화면 이동
// 없이 "검색→클릭"만으로 처음 보는 이름도 바로 부를 수 있다.
// 사용자 요청(2026-10-14, "초대화면 포함 선택 창에서 상하 및 엔터
// 키로도 선택 가능하도록 개선해줘") — 멘션 자동완성(`setupMentionAutocomplete`)
// 과 Task 워크스페이스 참가자 초대(`ws-invite-dropdown`)가 똑같은
// "입력하면 뜨는 목록에서 고른다" 패턴을 각자 따로 구현하고 있어서,
// 키보드 내비게이션도 공용 헬퍼 하나로 둘 다 적용한다. `.mention-dropdown
// li.active` CSS는 이미 있었다(하이라이트 스타일만 있고 실제로 켜주는
// 로직이 없었다).
function wireDropdownKeyboardNav(inputEl, dropdownEl, itemSelector, onSelect) {
  let highlightedIndex = -1;

  function items() {
    return Array.from(dropdownEl.querySelectorAll(itemSelector));
  }

  function updateHighlight() {
    const els = items();
    els.forEach((el, i) => el.classList.toggle("active", i === highlightedIndex));
    const active = els[highlightedIndex];
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  inputEl.addEventListener("keydown", (ev) => {
    if (dropdownEl.style.display === "none" || !dropdownEl.innerHTML) return;
    const els = items();
    if (!els.length) return;
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      highlightedIndex = (highlightedIndex + 1) % els.length;
      updateHighlight();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      highlightedIndex = (highlightedIndex - 1 + els.length) % els.length;
      updateHighlight();
    } else if (ev.key === "Enter") {
      // 하이라이트된 항목이 없으면(화살표로 고르기 전) 평범한 Enter로
      // 둔다 — 메시지 전송 폼의 기본 submit을 가로채지 않는다.
      if (highlightedIndex >= 0 && highlightedIndex < els.length) {
        ev.preventDefault();
        onSelect(els[highlightedIndex]);
      }
    } else if (ev.key === "Escape") {
      dropdownEl.style.display = "none";
      dropdownEl.innerHTML = "";
    }
  });

  return {
    // 드롭다운을 새로 그릴 때마다 첫 항목을 바로 하이라이트한다 — 타이핑
    // 후 바로 Enter만 쳐도 가장 위 후보가 선택되는, 흔한 자동완성 UX.
    highlightFirst() {
      highlightedIndex = items().length ? 0 : -1;
      updateHighlight();
    },
  };
}

function setupMentionAutocomplete(inputEl, dropdownEl, participants, opts) {
  opts = opts || {};
  // 사용자 보고(2026-10-14, "여전히 대화 참가자 pm_kim이 pm_kim에게
  // 멘션을 보낼 수 있어, 메시지 목록에서 본인은 제외해줘") — 자기
  // 멘션의 "효과"(기본 AI 억제·알림)는 이미 고쳤지만, 애초에 자동완성
  // 후보 목록에 본인이 그대로 떠서 고를 수 있었다. 이미 초대된 참가자
  // (로컬 후보)와 아직 초대 안 된 조직 전체 검색 결과(원격 후보) 둘
  // 다에서 "나 자신"(사람 타입 + 내 user id)을 제외한다.
  // 사용자 보고(2026-10-28, "대화에서 한번만 호출하고 대화에 참여했는데
  // 참가자에 두번 표기되고 있어") 근본원인 — `participants` 배열은
  // `_inviteAndTrackParticipant()`가 초대 성공 시 바로 push해 최신
  // 상태를 유지하는데, 이 함수는 그 배열을 한 번만 `.filter().map()`
  // 해서 `localCandidates`라는 별도 배열을 만들어 썼다 — 즉 설정 시점
  // 스냅샷이라 이후 초대된 참가자가 여기 반영되지 않았다. 그 결과
  // 같은 대화에서 같은 Agent를 두 번째로 멘션하면(이미 초대된 상태인데도)
  // 로컬 후보로 안 잡혀 "초대 필요"(원격) 쪽으로 다시 분류되고,
  // 서버는 중복 초대를 거절하지만(참가자 테이블엔 중복 안 생김) 이
  // 화면은 그 멘션을 "새 초대"로 착각해 참가자 목록에 같은 이름을
  // 한 번 더 그려 넣었다. `participants` 배열에서 매번 다시 계산해
  // 최신 상태를 반영한다.
  function getLocalCandidates() {
    return participants
      .filter(p => p.display_name)
      .filter(p => !(p.participant_type === "human" && p.participant_id === ME.id))
      .map(p => ({ name: p.display_name, type: p.participant_type, id: p.participant_id }));
  }
  if (!getLocalCandidates().length && !opts.search) return;

  // 사용자 요청(2026-10-28, "대화에서 @로 호출시 너무 많은 멤버가
  // 노출되고있어 사람/AI/팀 카테고리 구분을 추가, ... 호출자를 각각
  // 구분하는 등 개선") — (1) 섹션 구분: 결과를 사람/AI/팀 3개 묶음으로
  // 나눠 묶음별 머리글 + 개수를 보여주고, 한 묶음이 너무 길면
  // MAX_PER_CATEGORY개까지만 보여주고 나머지는 "+N개 더"로 접는다.
  // (2) 타이핑 접두사로 범위 좁히기: "@AI 번역"처럼 "사람"/"AI"/"팀"으로
  // 시작하면 그 종류만 검색한다 — 백엔드는 그대로 두고(이미 사람/AI/팀을
  // 구분해 돌려주고 있다) 프런트에서만 그 접두사를 떼어 필터링한다.
  const CATEGORY_LABEL = { human: "사람", agent: "AI", team: "팀" };
  const CATEGORY_ORDER = ["human", "agent", "team"];
  const CATEGORY_PREFIXES = [
    { prefix: "사람", type: "human" },
    { prefix: "AI", type: "agent" },
    { prefix: "팀", type: "team" },
  ];
  const MAX_PER_CATEGORY = 5;

  function parseCategoryFilter(raw) {
    // "게임 AI팀"처럼 이름 자체에 "AI"가 들어간 후보를 찾으려고 "@AI"만
    // 친 상태(아직 뒤에 공백 없음)는 카테고리 필터가 아니라 여전히
    // 자유 텍스트 검색이어야 한다(기존 동작 유지) — 접두사 뒤에 공백이
    // 와서 "다음 단어를 입력하기 시작했다"가 확실해졌을 때만 켠다.
    const lower = raw.toLowerCase();
    for (const { prefix, type } of CATEGORY_PREFIXES) {
      const p = prefix.toLowerCase();
      if (lower.startsWith(p + " ")) {
        return { type, rest: raw.slice(prefix.length).replace(/^\s+/, "") };
      }
    }
    return { type: null, rest: raw };
  }

  function renderCategoryGroup(items, isRemote) {
    let html = "";
    for (const type of CATEGORY_ORDER) {
      const group = items.filter(it => it.type === type);
      if (!group.length) continue;
      html += '<li class="muted" style="padding:0.2rem 0.5rem;font-size:0.75rem">' +
        CATEGORY_LABEL[type] + ' (' + group.length + ')</li>';
      html += group.slice(0, MAX_PER_CATEGORY).map(c => {
        const inviteAttrs = isRemote
          ? ' data-mention-invite-type="' + c.type + '" data-mention-invite-id="' + c.id + '"'
          : "";
        return '<li data-mention-name="' + escAttr(c.name) + '"' + inviteAttrs + '>' + esc(c.name) +
          (isRemote ? ' <span class="muted">· 초대 필요</span>' : "") + '</li>';
      }).join("");
      if (group.length > MAX_PER_CATEGORY) {
        html += '<li class="muted" style="padding:0.15rem 0.5rem;font-size:0.72rem">+' +
          (group.length - MAX_PER_CATEGORY) + '개 더 — 계속 입력해 좁혀보세요</li>';
      }
    }
    return html;
  }

  let searchToken = 0;
  let searchDebounce = null;

  function closeDropdown() {
    dropdownEl.style.display = "none";
    dropdownEl.innerHTML = "";
    searchToken++; // 아직 안 돌아온 원격 검색 응답을 무효화한다
  }

  function insertMention(name) {
    const val = inputEl.value;
    const cursor = inputEl.selectionStart;
    const atIndex = val.lastIndexOf("@", cursor - 1);
    if (atIndex === -1) return;
    const before = val.slice(0, atIndex);
    const after = val.slice(cursor);
    const insertedPrefix = before + "@" + name + " ";
    inputEl.value = insertedPrefix + after;
    inputEl.setSelectionRange(insertedPrefix.length, insertedPrefix.length);
    closeDropdown();
    inputEl.focus();
  }

  async function selectCandidate(li) {
    const name = li.dataset.mentionName;
    if (li.dataset.mentionInviteType && opts.onInvited) {
      try {
        await opts.onInvited({
          type: li.dataset.mentionInviteType,
          id: Number(li.dataset.mentionInviteId),
          name,
        });
      } catch (e) { return; } // onInvited가 이미 에러 메시지를 보여줬다
    }
    insertMention(name);
    // 사용자 요청(2026-10-22, "대화자(Human, Agent) 호출시 Skill 선택은
    // 취소되도록 해줘") — 스킬을 고른 채로 멘션까지 보내면 Agent/Team
    // 응답과 별개로 스킬 그라운딩된 "기본 AI" job이 하나 더 떴다(중복
    // 응답 혼란). 백엔드도 같이 고쳤지만(`app.py` — 멘션이 있으면 스킬이
    // 선택돼 있어도 기본 AI를 더 안 띄움), 멘션을 고르는 시점에 드롭다운
    // 선택 자체를 바로 지워서 사용자가 "지금은 스킬이 아니라 멘션 대상이
    // 응답한다"는 걸 눈으로 바로 알 수 있게 한다.
    if (opts.onMentionSelected) opts.onMentionSelected();
  }

  const keyboardNav = wireDropdownKeyboardNav(
    inputEl, dropdownEl, "li[data-mention-name]", selectCandidate
  );

  function renderGroups(localMatches, remoteItems, showHint) {
    if (!localMatches.length && !remoteItems.length) {
      dropdownEl.style.display = "none";
      dropdownEl.innerHTML = "";
      return;
    }
    let html = showHint
      ? '<li class="muted" style="padding:0.2rem 0.5rem;font-size:0.72rem">Tip: "AI "/"사람 "/"팀 "으로 시작하면 그 종류만 볼 수 있어요</li>'
      : "";
    html += renderCategoryGroup(localMatches, false);
    if (remoteItems.length) {
      html += '<li class="muted" style="padding:0.25rem 0.5rem">초대 후 멘션</li>' +
        renderCategoryGroup(remoteItems, true);
    }
    dropdownEl.innerHTML = html;
    dropdownEl.style.display = "block";
    keyboardNav.highlightFirst();
  }

  function handleQuery(query) {
    // 실측으로 발견한 버그(2026-10-08, 라이브 검증) — 원래 "@뒤에
    // 공백이 있으면 멘션이 끝난 것"으로 보고 닫았는데, "게임 AI팀"처럼
    // 이름 자체에 공백이 있는 참가자(흔한 한국어 Team/Agent 이름)를
    // 끝까지 타이핑하면 중간 공백에서 드롭다운이 영구히 닫혀버렸다.
    // 공백 여부로 판단하지 않고, 후보 이름에 그 쿼리가 부분 문자열로
    // 남아있는 동안만 계속 열어둔다.
    const { type: categoryType, rest } = parseCategoryFilter(query);
    const showHint = !categoryType && query.length === 0;
    const localCandidates = getLocalCandidates();
    const localMatches = localCandidates
      .filter(c => !categoryType || c.type === categoryType)
      .filter(c => c.name.includes(rest));
    if (!opts.search) { renderGroups(localMatches, [], showHint); return; }
    // 로컬 매치는 즉시 보여주고(깜빡임 방지), 조직 전체 검색만 디바운스
    // 한다 — 매치가 하나도 없는 상태에서 디바운스 중 잠깐 닫았다 여는
    // 깜빡임을 피하려고, 로컬 매치가 없을 땐 응답이 올 때까지 아무
    // 것도 안 바꾼다(기존 상태 유지).
    if (localMatches.length) renderGroups(localMatches, [], showHint);
    clearTimeout(searchDebounce);
    const myToken = ++searchToken;
    searchDebounce = setTimeout(async () => {
      let remote;
      try {
        remote = await opts.search(rest);
      } catch (e) {
        return;
      }
      if (myToken !== searchToken) return; // 늦게 도착한 응답은 버린다
      const localKeys = new Set(localCandidates.map(c => c.type + ":" + c.id));
      let remoteItems = [
        ...remote.humans.map(h => ({ type: "human", id: h.id, name: h.username })),
        ...remote.agents.map(a => ({ type: "agent", id: a.id, name: a.role_name })),
        ...remote.teams.map(t => ({ type: "team", id: t.id, name: t.name })),
      ]
        .filter(it => !localKeys.has(it.type + ":" + it.id))
        .filter(it => !(it.type === "human" && it.id === ME.id));
      if (categoryType) remoteItems = remoteItems.filter(it => it.type === categoryType);
      renderGroups(localMatches, remoteItems, showHint);
    }, 200);
  }

  inputEl.addEventListener("input", () => {
    const val = inputEl.value;
    const cursor = inputEl.selectionStart;
    const atIndex = val.lastIndexOf("@", cursor - 1);
    if (atIndex === -1) { closeDropdown(); return; }
    handleQuery(val.slice(atIndex + 1, cursor));
  });

  // blur가 드롭다운 클릭보다 먼저 발생해 클릭이 씹히는 것을 막기 위해
  // mousedown(클릭보다 먼저 발생)에서 선택을 처리하고, blur는 약간
  // 지연 후 닫는다.
  inputEl.addEventListener("blur", () => setTimeout(closeDropdown, 150));
  dropdownEl.addEventListener("mousedown", async (ev) => {
    const li = ev.target.closest("[data-mention-name]");
    if (!li) return;
    ev.preventDefault();
    await selectCandidate(li);
  });
}

// 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자 기능 삭제해줘")으로
// 댓글 입력창의 @멘션 참가자 자동완성(setupMentionAutocomplete 호출)을
// 제거했다 — 입력창 자체는 평범한 댓글 작성용으로 남는다.
async function loadDeliverableComments(deliverableId, taskId, containerId) {
  await refreshComments(deliverableId);
  // Phase 3 — "이걸 결과물로 제출" 버튼은 `refreshComments()`가
  // `box.innerHTML`을 매번 통째로 새로 그리므로, 그 자식 버튼에 직접
  // 리스너를 매번 새로 붙이면 이전에 겪었던 "리스너 누적" 버그가
  // 똑같이 재발한다 — 부모 `box`(innerHTML 교체에도 노드 자체는 안
  // 바뀜)에 한 번만 위임 바인딩한다.
  const box = document.getElementById("comments-" + deliverableId);
  if (box) {
    box.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-promote-comment]");
      if (!btn) return;
      const contentArea = document.getElementById("ws-content");
      if (!contentArea) return;
      contentArea.value = btn.dataset.promoteContent;
      contentArea.scrollIntoView({ behavior: "smooth", block: "center" });
      contentArea.focus();
      showMsg("global-msg", "결과물 작성란에 채웠습니다 — 확인 후 \"결과물 제출\"을 눌러주세요.", "ok");
    });
  }
  const form = document.querySelector('[data-comment-form="' + deliverableId + '"]');
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const input = document.getElementById("comment-input-" + deliverableId);
      const val = input.value.trim();
      if (!val) return;
      try {
        const result = await api(
          "POST", "/api/deliverables/" + deliverableId + "/comments", { content: val }
        );
        input.value = "";
        await refreshComments(deliverableId);
        // Phase 3 — @멘션이 트리거됐으면 백그라운드 완료를 폴링해
        // 응답이 오는 대로 댓글 목록을 다시 갱신한다(기존 "AI 에이전트
        // 실행" 버튼의 pollExecutionJob() 패턴 재사용).
        if (result.triggered_jobs && result.triggered_jobs.length) {
          showMsg("global-msg", "Agent에게 알렸습니다 — 응답을 기다리는 중…", "ok");
          Promise.all(result.triggered_jobs.map(j => pollExecutionJob(taskId, j.job_id)))
            .then(() => refreshComments(deliverableId))
            .catch(() => refreshComments(deliverableId));
        }
        if (result.skipped_agent_ids && result.skipped_agent_ids.length) {
          showMsg("global-msg", "다른 실행이 진행 중이라 일부 Agent는 호출하지 못했습니다 — 완료 후 다시 멘션해 주세요.", "error");
        }
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
}

/* ---------- 내 부재(2026-11-27, Resource Management Phase A) — 휴가·출장을 직접 적으면 그 기간엔 매칭에서 빠진다 ---------- */
async function _renderMyAbsences(slot) {
  if (!slot) return;
  let mine = [];
  try { mine = await api("GET", "/api/resource-absences"); } catch (_) { return; }
  const me = await api("GET", "/api/human-pool/me").catch(() => null);
  if (!me || !me.id) return;  // 사람 인력에 연결되지 않은 계정
  const today = new Date().toISOString().slice(0, 10);
  let skillBox = document.getElementById("myskill-slot");
  if (!skillBox) {  // 부재 일정을 다시 그려도 한 번만
    skillBox = document.createElement("div");
    skillBox.id = "myskill-slot";
    slot.after(skillBox);
  }
  _renderSkillLevels(skillBox, "human", me.id, "내 스킬 수준");
  slot.innerHTML = '<section class="card"><h3>내 부재 일정</h3><p class="muted">휴가·출장·교육처럼 일을 맡을 수 없는 기간을 적어 두면 그 기간에는 새 일이 배정 후보에서 빠지고, 2주 전부터 배정하는 사람에게 미리 알려 줍니다.</p>' +
    table(["기간", "사유", ""], mine.map(a => [esc(a.starts_on) + " ~ " + esc(a.ends_on), esc(a.reason || ""),
      '<button class="small danger" data-myabs-del="' + a.id + '">지우기</button>']), { empty: "적어 둔 부재가 없습니다." }) +
    '<div class="inline" style="flex-wrap:wrap;margin-top:0.4rem"><input type="date" id="myabs-from" value="' + today + '" aria-label="시작">' +
    '<input type="date" id="myabs-to" value="' + today + '" aria-label="끝"><input id="myabs-why" placeholder="사유(선택)" maxlength="200">' +
    '<button class="small" id="myabs-add">부재 추가</button> <span class="muted" id="myabs-msg"></span></div></section>';
  slot.querySelector("#myabs-add").onclick = async () => {
    try {
      await api("POST", "/api/human-pool/" + me.id + "/absences", { starts_on: slot.querySelector("#myabs-from").value,
        ends_on: slot.querySelector("#myabs-to").value, reason: slot.querySelector("#myabs-why").value });
      _renderMyAbsences(slot);
    } catch (e) { slot.querySelector("#myabs-msg").textContent = e.message || String(e); }
  };
  slot.querySelectorAll("[data-myabs-del]").forEach(b => b.onclick = async () => {
    try { await api("DELETE", "/api/resource-absences/" + b.dataset.myabsDel); _renderMyAbsences(slot); }
    catch (e) { slot.querySelector("#myabs-msg").textContent = e.message || String(e); }
  });
}

/* ---------- 기한·예상 시간·인원(2026-11-27, Resource Management Phase B) ---------- */
function _dueCell(due, est) {
  if (!due) return '<span class="muted">-</span>';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const d = new Date(due + "T00:00:00");
  const left = Math.round((d - today) / 86400000);
  const badge = left < 0 ? ' <span class="gate-badge gate-warn">' + (-left) + "일 지남</span>"
    : left <= 3 ? ' <span class="gate-badge gate-info">' + (left === 0 ? "오늘" : left + "일 남음") + "</span>" : "";
  return esc(due) + badge + (est ? '<div class="muted pm-note">예상 ' + est + "시간</div>" : "");
}

async function _renderTaskSchedule(slot, taskId) {
  if (!slot) return;
  let s;
  try { s = await api("GET", "/api/tasks/" + taskId + "/schedule"); } catch (_) { return; }
  const canEdit = ME.role !== "viewer";
  slot.innerHTML = '<div class="inline ws-schedule" style="flex-wrap:wrap;margin:0 0 0.75rem">' +
    '<label>기한 <input type="date" id="ws-due" value="' + escAttr(s.due_date || "") + '"' + (canEdit ? "" : " disabled") + "></label>" +
    '<label>예상 시간 <input type="number" id="ws-est" min="0.5" step="0.5" style="width:5rem" value="' + (s.estimate_hours || "") + '"' + (canEdit ? "" : " disabled") + "></label>" +
    (canEdit ? '<button class="small" id="ws-sched-save">저장</button>' : "") +
    (s.overdue ? ' <span class="gate-badge gate-warn">기한 ' + (-s.days_left) + "일 지남</span>" : "") +
    ' <span class="muted">인원 ' + s.assigned + "/" + s.headcount + (s.short ? ' — <b>' + s.short + "명 부족</b>" : "") + "</span>" +
    ' <span class="muted" id="ws-sched-msg"></span></div><div id="ws-time-slot"></div>';
  _renderTaskTime(slot.querySelector("#ws-time-slot"), taskId);
  const btn = slot.querySelector("#ws-sched-save");
  if (btn) btn.onclick = async () => {
    const est = slot.querySelector("#ws-est").value.trim();
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/schedule", { due_date: slot.querySelector("#ws-due").value || null,
        estimate_hours: est === "" ? null : Number(est) });
      _renderTaskSchedule(slot, taskId);
    } catch (e) { slot.querySelector("#ws-sched-msg").textContent = e.message || String(e); }
  };
}

/* ---------- 작업 시간 기록(2026-11-28, Resource Management 5번) — 인건비는 기록한 시간이 있으면 그것으로 ---------- */
async function _renderTaskTime(box, taskId) {
  if (!box) return;
  let d;
  try { d = await api("GET", "/api/tasks/" + taskId + "/time"); } catch (_) { box.innerHTML = ""; return; }
  const today = new Date().toISOString().slice(0, 10);
  const summary = "기록 " + d.logged_hours + "시간" + (d.estimate_hours ? " / 예상 " + d.estimate_hours + "시간" : "") +
    (d.totals.length > 1 ? " (" + d.totals.map(t => esc(t.name) + " " + t.hours + "h").join(", ") + ")" : "");
  const form = d.loggable.length ? '<div class="inline" style="flex-wrap:wrap">' +
    (d.loggable.length > 1 ? '<select id="wt-human" aria-label="누구의 시간">' + d.loggable.map(h => '<option value="' + h.id + '">' + esc(h.name) + "</option>").join("") + "</select>" : "") +
    '<input type="date" id="wt-date" value="' + today + '" max="' + today + '" aria-label="날짜">' +
    '<input type="number" id="wt-hours" min="0.25" max="24" step="0.25" style="width:5rem" placeholder="시간" aria-label="시간">' +
    '<input id="wt-note" maxlength="200" placeholder="메모(선택)" aria-label="메모">' +
    '<button class="small" id="wt-add">시간 기록</button> <span class="muted" id="wt-msg"></span></div>' : "";
  const rows = d.entries.map(e => [esc(e.work_date), esc(e.human_name), e.hours + "h", e.note ? esc(e.note) : '<span class="muted">-</span>',
    e.can_delete ? '<button class="small danger" data-wt-del="' + e.id + '">지우기</button>' : ""]);
  box.innerHTML = '<details class="ws-time"' + (d.entries.length ? "" : " open") + '><summary>작업 시간 — ' + summary + "</summary>" + form +
    (d.entries.length ? table(["날짜", "사람", "시간", "메모", ""], rows) : '<p class="muted">아직 기록이 없습니다. 기록하면 인건비가 예상 시간 대신 기록한 시간으로 계산됩니다.</p>') +
    "</details>";
  const add = box.querySelector("#wt-add");
  if (add) add.onclick = async () => {
    const hours = Number(box.querySelector("#wt-hours").value);
    const sel = box.querySelector("#wt-human");
    try {
      await api("POST", "/api/tasks/" + taskId + "/time", { hours, work_date: box.querySelector("#wt-date").value,
        note: box.querySelector("#wt-note").value, ...(sel ? { human_id: Number(sel.value) } : {}) });
      _renderTaskTime(box, taskId);
    } catch (e) { box.querySelector("#wt-msg").textContent = e.message || String(e); }
  };
  box.querySelectorAll("[data-wt-del]").forEach(b => b.onclick = async () => {
    try { await api("DELETE", "/api/time-entries/" + b.dataset.wtDel); _renderTaskTime(box, taskId); }
    catch (e) { b.textContent = e.message || String(e); }
  });
}

/* ---------- 스킬 숙련도(2026-11-28 — `skill_levels`) — 사람 인력 관리(확인)·내 업무(본인 기재)가 함께 쓴다 ---------- */
async function _renderSkillLevels(box, resourceType, resourceId, title) {
  if (!box) return;
  let d;
  try { d = await api("GET", "/api/skill-levels/" + resourceType + "/" + resourceId); } catch (_) { box.innerHTML = ""; return; }
  const decider = ["admin", "manager"].includes(ME.role);
  const opt = (sel) => '<option value="">—</option>' + Object.entries(d.levels).map(([k, v]) =>
    '<option value="' + k + '"' + (String(sel) === k ? " selected" : "") + ">" + k + " " + esc(v) + "</option>").join("");
  const rows = d.skills.map(s => [
    esc(s.skill),
    s.level ? "<b>" + s.level + " " + esc(s.label) + "</b>" + (s.unverified ? ' <span class="gate-badge gate-info">본인 기재</span>' : "") : '<span class="muted">-</span>',
    s.source ? esc(s.source) : '<span class="muted">-</span>',
    s.evidence ? "승인 " + s.evidence + "건(→ " + s.evidence_level + ")" : '<span class="muted">0</span>',
    '<select data-sl-skill="' + escAttr(s.skill) + '" aria-label="' + escAttr(s.skill) + ' 수준">' + opt(decider ? s.verified : s.self) + "</select>",
  ]);
  box.innerHTML = '<div class="card ab-box"><div class="ab-head"><b>' + esc(title) + "</b> " + (box.dataset.closable ? '<button class="small" data-sl-close>닫기</button>' : "") + "</div>" +
    // 한 문장 + 접힌 자세히(2026-11-28 — 네 줄 설명이 표보다 먼저 보였다)
    '<p class="muted">스킬마다 1 입문 · 2 실무 · 3 숙련 · 4 전문 중 ' + (decider ? "확인한 수준을" : "내 수준을") + ' 고르세요.</p>' +
    '<details class="muted"><summary>수준은 어떻게 정해지나요?</summary><p><b>근거</b>는 그 스킬이 필요한 할 일에서 사람이 승인한 결과물 수(최근 180일 — 1~2건 실무, 3~9건 숙련, 10건 이상 전문)로 자동 계산합니다. ' +
    (decider ? "여기서 고르는 값은 <b>확인</b> 수준입니다 — 적용 수준은 확인과 근거 중 높은 쪽." : "여기서 고르는 값은 <b>본인 기재</b>입니다 — 확인·근거가 없을 때만 쓰이고 '본인 기재'로 표시됩니다.") +
    " 매칭은 수준이 있는 후보에 작은 가점을 줍니다.</p></details>" +
    table(["스킬", "적용 수준", "출처", "근거", decider ? "확인 수준" : "본인 기재"], rows, { empty: "등록된 스킬이 없습니다." }) +
    '<p class="muted" data-sl-msg></p></div>';
  const closeBtn = box.querySelector("[data-sl-close]");
  if (closeBtn) closeBtn.onclick = () => { box.innerHTML = ""; };
  box.querySelectorAll("[data-sl-skill]").forEach(sel => sel.onchange = async () => {
    try {
      await api("PUT", "/api/skill-levels/" + resourceType + "/" + resourceId, { skill: sel.dataset.slSkill, level: sel.value ? Number(sel.value) : null });
      _renderSkillLevels(box, resourceType, resourceId, title);
    } catch (e) { box.querySelector("[data-sl-msg]").textContent = e.message || String(e); }
  });
}


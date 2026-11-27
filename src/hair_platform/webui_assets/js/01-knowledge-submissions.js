/* ---------- 지식베이스 등록(제출 → admin 결재 → 등록) ---------- */
// 사용자 요청(2026-11-20, "지식 베이스는 admin 담당자가 문서로 등록하는
// 경우와 대화 및 업무배정을 통해 생성된 산출물 중 '지식베이스로 보내기'
// 및 admin 결재 후 등록되는 것으로 개선") — 대화 메시지·산출물 카드가
// 같은 버튼/모달/배지를 공유한다(`knowledge_submissions.py` 참고).
function knowledgeSubmitButton(sourceType, sourceId) {
  return '<button type="button" class="small" data-submit-knowledge="' +
    sourceType + ":" + sourceId + '">지식베이스로 보내기</button>';
}

// 버튼이 DOM에 붙을 때마다(대화·갤러리·업무 화면 어디서든) 모아 뒀다가
// 원본 종류별로 한 번에 최신 제출 상태를 조회해 버튼 문구를 바꾼다 —
// 화면마다 따로 배선할 필요가 없도록 전역 MutationObserver에서 부른다.
let _knowledgeButtonQueue = [];
let _knowledgeButtonTimer = null;
function _queueKnowledgeButtons(root) {
  const found = root.matches && root.matches("[data-submit-knowledge]")
    ? [root]
    : Array.from(root.querySelectorAll ? root.querySelectorAll("[data-submit-knowledge]") : []);
  if (!found.length) return;
  _knowledgeButtonQueue.push(...found);
  clearTimeout(_knowledgeButtonTimer);
  _knowledgeButtonTimer = setTimeout(_decorateKnowledgeButtons, 80);
}

const _KNOWLEDGE_BUTTON_STATES = {
  pending: { text: "지식 결재 대기", disabled: true },
  registering: { text: "지식 등록 중", disabled: true },
  registered: { text: "지식 등록됨 · 갱신 요청", disabled: false },
  failed: { text: "지식 등록 실패", disabled: true },
  rejected: { text: "지식베이스로 보내기(반려됨)", disabled: false },
};

async function _decorateKnowledgeButtons() {
  const buttons = _knowledgeButtonQueue.filter(b => b.isConnected);
  _knowledgeButtonQueue = [];
  const byType = {};
  for (const b of buttons) {
    const [type, id] = b.dataset.submitKnowledge.split(":");
    (byType[type] = byType[type] || []).push([id, b]);
  }
  for (const [type, entries] of Object.entries(byType)) {
    let statuses;
    try {
      statuses = await api("GET", qs("/api/knowledge-submissions/status", {
        source_type: type, ids: entries.map(e => e[0]).join(","),
      }));
    } catch (e) {
      continue;  // 배지는 부가 정보 — 조회 실패해도 버튼 자체는 그대로 동작
    }
    for (const [id, btn] of entries) _applyKnowledgeButtonState(btn, statuses[id]);
  }
}

function _applyKnowledgeButtonState(btn, st) {
  const state = st && _KNOWLEDGE_BUTTON_STATES[st.status];
  if (!state) {
    btn.textContent = "지식베이스로 보내기";
    btn.disabled = false;
    btn.title = "";
    return;
  }
  btn.textContent = state.text;
  btn.disabled = state.disabled;
  const tips = ["'" + st.title + "' — " + label(st.status)];
  if (st.reject_reason) tips.push("반려 사유: " + st.reject_reason);
  if (st.error) tips.push("오류: " + st.error);
  btn.title = tips.join("\n");
}

async function _knowledgePreview(sourceType, sourceId, allowCode) {
  try {
    return await api("POST", "/api/knowledge-submissions/preview", {
      source_type: sourceType, source_id: sourceId, allow_code: allowCode,
    });
  } catch (e) {
    if (!allowCode && /allow_code/.test(e.message)) {
      const ok = await confirmModal("코드 산출물은 기본적으로 지식베이스 대상이 아닙니다. 그래도 보낼까요?");
      return ok ? _knowledgePreview(sourceType, sourceId, true) : null;
    }
    throw e;
  }
}

async function openKnowledgeSubmitModal(sourceType, sourceId, btn) {
  let pv;
  try {
    pv = await _knowledgePreview(sourceType, sourceId, false);
  } catch (e) {
    showMsg("global-msg", e.message, "error");
    return;
  }
  if (!pv) return;
  const allowCode = pv.content_type === "code";
  const notice = pv.requires_approval
    ? "admin 결재 후 지식베이스에 등록됩니다. 등록되면 <b>전사 공개 지식</b>이 되어 \"내부지식검색\" 스킬로 누구나 검색할 수 있습니다 — 기밀·개인정보가 없는지 확인하세요."
    : "admin 권한으로 <b>결재 없이 바로 등록</b>됩니다. 등록되면 전사 공개 지식이 됩니다.";
  const overlay = document.createElement("div");
  overlay.className = "confirm-modal-overlay";
  overlay.innerHTML =
    '<div class="confirm-modal-box" style="max-width:720px">' +
    '<h3 style="margin-top:0">지식베이스로 보내기' + (pv.is_update ? ' <span class="gate-badge gate-warn">기존 지식 갱신 요청</span>' : '') + '</h3>' +
    '<p class="muted" style="margin:0 0 0.6rem">출처: ' + esc(pv.source_label) + '</p>' +
    '<label class="muted">제목<input id="ks-title" value="' + escAttr(pv.title) + '" maxlength="80" style="width:100%;box-sizing:border-box;margin:0.2rem 0 0.6rem"></label>' +
    '<label class="muted">설명(선택 — 결재자에게 등록 이유를 알려주세요)<textarea id="ks-note" rows="2" maxlength="2000" style="width:100%;box-sizing:border-box;margin:0.2rem 0 0.6rem"></textarea></label>' +
    '<div class="muted">등록될 내용 미리보기(' + pv.content_snapshot.length.toLocaleString() + '자' +
    (pv.content_type === "html" ? " — HTML에서 본문 텍스트만 추출" : "") + ')</div>' +
    '<div style="max-height:280px;overflow:auto;border:1px solid var(--color-border, #cbd5e1);border-radius:6px;padding:0.5rem;margin:0.2rem 0 0.6rem">' +
    renderContent(pv.content_snapshot, pv.content_type === "code" ? "code" : "markdown") + '</div>' +
    '<p class="muted" style="font-size:0.85rem">' + notice + '</p>' +
    '<div class="confirm-modal-actions">' +
    '<button type="button" class="small" data-ks-cancel>취소</button>' +
    '<button type="button" class="small primary" data-ks-ok>' + (pv.requires_approval ? "결재 요청" : "바로 등록") + '</button>' +
    '</div></div>';
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("[data-ks-cancel]").onclick = close;
  overlay.onclick = (ev) => { if (ev.target === overlay) close(); };
  const okBtn = overlay.querySelector("[data-ks-ok]");
  okBtn.onclick = async () => {
    try {
      const sub = await withBusy(okBtn, "보내는 중…", () => api("POST", "/api/knowledge-submissions", {
        source_type: sourceType,
        source_id: sourceId,
        title: overlay.querySelector("#ks-title").value.trim() || null,
        note: overlay.querySelector("#ks-note").value.trim() || null,
        allow_code: allowCode,
      }));
      close();
      showMsg("global-msg", pv.requires_approval
        ? "지식 등록을 요청했습니다 — admin 결재 후 지식베이스에 등록됩니다."
        : "지식베이스 등록을 시작했습니다 — 잠시 후 내부지식검색에서 찾을 수 있습니다.", "ok");
      if (btn) _applyKnowledgeButtonState(btn, { status: sub.status, title: sub.title });
    } catch (e) {
      showMsg("global-msg", e.message, "error");
    }
  };
  overlay.querySelector("#ks-title").focus();
}

const LABELS = {
  human: "사람", agent: "AI 에이전트", team: "팀", pipeline: "파이프라인",
  brief: "업무 요청", assignment: "배정", deliverable: "결과물", human_pool: "사람 인력", agent_pool: "AI 에이전트",
  agent_execution_jobs: "AI 실행 작업",
  job: "생성 작업", job_item: "생성 항목", approval: "결재", user: "사용자",
  pending: "대기중", waiting: "대기(이전 단계)", approved: "승인됨", rejected: "반려됨", skipped: "건너뜀",
  requested: "요청됨", confirmed: "확정됨", superseded: "대체됨", cancelled: "취소됨",
  running: "진행중", succeeded: "성공", failed: "실패", partial: "일부 실패", completed: "완료",
  submitted: "제출됨", reviewed: "검토됨",
  available: "가능", unavailable: "불가", offboarded: "오프보딩됨",
  active: "활성", pending_onboarding: "온보딩 대기", deprecated: "비활성화됨",
  draft: "초안", pending_review: "리뷰 대기", revised: "수정됨",
  paused_for_approval: "승인 대기 중",
  // (2026-10-08, Phase 4) — 할 일 보드 상태/우선순위.
  backlog: "백로그", queued: "대기", in_progress: "진행중", done: "완료",
  low: "낮음", medium: "보통", high: "높음", urgent: "긴급",
  task_dependencies: "의존관계", tasks: "업무",
  // (2026-10-21, "Agent 직접 생성") — agent_pool.framework 표시용.
  manual: "직접 생성",
  // (2026-11-20, 지식베이스 등록 체계 개편) — 제출 → admin 결재 → 등록.
  knowledge_submission: "지식 등록", conversation_message: "대화 메시지",
  registering: "등록 중", registered: "등록됨", withdrawn: "회수됨", removed: "삭제됨",
};
function label(v) { return LABELS[v] || v; }

function headerColClass(h) {
  // 사용자 보고(2026-10-18, 스크린샷 — "사용자 관리" 화면의 "상사 지정"/
  // "비밀번호 재설정"/"계정 상태 변경" 열에서 select·input·button이 서로
  // 겹쳐 보임) — 원인은 "동작" 열과 똑같다: table-layout:fixed에서 폭을
  // 안 정해주면 짧은 열(ID/권한 등)과 균등하게 나눠 가져가 버려, 그 안의
  // select/input/button이 셀 폭을 넘기고(overflow: visible 기본값) 옆
  // 칸 위로 그대로 흘러나온다. "동작"과 같은 폭(col-actions)을 준다.
  // 사용자 보고(2026-10-27, 스크린샷 — "LLM 설정 최적화" 일괄 화면의
  // "추천 Provider/Model" 열(모델 선택 <select>)이 "tuning 변경 필드"
  // 열과 겹쳐 보임) — 바로 위 두 사례와 원인이 같다.
  if (h === "동작" || h === "추천 Provider/Model") return "col-actions";
  // 사용자 보고(2026-10-27, 스크린샷 — "사용자 목록"에서 컬럼들이 너무
  // 넓어 테이블에 좌우 스크롤이 생김) — "상사 지정"/"비밀번호 재설정"/
  // "계정 상태 변경" 세 열은 바로 위 "동작"(col-actions, 260px)과 같은
  // 폭을 썼었는데, 각 칸엔 select/input+button 한 쌍만 있어(여러 버튼이
  // 쌓이는 "동작" 열과 다름) 그렇게 넓을 필요가 없었다 — table-layout:
  // auto로 바뀐 지금은(2026-10-27, 앞선 요청) 겹침 방지용 최소폭만
  // 있으면 되므로 더 좁은 전용 클래스로 줄인다(col-actions 자체는
  // "동작" 열에 그대로 둔다 — 그쪽은 버튼이 여러 개라 여전히 넓어야 함).
  if (h === "상사 지정" || h === "비밀번호 재설정" || h === "계정 상태 변경" || h === "사번 변경") return "col-actions-compact";
  // 사용자 보고(2026-10-14, 스크린샷 — "스킬" 열이 단어 하나씩
  // 줄바꿈될 만큼 좁게 눌려 있음) — table-layout:fixed에서 폭을
  // 안 정해주면 ID/상태처럼 짧은 열과 똑같이 나눠 가져가 버린다.
  if (h === "스킬") return "col-skills";
  // "모델 실행 후 쌓이는 정보"를 접어 놓는 열(AI 에이전트 관리) — 펼쳤을
  // 때 소속팀/비용/LLM/최근 산출물 같은 일반 텍스트가 읽을 수 있게
  // 줄바꿈되려면 col-skills보다 넉넉한 폭이 필요하다.
  if (h === "상세정보") return "col-detail";
  return "";
}

// 사용자 요청(2026-10-14, "각 메뉴의 목록 기능에 필터/소트 기능을
// 추가해줘") — 이 앱의 모든 목록 화면(23곳)이 전부 이 table() 헬퍼
// 하나를 공유한다. 호출부마다 따로 구현하는 대신 여기 한 곳에
// 검색창 + 정렬 가능한 헤더를 붙이면 전체 메뉴에 자동으로 퍼진다.
// 각 호출부는 이미 렌더링된 HTML 문자열(버튼 등)을 셀 값으로 넘기므로
// (구조화된 데이터가 아님), 필터/정렬은 **렌더링된 DOM의 textContent**
// 기준으로 동작한다 — 데이터 모델을 바꾸는 23곳 전면 리팩터링 없이도
// 전부 적용된다는 뜻이다. "동작" 열(버튼만 있는 열)은 정렬 대상에서
// 제외한다(`headerColClass()`가 이미 "동작"을 이름으로 식별하는 기존
// 관례를 그대로 재사용).
let _tableSeq = 0;

// 사용자 요청(2026-11-03, "도움말이 펼침으로 확인 하도록 UI 개선하고
// 기본으로 닫힘으로 헤줘") — 화면마다 있는 "상단 도움말"(.help-list)을
// 네이티브 <details>로 감싼다(기본 닫힘은 `open` 속성을 안 붙이는
// 것만으로 충분 — 별도 JS 상태 관리 불필요).
function helpBox(innerHtml) {
  return '<details class="help-box"><summary class="help-summary">도움말</summary>' + innerHtml + '</details>';
}

// `opts.empty`(2026-11-27, 사용성 평가 3번) — 빈 목록일 때 보여 줄 안내(HTML). 공통 문구 "데이터가 없습니다"는 처음 쓰는
// 사람에게 다음에 무엇을 할지 알려 주지 않았다 — 사람이 보는 주요 목록은 무엇이 여기 모이는지와 다음 행동을 적는다.
function table(headers, rows, opts) {
  const tableId = "tbl-" + (++_tableSeq);
  const hasRows = rows.length > 0;
  let html = '<div class="table-wrap">';
  if (hasRows) {
    html += '<input type="search" class="table-filter" data-table-filter="' + tableId +
      '" placeholder="검색…" aria-label="목록 검색">';
  }
  html += '<table id="' + tableId + '"><thead><tr>';
  headers.forEach((h, i) => {
    const cls = headerColClass(h);
    const sortable = hasRows && h !== "동작";
    const thClass = [cls, sortable ? "sortable-col" : ""].filter(Boolean).join(" ");
    // 사용자 요청(2026-10-27, "컬럼폭 조정시에도 전체 폭이 영역을
    // 넘지 않도록") — 핸들은 "열의 오른쪽 끝"이 아니라 "열 i와 열
    // i+1 사이의 경계"다. 드래그하면 그 경계 양옆 두 열만 폭을
    // 맞바꾸므로(한쪽이 늘면 옆이 그만큼 줄어듦) 테이블 전체 폭은
    // 절대 안 바뀐다 — 그래서 마지막 열에는 핸들을 안 둔다(그 오른쪽엔
    // 맞바꿀 열이 없다).
    const isLast = i === headers.length - 1;
    html += "<th" + (thClass ? ' class="' + thClass + '"' : "") +
      (sortable ? ' data-sort-col="' + i + '" title="클릭해 정렬"' : "") +
      ">" + esc(h) + (sortable ? ' <span class="sort-indicator"></span>' : "") +
      (isLast ? "" : '<span class="col-resizer" title="드래그해 폭 조정"></span>') + "</th>";
  });
  html += "</tr></thead><tbody>";
  if (!hasRows) {
    html += '<tr class="table-empty"><td colspan="' + headers.length + '" class="muted">' + ((opts && opts.empty) || "아직 항목이 없습니다.") + "</td></tr>";
  }
  for (const r of rows) {
    html += "<tr>";
    r.forEach((c, i) => {
      const cls = headerColClass(headers[i]);
      html += "<td" + (cls ? ' class="' + cls + '"' : "") + ">" + c + "</td>";
    });
    html += "</tr>";
  }
  html += "</tbody></table></div>";
  return html;
}

// 정렬 클릭·검색 입력 둘 다 렌더링된 테이블의 textContent만 본다 —
// 숫자처럼 보이는 열(모든 셀이 Number()로 변환되면)은 숫자 비교,
// 아니면 한국어 로케일 문자열 비교로 정렬한다. "데이터가 없습니다"
// 자리표시 행은 hasRows가 false일 때만 생기고 그때는 헤더 자체가
// 정렬 불가능하므로 따로 걸러낼 필요가 없다.
function _sortTableByColumn(th) {
  const tbl = th.closest("table");
  if (!tbl) return;
  const colIndex = Number(th.dataset.sortCol);
  const tbody = tbl.querySelector("tbody");
  // 사용자 보고(2026-10-14, 스크린샷 — 정렬했더니 "구분/내용/타깃/동적
  // 테스트" 같은 엉뚱한 행이 섞여 테이블이 깨져 보임) — 실측으로 확인한
  // 원인: "AI 에이전트 관리"의 "최근 산출물"(상세정보 안)은 이제
  // marked.js로 실제 마크다운 **표**까지 렌더링한다(이전엔 불가능했던
  // 기능) — 그 결과 셀 안에 중첩된 <table>이 생길 수 있다.
  // `tbody.querySelectorAll("tr")`은 자손 전체를 뒤져 그 중첩 표의
  // <tr>까지 집어오고, 정렬 뒤 `tbody.appendChild(tr)`로 그 행들을
  // **바깥 테이블의 직계 자식**으로 떼어내 옮겨버렸다 — 중첩 표가
  // 산산조각나 바깥 테이블 행처럼 보인 것. 바깥 tbody의 **직계 자식만**
  // (`tbody.children`) 대상으로 삼아야 중첩 표는 손대지 않는다.
  const rows = Array.from(tbody.children);
  const nextDir = th.dataset.sortDir === "asc" ? "desc" : "asc";
  tbl.querySelectorAll("th[data-sort-col]").forEach(h => {
    h.removeAttribute("data-sort-dir");
    const indicator = h.querySelector(".sort-indicator");
    if (indicator) indicator.textContent = "";
  });
  th.dataset.sortDir = nextDir;
  const indicator = th.querySelector(".sort-indicator");
  if (indicator) indicator.textContent = nextDir === "asc" ? "▲" : "▼";

  const cellText = (tr) => {
    const cell = tr.children[colIndex];
    return cell ? cell.textContent.trim() : "";
  };
  const isNumericLike = (v) => v === "" || v === "-" || !isNaN(Number(v.replace(/,/g, "")));
  const allNumeric = rows.every(tr => isNumericLike(cellText(tr)));
  rows.sort((a, b) => {
    const va = cellText(a);
    const vb = cellText(b);
    let cmp;
    if (allNumeric) {
      const toNum = (v) => (v === "" || v === "-") ? -Infinity : Number(v.replace(/,/g, ""));
      cmp = toNum(va) - toNum(vb);
    } else {
      cmp = va.localeCompare(vb, "ko");
    }
    return nextDir === "asc" ? cmp : -cmp;
  });
  rows.forEach(tr => tbody.appendChild(tr));
}

// 사용자 요청(2026-10-27, "테이블 컬럼 폭을 마우스로 조정 가능하게
// 개선해줘") — table() 헬퍼가 공유되는 23곳 전부, 위 정렬/필터처럼
// 전역 위임 하나로 처리한다(호출부마다 배선 불필요). 드래그를 시작하는
// 순간 그 테이블의 모든 열 폭을 "지금 렌더된 그대로" px로 얼리고
// table-layout을 fixed로 바꾼다 — 그래야 한 열만 끌 때 다른 열들이
// auto 레이아웃 재계산으로 같이 흔들리지 않는다(평소엔 내용 기준
// 가변폭을 유지하다가, 실제로 조정한 테이블만 그 순간부터 고정폭).
// 핸들은 "열 i / 열 i+1 사이 경계"라, 드래그는 그 둘의 폭만 맞바꾼다
// (한쪽 + delta, 다른쪽 - delta).
//
// 사용자 보고(2026-10-27, "컬럼폭 조정시에도 전체 폭이 넘지 않도록" →
// "핸들을 드래그하는 순간 바로 스크롤" → "비용 메뉴는 기본으로도
// 스크롤, 다른 메뉴는 여전히" → "아직도 발생") — 이 테이블/컬럼 폭을
// .table-wrap보다 좁게 강제로 욱여넣으려는 시도(열 폭 비례 축소,
// table{max-width:100%} 등)를 몇 차례 더 해봤지만 전부 효과가 없어
// 전부 되돌렸다. 원인은 CSS의 근본 규칙이다 — 셀 안에 줄어들 수 없는
// 내용(`<input style="width:120px">`처럼 폭이 고정된 폼 컨트롤, 버튼
// 여러 개)이 있으면 그 "최소 콘텐츠 폭(min-content)"이 `max-width`나
// JS로 지정한 어떤 값보다도 우선한다 — 브라우저는 max-width가 더
// 좁아도 min-content 밑으로는 못 줄인다(table-layout 값과도 무관).
// 즉 "버튼/입력창이 있는 열이 여럿인 테이블은 어느 시점부터는 좌우
// 스크롤이 생기는 게 CSS 스펙상 정상 동작"이고, `.table-wrap`의
// `overflow-x: auto`(아래 CSS)가 바로 그 표준 해법이다 — 페이지
// 밖으로 터지지 않고 테이블 박스 안에서만 스크롤된다. 그래서 이
// 리사이즈 로직은 "드래그로 폭을 조정하는 기능" 하나만 하고, "스크롤
// 자체를 없애겠다"는 시도는 더 안 한다(이미 있는 폭을 얼릴 뿐 축소는
// 안 함) — 정말 스크롤을 줄이려면 그 테이블의 셀 내용 자체(버튼 개수,
// 입력창 폭)를 줄이는 수밖에 없다(예: 이미 compact 처리한 "상사
// 지정"류 열처럼, 테이블별로 따로 손봐야 함).
let _colResizeState = null;
document.addEventListener("mousedown", (ev) => {
  const handle = ev.target.closest(".col-resizer");
  if (!handle) return;
  const th = handle.closest("th");
  const next = th && th.nextElementSibling;
  const tbl = th && th.closest("table");
  if (!th || !next || !tbl) return;
  ev.preventDefault();
  tbl.querySelectorAll(":scope > thead > tr > th").forEach(h => {
    h.style.width = h.offsetWidth + "px";
  });
  tbl.style.tableLayout = "fixed";
  handle.classList.add("resizing");
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
  _colResizeState = {
    th, next, startX: ev.clientX,
    startWidth: th.offsetWidth, startNextWidth: next.offsetWidth,
    handle,
  };
});
document.addEventListener("mousemove", (ev) => {
  if (!_colResizeState) return;
  const { th, next, startX, startWidth, startNextWidth } = _colResizeState;
  const minDelta = 40 - startWidth;
  const maxDelta = startNextWidth - 40;
  const delta = Math.min(maxDelta, Math.max(minDelta, ev.clientX - startX));
  th.style.width = (startWidth + delta) + "px";
  next.style.width = (startNextWidth - delta) + "px";
});
document.addEventListener("mouseup", () => {
  if (!_colResizeState) return;
  _colResizeState.handle.classList.remove("resizing");
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  _colResizeState = null;
});

document.addEventListener("input", (ev) => {
  const filterInput = ev.target.closest("[data-table-filter]");
  if (!filterInput) return;
  const tbl = document.getElementById(filterInput.dataset.tableFilter);
  const tbody = tbl && tbl.querySelector("tbody");
  if (!tbody) return;
  const q = filterInput.value.trim().toLowerCase();
  // _sortTableByColumn()과 같은 이유로 `tbody.querySelectorAll("tbody tr")`
  // 대신 직계 자식(tbody.children)만 본다 — 셀 안에 중첩된 마크다운 표의
  // 행까지 숨김/표시 대상으로 잘못 집어오지 않도록.
  Array.from(tbody.children).forEach(tr => {
    const text = tr.textContent.toLowerCase();
    tr.style.display = (!q || text.includes(q)) ? "" : "none";
  });
});

/* Phase 5(사용자 요청, 2026-09-28, "협업 워크스페이스 UX 개선" — Skill
   자동완성). skill_pool에 등록된 이름을 <datalist>로 내려줘 스킬을 자유
   텍스트로 입력하는 곳마다 브라우저 기본 자동완성이 뜨게 한다 — Skill
   주입이 skill_pool 이름과 "정확히 일치"해야 매칭되므로, 오타로
   조용히 매칭이 안 되는 걸 줄이는 게 목적이다.

   **정정(2026-10-06, 사용자 보고 "하나의 스킬을 선택한 후에는 다른
   스킬을 펼쳐 확인할 수 없어")**: 콤마로 여러 개를 입력하는 단일
   텍스트필드(d-skills/hp-skills)는 브라우저가 입력값 전체를 기준으로
   자동완성을 맞춰서, 이미 뭔가 입력한 뒤 콤마 다음 항목부터는 드롭다운이
   거의 안 열렸다 — 매번 입력을 지우고 다시 써야 했다. `skillPickerHtml()`/
   `initSkillPicker()`로 작은 태그 입력 위젯을 추가해 해결한다: 값을
   하나씩 입력(Enter 또는 "추가" 버튼)할 때마다 칩으로 빠지고 입력창이
   비워지므로, 다음 항목을 고를 때도 매번 전체 자동완성 목록이 뜬다.
   skill_pool에 없는 자유 텍스트(예: 기존 데이터의 "인사 정책")도 그대로
   칩으로 추가할 수 있어 기존 자유 입력 동작은 그대로 유지한다. 단일 값
   필드인 검색의 s-skill은 애초에 문제가 없어 그대로 둔다. */
async function skillDatalistHtml() {
  let names;
  try {
    names = await api("GET", "/api/skill-pool/names");
  } catch (e) {
    return "";
  }
  return '<datalist id="skill-datalist">' +
    names.map(n => '<option value="' + esc(n) + '">').join("") +
    "</datalist>";
}

/* 사용자 요청(2026-10-06, "운영 관리에 사용 가능한 ollama 모델 및
   openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을 불러오는
   방식으로 UI/UX를 개선" → "에이전트 생성, 관리, Crew 실행 등에도
   연동되도록 적용해줘" → "진한 드롭다운으로 강제 선택") — 처음엔
   datalist 자동완성(참고용, 자유 입력 유지)으로 연결했으나, 사용자가
   실제 `<select>` 드롭다운으로 바꿔 등록된 모델 중에서 고르게 하는
   쪽을 선택했다. 레지스트리가 비어 있거나 원하는 모델이 아직 없는
   경우를 위해 "직접 입력..." 옵션을 두고, 고르면 그 옆에 텍스트
   입력이 나타난다 — 등록을 강제하지 않으면서도 평소엔 드롭다운으로
   오타/오입력을 막는다. "AI 에이전트 생성"·"AI 에이전트 관리(모델
   설정)"·"Crew 실행"·협업 워크스페이스 4곳이 전부 이 셋을 공유한다. */
async function fetchModelsByProvider() {
  const byProvider = { anthropic: [], openai: [], ollama: [] };
  let models;
  try {
    // 사용자 보고(2026-10-22, "member 권한으로 대화시 모델 프로바이더를
    // 선택해도 등록된 모델이 불려오지 않음") — `/api/model-registry`는
    // admin+manager 전용이라 member/viewer는 403을 받고 여기서 조용히
    // 빈 목록으로 폴백했다(드롭다운이 "기본값 사용"/"직접 입력..."만
    // 보임). `/api/model-registry/names`(모든 role 허용, provider+모델명
    // 만 노출)로 바꾼다.
    models = await api("GET", "/api/model-registry/names");
  } catch (e) {
    return byProvider;
  }
  models.forEach(m => {
    if (byProvider[m.provider]) byProvider[m.provider].push(m.name);
  });
  return byProvider;
}

function modelSelectHtml(selectId) {
  return '<select id="' + selectId + '"><option value="">기본값 사용</option>' +
    '<option value="__custom__">직접 입력...</option></select> ' +
    '<input id="' + selectId + '-custom" placeholder="모델명 직접 입력" style="display:none;width:160px">';
}

// Tier 2(2026-10-07, "Tier 2까지 진행해줘") — "결과물 작성"/"LLM으로
// 도와줘"/"AI 에이전트 실행"/"팀 Agent 실행" 4곳 모두 이 select 하나를
// 재사용한다 — 작성자가 고르는 형식과 Agent/LLM에게 지시하는 형식이
// 같아야 자연스럽기 때문이다(`deliverables._CONTENT_TYPE_INSTRUCTIONS`).
const CONTENT_TYPE_LABELS = { text: "일반 텍스트", markdown: "Markdown", html: "HTML", code: "코드" };
function contentTypeSelectHtml(selectId) {
  return '<select id="' + selectId + '" title="결과물 형식">' +
    Object.keys(CONTENT_TYPE_LABELS).map(v =>
      '<option value="' + v + '">' + esc(CONTENT_TYPE_LABELS[v]) + '</option>'
    ).join("") +
    '</select>';
}

// providerSelectId가 바뀔 때마다 modelSelectId의 옵션을 그 provider의
// 등록된 모델로 다시 채운다. `initialValue`는 이미 저장된 모델명(예:
// "AI 에이전트 관리"의 기존 llm_model)을 미리 선택해두기 위함 —
// 레지스트리에 없는(과거에 자유 입력으로 저장된) 값이면 "직접 입력..."
// 을 골라 그 값을 미리 채워 보여준다(데이터 손실 없음).
function wireProviderModelSelect(providerSelectId, modelSelectId, modelsByProvider, initialValue) {
  const providerSelect = document.getElementById(providerSelectId);
  const modelSelect = document.getElementById(modelSelectId);
  const customInput = document.getElementById(modelSelectId + "-custom");
  if (!providerSelect || !modelSelect || !customInput) return;

  const rebuildOptions = (preselect) => {
    const names = modelsByProvider[providerSelect.value] || [];
    const useCustom = !!preselect && !names.includes(preselect);
    modelSelect.innerHTML = '<option value="">기본값 사용</option>' +
      names.map(n => '<option value="' + esc(n) + '"' + (n === preselect ? " selected" : "") + '>' + esc(n) + "</option>").join("") +
      '<option value="__custom__"' + (useCustom ? " selected" : "") + '>직접 입력...</option>';
    customInput.style.display = useCustom ? "" : "none";
    customInput.value = useCustom ? preselect : "";
  };

  modelSelect.addEventListener("change", () => {
    const isCustom = modelSelect.value === "__custom__";
    customInput.style.display = isCustom ? "" : "none";
    if (!isCustom) customInput.value = "";
  });
  providerSelect.addEventListener("change", () => rebuildOptions(null));
  rebuildOptions(initialValue || null);
}

function modelSelectValue(modelSelectId) {
  const select = document.getElementById(modelSelectId);
  if (!select) return null;
  if (select.value === "__custom__") {
    const custom = document.getElementById(modelSelectId + "-custom");
    return custom && custom.value.trim() ? custom.value.trim() : null;
  }
  return select.value || null;
}

/* 여러 스킬을 하나씩 추가/삭제하는 작은 태그 입력 위젯. `hiddenId` 입력은
   기존 코드(제출 핸들러의 `.value.split(",")`)와 호환되도록 그대로
   콤마구분 문자열을 담아둔다 — 폼 제출 로직은 손대지 않는다. */
function skillPickerHtml(pickerId, hiddenId) {
  return '<div class="skill-picker" id="' + pickerId + '">' +
    '<div class="skill-picker-row">' +
    '<input type="text" class="skill-picker-input" list="skill-datalist" placeholder="스킬 입력 후 Enter 또는 추가">' +
    '<button type="button" class="small skill-picker-add">추가</button>' +
    '</div>' +
    '<div class="skill-picker-chips"></div>' +
    '</div>' +
    '<input type="hidden" id="' + hiddenId + '">';
}

/* 반환하는 `flush()`는 "추가" 버튼을 안 누르고 입력창에 값을 남겨둔 채
   제출 버튼을 바로 눌러도 놓치지 않도록, 제출 핸들러 맨 앞에서 명시적으로
   호출해 현재 입력을 칩으로 반영한다(같은 엘리먼트에 등록된 capture
   리스너가 bubble 리스너보다 먼저 실행된다고 가정하지 않는다 — AT_TARGET
   단계에선 등록 순서대로 실행되므로 암묵적 순서에 기대지 않는다). */
function initSkillPicker(pickerId, hiddenId, initial) {
  const root = document.getElementById(pickerId);
  const hidden = document.getElementById(hiddenId);
  const input = root.querySelector(".skill-picker-input");
  const chips = root.querySelector(".skill-picker-chips");
  const state = [...(initial || [])];

  const render = () => {
    hidden.value = state.join(",");
    chips.innerHTML = state.map(s =>
      '<span class="skill-chip">' + esc(s) +
      '<button type="button" data-skill="' + esc(s) + '" title="제거">×</button></span>'
    ).join("");
    chips.querySelectorAll("[data-skill]").forEach(btn => {
      btn.onclick = () => {
        const i = state.indexOf(btn.dataset.skill);
        if (i >= 0) state.splice(i, 1);
        render();
      };
    });
  };

  const addCurrent = () => {
    const v = input.value.trim();
    if (v && !state.includes(v)) state.push(v);
    input.value = "";
    render();
  };

  root.querySelector(".skill-picker-add").onclick = addCurrent;
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addCurrent(); }
  });

  render();
  return { flush: addCurrent };
}

let ME = null;
// 조직 정책(2026-11-21, "사번 형식 … 설정화") — 사번 입력란 안내 문구. 형식 검증은 서버가
// 한다(관리자가 정한 정규식이 브라우저 정규식과 문법이 다를 수 있어 pattern 속성은 쓰지 않음).
let ORG_POLICY = { employee_id: { hint: "숫자 8~12자리", is_default: true } };
async function loadOrgPolicy() {
  try { ORG_POLICY = await api("GET", "/api/org-policy"); } catch (e) { /* 기본 안내 유지 */ }
}
function empIdHint() { return (ORG_POLICY.employee_id && ORG_POLICY.employee_id.hint) || "숫자 8~12자리"; }
function empIdInputAttrs(optional) {
  const hint = empIdHint();
  return ' placeholder="' + escAttr("사번(" + (optional ? "선택, " : "") + hint + ")") + '" title="' + escAttr(hint) + '"';
}

/* 코드 재점검(2026-09-26) 사용자 요청 — 관리자 기능과 일반 업무 기능을
   화면상에서도 명확히 구분한다.

   사용자 요청(2026-09-29, "HAIR 관점에서 외부 사례들을 조사하고... 관리자와
   사용자 고유 업무와 공통 업무를 구분해줘" → "로그인한 계정 유형에 맞춰
   메뉴 노출을 개선해줘")에 따라 group을 둘에서 셋으로 늘렸다
   (관리자/사용자 고유·공통 업무 실측 재분류 그대로 반영):
   - "user": 담당자가 자기 업무를 처리·요청하는 화면(역할마다 안의 동작은
     다르지만 탭 자체는 전 역할에 열려 있다).
   - "common": 역할과 무관한 순수 조회/참조 화면(Viewer 포함 전부 동일하게
     봄) — 원래 "user" 그룹에 섞여 있어 "내 업무"와 성격이 다른데 구분이
     안 됐었다.
   - "admin": 조직/리소스를 운영·관리하는 화면. 예전엔 "관리자 도구"라고만
     불러 매니저도 대부분 쓸 수 있다는 걸 라벨만 보고는 알 수 없었다
     (실측: 11개 중 10개가 admin+manager 공용) — 라벨을 "운영 관리"로
     바꿔 그 실제 구성을 반영한다. adminOnly: true는 그 안에서도 진짜
     ADMIN 전용인 항목(현재 "사용자 관리" 하나)만 배지로 구분 표시한다.

   사용자 요청(2026-09-29, "내 업무, 공용 조회, 운영 관리 각 카테고리...
   프로세스 순에 맞춰서, 또는 자주 사용하는 업무가 먼저 위치하도록
   버튼 배치가 되어 있는지 확인해줘" → "진행해줘")에 따라 "운영 관리"
   그룹 순서를 조정했다 — 실측 비교 결과 "내 업무"/"공용 조회" 그룹은
   이미 합리적(인박스가 맨 앞인 건 프로세스 1단계가 아니라 "가장 자주
   보는 화면 우선"이라는 의도된 배치)이었지만, "운영 관리"는 프로세스
   맵(https://claude.ai/artifact/8dQVis1XEEEZdcdTsytvpv) 13단계에서
   "팀 현황 모니터링"으로 하나로 묶은 대시보드·비용·공정성 지표·활동
   기록 4개가 실제 화면에선 대시보드만 맨 앞에 있고 나머지 3개는
   8~10번째로 흩어져 있었다. 모니터링 4개를 앞으로 모으고, 그 뒤는
   프로세스 순서(배정 → Agent/사람 준비 → Pool 표준화 → 계정 관리)를
   그대로 따른다.

   사용자 요청(2026-10-13, "상단 메뉴 버튼의 순서도 검토해줘" →
   "humanpool/agentpool 순서만 바꿔줘")로 한 번 더 조정 — 프로세스 맵
   16단계가 "AI 에이전트 생성 → AI 에이전트 관리"를 화살표로 이은 한
   묶음으로, 18단계가 "도구/스킬 Pool"과 "사람 인력 관리"를 같은 단계로
   나란히 묶어뒀는데, 기존 순서(jobs → humanpool → agentpool →
   poolregistry)는 humanpool이 그 사이에 끼어 두 묶음을 모두 깨고
   있었다. humanpool과 agentpool 자리를 바꿔 jobs→agentpool(16단계),
   humanpool→poolregistry(18단계) 인접성을 둘 다 맞춘다. */
const TABS = [
  { id: "conversations", label: "대화", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderConversations },
  // 홈(2026-11-23, "P2 진행해줘" — P2-8 화면 복잡도·학습 곡선). 탭이 28개라 "무엇부터, 어디서"를
  // 몰라 이용 가이드를 읽어야 했다. 처리할 일과 "AI에게 일을 맡기는 방법"을 역할에 맞게 한 화면에
  // 모은다. 로그인 첫 화면은 홈이다(2026-11-27, 사용성 평가 1번 — 예전엔 메뉴 첫 탭인 빈 "대화" 표였다).
  { id: "home", label: "홈", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderHome },
  { id: "inbox", label: "인박스", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderInbox },
  { id: "mywork", label: "내 업무", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderMyWork },
  { id: "board", label: "할 일 보드", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBoard },
  { id: "gallery", label: "갤러리", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderGallery },
  { id: "briefs", label: "업무 요청", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBriefs },
  { id: "approvals", label: "결재", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderApprovals },
  { id: "performance", label: "성과 리뷰", group: "user", roles: ["admin", "manager", "member"], render: renderPerformance },
  { id: "search", label: "인력 검색", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderSearch },
  { id: "orgchart", label: "조직도", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderOrgChart },
  { id: "manual", label: "이용 가이드", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderManual },
  // 연동 설정(2026-11-23, P1-5) — 개인 API 키(전원), 정기 실행(admin/manager), 외부 알림 웹훅(admin).
  // "common" 그룹은 역할과 무관한 순수 조회 화면만 둔다(test_phase11) — 역할마다 보이는 구역이
  // 다른 작업 화면이라 "user" 그룹이다.
  { id: "integrations", label: "연동 설정", group: "user", roles: ["admin", "manager", "member"], render: renderIntegrations },
  { id: "dashboard", label: "대시보드", group: "admin", roles: ["admin", "manager"], render: renderDashboard },
  // 프로세스 성과(2026-11-27, AI Native 분석 1단계) — 파이프라인별 리드타임 분해·한 번에 완료·자동 처리율·병목 단계.
  { id: "processmetrics", label: "프로세스 성과", group: "admin", roles: ["admin", "manager"], render: renderProcessMetrics },
  // 자원 운용(2026-11-27, Resource Management Phase B) — 사람별 4주 활용도·기한·인원 부족·역할별 수요.
  { id: "resourceplanning", label: "자원 운용", group: "admin", roles: ["admin", "manager"], render: renderResourcePlanning },
  { id: "cost", label: "비용", group: "admin", roles: ["admin", "manager"], render: renderCost },
  { id: "fairness", label: "공정성 지표", group: "admin", roles: ["admin", "manager"], render: renderFairness },
  { id: "audit", label: "활동 기록", group: "admin", roles: ["admin", "manager"], render: renderAudit },
  { id: "match", label: "리소스 매칭/배정", group: "admin", roles: ["admin", "manager"], render: renderMatch },
  { id: "jobs", label: "AI 에이전트 생성", group: "admin", roles: ["admin", "manager"], render: renderJobs },
  { id: "agentpool", label: "AI 에이전트 관리", group: "admin", roles: ["admin", "manager"], render: renderAgentPool },
  { id: "agentoptimize", label: "LLM 설정 최적화", group: "admin", roles: ["admin"], adminOnly: true, render: renderAgentOptimization },
  { id: "humanpool", label: "사람 인력 관리", group: "admin", roles: ["admin", "manager"], render: renderHumanPool },
  { id: "poolregistry", label: "도구/스킬 Pool", group: "admin", roles: ["admin", "manager"], render: renderPoolRegistry },
  { id: "teams", label: "팀 관리", group: "admin", roles: ["admin", "manager"], render: renderTeams },
  { id: "pipelines", label: "파이프라인 관리", group: "admin", roles: ["admin", "manager"], render: renderPipelines },
  { id: "modelregistry", label: "모델 레지스트리", group: "admin", roles: ["admin"], adminOnly: true, render: renderModelRegistry },
  { id: "knowledgebase", label: "지식 베이스", group: "admin", roles: ["admin", "manager"], render: renderKnowledgeBase },
  { id: "crew", label: "Crew 빌더", group: "admin", roles: ["admin", "manager"], render: renderCrewBuilder },
  { id: "admin", label: "사용자 관리", group: "admin", roles: ["admin"], adminOnly: true, render: renderAdmin },
  // 평가 기준 설정(2026-11-25, 사용자 요청) — Agent-Evaluator 채점 기준(Gate별 통과 기준·SLA·추론 표지·도구 범위 감점).
  // 시스템 상태(2026-11-27, 사용성 평가 11번 — `system_status.py`) — 서버·DB·디스크·작업 큐·모델·연결·구성을 한 화면에.
  { id: "systemstatus", label: "시스템 상태", group: "admin", roles: ["admin"], adminOnly: true, render: renderSystemStatus },
  { id: "evalconfig", label: "평가 기준", group: "admin", roles: ["admin"], adminOnly: true, render: renderEvaluationConfig },
];

async function boot() {
  try {
    ME = await api("GET", "/api/me");
    await loadOrgPolicy();
    document.getElementById("login-view").style.display = "none";
    document.getElementById("app-view").style.display = "block";
    document.getElementById("whoami").textContent = ME.username + " (" + (ROLE_NAMES[ME.role] || ME.role) + ")";
    document.getElementById("whoami").onclick = openMyPasswordDialog;
    buildNav();
    // 첫 화면은 홈(2026-11-27, 사용성 평가 1번) — 이용 가이드·홈 안내가 "홈 → AI에게 일 맡기기"부터 시작하라고 하는데
    // 예전엔 메뉴 첫 탭(빈 "대화" 표)에 떨어졌다.
    const allowed = TABS.filter(t => t.roles.includes(ME.role));
    selectTab((allowed.find(t => t.id === "home") || allowed[0]).id);
    startMentionNotificationPolling();
  } catch (e) {
    ME = null;
    stopMentionNotificationPolling();
    document.getElementById("app-view").style.display = "none";
    document.getElementById("login-view").style.display = "flex";
  }
}

// (2026-10-08, Phase 5) — **탭 재구성**:
// 기존 3구역("내 업무"/"공용 조회"/"운영 관리")을 "오늘 할 일"/"설정"
// 2구역으로 합친다(설계 문서: "신규 사용자가 매일 보는 화면을 좁힌다").
// `TABS` 배열의 `group` 값(user/common/admin) 자체는 그대로 둔다 —
// "admin" 그룹(Pool 5종·Team·Model Registry 등 자주 안 바뀌는 설정성
// 화면)을 "설정"으로, 나머지(user+common — 업무 요청·성과 리뷰·
// 인력 검색·조직도처럼 역할과 무관하게 자주 참조하는 화면 포함)를
// "오늘 할 일"로 묶는다. 설계 문서가 예시로 든 "인박스·내 업무·할 일
// 보드·결재" 4개만 남기고 나머지(업무 요청 등)를 "설정"으로 보내는
// 더 좁은 해석도 가능했지만, 매일 쓰는 "업무 요청"을 "설정"에 숨기면
// 오히려 더 불편해질 위험이 커서 "admin 전용 여부"를 기준으로
// 묶었다 — 이 해석은 의도적 판단이었다는 걸 정직하게 기록해둔다.
//
// 사용자 요청(2026-11-02, "설정 메뉴 배치를 Group 1 리소스 관리...
// Group 4 운영관리 순으로 조정하고 각 그룹별 분리될 수 있도록") —
// 위에서 하나로 뭉쳐 있던 "설정"(`group==="admin"`인 16개 탭)을 4개
// 하위 그룹으로 다시 나눈다. `TABS`의 `group` 필드는 "오늘 할 일" vs
// "설정" 1차 분류에만 쓰이므로 그대로 두고, 여기 `SETTINGS_GROUPS`가
// "설정" 내부의 2차 배치(순서+묶음)만 따로 정의한다 — 두 분류 기준이
// 섞이면 나중에 셋 중 어느 쪽을 고치는 건지 헷갈리기 때문에 분리해
// 둔다. "공정성 지표"는 사용자 지정 목록에 없었으나(사용자 결정)
// 운영 성격상 운영관리 끝에 둔다.
//
// 사용자 요청(2026-11-02, "그라운딩 관리, 모델관리, 운영관리 3메뉴
// 영역은 숨겨져 있다가 펼쳐 보는 방식으로") — 가장 자주 쓰는 "리소스
// 관리"만 항상 펼쳐두고 나머지 3그룹은 기본 접힌 상태로 시작한다.
// 커스텀 토글 상태/아이콘을 직접 관리하는 대신 네이티브
// `<details>/<summary>`(이 파일의 다른 곳에서도 이미 쓰는 패턴 — 예:
// "팀에 배정 요청", "실행 이력 보기")를 쓴다 — 펼침 여부를 브라우저가
// 알아서 관리해주고, JS 쪽엔 상태 변수가 하나도 늘지 않는다.
// `collapsible: true`인 그룹만 `<details>`+`<summary>`로, 아니면 기존
// 그대로 `<div>`+평범한 `<span>` 라벨로 렌더링한다(`buildNav()` 참고).
//
// 사용자 요청(2026-11-02, "'인력 매칭/배정'을 '리소스 매칭/배정'으로
// 변경, 가장 먼저 위치 및 구분선 추가, agent 관리 문구 삭제") — 가장
// 자주 누르는 동작이라 그룹 맨 앞으로 옮기고 구분선(`"__divider__"`
// 토큰 — `buildNav()`가 라벨 없는 `.nav-divider`만 그린다)으로
// 나머지와 시각적으로 나눈다. "Agent 관리" 하위 소제목은 더 이상
// 안 쓴다 — 나머지(사람 인력 관리/AI 에이전트 생성·관리/Crew 빌더/팀
// 관리)는 전부 같은 레벨의 평범한 버튼으로 나열한다. 라벨 텍스트
// 자체("리소스 매칭/배정")는 `TABS`의 `match` 항목에서 바뀐다.
const SETTINGS_GROUPS = [
  {
    label: "리소스 관리",
    entries: [
      "match", "__divider__",
      "humanpool", "__divider__",
      "jobs", "agentpool", "crew", "__divider__",
      "teams",
    ],
  },
  {
    label: "지식·데이터",  // 예전 "그라운딩 관리"(2026-11-27, 사용성 평가 7번 — 전문 용어라 뜻이 안 전해졌다)
    entries: ["pipelines", "poolregistry", "knowledgebase"],
    collapsible: true,
  },
  {
    label: "모델 관리",
    entries: ["agentoptimize", "modelregistry"],
    collapsible: true,
  },
  {
    label: "운영 관리",
    entries: ["systemstatus", "evalconfig", "admin"],  // 대시보드·비용·공정성·활동 기록은 "현황"으로(2026-11-27)
    collapsible: true,
  },
];

// 메뉴 구조(2026-11-27, 사용성 평가 7번) — 역할 구분("오늘 할 일"·"설정")이 아니라 업무 흐름으로 묶는다. 한 줄에
// 일하기 · 검토·결재 · 현황(▾)을 두고, admin·manager에게만 설정(▾) 한 줄을 더 둔다(예전 요청대로 메뉴가 아래로 넓게
// 퍼지지 않게 두 줄 유지). 이용 가이드는 헤더의 ❓ 도움말·🔍 메뉴 찾기로, 연동 설정(개인 API 키 포함)은 헤더 "내 설정"으로 옮겼다.
const NAV_WORK = ["home", "conversations", "briefs", "mywork", "board"];
const NAV_REVIEW = ["inbox", "approvals", "gallery"];
const NAV_STATUS = ["dashboard", "processmetrics", "resourceplanning", "performance", "cost", "fairness", "audit", "orgchart", "search"];

function buildNav() {
  const nav = document.getElementById("tabs");
  nav.innerHTML = "";

  const byId = {};
  TABS.forEach(t => { byId[t.id] = t; });
  const visible = (ids) => ids.map(id => byId[id]).filter(t => t && t.roles.includes(ME.role));

  const makeButton = (t) => {
    const btn = document.createElement("button");
    btn.textContent = t.label;
    if (t.adminOnly) {
      // 2026-11-25 — "관리자 전용" 글자 배지가 버튼을 넓혀 🔒 표시 + 마우스를 올리면 설명으로 줄였다.
      const badge = document.createElement("span");
      badge.className = "badge-adminonly-icon";
      badge.textContent = " 🔒";
      btn.title = "관리자 전용";
      btn.appendChild(badge);
    }
    // 인박스 탭 버튼에 미읽은 건수(2026-11-12) — 폴링(`_pollInboxBadge()`)이 갱신한다.
    if (t.id === "inbox") {
      const badge = document.createElement("span");
      badge.className = "badge-inbox-count";
      badge.id = "inbox-badge-count";
      badge.hidden = true;
      btn.appendChild(badge);
    }
    btn.dataset.tab = t.id;
    btn.onclick = () => selectTab(t.id);
    return btn;
  };

  const label = (text) => {
    const tag = document.createElement("span");
    tag.className = "nav-label";
    tag.textContent = text;
    return tag;
  };

  // 누르면 아래에 작은 메뉴가 뜨는 그룹 버튼(바깥을 누르거나 Esc로 닫힘, 한 번에 하나만 열림). 지금 화면이 속한 그룹
  // 버튼에는 "그룹 · 화면 이름"이 보인다(`_syncNavDropdowns`).
  const makeDropdown = (groupLabel, items) => {
    const wrap = document.createElement("span");
    wrap.className = "nav-dropdown";
    wrap.dataset.groupLabel = groupLabel;
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "nav-dropdown-trigger";
    trigger.setAttribute("aria-haspopup", "true");
    trigger.setAttribute("aria-expanded", "false");
    trigger.textContent = groupLabel + " ▾";
    const menu = document.createElement("div");
    menu.className = "nav-dropdown-menu";
    menu.hidden = true;
    for (const item of items) {
      if (item === "__divider__") {
        const divider = document.createElement("span");
        divider.className = "nav-divider";
        menu.appendChild(divider);
      } else {
        const btn = makeButton(item);
        const go = btn.onclick;
        btn.onclick = () => { _closeNavDropdowns(); go(); };
        menu.appendChild(btn);
      }
    }
    trigger.onclick = (ev) => {
      ev.stopPropagation();
      const willOpen = menu.hidden;
      _closeNavDropdowns();
      menu.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", String(willOpen));
      // 폭 상한을 화면 너비로 넓혀 한 줄로 두고, 오른쪽 끝을 넘으면 그만큼 왼쪽으로 당긴다(좁은 화면에서만 줄바꿈).
      if (willOpen) {
        menu.style.left = "0px";
        const overflow = menu.getBoundingClientRect().right - (window.innerWidth - 12);
        if (overflow > 0) menu.style.left = -Math.min(overflow, wrap.getBoundingClientRect().left - 12) + "px";
      }
    };
    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    return wrap;
  };

  const mainRow = document.createElement("div");
  mainRow.className = "nav-group nav-main";
  const work = visible(NAV_WORK), review = visible(NAV_REVIEW), status = visible(NAV_STATUS);
  if (work.length) { mainRow.appendChild(label("일하기")); work.forEach(t => mainRow.appendChild(makeButton(t))); }
  if (review.length) {
    const sep = document.createElement("span"); sep.className = "nav-divider"; mainRow.appendChild(sep);
    mainRow.appendChild(label("검토·결재")); review.forEach(t => mainRow.appendChild(makeButton(t)));
  }
  if (status.length) {
    const sep = document.createElement("span"); sep.className = "nav-divider"; mainRow.appendChild(sep);
    mainRow.appendChild(makeDropdown("현황", status));
  }
  nav.appendChild(mainRow);

  // 헤더 "내 설정"(연동 설정 — 개인 API 키·정기 실행·외부 알림). 볼 수 없는 역할(viewer)에는 숨긴다.
  const mySettings = document.getElementById("my-settings-btn");
  if (mySettings) {
    const t = byId["integrations"];
    mySettings.hidden = !(t && t.roles.includes(ME.role));
    mySettings.onclick = () => selectTab("integrations");
  }

  // 설정 메뉴 한 줄(2026-11-25, 사용자 요청 "admin 메뉴가 아래로 넓게 펼쳐져 화면을 많이 차지" → A안) —
  // 예전엔 SETTINGS_GROUPS 그룹마다 한 줄씩(4줄) 그렸다. 이제 "설정" 한 줄에 그룹 버튼 4개를 두고, 누르면 그
  // 버튼 아래에 그 그룹의 화면 버튼이 작은 메뉴로 뜬다(바깥을 누르거나 Esc로 닫힘, 한 번에 하나만 열림).
  // 지금 보고 있는 화면이 속한 그룹 버튼에는 "그룹 · 화면 이름"이 보인다(`_syncNavDropdowns`).
  const settingsRow = document.createElement("div");
  settingsRow.className = "nav-group admin-group";
  const settingsTag = document.createElement("span");
  settingsTag.className = "nav-label";
  settingsTag.textContent = "설정";
  settingsRow.appendChild(settingsTag);
  for (const group of SETTINGS_GROUPS) {
    const items = [];
    for (const entry of group.entries) {
      if (entry === "__divider__") {
        if (items.length && items[items.length - 1] !== "__divider__") items.push("__divider__");
        continue;
      }
      const t = byId[entry];
      if (t && t.roles.includes(ME.role)) items.push(t);
    }
    while (items.length && items[items.length - 1] === "__divider__") items.pop();
    if (!items.length) continue;
    const wrap = makeDropdown(group.label, items);
    settingsRow.appendChild(wrap);
  }
  if (settingsRow.querySelector(".nav-dropdown")) nav.appendChild(settingsRow);
  _syncNavDropdowns();
}

function _closeNavDropdowns() {
  document.querySelectorAll(".nav-dropdown-menu").forEach(m => { m.hidden = true; });
  document.querySelectorAll(".nav-dropdown-trigger").forEach(b => b.setAttribute("aria-expanded", "false"));
}

// 지금 화면이 속한 그룹 버튼에 "그룹 · 화면 이름"을 보이고 강조한다(메뉴가 닫혀 있어도 위치를 알 수 있게).
function _syncNavDropdowns() {
  document.querySelectorAll(".nav-dropdown").forEach(wrap => {
    const trigger = wrap.querySelector(".nav-dropdown-trigger");
    const active = wrap.querySelector(".nav-dropdown-menu button.active");
    const name = active ? active.firstChild.textContent : "";
    trigger.textContent = wrap.dataset.groupLabel + (name ? " · " + name : "") + " ▾";
    trigger.classList.toggle("active", Boolean(active));
  });
}

document.addEventListener("click", (ev) => {
  if (!ev.target.closest(".nav-dropdown")) _closeNavDropdowns();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") _closeNavDropdowns();
});

let currentTab = null;

async function selectTab(id, opts) {
  // "경량 폴링" — 대화 화면을 벗어나면
  // 어느 쪽에서 열었든(독립 "대화" 탭/Task 워크스페이스 임베드) 반드시
  // 멈춘다(단일 전역 타이머라 여기 한 곳에서만 끊어도 충분하다).
  _stopConversationPolling();
  // 사용자 요청(2026-10-14, "모델설정 버튼 클릭시 웹 화면이 가장 위로
  // 자동이동하고 있어, 다른 버튼들도 모두 점검해줘") — 버그 원인: 거의
  // 모든 버튼 핸들러가 성공 후 `refresh()`(=같은 탭을 `selectTab()`으로
  // 다시 그림)를 부르는데, 이 함수가 매번 먼저 `content.innerHTML`을
  // 아주 짧은 "불러오는 중..." 문구로 비웠다 — 긴 목록 아래쪽 버튼을
  // 눌렀다가 스크롤이 남아있는 채로 내용이 한 줄짜리로 줄어들면 브라우저가
  // 스크롤 위치를 0에 가깝게 강제로 잘라낸다(실제 내용이 다시 채워져도
  // 스크롤은 복구되지 않는다). "다른 탭으로 전환"할 때는 맨 위에서
  // 시작하는 게 맞지만 "같은 탭을 제자리에서 새로고침"할 때는 아니다 —
  // `refresh()`만 `preserveScroll: true`로 불러 이 둘을 구분한다.
  const isTabSwitch = currentTab !== id;
  const scrollY = opts && opts.preserveScroll ? window.scrollY : 0;
  currentTab = id;
  for (const btn of document.querySelectorAll("#tabs button")) {
    btn.classList.toggle("active", btn.dataset.tab === id);
  }
  const mySettings = document.getElementById("my-settings-btn");
  if (mySettings) mySettings.classList.toggle("active", id === "integrations");
  _syncNavDropdowns();  // 설정 그룹 버튼에 지금 화면 표시(2026-11-25)
  // 다른 화면으로 옮길 때만 지운다(2026-11-27) — 저장 직후 `refresh()`가 "저장했습니다" 안내를 바로 지워 결과를 볼 수
  // 없었다(모델 레지스트리 화면 테스트에서 발견). 안내는 몇 초 뒤 스스로 사라진다(`showMsg`).
  if (isTabSwitch) showMsg("global-msg", "", "ok");
  const tab = TABS.find(t => t.id === id);
  let content = document.getElementById("content");
  if (isTabSwitch) {
    // 화면을 옮길 때는 본문 상자를 새것으로 바꾼다(2026-11-27). 화면 그리기는 서버 응답을 기다리는 비동기라, 앞 화면의
    // 응답이 늦게 오면 이미 옮겨 간 새 화면을 앞 화면 내용으로 덮어썼다(첫 화면을 홈으로 바꾼 뒤 화면 테스트에서 드러남 —
    // 홈을 그리는 중에 다른 메뉴를 누르면 그 메뉴 대신 홈이 보였다). 늦게 끝난 앞 화면은 떨어져 나간 옛 상자에만 쓴다.
    const fresh = content.cloneNode(false);
    fresh.innerHTML = "<p class=\"muted\">불러오는 중...</p>";
    content.replaceWith(fresh);
    content = fresh;
  }
  try {
    await tab.render(content);
  } catch (e) {
    if (content.isConnected) {  // 이미 다른 화면으로 옮겼으면 앞 화면의 오류는 보이지 않는다
      content.innerHTML = "";
      showMsg("global-msg", e.message, "error");
    }
  }
  if (opts && opts.preserveScroll) {
    window.scrollTo(0, scrollY);
  }
}

function refresh() { if (currentTab) selectTab(currentTab, { preserveScroll: true }); }

document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  showMsg("login-msg", "", "ok");
  try {
    const data = await api("POST", "/login", {
      username: document.getElementById("login-username").value,
      password: document.getElementById("login-password").value,
    });
    // 탭별 다계정 로그인 — 토큰을 이 탭의 sessionStorage에만 저장한다.
    sessionStorage.setItem("hair_token", data.token);
    await boot();
  } catch (e) {
    showMsg("login-msg", e.message, "error");
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await api("POST", "/logout");
  sessionStorage.removeItem("hair_token");
  await boot();
});


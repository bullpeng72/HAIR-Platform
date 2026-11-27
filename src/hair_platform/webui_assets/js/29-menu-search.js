/* ---------- 메뉴 찾기·이 화면 도움말(2026-11-26, P2-12 — "화면 수가 많고 배우기 어려움") ----------
   탭이 31개라 "그 기능이 어느 메뉴에 있는지"를 이용 가이드에서 찾아야 했다. 사용자 결정: 기존 메뉴 배치는 그대로 두고
   (1) 메뉴 찾기 — Ctrl+K(⌘K)나 상단 "🔍 메뉴 찾기"로 이름·하는 일·낱말로 화면과 이용 가이드 절을 찾아 바로 이동,
   (2) 이 화면 도움말 — 상단 "❓ 도움말"이 지금 화면에 맞는 이용 가이드 절을 연다.
   화면별 낱말·설명·도움말 위치는 아래 MENU_HINTS 한 곳에 둔다(도움말 위치가 가이드에 실제로 있는지는
   test_phase137이 확인한다 — 가이드 제목을 바꾸면 여기도 고친다). */

// help: "#id"(가이드 큰 절의 id) 또는 "### 제목"에 들어 있는 글(앞부분 일치로 찾는다).
const MENU_HINTS = {
  conversations: { help: "#chat", desc: "사람·Agent·팀과 대화하고 @멘션으로 일을 맡긴다", words: "채팅 멘션 질문 기본 AI 첨부 모델 설정 업무 요청으로 전환" },
  home: { help: "0단계 — 어디서 시작할지 모르겠다면", desc: "처리할 일 요약과 AI에게 일 맡기기 추천", words: "시작 추천 마법사 맡기기 관리 점검" },
  inbox: { help: "알림·비용·결재", desc: "결재 대기·실행 실패·파이프라인 실패·완료 알림을 한곳에서", words: "알림 실패 완료 낮은 점수 데스크톱 알림 멘션" },
  mywork: { help: "5단계 — 협업 워크스페이스에서 실행", desc: "내 할 일을 열어 Agent·팀·파이프라인을 실행하고 결과물을 다룬다", words: "할 일 실행 워크스페이스 파이프라인 실행 결과물 이어서 실행" },
  board: { help: "진행 한눈에 보기", desc: "할 일을 상태별 칸으로 본다", words: "칸반 상태 진행 현황" },
  gallery: { help: "7단계 — 결과물 활용", desc: "여러 할 일의 결과물을 모아 본다", words: "결과물 산출물 미리보기 다운로드 내보내기 Word Excel PowerPoint" },
  briefs: { help: "진행 한눈에 보기", desc: "업무 요청을 올리고 할 일로 나눈다", words: "브리프 요청 할 일 나누기 분해 배정" },
  approvals: { help: "4단계 — 결재 처리", desc: "배정·결과물·지식 등록 결재를 승인하거나 반려한다", words: "승인 반려 결재 대기 조건부 승인 결재 도우미 위험도 추천 이전 판 비교 위임 대결 대리 휴가 에스컬레이션" },
  performance: { help: "현황 화면", desc: "사람·Agent 성과와 Gate 점수 추이", words: "성과 점수 리뷰 평가" },
  search: { help: "3단계 — 사람·Agent·팀·파이프라인 붙이기", desc: "스킬·역할로 사람과 Agent를 찾는다", words: "인력 찾기 스킬 검색 Agent 목록" },
  orgchart: { help: "현황 화면", desc: "사람과 Agent가 함께 있는 조직도", words: "조직 팀 구성 매니저" },
  manual: { help: "#toc", desc: "이용 가이드 전체", words: "도움말 사용법 설명서 매뉴얼 가이드" },
  integrations: { help: "사람이 누르지 않아도 돌게 하기", desc: "API 키·정기 실행·이벤트 트리거·외부 입력 트리거·외부 알림·결재 후 실행 동작·저장소·사내 DB·메일·캘린더 연결", words: "API 키 정기 실행 스케줄 트리거 웹훅 Slack Teams 메일 알림 SMTP 저장소 Git 사내 DB 메일 수신함 캘린더 연동 외부 입력 인바운드 메일 도착 티켓 일정 실행 동작 메일 보내기 회신 승인하고 실행" },
  dashboard: { help: "현황 화면", desc: "팀 현황 모니터링", words: "현황 모니터링 진행률" },
  processmetrics: { help: "프로세스 성과", desc: "파이프라인별 완료까지 걸린 시간·한 번에 완료·자동 처리율·병목 단계", words: "리드타임 소요 시간 병목 재작업 자동화 효과 절감 기준 시간 KPI 성과 측정 흐름 배운 점 개선 제안 학습 반려 사유 변경 기록 되돌리기 전후 비교" },
  cost: { help: "AI 사용 한도", desc: "AI 사용량과 비용, 사용 한도", words: "비용 토큰 사용량 예산 한도 단가" },
  fairness: { help: "현황 화면", desc: "배정이 한쪽으로 쏠리지 않았는지", words: "공정성 쏠림 배정 분포" },
  audit: { help: "현황 화면", desc: "누가 무엇을 했는지 기록(조회·변경·실행)", words: "감사 로그 기록 이력 조회 기록" },
  match: { help: "3단계 — 사람·Agent·팀·파이프라인 붙이기", desc: "할 일에 사람·Agent·팀을 추천받아 배정", words: "배정 매칭 추천 리소스" },
  jobs: { help: "단일 Agent 만들기", desc: "새 AI 에이전트를 만들고 온보딩 승인을 받는다", words: "Agent 생성 만들기 역할 온보딩" },
  agentpool: { help: "AI 에이전트 관리", desc: "AI 에이전트의 스킬·도구·모델·파인튜닝 관리", words: "Agent 관리 스킬 도구 모델 파인튜닝 추론 모드 최적화 추천 비활성" },
  agentoptimize: { help: "기본 모델과 보조 작업 모델", desc: "Agent들의 모델 설정을 한꺼번에 바꾸고 플랫폼 공통 기본 LLM을 정한다", words: "LLM 설정 모델 일괄 변경 공통 기본 보조 작업 모델 임베딩" },
  resourceplanning: { help: "자원 운용", desc: "사람별 앞으로 4주 활용도·기한 지남·임박·인원 부족·역할별 수요와 공급", words: "활용도 가동률 업무량 일정 기한 마감 지연 인원 부족 수요 공급 여유 인력 벤치 히트맵 계획" },
  humanpool: { help: "사람 인력 관리", desc: "사람 인력 등록·스킬·용량·부재 관리", words: "사람 직원 인력 등록 스킬 용량 부하 부재 휴가 출장 가용 업무량 과부하" },
  poolregistry: { help: "Skill 관리", desc: "도구·스킬 등록과 지침 관리, MCP 도구", words: "도구 스킬 MCP 지침 등록 Tool Skill" },
  teams: { help: "방법 A — 팀(Team) 등록", desc: "사람·Agent로 팀을 만들고 매니저·실행 방식을 정한다", words: "팀 구성 매니저 위임 초기화 시 보호" },
  pipelines: { help: "새 파이프라인 만들기", desc: "단계별 파이프라인 템플릿을 만들고 고친다", words: "파이프라인 단계 템플릿 Gate 판정 되돌림 승인 단계 초안 업무 설명 자동 설계 AI 설계" },
  modelregistry: { help: "모델 레지스트리", desc: "모델 등록·단가·성능 테스트·실제 사용량", words: "모델 등록 Ollama 단가 성능 테스트 사용량" },
  knowledgebase: { help: "지식 베이스 운영", desc: "지식 베이스 자료 등록·검색·공개 범위", words: "지식 문서 RAG 벡터 검색 공개 범위 업로드" },
  crew: { help: "방법 B — Crew 빌더", desc: "등록 없이 Agent를 즉석으로 묶어 실행", words: "Crew 즉석 협업 조합" },
  admin: { help: "사용자·보안 관리", desc: "사용자 계정·유형·조직 정책·기본 구성 점검", words: "사용자 계정 권한 역할 비밀번호 조직 정책 사번 결과물 언어 기본 구성 점검" },
  systemstatus: { help: "시스템 상태", desc: "서버·DB·디스크·작업 큐·AI 모델 연결 상태를 한 화면에", words: "시스템 상태 서버 DB 디스크 용량 작업 큐 Ollama 연결 헬스 health 장애 점검" },
  evalconfig: { help: "평가 기준 설정", desc: "Gate별 통과 기준·응답 시간 SLA", words: "평가 기준 Gate 통과 기준 SLA 임계" },
};

const _CHOSUNG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
function _chosung(text) {
  let out = "";
  for (const ch of text) {
    const c = ch.charCodeAt(0) - 0xAC00;
    out += c >= 0 && c < 11172 ? _CHOSUNG[Math.floor(c / 588)] : ch;
  }
  return out;
}
function _norm(s) { return String(s || "").toLowerCase().replace(/\s+/g, ""); }

function _visibleTabs() {
  if (!ME) return [];
  return TABS.filter(t => t.roles.includes(ME.role));
}

// 이용 가이드의 절 제목(## id·###)도 찾기 대상 — "삭제가 안 될 때", "환경변수"처럼 문제 이름으로 찾을 수 있게.
function _guideEntries() {
  const out = [];
  for (const line of MANUAL_MARKDOWN.split("\n")) {
    const h2 = line.match(/^<h2 id="([^"]+)">(.+)<\/h2>/);
    if (h2 && h2[1] !== "toc") out.push({ kind: "guide", title: h2[2], target: "#" + h2[1] });
    const h3 = line.match(/^### (.+)/);
    if (h3) out.push({ kind: "guide", title: h3[1].replace(/`/g, ""), target: h3[1] });
  }
  return out;
}

function _menuSearchResults(query) {
  const q = _norm(query);
  const qc = /^[ㄱ-ㅎ]+$/.test(q) ? q : null;  // 초성만 입력
  const scored = [];
  for (const t of _visibleTabs()) {
    const h = MENU_HINTS[t.id] || {};
    const label = _norm(t.label);
    let score = 0;
    if (!q) score = 1;
    else if (label.startsWith(q)) score = 100;
    else if (label.includes(q)) score = 80;
    else if (qc && _chosung(label).includes(qc)) score = 70;
    else if (_norm(h.words).includes(q)) score = 50;
    else if (_norm(h.desc).includes(q)) score = 40;
    if (score) scored.push({ score, kind: "tab", id: t.id, title: t.label, desc: h.desc || "" });
  }
  if (q) {
    for (const g of _guideEntries()) {
      const title = _norm(g.title);
      let score = 0;
      if (title.includes(q)) score = 30;
      else if (qc && _chosung(title).includes(qc)) score = 20;
      if (score) scored.push({ score, kind: "guide", target: g.target, title: g.title, desc: "이용 가이드" });
    }
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, 12);
}

// 이용 가이드를 열고 해당 절로 스크롤한다. target: "#id" 또는 "### 제목"의 앞부분 글.
async function openManualAt(target) {
  await selectTab("manual");
  const root = document.querySelector("#content .manual-content");
  if (!root || !target) return;
  let el = null;
  if (target.startsWith("#")) el = root.querySelector(target);
  else {
    const want = _norm(target.replace(/`/g, ""));
    el = [...root.querySelectorAll("h2, h3")].find(h => _norm(h.textContent).startsWith(want)) ||
      [...root.querySelectorAll("h2, h3")].find(h => _norm(h.textContent).includes(want));
  }
  if (!el) return;
  _manualReveal(el);  // 절 하나씩 보기에서도 그 절을 열고 간다(2026-11-27)
}

function openHelpForCurrentScreen() {
  const h = MENU_HINTS[currentTab];
  openManualAt(h && h.help ? h.help : "#menu");
}

let _menuSearchIndex = 0;
function _renderMenuSearch() {
  const box = document.getElementById("menu-search-results");
  const input = document.getElementById("menu-search-input");
  if (!box || !input) return;
  const results = _menuSearchResults(input.value);
  if (_menuSearchIndex >= results.length) _menuSearchIndex = Math.max(0, results.length - 1);
  box.innerHTML = results.length
    ? results.map((r, i) =>
      '<li class="' + (i === _menuSearchIndex ? "active" : "") + '" data-i="' + i + '">' +
      '<span class="menu-search-kind">' + (r.kind === "tab" ? "화면" : "가이드") + "</span>" +
      "<b>" + esc(r.title) + "</b>" + (r.desc ? ' <span class="muted">' + esc(r.desc) + "</span>" : "") + "</li>"
    ).join("")
    : '<li class="muted">찾는 화면이 없습니다 — 다른 낱말(예: 비용, API 키, 결재, 환경변수)로 찾아보세요.</li>';
  box._results = results;
  box.querySelectorAll("li[data-i]").forEach(li => li.addEventListener("click", () => _chooseMenuSearch(Number(li.dataset.i))));
}

function _chooseMenuSearch(i) {
  const box = document.getElementById("menu-search-results");
  const r = box && box._results ? box._results[i] : null;
  if (!r) return;
  closeMenuSearch();
  if (r.kind === "tab") selectTab(r.id);
  else openManualAt(r.target);
}

function openMenuSearch() {
  if (!ME) return;
  let overlay = document.getElementById("menu-search");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "menu-search";
    overlay.innerHTML =
      '<div class="menu-search-panel" role="dialog" aria-label="메뉴 찾기">' +
      '<input id="menu-search-input" type="search" placeholder="화면 이름이나 하는 일(예: 비용, API 키, 결재, 삭제가 안 될 때)" autocomplete="off">' +
      '<ul id="menu-search-results"></ul>' +
      '<p class="muted menu-search-tip">↑↓로 고르고 Enter · Esc로 닫기 · 초성(예: ㅂㅇ)으로도 찾습니다</p></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", ev => { if (ev.target === overlay) closeMenuSearch(); });
    const input = overlay.querySelector("#menu-search-input");
    input.addEventListener("input", () => { _menuSearchIndex = 0; _renderMenuSearch(); });
    input.addEventListener("keydown", ev => {
      const box = document.getElementById("menu-search-results");
      const n = box && box._results ? box._results.length : 0;
      if (ev.key === "ArrowDown") { ev.preventDefault(); _menuSearchIndex = n ? (_menuSearchIndex + 1) % n : 0; _renderMenuSearch(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); _menuSearchIndex = n ? (_menuSearchIndex - 1 + n) % n : 0; _renderMenuSearch(); }
      else if (ev.key === "Enter") { ev.preventDefault(); _chooseMenuSearch(_menuSearchIndex); }
      else if (ev.key === "Escape") { ev.preventDefault(); closeMenuSearch(); }
    });
  }
  overlay.hidden = false;
  _menuSearchIndex = 0;
  const input = document.getElementById("menu-search-input");
  input.value = "";
  _renderMenuSearch();
  input.focus();
}

function closeMenuSearch() {
  const overlay = document.getElementById("menu-search");
  if (overlay) overlay.hidden = true;
}

document.addEventListener("keydown", ev => {
  if ((ev.ctrlKey || ev.metaKey) && (ev.key === "k" || ev.key === "K")) {
    if (!ME) return;
    ev.preventDefault();
    const overlay = document.getElementById("menu-search");
    if (overlay && !overlay.hidden) closeMenuSearch(); else openMenuSearch();
  }
});
document.getElementById("menu-search-btn").addEventListener("click", openMenuSearch);
document.getElementById("screen-help-btn").addEventListener("click", openHelpForCurrentScreen);

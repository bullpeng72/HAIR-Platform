"use strict";

function esc(s) {
  const d = document.createElement("div");
  d.textContent = (s === null || s === undefined) ? "" : String(s);
  return d.innerHTML;
}

// esc()는 엘리먼트 콘텐츠로 쓰기엔 안전하지만 따옴표(")는 안 바꿔준다
// (텍스트 노드엔 필요 없어서) — HTML 속성값(예: iframe srcdoc="...")에
// 그대로 넣으면 따옴표에서 값이 끊길 수 있어 별도로 이스케이프한다.
// 빈 목록의 "홈에서 AI에게 일 맡기기"(2026-11-28) — viewer의 홈에는 그 입력창이 없어 막다른 길이었다.
function _homeWizardLink() {
  return (typeof ME !== "undefined" && ME && ME.role === "viewer") ? ""
    : '<button class="small" data-go-tab="home">홈에서 AI에게 일 맡기기</button>';
}

function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;");
}

// 사용자 보고(2026-11-17, "대화에 '... 2026-11-17 08:39:05'라고
// 시간정보가 표시되지만, 실제 시간은 17:39:05 — 한국시간 기준이
// 아님") — 모든 `*_at` 타임스탬프는 SQLite `datetime('now')`로
// 저장되는데, 이 함수는 **UTC**를 "YYYY-MM-DD HH:MM:SS" 형태(타임존
// 표시 없이)로 반환한다(직접 확인: 같은 순간에 KST는 그보다 9시간
// 빠르다). 지금까지 화면에는 이 문자열을 변환 없이 그대로 보여줬다
// — 저장 형식(UTC, 올바른 관행)은 그대로 두고, 보여줄 때만 'Z'를
// 붙여 Date가 UTC로 해석하게 한 뒤 **보는 사람의 브라우저 로컬
// 타임존**으로 다시 포맷한다(한국에서 열면 자동으로 KST로 보정되고,
// 다른 타임존에서 열어도 그 타임존에 맞게 자동으로 보정된다 — 서버가
// 어디서 돌든, 보는 사람이 어디에 있든 하드코딩 없이 맞는 방식).
function fmtTime(raw) {
  if (!raw) return "";
  const d = new Date(String(raw).replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return String(raw);  // 파싱 실패 시 원본 그대로(안전장치)
  const pad = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
    pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}

// 복사·전체화면·다이어그램 id 발급용 — renderContent()와
// _activateRichBlocks() 양쪽이 공유한다(모두 아래에서 쓴다).
let _artifactSeq = 0;

// 사용자 요청(2026-10-14, "마크다운 처리 및 다이어그램, 차트, HTML
// 렌더링, 코드 표시가 모두 렌더링되어 미려하게 보이도록") — 손수 짠
// 정규식 렌더러(표·블록인용·다이어그램·차트를 의도적으로 지원 범위
// 밖에 뒀던 버전, 아래 stripOuterFence() 위 주석이 그 경위)를 걷어내고
// marked(마크다운 파서)+highlight.js(코드 하이라이팅)+DOMPurify(살균)
// +mermaid(다이어그램)+Chart.js(차트)로 교체한다(사용자 확인, "CDN에서
// 불러오기"). 다만 CDN을 못 불러오는 환경(폐쇄망 등)에서도 **앱 전체가
// 깨지면 안 된다** — 그 경우 리치 렌더링만 평문 폴백으로 내려가고 나머지
// 기능은 그대로 동작해야 한다고 사용자에게 안내했으므로, 아래 모든
// 라이브러리 접근은 `typeof X === "undefined"` 가드를 거친다.
if (typeof marked !== "undefined") {
  // marked는 코드펜스 언어가 "mermaid"/"chart"면 일반 코드블록 대신
  // 전용 플레이스홀더를 내보내도록 커스텀 렌더러를 쓴다 — 실제 그리기는
  // DOM에 붙은 뒤에만 가능해서(마크다운 문자열 생성 시점엔 아직 그릴
  // 대상이 없음) 아래 MutationObserver가 비동기로 처리한다.
  // 사용자 보고(2026-10-14, "대화를 열려고 하니 '불러오는 중…'로
  // 표시되면 열리지 않아") — 실측·재현으로 확인한 원인: marked v5+
  // (여기선 v13.0.3)는 커스텀 렌더러의 code()에 `(code, infostring)`
  // 위치 인자가 아니라 토큰 **객체**(`{type, raw, lang, text}`)를
  // 하나만 넘긴다. 객체를 문자열로 착각해 hljs.highlight()에 그대로
  // 넘기면 "e.replace is not a function"으로 marked.parse() 자체가
  // 예외를 던지고, 그 예외가 renderContent()→
  // _renderConversationMessage()→renderConversationDetail()까지
  // 그대로 전파돼 `detail.innerHTML`을 끝내 "불러오는 중…"에서 못
  // 벗어났다(실제 marked@13.0.3 소스로 재현·확인). `{ text, lang }`로
  // 구조분해해야 한다.
  const _markedRenderer = new marked.Renderer();
  _markedRenderer.code = function (token) {
    const code = token.text;
    const lang = (token.lang || "").trim().split(/\s+/)[0].toLowerCase();
    if (lang === "mermaid") {
      return '<div class="mermaid-block" data-mermaid-src="' + escAttr(code) + '"><p class="muted">다이어그램 렌더링 중…</p></div>';
    }
    if (lang === "chart") {
      return '<div class="chart-block"><canvas data-chart-config="' + escAttr(code) + '"></canvas></div>';
    }
    return '<pre class="code-block">' + _highlightCode(code, lang) + "</pre>";
  };
  marked.setOptions({ renderer: _markedRenderer, gfm: true, breaks: true });
}
if (typeof mermaid !== "undefined") {
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
}

// highlight.js 호출 지점을 하나로 모은다 — "code" content_type과 marked
// 코드펜스 렌더러 양쪽이 공유한다. 언어를 모르면(또는 지원 안 하면)
// highlightAuto로 추측한다 — content_type='code'엔 언어 필드가 없다는
// 기존 설계(스키마 변경 없이 가는 쪽을 택함)를 그대로 유지한다.
// 사용자 보고(2026-10-14, "대화를 열려고 하니 '불러오는 중…'") 재발
// 방지 — 라이브러리 버전 차이·예상 밖 입력으로 hljs가 던지더라도
// (실제로 marked 렌더러 인자 버그가 바로 이 경로에서 예외를 던져
// 대화 화면 전체를 못 열게 만든 전례가 있다) 호출부(renderContent 등
// 여러 화면)까지 예외가 전파되지 않도록 여기서 막는다 — 실패하면
// 하이라이팅만 포기하고 이스케이프된 평문으로 폴백한다.
function _highlightCode(raw, lang) {
  if (typeof hljs === "undefined") {
    return "<code>" + esc(raw) + "</code>"; // CDN 미로드 폴백 — 최소한 안전하게는 보여준다.
  }
  try {
    const known = lang && hljs.getLanguage(lang) ? lang : null;
    const result = known ? hljs.highlight(raw, { language: known }) : hljs.highlightAuto(raw);
    return '<code class="hljs language-' + (known || result.language || "plaintext") + '">' + result.value + "</code>";
  } catch (e) {
    console.error("hljs highlighting failed, falling back to plain text:", e);
    return "<code>" + esc(raw) + "</code>";
  }
}

// 보안: marked는 입력 마크다운에 섞인 원문 HTML(인라인/블록)을 그대로
// 통과시킨다 — Agent/LLM 산출물처럼 신뢰할 수 없는 텍스트이므로, 매번
// DOMPurify.sanitize()를 거친 결과만 innerHTML에 넣는다(<script>/on*
// 이벤트 핸들러/javascript: 링크 등을 전부 제거) — 기존의 "esc() 먼저,
// 그 뒤엔 신뢰한 태그만 추가"라는 안전장치를, 더 검증된 라이브러리
// 기반 살균으로 교체한 것뿐 안전 수준은 유지(오히려 표·인용문·이미지
// 같은 더 넓은 HTML 표면을 다루면서도 DOMPurify가 전부 검사한다).
// 사용자 보고(2026-11-16, "**굵게** 마크다운이 생성 결과물에서 동작
// 안 하는 버그") — 직접 재현해보니 두 가지 서로 다른 경계 조건에서
// marked.js(13.0.3은 물론 2026-11-16 시점 최신 18.2.0에서도 재현 —
// 업스트림 라이브러리 자체의 한계, 버전을 올려도 안 고쳐짐)가 강조
// 판정 자체를 포기하고 `**`를 리터럴 텍스트로 그대로 남겨버린다:
// (1) `**`의 안쪽이 따옴표 등 구두점이고 바깥쪽이 공백 없이 한글/
// 한자와 바로 붙어 있는 경우(예: `핵심과제를 **"신뢰 및 통제 1위"**로`),
// (2) `**` 안쪽에 공백이 남아 있는 경우(예: `**심도 조절 **: ...` —
// 사용자가 실제로 겪은 두 번째 사례, CommonMark 명세상 원래 강조로
// 안 쳐주는 게 맞지만 LLM이 흔히 실수로 만들어내는 패턴). marked.parse()
// 전에 `**...**` 쌍 하나하나를 직접 찾아 안쪽 공백을 지우고, 그 경계가
// (1)의 조건에 해당할 때만 공백 하나를 끼워 넣어 우회한다 — 이미
// 정상이던 케이스는 두 조건 모두 해당 안 돼 전혀 안 건드린다(직접
// 여러 케이스로 node에서 회귀 없음 확인).
//
// 코드펜스(```...```) 안쪽은 절대 건드리지 않는다 — 처음엔 안쪽/바깥쪽
// 전체를 한 정규식으로 훑었더니, 파이썬 `def f(**kwargs):` 같은 코드
// 예시의 짝 없는 `**`가 그 블록 밖 멀리 떨어진 다른 `**강조**`와 잘못
// 짝지어지며 그 사이 텍스트를 통째로 삼켜버리는 회귀를 직접 재현해
// 확인했다 — 코드펜스 구간은 분리해 건드리지 않는 것으로 막는다.
const _CJK_CHAR_RANGE = "\u3131-\u318E\uAC00-\uD7A3\u4E00-\u9FFF\u3400-\u4DBF";
const _BOLD_BOUNDARY_PUNCT = "\"'\u201C\u201D\u2018\u2019().,!?:;\u2026\u00B7";
const _CJK_RE = new RegExp("[" + _CJK_CHAR_RANGE + "]");
const _BOLD_BOUNDARY_PUNCT_RE = new RegExp("[" + _BOLD_BOUNDARY_PUNCT + "]");

function _fixBoldMarkdownSegment(raw) {
  return raw.replace(
    /([\s\S]?)\*\*(\s*)([\s\S]*?)(\s*)\*\*([\s\S]?)/g,
    (m, before, lead, inner, trail, after) => {
      if (!inner.trim()) return m; // "**" 쌍 사이가 통째로 공백뿐이면 강조가 아닐 가능성 — 손대지 않음
      const trimmedInner = inner.trim();
      let openSep = "";
      if (before && _CJK_RE.test(before) && _BOLD_BOUNDARY_PUNCT_RE.test(trimmedInner[0])) openSep = " ";
      let closeSep = "";
      if (_BOLD_BOUNDARY_PUNCT_RE.test(trimmedInner[trimmedInner.length - 1]) && after && _CJK_RE.test(after)) closeSep = " ";
      return before + openSep + "**" + trimmedInner + "**" + closeSep + after;
    }
  );
}
function _fixBoldMarkdown(raw) {
  const parts = raw.split(/(```[\s\S]*?```)/);
  return parts.map((part, i) => (i % 2 === 1 ? part : _fixBoldMarkdownSegment(part))).join("");
}

// 쉬운 말 우선(2026-11-27, 사용성 평가 4번) — 화면에는 내부 용어 대신 뜻을 먼저 보인다. "Gate A-G"는 결과물 품질 점검
// 7가지(평가 도구 Agent-Evaluator의 식별자)라 사용자 화면에서는 "품질 점검"과 아래 이름으로 보이고, 글자는 툴팁에만 둔다.
// 공식 이름(성능 계약·관측성 등)은 관리자가 기준을 정하는 "평가 기준" 화면(`_GATE_NAMES`)에 그대로 둔다.
const GATE_SHORT_NAMES = { A: "목표 달성", B: "행동 무결성", C: "신뢰성", D: "응답 시간", E: "보안", F: "협업", G: "근거 제시" };
const ROLE_NAMES = { admin: "관리자", manager: "매니저", member: "구성원", viewer: "열람자" };

// 화면 이동 링크(2026-11-27, 사용성 평가 3번) — 빈 상태 안내 등에서 `<button data-go-tab="home">`으로 다른 화면을 연다.
document.addEventListener("click", (ev) => {
  const el = ev.target.closest && ev.target.closest("[data-go-tab]");
  if (!el || typeof selectTab !== "function") return;
  ev.preventDefault();
  selectTab(el.getAttribute("data-go-tab"));
});

function _renderMarkdown(raw) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return "<p>" + esc(raw).replace(/\n/g, "<br>") + "</p>"; // CDN 미로드 폴백
  }
  // marked.parse()가 던지면(라이브러리 버전 변경·예상 밖 입력 등) 이
  // 함수를 호출한 renderContent()/_renderConversationMessage()/
  // renderConversationDetail() 전체로 예외가 전파돼 화면이 "불러오는
  // 중…"에서 멈춘 전례가 실제로 있었다 — 여기서 잡아 평문 폴백으로
  // 내려가고, 화면 자체는 계속 열리게 한다.
  try {
    // 사용자 보고(2026-11-19, 스크린샷 — "AI 에이전트 관리" 목록만 폰트·
    // 글자색이 다른 메뉴 테이블과 다름) — 원인: Agent "최근 산출물"이
    // `<style>`(`* {margin:0;padding:0}`, `html, body {font-family…}` 등)을
    // 담은 HTML 문서였는데, 마크다운 경로는 iframe이 아니라 앱 DOM에
    // 직접 들어가므로 그 전역 스타일이 페이지 전체로 새어 나갔다.
    // 마크다운 미리보기에서 `<style>`은 절대 앱 전체에 적용되면 안 되므로
    // 여기서 일괄 제거한다(HTML 결과물은 `contentType === "html"`의
    // 샌드박스 iframe 경로로 그대로 보인다).
    // 화면 일관성(2026-11-27, 개선 계획 4-4) — 마크다운 표도 앱의 다른 표처럼 `.table-wrap` 안에 둔다. 좁은 화면에서 표가
    // 페이지 전체를 가로로 밀어내지 않고(실측: 이용 가이드가 폰 폭에서 2,282px 넘침) 표만 옆으로 스크롤된다.
    return DOMPurify.sanitize(marked.parse(_fixBoldMarkdown(raw)), { FORBID_TAGS: ["style"] })
      .replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, "</table></div>");
  } catch (e) {
    console.error("markdown rendering failed, falling back to plain text:", e);
    return "<p>" + esc(raw).replace(/\n/g, "<br>") + "</p>";
  }
}

// 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 1/2 실행 중) — Agent/사람
// 산출물에 마크다운 코드펜스(```)나 **굵게**가 섞여 있어도 전부 이스케이프
// 후 줄바꿈만 <br>로 바꿔 보여줘, 백틱·별표가 그대로 노출돼 가독성이
// 떨어졌다(코드 패치 설명 같은 산출물일수록 심함).
//
// html/markdown 둘 다 LLM이 응답 전체를 ```html.../```markdown...```
// 한 겹으로 또 감싸는 경우가 흔하다(실측, 2026-10-07, 실제 gpt-4o-mini로
// 시나리오를 돌려보다 발견 — 리뷰 서버의 손수 만든 가짜 응답으로는 한
// 번도 안 드러났다). 응답 "전체"가 코드 펜스 하나로만 감싸여 있을 때만
// 그 바깥 펜스를 벗겨내고 안쪽 내용으로 렌더링한다 — 본문 중간에 섞인
// 펜스(예: 설명+코드 혼합 응답)는 그대로 둔다(그건 의도된 "그 형식을
// 먼저 보여주는" 문서지, 벗겨낼 "감싸기"가 아니다).
function stripOuterFence(raw) {
  const closed = raw.match(/^```[a-zA-Z0-9]*\n([\s\S]*?)\n?```\s*$/);
  if (closed) return closed[1];
  // 사용자 보고(2026-11-13, "결과물이 끝까지 생성되지 않고 잘리는 문제...
  // HTML 형태로 표시되지 않는 문제") — 응답이 중간에 잘리면(로컬 모델이
  // 문서를 다 쓰기 전에 멈추면) 여는 펜스(```html)만 있고 닫는 펜스가
  // 없다. 위 정규식은 닫는 펜스까지 있어야만 매칭되므로, 잘린 응답은
  // "```html\n<!DOCTYPE html>..."가 안 벗겨진 채 그대로 반환돼
  // `_looksLikeFullHtmlDocument()`의 DOCTYPE 검사도 통과하지 못하고
  // 평범한 코드블록으로 떨어졌다. 닫는 펜스가 없어도 여는 펜스 한 줄만
  // 떼어내고 나머지(잘린 내용 그대로)는 살린다 — 완성본이든 잘린
  // 본이든 최소한 HTML로는 보이게 한다.
  const openOnly = raw.match(/^```[a-zA-Z0-9]*\n([\s\S]*)$/);
  return openOnly ? openOnly[1] : raw;
}

// 사용자 요청(2026-10-14) — 다이어그램/차트는 DOM에 실제로 붙은 뒤에만
// 그릴 수 있는데, renderContent()는 여러 화면(Task 워크스페이스/Crew
// 실행 결과/대화/Agent 관리 등)에서 호출되는 순수 문자열 생성 함수라
// "언제 DOM에 붙는지"를 알 방법이 없다 — 매 호출부마다 "다 그렸으면
// 그려라" 코드를 심는 대신, 복사/전체화면 버튼과 같은 원칙(한 번만
// 전역 연결)으로 MutationObserver 하나만 걸어 새로 추가되는 노드를
// 감시한다. mermaid는 렌더가 끝나야 `data-mermaid-rendering`을 지우지
// 않고 그대로 둔다 — "전체화면" 오버레이가 아직 렌더 중인 블록을
// innerHTML로 복제해가면(드문 경쟁 상태) 그 복제본은 아직 렌더
// 전이므로 옵저버가 다시 잡아 독립적으로 렌더해준다.
// 사용자 보고(2026-10-18, "Syntax error in text / mermaid version 10.9.1"가
// 대화에 반복 노출됨) 원인 분석 — mermaid.js v10은 다이어그램 문법이
// 깨지면 `render()`의 Promise를 **reject하지 않는다**. 대신 내부
// "error diagram" 렌더러가 "Syntax error in text"+버전 문구를 큼직하게
// 그린 SVG를 **resolve**로 돌려준다(실측: mermaid.min.js 안에
// `.text("Syntax error in text")` 렌더러가 그대로 있음) — 그래서 아래
// `.catch()`가 한 번도 안 불리고, 그 못생긴 에러 그림이 `svg` 그대로
// 들어갔다. 메시지 목록을 다시 그릴 때마다(새 메시지 전송 등) 이
// DOM 노드가 통째로 재생성돼 같은 실패를 매번 새로 반복한 것도
// 그래서다. `mermaid.render()` 전에 `mermaid.parse(src, {suppressErrors:
// true})`로 먼저 검사해 — 이 함수는 실패해도 그 보기 흉한 SVG를 만들지
// 않고 그냥 `false`만 돌려준다 — 실패 시 우리가 직접 깔끔한 안내 +
// 원본 소스를 보여준다.
function _mermaidFallbackHtml(src) {
  return '<p class="muted">⚠ 다이어그램 문법 오류로 그릴 수 없습니다 — 원본 코드:</p>' +
    '<pre style="white-space:pre-wrap;text-align:left">' + esc(src) + "</pre>";
}

// 사용자 보고(2026-10-18, "대화 내용에 머메이드 그래프가 보이지 않아")
// 재현으로 확인한 원인 — `root.querySelectorAll(selector)`는 root
// 자신은 절대 대상에 포함하지 않고 **후손만** 검사한다(DOM 표준
// 동작). `detail.innerHTML = ...`로 메시지 목록을 한 번에 갈아끼우면
// MutationObserver의 addedNodes가 그 최상위 형제 노드들을 하나씩
// 넘겨주는데, 마크다운 코드펜스가 문단에 안 감싸인 채 메시지 바로
// 아래 최상위 블록으로 나오면(ADR처럼 코드펜스가 통째로 한 줄만 있는
// 경우) `.mermaid-block` div 자체가 그 "최상위로 추가된 노드"가 된다
// — 그러면 `root`가 바로 그 블록이라 `root.querySelectorAll(...)`가
// 후손에서만 찾느라 자기 자신을 영영 못 찾아 "다이어그램 렌더링
// 중…" 플레이스홀더에 멈춰 있었다(실제 마크다운+DOMPurify+mermaid
// CDN 조합으로 재현해 확인). root 자신이 선택자와 일치하는 경우도
// 포함해야 한다.
function _selfAndDescendants(root, selector) {
  const out = root.matches && root.matches(selector) ? [root] : [];
  return out.concat(Array.from(root.querySelectorAll(selector)));
}
function _activateRichBlocks(root) {
  _selfAndDescendants(root, ".mermaid-block[data-mermaid-src]").forEach(el => {
    if (el.dataset.mermaidRendering === "1") return;
    if (typeof mermaid === "undefined") {
      el.innerHTML = '<p class="muted">다이어그램 라이브러리를 불러오지 못했습니다.</p>';
      return;
    }
    el.dataset.mermaidRendering = "1";
    const src = el.dataset.mermaidSrc;
    const id = "mmd-" + (++_artifactSeq);
    Promise.resolve(mermaid.parse(src, { suppressErrors: true }))
      .then(ok => {
        if (!ok) {
          el.innerHTML = _mermaidFallbackHtml(src);
          return;
        }
        return mermaid.render(id, src).then(({ svg }) => { el.innerHTML = svg; });
      })
      .catch(e => {
        // parse()가 suppressErrors에도 예상 밖으로 던지거나, render()
        // 자체가 실패하는 그 외의 경우(예: 라이브러리 내부 런타임 오류)
        // 를 위한 마지막 방어선 — 이 경로는 실제로는 거의 안 탄다.
        el.innerHTML = _mermaidFallbackHtml(src) +
          '<p class="muted">(' + esc(e.message || String(e)) + ")</p>";
      });
  });
  // Chart.js는 <canvas> DOM 노드 자체에 차트 인스턴스를 붙인다 — innerHTML
  // 복제(전체화면 오버레이)는 마크업만 복사하고 그 붙은 상태는 복사하지
  // 않으므로, data-* 플래그가 아니라 `Chart.getChart(canvas)`로 "이
  // 정확한 노드에 이미 차트가 있는지"를 확인해야 복제본도 올바르게
  // 다시 그려진다.
  _selfAndDescendants(root, "canvas[data-chart-config]").forEach(canvas => {
    if (typeof Chart === "undefined" || Chart.getChart(canvas)) return;
    try {
      new Chart(canvas, _chartConfigFromSpec(JSON.parse(canvas.dataset.chartConfig)));
    } catch (e) {
      canvas.insertAdjacentHTML("afterend", '<p class="muted">차트 설정을 해석하지 못했습니다: ' + esc(e.message) + "</p>");
    }
  });
}
// ```chart 간단 형식(2026-11-21, "데이터 시각화 스킬을 에이전트에 적극 활용") —
// {"type","title","labels","series":{"이름":[…]},"unit"}을 Chart.js 설정으로 바꾼다.
// Chart.js 전체 설정({type, data, …} — '차트 생성기' 도구 결과)은 그대로 쓴다. 모델이
// 도구 없이 직접 써도 틀리기 어려운 형식이라 대화·업무 실행 경로에서도 차트가 나온다.
const _CHART_PALETTE = ["#3b5bdb", "#868e96", "#20c997", "#f59f00", "#e64980", "#7048e8"];
function _chartConfigFromSpec(spec) {
  if (!spec || typeof spec !== "object") throw new Error("JSON 객체가 아닙니다");
  if (spec.data && spec.type) {
    // 전체 설정('차트 생성기' 결과)에 색이 없으면 플랫폼 팔레트를 채운다(기본 회색·연파랑 대신).
    const rd = spec.type === "pie" || spec.type === "doughnut";
    (spec.data.datasets || []).forEach((d, i) => {
      if (!d.backgroundColor) {
        d.backgroundColor = rd
          ? (spec.data.labels || []).map((_, j) => _CHART_PALETTE[j % _CHART_PALETTE.length])
          : _CHART_PALETTE[i % _CHART_PALETTE.length];
      }
      if (!d.borderColor) d.borderColor = rd ? "#fff" : _CHART_PALETTE[i % _CHART_PALETTE.length];
    });
    return spec;
  }
  const type = spec.type || spec.chart_type || "bar";
  const labels = Array.isArray(spec.labels) ? spec.labels : [];
  const series = spec.series && typeof spec.series === "object" ? spec.series : {};
  const names = Object.keys(series);
  if (!labels.length || !names.length) throw new Error("labels와 series가 필요합니다");
  const round = type === "pie" || type === "doughnut";
  const datasets = names.map((name, i) => {
    const color = _CHART_PALETTE[i % _CHART_PALETTE.length];
    return {
      label: name, data: series[name], borderWidth: 2, tension: 0.25, pointRadius: 3, fill: false,
      backgroundColor: round ? labels.map((_, j) => _CHART_PALETTE[j % _CHART_PALETTE.length]) : color,
      borderColor: round ? "#fff" : color,
    };
  });
  return {
    type, data: { labels, datasets },
    options: {
      responsive: true,
      // 도넛·파이 기본 비율은 1:1이라 넓은 본문에서 화면을 가득 채웠다 — 막대·선과 같은 2:1로.
      aspectRatio: 2,
      plugins: {
        legend: { display: round || names.length > 1 },
        title: { display: !!spec.title, text: spec.title },
      },
      scales: round ? {} : { y: { beginAtZero: true, title: { display: !!spec.unit, text: spec.unit } } },
    },
  };
}
new MutationObserver(mutations => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (node.nodeType === 1) {
        _activateRichBlocks(node);
        _queueKnowledgeButtons(node);
      }
    }
  }
}).observe(document.body, { childList: true, subtree: true });

// 복사 버튼/미리보기·소스 토글(2026-10-12, "Phase 1 (A)(B)(H1)(H2)(I5)
// 부터 구현 시작해줘") — 매 호출마다
// 고유 id를 줘서, 렌더링된 위치가 어디든(Task 워크스페이스/Crew 실행
// 결과/대화 등 renderContent() 호출부 전부) 버튼이 자기 블록만 찾게 한다.
// 콜러마다 버튼 이벤트를 따로 연결할 필요가 없도록 전역 위임
// 리스너(`document.addEventListener("click", ...)`, 아래)로 한 번만
// 연결한다.
// 사용자 요청(2026-10-14, "결과 파일을 복사 및 다운로드 가능하도록") —
// content_type별로 받는 파일 확장자/MIME. 기본(타입 미지정)도 마크다운
// 으로 렌더링하므로 .md로 묶는다.
const _DOWNLOAD_EXT = { html: "html", code: "txt", markdown: "md" };
const _DOWNLOAD_MIME = { html: "text/html", code: "text/plain", md: "text/plain", txt: "text/plain" };

// 사용자 요청(2026-11-05, 로드맵 8번 "Crew 빌더 diff 모드") — 로드러너
// 데모에서 Crew 재시도마다 기존 기능이 조용히 퇴행해도 매번 전체
// 산출물을 사람이 다시 읽어 수동 비교해야 했다. jsdiff(CDN, <head>
// 참고)의 Diff.diffLines()로 줄 단위 추가/삭제를 시각화한다. CDN 로드
// 실패 시(이 플랫폼의 기존 CDN 라이브러리 전부와 같은 원칙) diff 없이
// 두 산출물을 그대로 나열해 보여주는 것으로 대체 — 나머지 기능은
// 전혀 영향받지 않는다.
function renderTextDiff(oldText, newText) {
  if (typeof Diff === "undefined") {
    return '<p class="muted">diff 라이브러리를 불러오지 못했습니다 — 두 산출물을 그대로 비교하세요.</p>' +
      '<p><b>이전 실행</b></p><pre class="code-block">' + esc(oldText || "(없음)") + '</pre>' +
      '<p><b>이번 실행</b></p><pre class="code-block">' + esc(newText || "(없음)") + '</pre>';
  }
  const parts = Diff.diffLines(oldText || "", newText || "");
  const html = parts.map(p =>
    '<span class="' + (p.added ? "diff-added" : p.removed ? "diff-removed" : "diff-unchanged") +
    '">' + esc(p.value) + '</span>'
  ).join("");
  return '<pre class="code-block diff-block">' + html + '</pre>';
}

// 사용자 요청(2026-11-16, "HTML 문서 생성시 프레젠테이션 발표에
// 적합한 슬라이드 size와 비율로 생성되고, 전체화면 등에서 slide show
// 기능이 제공되도록") — LLM이 매번 스스로 만들어 넣게 기대하는 대신
// (이 세션에서 반복 확인된 "일관성 없음" 문제와 같은 성격 — 색 대비/
// 굵게 마크다운 등), `.slide` 클래스 섹션이 2개 이상이면(프레젠테이션
// 자료 작성자 Agent의 expected_output이 이미 그렇게 구조화하도록
// 지시해둠 — 16:9 비율도 그쪽에서 지시) 플랫폼이 항상 같은 이전/다음·
// 슬라이드 번호·전체화면 컨트롤을 직접 주입한다. 저장된 원본 HTML은
// 절대 안 건드린다 — 이 함수는 "보여줄 때"(미리보기 iframe·전체화면
// 새 탭·단독 뷰 페이지)만 거치고, 복사/다운로드/수정 화면이 쓰는
// 원본 텍스트(`effectiveRaw`)는 그대로다.
function _injectSlideshowController(html) {
  const slideCount = (html.match(/class=["'][^"']*\bslide\b[^"']*["']/g) || []).length;
  if (slideCount < 2) return html;
  const controller =
    // 사용자 보고(2026-11-16, "슬라이드 크기가 변화지 않아 상단
    // 중앙에 작게 표시됨") — 원인 확인: 이 결과물은 16:9/반응형
    // 크기 지시가 생기기 *전에* 만들어진 구형 산출물이라, `.slide`에
    // `width` 지정이 아예 없고 `margin:0 auto`(가운데 정렬)·
    // `min-height:640px`(고정값)만 있었다 — 화면 크기와 무관하게
    // 늘 그 작은 고정 크기로만 보였다. 새로 생성되는 산출물은 Agent
    // 지시에 `width:100%;aspect-ratio:16/9`가 포함돼 있어 나아지긴
    // 하겠지만, 그것도 LLM이 지시를 정확히 따랐을 때만 보장된다 —
    // 이 세션 내내 반복 확인된 "LLM 지시 준수는 보장이 아니다"라는
    // 원칙대로, 산출물이 신형이든 구형이든 **항상** 전체 화면을
    // 채우도록 플랫폼이 강제로 덮어쓴다(내용의 색·폰트·여백 등은
    // 그대로 두고 크기/위치만 관여).
    '<style id="__hair_slideshow_style">' +
    'body.__hair_slideshow .slide{display:none!important}' +
    'body.__hair_slideshow .slide.__hair_active{display:flex!important;flex-direction:column;' +
    'position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;' +
    'max-width:none!important;max-height:none!important;min-width:0!important;min-height:0!important;' +
    'margin:0!important;box-sizing:border-box!important;overflow:auto!important}' +
    '#__hair_slideshow_bar{position:fixed;left:0;right:0;bottom:0;z-index:999999;display:flex;' +
    'align-items:center;justify-content:center;gap:0.5rem;padding:0.5rem;' +
    'background:rgba(15,23,42,0.75);font-family:sans-serif}' +
    '#__hair_slideshow_bar button{background:#fff;border:none;border-radius:4px;' +
    'padding:0.3rem 0.7rem;cursor:pointer;font-size:0.85rem}' +
    '#__hair_slideshow_bar span{color:#fff;font-size:0.85rem;min-width:3.5rem;text-align:center}' +
    '</style>' +
    '<div id="__hair_slideshow_bar">' +
    '<button id="__hair_prev" type="button">이전</button>' +
    '<span id="__hair_counter"></span>' +
    '<button id="__hair_next" type="button">다음</button>' +
    '<button id="__hair_fullscreen" type="button">슬라이드 재생</button>' +
    '</div>' +
    '<script>(function(){' +
    'var slides=Array.prototype.slice.call(document.querySelectorAll(".slide"));' +
    'if(slides.length<2)return;' +
    'document.body.classList.add("__hair_slideshow");' +
    'var idx=0;' +
    'function show(i){' +
    'idx=Math.max(0,Math.min(slides.length-1,i));' +
    'slides.forEach(function(s,j){s.classList.toggle("__hair_active",j===idx)});' +
    'var c=document.getElementById("__hair_counter");' +
    'if(c)c.textContent=(idx+1)+" / "+slides.length;' +
    '}' +
    'show(0);' +
    'var prevBtn=document.getElementById("__hair_prev");' +
    'var nextBtn=document.getElementById("__hair_next");' +
    'var fsBtn=document.getElementById("__hair_fullscreen");' +
    'if(prevBtn)prevBtn.addEventListener("click",function(){show(idx-1)});' +
    'if(nextBtn)nextBtn.addEventListener("click",function(){show(idx+1)});' +
    // 사용자 보고(2026-11-16, "전체화면 버튼 클릭시 아무 동작 안 함") —
    // sandbox 토큰을 고쳐도(위 renderContent()의 iframe 주석 참고)
    // 브라우저별 Fullscreen API 지원 차이가 남아 100% 보장은 못 한다.
    // requestFullscreen()이 없거나 거부되면, 부모 페이지(SPA)에
    // postMessage로 알려 이미 확실히 동작하는 "전체화면"(새 탭 열기,
    // openArtifactFullscreen())으로 대신 처리하게 한다 — 버튼을 누르면
    // 항상 뭔가는 반드시 일어나게 만드는 이중 안전장치.
    'if(fsBtn)fsBtn.addEventListener("click",function(){' +
    'if(document.fullscreenElement){document.exitFullscreen();return}' +
    'var fallback=function(){try{window.parent.postMessage({__hair_slideshow_fullscreen:true},"*")}catch(e){}};' +
    'if(document.documentElement.requestFullscreen){' +
    'var p=document.documentElement.requestFullscreen();' +
    'if(p&&p.catch){p.catch(fallback)}' +
    '}else{fallback()}' +
    '});' +
    'document.addEventListener("keydown",function(ev){' +
    'if(ev.key==="ArrowRight"||ev.key===" "||ev.key==="PageDown"){show(idx+1);ev.preventDefault()}' +
    'else if(ev.key==="ArrowLeft"||ev.key==="PageUp"){show(idx-1);ev.preventDefault()}' +
    '});' +
    '})();<\/script>';
  return /<\/body>/i.test(html) ? html.replace(/<\/body>/i, controller + "</body>") : html + controller;
}

// 사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된 HTML을
// 수정할 수 있는 기능 추가 제안" → "1단계부터 구현해줘") — 미리보기/
// 소스보기 HTML을 만드는 로직을 `renderContent()`의 최초 렌더와, 수정
// 저장 후 그 자리에서 다시 그리는 경로(아래 `data-save-artifact` 핸들러)
// 양쪽이 공유한다 — 중복 없이 하나의 소스에서 나온다.
// `rawForStorage`(선택, 2026-11-17 "모든 대화 결과물에서도 수정이
// 가능하도록") — 대화 메시지는 미리보기에 @멘션 강조 HTML이 섞인
// `raw`(예: `_highlightMentions()`가 만든 문자열)를 넘기면서도,
// 복사/다운로드/수정 textarea/저장은 그 강조 태그가 섞이지 않은
// **진짜 원본**(`m.content`)을 써야 한다 — 안 그러면 수정 후 저장 시
// 멘션 강조 `<span>` 태그가 메시지 내용에 그대로 끼어 들어간다. 이
// 값이 있으면 저장용 effectiveRaw만 이걸로 바꾸고, 미리보기는 여전히
// `raw`(강조 포함)로 그대로 렌더한다 — 기존 호출부(이 인자를 안 주는
// 전부)는 100% 그대로 동작한다.
function _buildArtifactParts(raw, contentType, rawForStorage) {
  const needsFenceStrip = contentType === "html" || contentType === "code" || contentType === "markdown";
  const displayRaw = needsFenceStrip ? stripOuterFence(raw) : raw;
  const effectiveRaw = rawForStorage !== undefined
    ? (needsFenceStrip ? stripOuterFence(rawForStorage) : rawForStorage)
    : displayRaw;
  let previewHtml;
  if (contentType === "html") {
    // 사용자 보고(2026-11-16, "슬라이드 이동 버튼 옆 전체화면 버튼
    // 클릭시 아무 동작 안 함") — `allow="fullscreen"`/`allowfullscreen`
    // 속성만으로는 부족했다. `sandbox` 속성이 있는 iframe은 그 안의
    // 별도 토큰 `allow-fullscreen`이 없으면 Fullscreen API 자체가
    // 거부된다(Permissions Policy의 `allow`와는 별개의, sandbox
    // 전용 허용 토큰) — 이게 빠져 있었다.
    previewHtml = '<iframe class="html-preview" sandbox="allow-scripts allow-fullscreen" allow="fullscreen" allowfullscreen ' +
      'srcdoc="' + escAttr(_injectSlideshowController(displayRaw)) + '"></iframe>';
  } else if (contentType === "code") {
    previewHtml = '<pre class="code-block">' + _highlightCode(displayRaw) + "</pre>";
  } else {
    // markdown이든 타입 미지정이든 전부 실제 마크다운 렌더러를 거친다
    // (기존에도 타입 미지정은 **굵게**/코드펜스만 가볍게 처리하는
    // "마크다운 라이트" 취급이었다 — 이제 똑같이 풀 렌더링으로 올린다).
    previewHtml = '<div class="md-content">' + _renderMarkdown(displayRaw) + "</div>";
  }
  return { effectiveRaw, previewHtml };
}

// `editTarget`(선택) — {kind: "message", id, conversationId} 또는
// {kind: "deliverable", id}. 넘기면 "수정" 버튼과 편집 영역이 추가돼
// 소스를 직접 고쳐 저장할 수 있다(1단계: 수동 편집 — AI가 만든 HTML에
// 색 대비 등의 버그가 있을 때 즉석에서 고치는 용도). 넘기지 않으면
// 기존 동작과 100% 동일(대부분의 renderContent() 호출부 — Crew 실행
// 결과·샌드박스 미리보기·근거 블록 등은 원본 데이터가 없거나 수정이
// 의미가 없는 이력성 데이터라 이 옵션을 쓰지 않는다).
// `rawForStorage`(선택) — `_buildArtifactParts()` 주석 참고.
// 덱 클래스 점검(2026-11-21) — HTML 발표자료(덱)가 디자인 시스템에 없는 클래스를 쓰면
// 그 부분은 서식 없이 보인다(실측: hair-bignum·hair-bullets). 정의된 클래스 목록은
// 서버(`presentation_deck.defined_classes()`)가 페이지에 넣어 준다 — 목록을 여기 따로
// 적지 않는다.
const _DECK_CLASSES = new Set(__DECK_CLASSES_JSON__);
function _deckLintHtml(raw, contentType) {
  if (contentType !== "html" || typeof raw !== "string" || !/class\s*=\s*["'][^"']*\bhair-deck\b/.test(raw)) return "";
  const unknown = new Set();
  for (const m of raw.matchAll(/\bclass\s*=\s*["']([^"']*)["']/gi)) {
    for (const c of m[1].split(/\s+/)) if (c && !_DECK_CLASSES.has(c)) unknown.add(c);
  }
  if (!unknown.size) return "";
  const list = Array.from(unknown).slice(0, 8).join(", ") + (unknown.size > 8 ? " 외 " + (unknown.size - 8) + "개" : "");
  return '<p class="muted deck-lint" style="margin:0.3rem 0">⚠ 덱 디자인에 없는 클래스 ' + unknown.size +
    '개가 쓰여 그 부분은 기본 서식으로 보입니다: ' + esc(list) + ' — "AI에게 다시 요청"으로 고치거나 다시 생성하세요.</p>';
}
function renderContent(raw, contentType, editTarget, rawForStorage) {
  const artifactId = "artifact-" + (++_artifactSeq);
  const { effectiveRaw, previewHtml } = _buildArtifactParts(raw, contentType, rawForStorage);

  // 복사 버튼은 렌더링된(하이라이팅/HTML태그 섞인) 결과가 아니라 원본
  // raw 텍스트를 복사해야 한다 — <textarea> 안에 esc()로 이스케이프해
  // 넣으면 브라우저가 파싱 시점에 엔티티를 다시 원문으로 풀어주므로,
  // `.value`를 읽기만 하면 별도 보관 없이 정확한 원본을 얻는다.
  const downloadExt = _DOWNLOAD_EXT[contentType] || "md";
  // 사용자 보고(2026-11-16, "'소스보기' 버튼 클릭시 다시 리프레시되어
  // 화면 닫힘, 수정 기능에서 소스 보기 기능이 있어 중복이므로 '소스
  // 보기' 버튼 삭제해줘") — 읽기 전용 소스 토글(이 버튼 + `.artifact-
  // source` div)을 완전히 제거한다. "수정"을 열면 소스 모드 textarea/
  // CodeMirror로 원본을 그대로 볼 수 있어 editTarget이 있는 콘텐츠는
  // 중복이었고, editTarget이 없는 콘텐츠는 복사/다운로드로 원본을
  // 그대로 받을 수 있다.
  const toolbarHtml =
    '<div class="artifact-toolbar">' +
    '<button type="button" class="small" data-copy-artifact="' + artifactId + '">복사</button>' +
    '<button type="button" class="small" data-download-artifact="' + artifactId + '" data-download-ext="' + downloadExt + '">다운로드</button>' +
    // 사용자 요청(2026-10-13, "claude artifact 수준으로") — Claude
    // Artifact의 대표적인 특징인 "전체화면으로 보기"를 추가한다.
    '<button type="button" class="small" data-fullscreen-artifact="' + artifactId + '">전체화면</button>' +
    (editTarget
      ? '<button type="button" class="small" data-edit-artifact="' + artifactId + '">수정</button>'
      : "") +
    '</div>';
  // `data-content-type`은 editTarget 유무와 무관하게 항상 붙인다 —
  // "전체화면"을 새 탭으로 여는 로직(`openArtifactFullscreen()`)이
  // HTML 콘텐츠인지 구분하는 데 모든 renderContent() 호출부에서 필요.
  const editAttrs =
    ' data-content-type="' + esc(contentType || "") + '"' +
    (editTarget
      ? ' data-edit-kind="' + esc(editTarget.kind) + '" data-edit-id="' + editTarget.id + '"' +
        (editTarget.conversationId != null ? ' data-edit-conversation-id="' + editTarget.conversationId + '"' : "")
      : "");
  // 3단계("AI에게 다시 요청") — 손으로 직접 고치는 대신 "이 부분 색이
  // 안 보여" 같은 자연어 피드백을 주면 LLM이 수정안을 textarea에
  // 채워준다(저장은 안 됨 — 사람이 검토 후 아래 "저장" 버튼을 직접
  // 눌러야 한다, `assist()`와 같은 설계).
  //
  // 사용자 요청(2026-11-16, "html 수정에 소스 수정이 아닌 Editor 도입이
  // 가능한지") — 비주얼(WYSIWYG) 편집 모드를 추가한다. iframe 샌드박스
  // 안쪽은 `allow-same-origin` 없이는 부모가 손댈 방법이 없고(의도된
  // 보안장치), 그걸 풀면 신뢰 못 하는 LLM 생성 스크립트가 부모 앱 DOM에
  // 접근할 길이 열려 위험하다 — 그래서 iframe이 아니라 DOMPurify로
  // 살균한 HTML을 일반 `contenteditable` div에 직접 넣는 방식을 쓴다
  // (DOMPurify 기본 설정은 `<style>`은 허용하고 `<script>`는 항상
  // 제거한다 — 직접 소스로 확인함). 이 제거는 되돌릴 수 없으므로,
  // `<script>`나 `on*` 이벤트 핸들러가 있는 결과물(게임 등 인터랙티브
  // HTML)에는 비주얼 모드 버튼 자체를 안 보여주고 소스 편집만 쓸 수
  // 있게 한다 — 발표자료/보고서류(색 대비 버그가 실제로 나온 케이스)는
  // 스크립트가 없어 비주얼 모드가 안전하게 열린다.
  // 사용자 보고(2026-11-16, "대화의 HTML 결과물 확인시 비주얼 편집
  // 기능이 표시안됨") — 실제 저장된 메시지로 재현 확인한 원인: 아래
  // 정규식에 단어 경계(\b)가 없어 `<meta ... content="...">`의
  // "content="(그 안에 "on"으로 끝나는 "c**on**tent" 부분 문자열)
  // 같은 평범한 속성까지 "이벤트 핸들러"로 오인해 거의 모든 실제
  // 문서에서 비주얼 모드를 숨겨버렸다 — 진짜 이벤트 핸들러(onclick=
  // 등)는 항상 공백/태그 경계 뒤에서 시작하므로 \b를 앞에 붙이면
  // "content="/"action="처럼 단어 중간에 "on"이 낀 경우는 더 이상
  // 안 걸리고, 진짜 on* 핸들러만 정확히 잡힌다(직접 재현해 확인).
  const _hasScriptOrHandler = /<script[\s>]|\bon\w+\s*=/i.test(effectiveRaw);
  const showVisualToggle = !!(editTarget && contentType === "html" && !_hasScriptOrHandler);
  const modeToggleHtml = showVisualToggle
    ? '<div class="artifact-edit-mode-toggle" style="display:flex;gap:0.3rem;margin-bottom:0.4rem">' +
      '<button type="button" class="small primary" data-edit-mode-btn="' + artifactId + '" data-mode="source">소스 편집</button>' +
      '<button type="button" class="small" data-edit-mode-btn="' + artifactId + '" data-mode="visual">비주얼 편집</button>' +
      '</div>'
    : "";
  const visualEditHtml = showVisualToggle
    ? '<div class="artifact-edit-visual" id="' + artifactId + '-edit-visual-wrap" style="display:none">' +
      '<div style="display:flex;gap:0.8rem;align-items:center;margin-bottom:0.4rem">' +
      '<label class="muted">글자색 <input type="color" data-visual-color="fore" data-artifact="' + artifactId + '" value="#000000"></label>' +
      '<label class="muted">배경색 <input type="color" data-visual-color="back" data-artifact="' + artifactId + '" value="#ffffff"></label>' +
      '<span class="muted" style="font-size:0.8rem">글자를 드래그로 선택한 뒤 색을 고르세요</span>' +
      '</div>' +
      '<div class="artifact-visual-editable" id="' + artifactId + '-edit-visual" contenteditable="true" ' +
      'style="border:1px solid #14213d;border-radius:6px;padding:0.75rem;min-height:220px;max-height:500px;overflow:auto;background:#fff;color:#000"></div>' +
      '</div>'
    : "";
  const editHtml = editTarget
    ? '<div class="artifact-edit" id="' + artifactId + '-edit" data-edit-mode="source" style="display:none">' +
      '<div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.4rem">' +
      '<input type="text" id="' + artifactId + '-fix-feedback" placeholder="' +
      (contentType === "html" ? "예: 이 부분 색이 안 보여" : "예: 이 부분을 더 간결하게 줄여줘") + '" ' +
      'style="flex:1;box-sizing:border-box" autocomplete="off">' +
      '<button type="button" class="small" data-suggest-fix-artifact="' + artifactId + '">AI에게 요청</button>' +
      '</div>' +
      modeToggleHtml +
      '<div class="artifact-edit-source" id="' + artifactId + '-edit-source-wrap">' +
      '<textarea class="artifact-edit-textarea" id="' + artifactId + '-edit-textarea" ' +
      'style="width:100%;box-sizing:border-box;min-height:220px;font-family:monospace;font-size:0.85rem"></textarea>' +
      '</div>' +
      visualEditHtml +
      '<div class="artifact-toolbar">' +
      '<button type="button" class="small primary" data-save-artifact="' + artifactId + '">저장</button>' +
      '<button type="button" class="small" data-cancel-edit-artifact="' + artifactId + '">취소</button>' +
      '<span class="muted" id="' + artifactId + '-edit-status" style="font-size:0.85rem"></span>' +
      '</div></div>'
    : "";

  return (
    '<div class="artifact-block" data-artifact-id="' + artifactId + '"' + editAttrs + '>' +
    toolbarHtml + _deckLintHtml(effectiveRaw, contentType) +
    '<div class="artifact-preview" id="' + artifactId + '-preview">' + previewHtml + '</div>' +
    editHtml +
    '<textarea class="artifact-raw-store" id="' + artifactId + '-raw" style="display:none" aria-hidden="true">' +
    esc(effectiveRaw) + '</textarea>' +
    '</div>'
  );
}

// 대화창 스킬 선택 — 네이티브 <select>를 버튼+목록으로 대체(위
// renderConversationDetail의 "Firefox 스크린샷" 주석 참고)한 뒤 그
// 현재값/표시 라벨을 함께 갱신하는 공용 헬퍼. 숨은 input의 `.value`는
// 기존 코드(메시지 전송 시 읽는 부분)가 그대로 쓰므로 수정 불필요.
function _setSkillSelectDisplay(value, label) {
  const hidden = document.getElementById("conv-skill-select");
  const btn = document.getElementById("conv-skill-select-btn");
  if (hidden) hidden.value = value;
  if (btn) btn.textContent = (label || "스킬 선택 안 함") + " ▾";
  _renderContextChip();
}

// 대화 컨텍스트 정책(2026-11-20, 서버 `context_policy.py`) — 입력창
// "이전 대화" 칩과 응답 배지가 공유하는 표시 문구.
let _skillContextPolicies = {};
const _HISTORY_MODE_LABEL = { full: "전체", recent: "최근만", none: "사용 안 함" };
const _CONTEXT_CYCLE = ["", "full", "recent", "none"];

function _renderContextChip() {
  const btn = document.getElementById("conv-context-btn");
  const hidden = document.getElementById("conv-context-select");
  if (!btn || !hidden) return;
  const skill = (document.getElementById("conv-skill-select") || {}).value || "";
  const skillPolicy = _skillContextPolicies[skill];
  const defaultMode = skillPolicy ? skillPolicy.history : "full";
  btn.textContent = hidden.value
    ? "이전 대화: " + _HISTORY_MODE_LABEL[hidden.value]
    : "이전 대화: 기본(" + _HISTORY_MODE_LABEL[defaultMode] + ")";
  btn.classList.toggle("primary", !!hidden.value);
}

document.addEventListener("click", (ev) => {
  if (!ev.target.closest("[data-context-cycle]")) return;
  const hidden = document.getElementById("conv-context-select");
  if (!hidden) return;
  const next = (_CONTEXT_CYCLE.indexOf(hidden.value) + 1) % _CONTEXT_CYCLE.length;
  hidden.value = _CONTEXT_CYCLE[next];
  _renderContextChip();
});

function _conversationContextBadge(m) {
  if (m.author_type !== "agent" || !m.context_policy) return "";
  let p;
  try { p = JSON.parse(m.context_policy); } catch (e) { return ""; }
  if (p.history === "full" && p.attachments !== false && p.source !== "override") return "";
  const text = p.source === "auto"
    ? "후속 질문 → 최근 대화 포함"
    : "이전 대화 " + _HISTORY_MODE_LABEL[p.history] + (p.source === "override" ? "(직접 지정)" : "");
  const tip = {
    none: "이전 대화를 넣지 않고 현재 질문만으로 답했습니다(스킬 기본값 또는 직접 지정).",
    recent: "직전 1~2턴만 참고해 답했습니다.",
    full: "최근 10건과 요약을 참고해 답했습니다.",
  }[p.history] + (p.attachments === false ? " 첨부 자료는 쓰지 않았습니다." : "");
  return ' <span class="gate-badge gate-na" title="' + escAttr(tip) + '">' + esc(text) +
    (p.attachments === false ? " · 첨부 미사용" : "") + '</span>';
}

// 사용자 요청(2026-11-16, "html 수정에 소스 수정이 아닌 Editor 도입이
// 가능한지" → "개선해줘") — 비주얼(WYSIWYG) 편집 모드. 소스/비주얼
// 전환 시 양방향으로 내용을 동기화한다(한쪽에서 고친 걸 다른 쪽으로
// 넘어가도 잃지 않게). `renderContent()`의 `showVisualToggle` 주석
// 참고 — `<script>`가 있는 결과물엔 이 모드 버튼 자체가 안 뜬다.
function _switchArtifactEditMode(id, mode) {
  const editBox = document.getElementById(id + "-edit");
  const sourceWrap = document.getElementById(id + "-edit-source-wrap");
  const visualWrap = document.getElementById(id + "-edit-visual-wrap");
  const editTextarea = document.getElementById(id + "-edit-textarea");
  const visualEl = document.getElementById(id + "-edit-visual");
  if (!editBox || !sourceWrap || !visualWrap || !editTextarea || !visualEl) return;
  if ((editBox.dataset.editMode || "source") === mode) return;
  if (mode === "visual") {
    if (editTextarea._cm) editTextarea._cm.save();
    // DOMPurify로 살균한 뒤 contenteditable에 넣는다 — <script>는
    // 이미 이 모드 자체가 안 뜨는 콘텐츠로 걸러졌지만, 혹시 모를
    // on* 핸들러 등은 여기서도 한 번 더 걸러진다(이중 안전장치).
    visualEl.innerHTML = (typeof DOMPurify !== "undefined")
      ? DOMPurify.sanitize(editTextarea.value)
      : esc(editTextarea.value);
    sourceWrap.style.display = "none";
    visualWrap.style.display = "block";
  } else {
    const rebuilt = _rebuildHtmlDocument(visualEl.innerHTML);
    editTextarea.value = rebuilt;
    if (editTextarea._cm) editTextarea._cm.setValue(rebuilt);
    sourceWrap.style.display = "block";
    visualWrap.style.display = "none";
  }
  editBox.dataset.editMode = mode;
  const toggleWrap = editBox.querySelector(".artifact-edit-mode-toggle");
  if (toggleWrap) {
    toggleWrap.querySelectorAll("[data-edit-mode-btn]").forEach(btn => {
      btn.classList.toggle("primary", btn.dataset.mode === mode);
    });
  }
}

// 비주얼 편집 결과(살균된 body 내용물)를 다시 완전한 HTML 문서로
// 감싼다 — DOMPurify가 펼쳐버린 <head> 메타데이터(title 등)는 복구
// 못 하지만(색 대비 같은 단순 수정 목적엔 영향 없음), <style>은 그
// 내용 안에 그대로 남아 있어 위치와 무관하게 똑같이 적용된다.
function _rebuildHtmlDocument(bodyInner) {
  return "<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\"></head>\n<body>\n" +
    bodyInner + "\n</body>\n</html>";
}

// <input type="color"> 클릭으로 포커스가 옮겨가면 contenteditable 안의
// 텍스트 선택(Selection)이 끊겨 execCommand가 적용될 곳을 잃는다 —
// 마우스를 떼거나 키를 뗄 때마다 그 시점의 선택 범위를 엘리먼트에
// 직접 저장해뒀다가, 색상 적용 시 그 범위를 되살린 뒤 명령을 내린다.
function _saveVisualEditorSelection(ev) {
  const el = ev.target.closest(".artifact-visual-editable");
  if (!el) return;
  const sel = window.getSelection();
  if (sel.rangeCount > 0 && el.contains(sel.anchorNode)) {
    el._savedRange = sel.getRangeAt(0).cloneRange();
  }
}
document.addEventListener("mouseup", _saveVisualEditorSelection);
document.addEventListener("keyup", _saveVisualEditorSelection);
document.addEventListener("change", (ev) => {
  const colorInput = ev.target.closest("[data-visual-color]");
  if (!colorInput) return;
  const visualEl = document.getElementById(colorInput.dataset.artifact + "-edit-visual");
  if (!visualEl) return;
  if (visualEl._savedRange) {
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(visualEl._savedRange);
  }
  visualEl.focus();
  const command = colorInput.dataset.visualColor === "fore" ? "foreColor" : "hiliteColor";
  try {
    document.execCommand(command, false, colorInput.value);
  } catch (e) {
    // 구형/일부 브라우저에서 execCommand 자체가 없거나 실패해도
    // 나머지 편집(텍스트 직접 타이핑) 기능은 그대로 쓸 수 있다.
  }
});

// 전역 위임(한 번만 등록) — renderContent()가 어디서 호출되든(Task
// 워크스페이스/Crew 실행 결과/대화 등) 매번 버튼을 다시 연결할 필요가
// 없다.
document.addEventListener("click", async (ev) => {
  // 컬럼 폭 조정 핸들은 정렬 가능한 th 안에 들어있어, 드래그가 끝난 뒤
  // 발생하는 네이티브 click이 그대로 버블되면 정렬(data-sort-col)까지
  // 같이 트리거된다 — 여기서 먼저 걸러낸다.
  if (ev.target.closest(".col-resizer")) return;
  // 스킬 선택 드롭다운 — 바깥을 클릭하면 열려 있던 목록을 닫는다
  // (다른 분기보다 먼저, return 없이 실행해 아래 토글/선택 처리와
  // 함께 동작하게 한다).
  const openSkillList = document.getElementById("conv-skill-select-list");
  if (openSkillList && openSkillList.style.display !== "none" && !ev.target.closest(".skill-select-wrap")) {
    openSkillList.style.display = "none";
  }
  const skillToggleBtn = ev.target.closest("[data-skill-select-toggle]");
  if (skillToggleBtn) {
    const list = document.getElementById("conv-skill-select-list");
    if (list) list.style.display = list.style.display === "none" ? "block" : "none";
    return;
  }
  const skillOption = ev.target.closest("[data-skill-select-option]");
  if (skillOption) {
    _setSkillSelectDisplay(skillOption.dataset.skillSelectOption, skillOption.textContent);
    const list = document.getElementById("conv-skill-select-list");
    if (list) list.style.display = "none";
    return;
  }
  const copyBtn = ev.target.closest("[data-copy-artifact]");
  if (copyBtn) {
    const raw = document.getElementById(copyBtn.dataset.copyArtifact + "-raw");
    if (!raw) return;
    try { localStorage.setItem("hair.firstday.saved", "1"); } catch (_) { /* 홈 첫날 점검표 3단계 */ }
    // 서식 그대로 복사(2026-11-28, Phase C) — 메일·워드에 붙이면 제목·굵게·표가 살아 있게 화면에 그린 HTML도 함께 넣는다.
    // 메일 프로그램은 text/html을, 메모장은 text/plain을 쓴다. 지원하지 않는 브라우저·iframe 미리 보기는 예전처럼 원문만.
    const preview = document.getElementById(copyBtn.dataset.copyArtifact + "-preview");
    const richOk = preview && !preview.querySelector("iframe") && window.ClipboardItem && navigator.clipboard.write;
    const writing = richOk
      ? navigator.clipboard.write([new ClipboardItem({
          "text/html": new Blob([preview.innerHTML], { type: "text/html" }),
          "text/plain": new Blob([raw.value], { type: "text/plain" }),
        })]).catch(() => navigator.clipboard.writeText(raw.value))
      : navigator.clipboard.writeText(raw.value);
    writing.then(() => {
      const original = copyBtn.textContent;
      copyBtn.textContent = "복사됨";
      setTimeout(() => { copyBtn.textContent = original; }, 1200);
    }).catch(() => showMsg("global-msg", "클립보드 복사에 실패했습니다", "error"));
    return;
  }
  const fullscreenBtn = ev.target.closest("[data-fullscreen-artifact]");
  if (fullscreenBtn) {
    openArtifactFullscreen(fullscreenBtn.dataset.fullscreenArtifact);
    return;
  }
  // 사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된 HTML을
  // 수정할 수 있는 기능" 1단계) — "수정" 버튼은 `renderContent()`가
  // `editTarget`을 받은 블록에만 존재하므로(대화 메시지의 완전한 HTML
  // 문서·Task 산출물), 다른 renderContent() 호출부(Crew 실행 이력,
  // 근거 블록 등)는 이 분기에 전혀 걸리지 않는다 — 기존 동작 무변화.
  const editArtifactBtn = ev.target.closest("[data-edit-artifact]");
  if (editArtifactBtn) {
    const id = editArtifactBtn.dataset.editArtifact;
    const preview = document.getElementById(id + "-preview");
    const editBox = document.getElementById(id + "-edit");
    const editTextarea = document.getElementById(id + "-edit-textarea");
    const rawEl = document.getElementById(id + "-raw");
    if (!preview || !editBox || !editTextarea || !rawEl) return;
    editTextarea.value = rawEl.value;
    // 2단계("가벼운 코드 에디터") — HTML 수정에만 CodeMirror로 문법
    // 강조를 붙인다(htmlmixed 모드, CDN 로드 성공 시에만). CDN 로드
    // 실패 시 `typeof CodeMirror === "undefined"`라 그냥 지나가고
    // 위에서 이미 채운 평범한 textarea(1단계)가 그대로 쓰인다.
    const block = document.querySelector('[data-artifact-id="' + id + '"]');
    const contentType = block ? block.dataset.contentType : "";
    if (editTextarea._cm) {
      editTextarea._cm.setValue(rawEl.value);
    } else if (typeof CodeMirror !== "undefined" && contentType === "html") {
      editTextarea._cm = CodeMirror.fromTextArea(editTextarea, {
        mode: "htmlmixed", lineNumbers: true, lineWrapping: true, viewportMargin: Infinity,
      });
    }
    // 매번 열 때 소스 모드로 리셋한다 — 지난번에 비주얼 모드로 저장/
    // 취소하고 닫았어도 다음에 열면 항상 소스부터 보여준다(예측 가능성).
    editBox.dataset.editMode = "source";
    const sourceWrap = document.getElementById(id + "-edit-source-wrap");
    const visualWrap = document.getElementById(id + "-edit-visual-wrap");
    if (sourceWrap) sourceWrap.style.display = "block";
    if (visualWrap) visualWrap.style.display = "none";
    const toggleWrap = editBox.querySelector(".artifact-edit-mode-toggle");
    if (toggleWrap) {
      toggleWrap.querySelectorAll("[data-edit-mode-btn]").forEach(btn => {
        btn.classList.toggle("primary", btn.dataset.mode === "source");
      });
    }
    preview.style.display = "none";
    editBox.style.display = "block";
    if (editTextarea._cm) editTextarea._cm.refresh();
    return;
  }
  const cancelEditArtifactBtn = ev.target.closest("[data-cancel-edit-artifact]");
  if (cancelEditArtifactBtn) {
    const id = cancelEditArtifactBtn.dataset.cancelEditArtifact;
    const preview = document.getElementById(id + "-preview");
    const editBox = document.getElementById(id + "-edit");
    if (preview) preview.style.display = "block";
    if (editBox) editBox.style.display = "none";
    return;
  }
  // 소스/비주얼 편집 모드 전환 버튼 — 실제 전환·동기화 로직은
  // `_switchArtifactEditMode()`(아래, 편집 패널 밖에서도 재사용 가능하게
  // 독립 함수로 뺌)가 한다.
  const editModeBtn = ev.target.closest("[data-edit-mode-btn]");
  if (editModeBtn) {
    _switchArtifactEditMode(editModeBtn.dataset.editModeBtn, editModeBtn.dataset.mode);
    return;
  }
  // 3단계("AI에게 다시 요청") — 결과는 저장하지 않고 편집 textarea를
  // 채우기만 한다(검토 후 "저장" 버튼을 직접 눌러야 실제로 반영됨).
  const suggestFixBtn = ev.target.closest("[data-suggest-fix-artifact]");
  if (suggestFixBtn) {
    const id = suggestFixBtn.dataset.suggestFixArtifact;
    const block = document.querySelector('[data-artifact-id="' + id + '"]');
    const feedbackEl = document.getElementById(id + "-fix-feedback");
    const editTextarea = document.getElementById(id + "-edit-textarea");
    const statusEl = document.getElementById(id + "-edit-status");
    if (!block || !feedbackEl || !editTextarea) return;
    const feedback = feedbackEl.value.trim();
    if (!feedback) {
      showMsg("global-msg", "어떤 부분을 고칠지 피드백을 먼저 입력하세요", "error");
      return;
    }
    const kind = block.dataset.editKind;
    const targetId = block.dataset.editId;
    const conversationId = block.dataset.editConversationId;
    const endpoint = kind === "message"
      ? "/api/conversations/" + conversationId + "/messages/" + targetId + "/suggest-fix"
      : "/api/deliverables/" + targetId + "/suggest-fix";
    suggestFixBtn.disabled = true;
    if (statusEl) statusEl.textContent = "AI가 수정안을 만드는 중...";
    try {
      const result = await api("POST", endpoint, { feedback });
      // 비주얼 모드로 보고 있었다면 소스 모드로 전환해 수정안을 바로
      // 볼 수 있게 한다(비주얼 영역은 그대로 둬도 안 보이는 곳이라
      // 채워봐야 소용없다).
      const editBoxForSuggest = document.getElementById(id + "-edit");
      if (editBoxForSuggest && editBoxForSuggest.dataset.editMode === "visual") {
        _switchArtifactEditMode(id, "source");
      }
      editTextarea.value = result.suggestion;
      if (editTextarea._cm) editTextarea._cm.setValue(result.suggestion);
      showMsg("global-msg", "수정안을 받았습니다 — 검토 후 저장하세요", "ok");
    } catch (e) {
      showMsg("global-msg", e.message, "error");
    } finally {
      suggestFixBtn.disabled = false;
      if (statusEl) statusEl.textContent = "";
    }
    return;
  }
  const saveArtifactBtn = ev.target.closest("[data-save-artifact]");
  if (saveArtifactBtn) {
    const id = saveArtifactBtn.dataset.saveArtifact;
    const block = document.querySelector('[data-artifact-id="' + id + '"]');
    const editBox = document.getElementById(id + "-edit");
    const editTextarea = document.getElementById(id + "-edit-textarea");
    const statusEl = document.getElementById(id + "-edit-status");
    if (!block || !editBox || !editTextarea) return;
    const kind = block.dataset.editKind;
    const targetId = block.dataset.editId;
    const conversationId = block.dataset.editConversationId;
    const contentType = block.dataset.contentType || undefined;
    // 비주얼 모드로 편집 중이었으면 그 내용을 완전한 HTML 문서로
    // 다시 감싸 저장한다(아래 `_rebuildHtmlDocument()` 참고) — 소스
    // 모드면 지금까지와 동일하게 textarea/CodeMirror 값을 그대로 쓴다.
    let newContent;
    if (editBox.dataset.editMode === "visual") {
      const visualEl = document.getElementById(id + "-edit-visual");
      newContent = visualEl ? _rebuildHtmlDocument(visualEl.innerHTML) : editTextarea.value;
    } else {
      if (editTextarea._cm) editTextarea._cm.save(); // CodeMirror 내용을 원본 textarea.value로 동기화
      newContent = editTextarea.value;
    }
    const endpoint = kind === "message"
      ? "/api/conversations/" + conversationId + "/messages/" + targetId
      : "/api/deliverables/" + targetId;
    saveArtifactBtn.disabled = true;
    if (statusEl) statusEl.textContent = "저장 중...";
    try {
      await api("PATCH", endpoint, { content: newContent });
      const built = _buildArtifactParts(newContent, contentType);
      const previewEl = document.getElementById(id + "-preview");
      const rawEl = document.getElementById(id + "-raw");
      if (previewEl) { previewEl.innerHTML = built.previewHtml; previewEl.style.display = "block"; }
      if (rawEl) rawEl.value = built.effectiveRaw;
      editTextarea.value = built.effectiveRaw;
      if (editTextarea._cm) editTextarea._cm.setValue(built.effectiveRaw);
      editBox.style.display = "none";
      showMsg("global-msg", "저장했습니다", "ok");
    } catch (e) {
      showMsg("global-msg", e.message, "error");
    } finally {
      saveArtifactBtn.disabled = false;
      if (statusEl) statusEl.textContent = "";
    }
    return;
  }
  // 사용자 요청(2026-10-14, "결과 파일을 복사 및 다운로드 가능하도록") —
  // 복사(클립보드)와 별개로 실제 파일로 저장할 수 있게 한다. 원본 raw
  // 텍스트는 복사 버튼과 똑같이 숨겨진 textarea에서 그대로 읽는다(다시
  // 렌더링된 HTML이 아니라 원본을 받아야 한다).
  const downloadBtn = ev.target.closest("[data-download-artifact]");
  if (downloadBtn) {
    const id = downloadBtn.dataset.downloadArtifact;
    const rawEl = document.getElementById(id + "-raw");
    if (!rawEl) return;
    const ext = downloadBtn.dataset.downloadExt || "txt";
    const mime = _DOWNLOAD_MIME[ext] || "text/plain";
    const blob = new Blob([rawEl.value], { type: mime + ";charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = id + "." + ext;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return;
  }
  // 사용자 요청(2026-10-14, "단위 메시지 삭제 기능") — 메시지 목록은
  // innerHTML로 통째로 다시 그려지므로(`refreshConversationMessages()`),
  // 버튼마다 개별 리스너를 거는 대신 복사/전체화면 버튼과 같은 전역
  // 위임 방식을 쓴다 — 재렌더될 때마다 다시 배선할 필요가 없다.
  // 대화 답변 Word·Excel로 받기(2026-11-28) — 첨부 파일 응답이라 화면은 그대로 남는다.
  const exportMsgBtn = ev.target.closest("[data-export-message]");
  if (exportMsgBtn) {
    try { localStorage.setItem("hair.firstday.saved", "1"); } catch (_) { /* 홈 첫날 점검표 3단계 */ }
    const [convId, msgId, fmt] = exportMsgBtn.dataset.exportMessage.split(":");
    window.location.href = "/api/conversations/" + convId + "/messages/" + msgId + "/export." + fmt;
    return;
  }
  const deleteMsgBtn = ev.target.closest("[data-delete-message]");
  if (deleteMsgBtn) {
    const [convId, msgId] = deleteMsgBtn.dataset.deleteMessage.split(":");
    if (!(await confirmModal("이 메시지를 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
    api("DELETE", "/api/conversations/" + convId + "/messages/" + msgId)
      .then(() => _reloadConversationMessagesInPlace(convId))
      .catch((e) => showMsg("global-msg", e.message, "error"));
    return;
  }
  const saveDeliverableBtn = ev.target.closest("[data-save-deliverable]");
  if (saveDeliverableBtn) {
    const [convId, msgId] = saveDeliverableBtn.dataset.saveDeliverable.split(":");
    saveDeliverableBtn.disabled = true;
    api("POST", "/api/conversations/" + convId + "/messages/" + msgId + "/save-as-deliverable")
      .then(() => {
        showMsg("global-msg", "갤러리로 보냈습니다 — '갤러리' 탭에서 확인할 수 있습니다.", "ok");
        saveDeliverableBtn.textContent = "보냄";
      })
      .catch((e) => {
        saveDeliverableBtn.disabled = false;
        showMsg("global-msg", e.message, "error");
      });
    return;
  }
  const submitKnowledgeBtn = ev.target.closest("[data-submit-knowledge]");
  if (submitKnowledgeBtn) {
    const [sourceType, sourceId] = submitKnowledgeBtn.dataset.submitKnowledge.split(":");
    openKnowledgeSubmitModal(sourceType, Number(sourceId), submitKnowledgeBtn);
    return;
  }
  // 사용자 요청(2026-10-14, "각 메뉴의 목록 기능에 필터/소트 기능을
  // 추가해줘") — table()이 렌더되는 23곳 전부 이 전역 위임 하나로
  // 정렬 클릭을 받는다(호출부마다 따로 배선할 필요 없음).
  const sortTh = ev.target.closest("[data-sort-col]");
  if (sortTh) {
    _sortTableByColumn(sortTh);
  }
  // LLM 설정 최적화 추천(2026-10-18) — "AI 에이전트 관리" 화면의 버튼은
  // 클릭 시점에 비동기로 결과 패널을 새로 주입하므로(최초 렌더 때는
  // 존재하지 않음), 복사/삭제 버튼과 같은 전역 위임으로 처리한다.
  const recommendOptBtn = ev.target.closest("[data-recommend-optimize]");
  if (recommendOptBtn) {
    const id = recommendOptBtn.dataset.recommendOptimize;
    const resultEl = document.getElementById("optimize-result-" + id);
    if (!resultEl) return;
    resultEl.innerHTML = '<p class="muted">추천 계산 중…</p>';
    api("GET", "/api/agent-pool/" + id + "/optimize-recommendation")
      .then(rec => { resultEl.innerHTML = _renderOptimizeRecommendation(id, rec, "rule"); })
      .catch(e => { resultEl.innerHTML = '<p class="msg error">' + esc(e.message) + "</p>"; });
    return;
  }
  const refineOptBtn = ev.target.closest("[data-refine-optimize]");
  if (refineOptBtn) {
    const id = refineOptBtn.dataset.refineOptimize;
    const resultEl = document.getElementById("optimize-result-" + id);
    if (!resultEl) return;
    if (!(await confirmModal("AI에게 추천을 보정받습니다 — 실제 LLM API 호출로 비용이 발생합니다. 계속할까요?"))) return;
    resultEl.innerHTML = '<p class="muted">AI 의견 요청 중…(실제 LLM 호출)</p>';
    api("POST", "/api/agent-pool/" + id + "/optimize-recommendation/refine", {})
      .then(rec => {
        resultEl.innerHTML = _renderOptimizeRecommendation(id, rec, rec.source === "llm_refined" ? "llm" : "rule");
      })
      .catch(e => { resultEl.innerHTML = '<p class="msg error">' + esc(e.message) + "</p>"; });
    return;
  }
  const fillOptBtn = ev.target.closest("[data-fill-optimize]");
  if (fillOptBtn) {
    const id = fillOptBtn.dataset.fillOptimize;
    const payload = JSON.parse(fillOptBtn.dataset.optimizePayload || "{}");
    // admin이 "등록된 모델 중 선택" 셀렉트에서 1위 추천과 다른 후보를
    // 골랐으면 그 선택을 우선한다(레지스트리 밖 값은 애초에 옵션으로
    // 나타나지 않으므로 여기서 가져온 provider/model은 항상 등록된
    // 활성 모델이다).
    const pickSelId = fillOptBtn.dataset.optimizePickSel;
    if (pickSelId) {
      const pickSel = document.getElementById(pickSelId);
      if (pickSel) {
        const cands = JSON.parse(pickSel.dataset.candidates || "[]");
        const chosen = cands[Number(pickSel.value)];
        if (chosen) {
          payload.provider = chosen.provider;
          payload.model = chosen.model;
        }
      }
    }
    const providerSel = document.getElementById("provider-" + id);
    const modelSel = document.getElementById("model-" + id);
    if (providerSel && payload.provider) {
      providerSel.value = payload.provider;
      providerSel.dispatchEvent(new Event("change"));
    }
    if (modelSel && payload.model) {
      const hasOption = Array.from(modelSel.options).some(o => o.value === payload.model);
      if (hasOption) {
        modelSel.value = payload.model;
      } else {
        modelSel.value = "__custom__";
        const customInput = document.getElementById("model-" + id + "-custom");
        if (customInput) { customInput.style.display = ""; customInput.value = payload.model; }
      }
    }
    const t = payload.tuning_config || {};
    const setVal = (elId, v) => {
      const el = document.getElementById(elId);
      if (el && v !== undefined && v !== null) el.value = String(v);
    };
    setVal("tune-maxiter-" + id, t.max_iter);
    setVal("tune-maxrpm-" + id, t.max_rpm);
    setVal("tune-maxtime-" + id, t.max_execution_time);
    setVal("tune-maxretry-" + id, t.max_retry_limit);
    setVal("tune-temp-" + id, t.temperature);
    setVal("tune-reasoning-" + id, t.reasoning);
    setVal("tune-maxreasonattempts-" + id, t.max_reasoning_attempts);
    setVal("tune-codeexec-" + id, t.allow_code_execution);
    setVal("tune-respectctx-" + id, t.respect_context_window);
    showMsg("global-msg", "추천값을 입력란에 채웠습니다 — '모델 설정'/'파인튜닝 저장' 버튼을 눌러야 실제로 적용됩니다.", "ok");
    return;
  }
});

// 사용자 보고(2026-10-14, "삭제는 되는데 삭제 후 대화 창을 빠져나가
// 다시 대화창을 열어야 해") — 실측으로 확인한 원인: 삭제 직후 전역
// `refresh()`(현재 탭 전체를 `selectTab()`으로 다시 그림)를 불렀는데,
// "대화" 탭의 기본 화면(`tab.render()`)은 대화 목록이라 상세 화면 안에서
// 호출하면 그 목록으로 되돌아가 버린다(Task 워크스페이스에 임베드된
// 경우도 마찬가지로 그 자리가 통째로 다시 그려져 스크롤까지 끊긴다).
// 지금 열려 있는 대화 상세 화면은 그대로 두고 메시지 영역
// (`#conv-messages`)만 다시 불러온다 — `refreshConversationMessages()`와
// 같은 조회를 하지만, 전역 클릭 핸들러에는 그 함수가 쓰는 `conv`/
// `participants` 클로저가 없어 API로 새로 가져온다.
async function _reloadConversationMessagesInPlace(conversationId) {
  const box = document.getElementById("conv-messages");
  if (!box) return;
  // `refreshConversationMessages()`와 같은 이유로 — 열려 있는 편집
  // 영역이 있으면 건너뛴다(아래 해당 함수의 2026-11-16 주석 참고).
  const openEdit = Array.from(box.querySelectorAll(".artifact-edit"))
    .some(el => el.style.display !== "none");
  if (openEdit) return;
  try {
    const [conv, participants, messages] = await Promise.all([
      api("GET", "/api/conversations/" + conversationId),
      api("GET", "/api/conversations/" + conversationId + "/participants"),
      api("GET", "/api/conversations/" + conversationId + "/messages"),
    ]);
    box.innerHTML = messages.length
      ? messages.map(m => _renderConversationMessage(m, participants, conv)).join("")
      : '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>';
  } catch (e) {
    // 참가자가 아니게 됐거나 대화 자체가 지워졌으면 조용히 무시
    // (`refreshConversationMessages()`와 같은 처리).
  }
}

// 사용자 요청(2026-10-13, "claude artifact 수준으로") — 처음엔 모달
// 오버레이(팝업)로 구현했으나, 사용자 보고(2026-11-16, "대화 중
// '전체화면'의 경우 팝업창으로 표시되는데, 이를 새로운 탭으로
// 변경해줘")로 새 브라우저 탭에서 여는 방식으로 교체한다.
// `content_type === "html"`이면 raw HTML을 그대로 새 탭의 문서로
// 띄운다(이미 완결된 문서라 그대로 열면 됨 — 다운로드 버튼이 받는
// 파일과 동일한 내용). 그 외(markdown/code/text)는 현재 페이지의
// <style> 전체(모든 .code-block/.md-content 등 클래스가 그 안에
// 있음)와 highlight.js 테마만 같이 넣어 새 문서를 만들어, 지금 보이는
// 미리보기와 똑같은 모양으로 렌더되게 한다(mermaid로 이미 그려진
// SVG는 innerHTML에 그대로 포함돼 그대로 뜨지만, Chart.js 캔버스는
// 비트맵이라 innerHTML 복제로는 다시 안 그려지는 알려진 한계로 남겨둔다
// — 이 플랫폼에서 전체화면을 쓰는 대부분의 경우가 HTML 프레젠테이션/
// 코드/문서라 영향이 작다).
function openArtifactFullscreen(artifactId) {
  const preview = document.getElementById(artifactId + "-preview");
  const rawEl = document.getElementById(artifactId + "-raw");
  const block = document.querySelector('[data-artifact-id="' + artifactId + '"]');
  if (!preview || !rawEl) return;
  const contentType = block ? block.dataset.contentType : "";
  let docHtml;
  if (contentType === "html") {
    docHtml = _injectSlideshowController(rawEl.value);
  } else {
    const styleTag = document.querySelector("head > style");
    docHtml =
      '<!doctype html><html><head><meta charset="utf-8">' +
      "<title>HAIR-Platform 결과물</title>" +
      (styleTag ? styleTag.outerHTML : "") +
      '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/atom-one-dark.min.css">' +
      '</head><body style="padding:1.5rem;box-sizing:border-box;max-width:900px;margin:0 auto">' +
      preview.innerHTML +
      "</body></html>";
  }
  const blob = new Blob([docHtml], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const win = window.open(url, "_blank");
  if (!win) {
    showMsg("global-msg", "새 탭을 열지 못했습니다 — 팝업 차단을 해제해주세요", "error");
  }
  // 새 탭이 blob을 로드할 시간을 넉넉히 준 뒤 해제 — 너무 빨리 해제하면
  // 느린 환경에서 탭이 콘텐츠를 못 읽어올 수 있다.
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

// 주입된 슬라이드쇼 컨트롤(위 `_injectSlideshowController()`)의 "슬라이드
// 재생" 버튼이 iframe 안에서 Fullscreen API를 못 쓰면(브라우저별 지원
// 차이) 이 메시지로 알려온다 — 어느 iframe이 보냈는지는 `event.source`
// (그 iframe의 contentWindow)로 역으로 찾아, 이미 확실히 동작하는
// "전체화면"(새 탭 열기)으로 대신 열어준다.
window.addEventListener("message", (ev) => {
  if (!ev.data || !ev.data.__hair_slideshow_fullscreen) return;
  const iframe = Array.from(document.querySelectorAll(".html-preview"))
    .find(el => el.contentWindow === ev.source);
  const block = iframe && iframe.closest(".artifact-block");
  if (block) openArtifactFullscreen(block.dataset.artifactId);
});

// 결과물을 그대로 다운로드(2026-10-07, Tier 1 — "다운로드는 가능한지
// 확인해줘"에 대한 답). content_type별 확장자만 붙이고 Blob으로 바로
// 저장한다 — 서버에 파일로 저장하는 게 아니라 브라우저가 들고 있는
// 문자열을 그 순간 파일로 바꿔주는 것뿐이라 Tier 3(실제 파일 저장소)
// 없이도 가능하다.
const _CONTENT_TYPE_EXTENSIONS = { text: "txt", markdown: "md", html: "html", code: "txt" };
function downloadDeliverable(content, contentType, deliverableId) {
  const ext = _CONTENT_TYPE_EXTENSIONS[contentType] || "txt";
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "deliverable-" + deliverableId + "." + ext;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  // 사용자 요청(2026-10-12, "하나의 브라우저에서 두 개의 계정을 동시에 로그인") —
  // 쿠키는 브라우저 전체가 공유해 탭마다 다른 계정을 유지할 수 없다.
  // sessionStorage는 탭마다 독립이라, 로그인 시 받은 토큰을 여기 저장해두고
  // 매 요청마다 헤더로 보내면 탭 B에서 로그인/로그아웃해도 탭 A는 영향을 안 받는다.
  const token = sessionStorage.getItem("hair_token");
  if (token) opts.headers["Authorization"] = "Bearer " + token;
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* no body */ }
  if (!resp.ok) {
    const detail = (data && data.detail) ? data.detail : (resp.status + " " + resp.statusText);
    throw new Error(detail);
  }
  return data;
}

// 사용자 요청(2026-10-26, 외부 자료 업로드) — `api()`는 항상
// `Content-Type: application/json`을 세팅해 멀티파트 파일 업로드에 못
// 쓴다(서버가 바운더리를 못 찾아 폼 파싱이 깨진다). Content-Type을 직접
// 세팅하지 않아야 브라우저가 멀티파트 바운더리를 자동으로 넣어준다 —
// 그 외(토큰 헤더·에러 처리)는 `api()`와 동일하게 맞춘다.
async function apiUpload(path, formData) {
  const opts = { method: "POST", headers: {}, credentials: "same-origin", body: formData };
  const token = sessionStorage.getItem("hair_token");
  if (token) opts.headers["Authorization"] = "Bearer " + token;
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* no body */ }
  if (!resp.ok) {
    const detail = (data && data.detail) ? data.detail : (resp.status + " " + resp.statusText);
    throw new Error(detail);
  }
  return data;
}

function qs(path, params) {
  const usp = new URLSearchParams();
  for (const k in params) {
    if (params[k] !== undefined && params[k] !== null && params[k] !== "") usp.set(k, params[k]);
  }
  const s = usp.toString();
  return s ? (path + "?" + s) : path;
}

// 사용자 요청(2026-11-12, "꼭 필요한 메시지만 노출되도록") — #global-msg는
// 뷰포트에 고정된 토스트라(위 CSS 참고) 다음 메시지가 올 때까지 안 사라지면
// 화면에 계속 떠서 오히려 방해가 된다. 성공은 짧게, 오류는 좀 더 오래 보여준
// 뒤 자동으로 지운다 — 호출부(274곳)는 아무것도 안 바꿔도 된다. "login-msg"
// (로그인 폼 전용, 뷰포트 고정 아님)는 기존처럼 자동 소멸 없이 그대로 둔다.
let _globalMsgTimer = null;
function showMsg(elId, text, kind) {
  const el = document.getElementById(elId);
  if (elId === "global-msg" && _globalMsgTimer) {
    clearTimeout(_globalMsgTimer);
    _globalMsgTimer = null;
  }
  if (!text) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="msg ' + (kind || "ok") + '">' + esc(text) + "</div>";
  // 에이전트 설정 창이 열려 있으면 창 안에도 보여준다(페이지 상단 메시지는 창에 가려진다).
  const asMsg = elId === "global-msg" && document.getElementById("agent-settings-msg");
  if (asMsg) asMsg.innerHTML = el.innerHTML;
  if (elId === "global-msg") {
    _globalMsgTimer = setTimeout(() => { el.innerHTML = ""; }, kind === "error" ? 8000 : 4000);
  }
}

/* 실제 LLM API를 호출해 오래 걸릴 수 있는 버튼(Crew 실행, AI 에이전트
   생성, 개별 실행, 지식베이스로 보내기)에 "처리 중" 표시를 준다 — 요청 중 버튼을
   비활성화해 중복 클릭도 막는다(사용자 요청, 2026-09-27, "실행 중임을
   알려주는 UI를 추가해줘"). */
async function withBusy(btn, busyLabel, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyLabel;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// 사용자 요청(2026-11-12, "UI/UX 개선") — 지금까지 파괴적/비용 발생
// 액션 확인을 전부 브라우저 네이티브 `confirm()`으로 했다(스타일링
// 불가·앱 전체를 막는 모달·나머지 UI와 톤이 다름). 커스텀 오버레이로
// 교체한다(어두운 반투명 배경 + 둥근 모서리 패널의 시각 톤은
// 이 파일 전역의 모달류와 통일). 반환값은 네이티브 confirm()과
// 동일하게 Promise<boolean>이라, 호출부는 `if (!window.confirm(msg))
// return;`을 `if (!(await confirmModal(msg))) return;`로만 바꾸면
// 된다(그 함수가 async가 아니었다면 그 함수 자체도 async로 바꿔야
// 함 — 호출부별로 직접 확인함).
// 내 비밀번호 바꾸기(2026-11-27, 사용성 평가 13번) — 헤더의 내 계정 이름을 누르면 연다. 바꾸면 다른 브라우저·탭의
// 로그인은 끊기고 이 탭은 새 토큰으로 이어진다.
function openMyPasswordDialog() {
  const overlay = document.createElement("div");
  overlay.className = "confirm-modal-overlay";
  overlay.id = "my-password-overlay";
  overlay.innerHTML =
    '<form class="confirm-modal-box" id="my-password-form" autocomplete="off">' +
    '<h3 style="margin-top:0">비밀번호 바꾸기</h3>' +
    '<p class="muted">바꾸면 다른 브라우저·탭에서 로그인한 곳은 모두 로그아웃됩니다.</p>' +
    '<label style="display:block">지금 비밀번호<br><input type="password" id="mp-current" required style="width:100%" autocomplete="current-password"></label>' +
    '<label style="display:block;margin-top:0.5rem">새 비밀번호(8자 이상)<br><input type="password" id="mp-new" required minlength="8" style="width:100%" autocomplete="new-password"></label>' +
    '<label style="display:block;margin-top:0.5rem">새 비밀번호 확인<br><input type="password" id="mp-new2" required minlength="8" style="width:100%" autocomplete="new-password"></label>' +
    '<p class="msg error" id="mp-msg" hidden></p>' +
    '<div class="confirm-modal-actions"><button type="button" class="small" data-mp-cancel>취소</button>' +
    '<button type="submit" class="small primary">바꾸기</button></div></form>';
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("[data-mp-cancel]").onclick = close;
  overlay.onclick = (ev) => { if (ev.target === overlay) close(); };
  const msg = overlay.querySelector("#mp-msg");
  overlay.querySelector("#my-password-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const cur = overlay.querySelector("#mp-current").value;
    const n1 = overlay.querySelector("#mp-new").value, n2 = overlay.querySelector("#mp-new2").value;
    if (n1 !== n2) { msg.textContent = "새 비밀번호 두 칸이 다릅니다."; msg.hidden = false; return; }
    try {
      const r = await api("POST", "/api/me/password", { current_password: cur, new_password: n1 });
      try { sessionStorage.setItem("hair_token", r.token); } catch (e) { /* 쿠키 세션으로 이어짐 */ }
      close();
      showMsg("global-msg", "비밀번호를 바꿨습니다. 다른 곳의 로그인은 끊겼습니다.", "ok");
    } catch (e) { msg.textContent = e.message; msg.hidden = false; }
  };
  overlay.querySelector("#mp-current").focus();
}

function confirmModal(message, { danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-modal-overlay";
    overlay.innerHTML =
      '<div class="confirm-modal-box">' +
      '<p class="confirm-modal-text">' + esc(message) + '</p>' +
      '<div class="confirm-modal-actions">' +
      '<button type="button" class="small" data-confirm-cancel>취소</button>' +
      '<button type="button" class="small' + (danger ? " danger" : " primary") + '" data-confirm-ok>확인</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    const settle = (result) => { overlay.remove(); resolve(result); };
    overlay.querySelector("[data-confirm-ok]").onclick = () => settle(true);
    overlay.querySelector("[data-confirm-cancel]").onclick = () => settle(false);
    overlay.onclick = (ev) => { if (ev.target === overlay) settle(false); };
    const onKey = (ev) => {
      if (ev.key === "Escape") { settle(false); document.removeEventListener("keydown", onKey); }
    };
    document.addEventListener("keydown", onKey);
    overlay.querySelector("[data-confirm-ok]").focus();
  });
}


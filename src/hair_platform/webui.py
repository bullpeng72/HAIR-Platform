"""Web UI 셸. AgentForge의 웹 UI 패턴(f-string+html.escape, Jinja2 미사용,
빌드 스텝 없는 순수 HTML/CSS/JS)을 그대로 계승한다.

코드 재점검(2026-09-26, R4)에서 발견: 명세가 요구사항 전부에 "화면"·"버튼"·
"Web UI 폼"을 명시적으로 요구했는데도, v0-v4 개발과 3라운드의 코드 재점검을
거치는 동안 HTML/CSS/JS가 단 한 줄도 없었다 — 지금까지 만든 건 잘 테스트된
JSON REST API뿐, 브라우저에서 조작 가능한 화면은 전무했다("전 기능을 인증된
Web UI에서 조작 가능하게 하는 것이 AgentForge와의 핵심 차이다"라는 원칙).

이 모듈은 그 화면을 채운다 — 단일 HTML 페이지 + 바닐라 JS(프레임워크·빌드
스텝 없음)로, 이미 구현·검증된 JSON API(`app.py`)를 그대로 호출한다. 새 비즈니스
로직은 없다 — 순수 프레젠테이션 레이어다.
"""

from __future__ import annotations

import html
import json
from importlib.resources import files

from hair_platform import get_version, presentation_deck

# 화면 원본(2026-11-26, P2-13 — "화면 코드가 13,325줄짜리 파일 하나"): HTML 셸·CSS·화면별 JS·이용 가이드(마크다운)를
# `webui_assets/`의 파일로 나눴다. 여기서 기동 때 한 번 읽어 예전과 같은 한 장짜리 페이지로 조립한다(브라우저·API·
# 테스트가 보는 결과는 그대로). JS는 `js/ORDER` 순서로 이어 붙인다 — 파일을 추가하면 ORDER에도 넣는다. 이제 파이썬
# 문자열이 아니므로 JS·마크다운 안의 역슬래시가 그대로 전달된다.
_ASSETS = files("hair_platform") / "webui_assets"
_CSS_MARK, _JS_MARK, _MANUAL_MARK = "__HAIR_APP_CSS__", "__HAIR_APP_JS__", "__HAIR_MANUAL_MARKDOWN_JSON__"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def _assemble_index_html() -> str:
    order = [n for n in _asset("js/ORDER").split() if n]
    script = "".join(_asset("js/" + n) for n in order)
    manual = json.dumps(_asset("manual.md"), ensure_ascii=False).replace("</", "<\\/")  # </script> 조기 종료 방지
    script = script.replace(_MANUAL_MARK, manual, 1)
    return _asset("index.html").replace(_CSS_MARK, _asset("app.css"), 1).replace(_JS_MARK, script, 1)


INDEX_HTML = _assemble_index_html()
# 이용 가이드 원문(마크다운) — 페이지 안에서는 JSON 문자열(따옴표가 \" 로 이스케이프)이라, 낱말확인 어휘
# (`korean_lexicon._platform_corpus`)처럼 글 자체가 필요한 곳은 이 원문을 쓴다.
MANUAL_MARKDOWN = _asset("manual.md")
# 덱 클래스 점검용 목록 주입(2026-11-21) — 화면(`_deckLintHtml`)과 서버가 같은 원천을 쓴다.
INDEX_HTML = INDEX_HTML.replace(
    "__DECK_CLASSES_JSON__", json.dumps(presentation_deck.defined_classes())
)
# Chart.js 주소·버전도 한 곳(`presentation_deck.CHART_JS_URL`)에서 — 플랫폼 화면과 덱이 같은
# 버전으로 그린다(2026-11-21 하드코딩 재점검: 두 곳에 같은 URL이 따로 적혀 있었다).
INDEX_HTML = INDEX_HTML.replace("__CHART_JS_URL__", presentation_deck.CHART_JS_URL)
# 상단 버전 표기(2026-11-23) — `hair --version`과 같은 값(`hair_platform.get_version()`).
INDEX_HTML = INDEX_HTML.replace("__APP_VERSION__", html.escape(get_version()))


def render_deliverable_view_page(deliverable: dict, task_role_name: str | None) -> str:
    """단독 결과물 뷰 페이지(`GET /deliverables/{id}/view`) — SPA 전체(탭·
    네비게이션)를 띄우지 않고 그 결과물 하나만 담은 가벼운 독립 페이지를
    새 탭에서 연다. 로그인 세션은 그대로 필요하다(`app.py`가
    `Depends(get_current_user)`를 유지 — 비인증 공개 URL이 아니다).

    SPA 안의 `renderContent()`를 그대로 끌어오지 않고 최소한의 자체
    렌더링만 둔다(의도적 — 수백 줄짜리 SPA 전용 함수를 여기로 끌어오면
    결합도가 더 커진다): html은 sandbox iframe, code는 highlight.js,
    markdown/text는 marked.js+DOMPurify, 전부 CDN 로드 실패 시
    textContent로 안전하게 폴백한다(이 플랫폼의 기존 CDN 라이브러리
    전부와 같은 원칙)."""
    title = f"결과물 #{deliverable['id']}"
    if task_role_name:
        title += f" — {task_role_name}"
    payload = json.dumps(
        {"content": deliverable["content"], "content_type": deliverable["content_type"]},
        ensure_ascii=False,
    )
    escaped_title = html.escape(title)
    escaped_content_type = html.escape(deliverable["content_type"])
    escaped_created_at = html.escape(deliverable["created_at"])
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@13.0.3/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px;
    margin: 0 auto; padding: 24px 16px; background: #f7f7f9; color: #1a1a1a; }}
  h1 {{ font-size: 18px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  pre {{ overflow-x: auto; padding: 16px; border-radius: 6px; }}
  .md-content table {{ border-collapse: collapse; }}
  .md-content th, .md-content td {{ border: 1px solid #ccc; padding: 4px 8px; }}
  iframe.html-view {{ width: 100%; height: 80vh; border: 1px solid #ccc; border-radius: 6px; }}
</style>
</head>
<body>
<h1>{escaped_title}</h1>
<div class="meta">content_type: {escaped_content_type} · 작성: <span id="created-at" data-raw-utc="{escaped_created_at}">{escaped_created_at}</span></div>
<div id="content"></div>
<script>
// 사용자 보고(2026-11-17, "대화에 ... 시간정보가 표시되지만 실제
// 시간과 차이가 남 — 한국시간 기준이 아님") — SQLite datetime('now')
// 는 UTC를 타임존 표시 없이 저장한다. 메인 SPA의 fmtTime()과 같은
// 변환을 이 독립 페이지에도 똑같이 적용한다(이 페이지는 SPA의 JS를
// 안 불러오므로 같은 로직을 여기 따로 둔다).
(function () {{
  const span = document.getElementById("created-at");
  const raw = span && span.dataset.rawUtc;
  if (!raw) return;
  const d = new Date(raw.replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return;
  const pad = (n) => String(n).padStart(2, "0");
  span.textContent = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
    pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}})();
const DATA = {payload};
const el = document.getElementById("content");
// 사용자 보고(2026-11-16, "새탭에서 보기에서도 소스로 보임") — SPA의
// `stripOuterFence()`와 똑같은 로직. 이 결과물이 ```html/```code
// 펜스로 통째로 감싸여 저장돼 있으면(LLM이 흔히 이렇게 응답) 벗겨내지
// 않고는 그대로 iframe/코드블록에 그 펜스 문자까지 리터럴로 보인다.
function stripOuterFence(raw) {{
  const closed = raw.match(/^```[a-zA-Z0-9]*\\n([\\s\\S]*?)\\n?```\\s*$/);
  if (closed) return closed[1];
  const openOnly = raw.match(/^```[a-zA-Z0-9]*\\n([\\s\\S]*)$/);
  return openOnly ? openOnly[1] : raw;
}}
// 사용자 요청(2026-11-16, "전체화면 등에서 slide show 기능이
// 제공되도록") — SPA의 `_injectSlideshowController()`와 똑같은 로직
// (`.slide` 섹션이 2개 이상이면 이전/다음·번호·전체화면 컨트롤을
// 직접 주입). 저장된 원본은 안 건드리고 이 보여주기 전용 복사본만
// 바뀐다.
function injectSlideshowController(html) {{
  const slideCount = (html.match(/class=["'][^"']*\\bslide\\b[^"']*["']/g) || []).length;
  if (slideCount < 2) return html;
  const controller =
    // 사용자 보고(2026-11-16, "슬라이드 크기가 변화지 않아 상단
    // 중앙에 작게 표시됨") — SPA의 `_injectSlideshowController()`와
    // 같은 이유로 같은 수정을 적용한다(구형 산출물의 고정 크기
    // CSS·신형 산출물의 LLM 지시 미준수 둘 다에 대비해, 크기/위치는
    // 플랫폼이 항상 강제로 전체 화면을 채우게 함).
    '<style id="__hair_slideshow_style">' +
    'body.__hair_slideshow .slide{{display:none!important}}' +
    'body.__hair_slideshow .slide.__hair_active{{display:flex!important;flex-direction:column;' +
    'position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;' +
    'max-width:none!important;max-height:none!important;min-width:0!important;min-height:0!important;' +
    'margin:0!important;box-sizing:border-box!important;overflow:auto!important}}' +
    '#__hair_slideshow_bar{{position:fixed;left:0;right:0;bottom:0;z-index:999999;display:flex;' +
    'align-items:center;justify-content:center;gap:0.5rem;padding:0.5rem;' +
    'background:rgba(15,23,42,0.75);font-family:sans-serif}}' +
    '#__hair_slideshow_bar button{{background:#fff;border:none;border-radius:4px;' +
    'padding:0.3rem 0.7rem;cursor:pointer;font-size:0.85rem}}' +
    '#__hair_slideshow_bar span{{color:#fff;font-size:0.85rem;min-width:3.5rem;text-align:center}}' +
    '</style>' +
    '<div id="__hair_slideshow_bar">' +
    '<button id="__hair_prev" type="button">이전</button>' +
    '<span id="__hair_counter"></span>' +
    '<button id="__hair_next" type="button">다음</button>' +
    '<button id="__hair_fullscreen" type="button">슬라이드 재생</button>' +
    '</div>' +
    '<script>(function(){{' +
    'var slides=Array.prototype.slice.call(document.querySelectorAll(".slide"));' +
    'if(slides.length<2)return;' +
    'document.body.classList.add("__hair_slideshow");' +
    'var idx=0;' +
    'function show(i){{' +
    'idx=Math.max(0,Math.min(slides.length-1,i));' +
    'slides.forEach(function(s,j){{s.classList.toggle("__hair_active",j===idx)}});' +
    'var c=document.getElementById("__hair_counter");' +
    'if(c)c.textContent=(idx+1)+" / "+slides.length;' +
    '}}' +
    'show(0);' +
    'var prevBtn=document.getElementById("__hair_prev");' +
    'var nextBtn=document.getElementById("__hair_next");' +
    'var fsBtn=document.getElementById("__hair_fullscreen");' +
    'if(prevBtn)prevBtn.addEventListener("click",function(){{show(idx-1)}});' +
    'if(nextBtn)nextBtn.addEventListener("click",function(){{show(idx+1)}});' +
    // 사용자 보고(2026-11-16, "전체화면 버튼 클릭시 아무 동작 안 함") —
    // requestFullscreen()이 없거나 거부되면 이 페이지(부모)에
    // postMessage로 알려 대신 전체화면을 시도하게 한다(아래 이 파일의
    // "message" 리스너 참고).
    'if(fsBtn)fsBtn.addEventListener("click",function(){{' +
    'if(document.fullscreenElement){{document.exitFullscreen();return}}' +
    'var fallback=function(){{try{{window.parent.postMessage({{__hair_slideshow_fullscreen:true}},"*")}}catch(e){{}}}};' +
    'if(document.documentElement.requestFullscreen){{' +
    'var p=document.documentElement.requestFullscreen();' +
    'if(p&&p.catch){{p.catch(fallback)}}' +
    '}}else{{fallback()}}' +
    '}});' +
    'document.addEventListener("keydown",function(ev){{' +
    'if(ev.key==="ArrowRight"||ev.key===" "||ev.key==="PageDown"){{show(idx+1);ev.preventDefault()}}' +
    'else if(ev.key==="ArrowLeft"||ev.key==="PageUp"){{show(idx-1);ev.preventDefault()}}' +
    '}});' +
    '}})();<\\/script>';
  return /<\\/body>/i.test(html) ? html.replace(/<\\/body>/i, controller + "</body>") : html + controller;
}}
const content = (DATA.content_type === "html" || DATA.content_type === "code" || DATA.content_type === "markdown")
  ? stripOuterFence(DATA.content)
  : DATA.content;
if (DATA.content_type === "html") {{
  const iframe = document.createElement("iframe");
  iframe.className = "html-view";
  iframe.sandbox = "allow-scripts allow-fullscreen";
  iframe.setAttribute("allow", "fullscreen");
  iframe.setAttribute("allowfullscreen", "");
  iframe.srcdoc = injectSlideshowController(content);
  el.appendChild(iframe);
  // 슬라이드쇼 "슬라이드 재생" 버튼이 iframe 안에서 Fullscreen API를
  // 못 쓰면 이 페이지(부모) 쪽에 알려온다 — 이 페이지 전체를 대신
  // 전체화면으로 돌린다(SPA와 달리 "새 탭 열기"로 위임할 곳이 없으므로
  // 이 페이지 자체를 전체화면으로).
  window.addEventListener("message", (ev) => {{
    if (!ev.data || !ev.data.__hair_slideshow_fullscreen) return;
    if (document.documentElement.requestFullscreen) {{
      document.documentElement.requestFullscreen().catch(() => {{}});
    }}
  }});
}} else if (DATA.content_type === "code") {{
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = content;
  pre.appendChild(code);
  el.appendChild(pre);
  if (window.hljs) {{ hljs.highlightElement(code); }}
}} else if (window.marked && window.DOMPurify) {{
  const div = document.createElement("div");
  div.className = "md-content";
  div.innerHTML = DOMPurify.sanitize(marked.parse(content));
  el.appendChild(div);
}} else {{
  const pre = document.createElement("pre");
  pre.textContent = content;
  el.appendChild(pre);
}}
</script>
</body>
</html>
"""

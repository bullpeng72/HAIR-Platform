"""HAIR 덱 — HTML 발표자료 디자인 시스템 + "프레젠테이션작성" 스킬.

사용자 요청(2026-11-20, "프레젠테이션작성 skill을 새로 추가해줘, 참조의
내용을 분석해 품질 높은 HTML 방식의 발표자료 생성이 가능하도록 skill 개발").

설계 판단 — 왜 프롬프트만이 아니라 "플랫폼 내장 디자인 시스템"인가:
실측(2026-11-19, AGT-REF-012 "프레젠테이션 자료 작성자")으로 48,929자짜리
HTML 산출물의 대부분이 모델이 매번 새로 쓴 CSS였다 — 로컬 모델에서 생성에
수 분이 걸렸고(2026-11-13 응답 잘림 보고), 배경색·글자색 대비 버그
(2026-11-16 보고)도 그 CSS에서 났다. 그래서 디자인은 이 모듈이 한 번
정의하고, 모델은 정해진 클래스로 **슬라이드 마크업만** 쓴다.
`finalize()`가 저장 시점에 CSS를 문서 안에 인라인으로 넣어, 외부 리소스
없이 혼자 열리는(오프라인·다운로드·인쇄) 완전한 HTML 문서로 만든다.

참조(라이선스 고지 — 아이디어·규칙·토큰 값을 각색해 사용):
- korean-report-skills (Apache-2.0, https://github.com/JangHyun-bin/korean-report-skills)
  — 단일 강조색 토큰, 타일(표면) 교차 규칙, 테두리 기반 콜아웃, 세로선·
  줄무늬 없는 표, 상태 배지, 한국어 보고 문체(평서체·의인화/과장/번역투 금지).
- build-html-ppt (MIT, https://github.com/mclub4/build-html-ppt) — 슬라이드당
  주장 1개, "작업 기록이 아니라 근거의 의미"를 말하는 제목, 구성 유형(composition
  family) 교차, 청중 의사결정 순서, 1280×720 기준 캔버스와 최소 글자 크기,
  설계된 결말. (그 저장소의 한국어 문장 규칙은 epoko77-ai/im-not-ai에서 각색.)
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import overload

SKILL_NAME = "프레젠테이션작성"

# ---------------------------------------------------------------------------
# 디자인 시스템 CSS
# ---------------------------------------------------------------------------
# 단위: `--px`는 "1280×720 논리 캔버스의 1px" — 슬라이드가 어떤 크기로
# 그려지든(목록 보기 카드 / 전체화면 슬라이드쇼) 같은 비율로 축소·확대된다
# (build-html-ppt의 "고정 1280×720 무대를 균일하게 스케일" 원칙을 JS 없이
# CSS 컨테이너 단위로 구현). `--px`는 자식 요소에서 계산되므로 `.slide` 자신의
# 패딩은 %(너비 기준)로 둔다.
DECK_CSS = """
.hair-deck{--accent:#0066cc;--accent-soft:rgba(0,102,204,.08);--on-accent:#fff;
 --ink:#1d1d1f;--ink-2:#3a3a3c;--ink-3:#76767b;--surface:#fff;--surface-2:#f5f5f7;
 --rule:#e0e0e3;--mark:#fbeaa0;--mark-ink:#1d1d1f;
 --font:'Pretendard','Pretendard Variable','Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif;
 --mono:'SF Mono',ui-monospace,Menlo,Consolas,monospace;
 font-family:var(--font);color:var(--ink);background:#e8e8ec;padding:24px 0;margin:0;
 -webkit-font-smoothing:antialiased;word-break:keep-all;overflow-wrap:break-word}
.hair-deck[data-accent=teal]{--accent:#0b7a75;--accent-soft:rgba(11,122,117,.08)}
.hair-deck[data-accent=indigo]{--accent:#4338ca;--accent-soft:rgba(67,56,202,.08)}
.hair-deck[data-accent=orange]{--accent:#c2410c;--accent-soft:rgba(194,65,12,.08)}
.hair-deck[data-accent=green]{--accent:#15803d;--accent-soft:rgba(21,128,61,.08)}
html,body{margin:0;padding:0;background:#e8e8ec}
.hair-deck *{box-sizing:border-box}
.hair-deck .slide{position:relative;width:min(1280px,calc(100vw - 32px));aspect-ratio:16/9;
 margin:0 auto 24px;container-type:size;overflow:hidden;display:flex;flex-direction:column;
 justify-content:safe center;padding:4.8% 6.25%;background:var(--surface);color:var(--ink);
 border:1px solid var(--rule);--px:min(calc(100cqw / 1120),calc(100cqh / 597))}
/* 컨테이너 단위는 패딩을 뺀 내용 영역 기준이다 — 1280×720에서 내용 영역은
   1120×597(패딩 6.25%·4.8%, 둘 다 너비 기준)이므로 그 값으로 나눠야 1:1이 된다.
   모델이 부품 클래스(cols·metrics…)를 section 자체에 붙여도 슬라이드는
   세로 흐름을 유지한다(실측: "slide cols c3"가 슬라이드 전체를 격자로 만들었다). */
.hair-deck section.slide{display:flex;grid-template-columns:none}
/* 표면 — 인접 슬라이드는 같은 표면을 쓰지 않는다. dark는 핵심 2~3장에만 */
.hair-deck .slide.parch{--surface:#f5f5f7;--surface-2:#fff}
.hair-deck .slide.dark,.hair-deck .slide.black{--ink:#fff;--ink-2:#d6d6da;--ink-3:#9a9aa0;
 --rule:#3a3a3e;--surface-2:rgba(255,255,255,.08);--accent:#4da3ff;--accent-soft:rgba(77,163,255,.14)}
.hair-deck[data-accent=teal] .slide.dark,.hair-deck[data-accent=teal] .slide.black{--accent:#3fc1b9}
.hair-deck[data-accent=indigo] .slide.dark,.hair-deck[data-accent=indigo] .slide.black{--accent:#8b8cf8}
.hair-deck[data-accent=orange] .slide.dark,.hair-deck[data-accent=orange] .slide.black{--accent:#fb923c}
.hair-deck[data-accent=green] .slide.dark,.hair-deck[data-accent=green] .slide.black{--accent:#4ade80}
.hair-deck .slide.dark{--surface:#26262a}
.hair-deck .slide.black{--surface:#0b0b0d}
/* 흐름 — 첫 요소 외에는 위 간격을 준다. 출처 줄은 맨 아래로 */
.hair-deck .slide>*{margin:0}
.hair-deck .slide>*+*{margin-top:calc(var(--px)*22)}
.hair-deck .slide>.src{margin-top:auto;padding-top:calc(var(--px)*14)}
.hair-deck .slide:has(>.src)>:first-child{margin-top:auto}
/* 타이포 — 본문 최소 16px(1280 기준) */
.hair-deck .eyebrow{font-size:calc(var(--px)*16);font-weight:600;letter-spacing:.06em;color:var(--accent)}
.hair-deck .eyebrow+h1,.hair-deck .eyebrow+h2{margin-top:calc(var(--px)*10)}
.hair-deck h1,.hair-deck h2,.hair-deck h3{font-weight:700;color:var(--ink);text-wrap:balance}
.hair-deck h1.hero{font-size:calc(var(--px)*60);line-height:1.18;letter-spacing:-.025em;max-width:92%}
.hair-deck h2{font-size:calc(var(--px)*40);line-height:1.25;letter-spacing:-.02em;max-width:90%}
.hair-deck h3{font-size:calc(var(--px)*22);line-height:1.35;letter-spacing:-.01em}
.hair-deck .sub{font-size:calc(var(--px)*22);line-height:1.5;color:var(--ink-2);max-width:85%}
.hair-deck p,.hair-deck li,.hair-deck td,.hair-deck th{font-size:calc(var(--px)*20);line-height:1.55;color:var(--ink-2)}
.hair-deck small,.hair-deck .muted{font-size:calc(var(--px)*16);color:var(--ink-3)}
.hair-deck strong,.hair-deck b{font-weight:700;color:var(--ink)}
.hair-deck mark{background:var(--mark);color:var(--mark-ink);padding:0 .15em;border-radius:3px}
.hair-deck code{font-family:var(--mono);font-size:.9em;background:var(--surface-2);padding:.05em .3em;border-radius:4px}
.hair-deck .src{font-size:calc(var(--px)*14);line-height:1.4;color:var(--ink-3)}
/* 표지 · 결말 */
.hair-deck .slide.cover .rule{width:calc(var(--px)*64);height:1px;background:var(--ink-3)}
.hair-deck .meta{font-size:calc(var(--px)*18);line-height:1.6;color:var(--ink-3)}
.hair-deck .slide.statement h2{font-size:calc(var(--px)*54);line-height:1.22;max-width:88%}
.hair-deck .chapter-no{font-size:calc(var(--px)*96);font-weight:700;line-height:1;color:var(--accent)}
/* 목록 — 강조색 사각 표식, 항목당 2줄 이내 권장 */
.hair-deck ul.points{list-style:none;padding:0}
.hair-deck ul.points li{position:relative;padding-left:calc(var(--px)*26)}
.hair-deck ul.points li+li{margin-top:calc(var(--px)*12)}
.hair-deck ul.points li::before{content:"";position:absolute;left:0;top:.62em;
 width:calc(var(--px)*9);height:calc(var(--px)*9);background:var(--accent);border-radius:2px}
.hair-deck ol.points{padding-left:1.2em}
.hair-deck ol.points li+li{margin-top:calc(var(--px)*10)}
/* 단 나누기 — split은 좁은 주장 단 + 넓은 근거 단 */
.hair-deck .cols{display:grid;grid-template-columns:1fr 1fr;gap:calc(var(--px)*48);align-items:start}
.hair-deck .cols.c3{grid-template-columns:repeat(3,1fr);gap:calc(var(--px)*32)}
.hair-deck .cols.split{grid-template-columns:5fr 7fr}
.hair-deck .cols>*>*+*{margin-top:calc(var(--px)*12)}
/* 지표 카드 — 2~4개. 측정 안 된 값은 .gap(점선)으로 같은 무게로 보인다 */
.hair-deck .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(0,1fr));gap:calc(var(--px)*24)}
.hair-deck .metric{border:1px solid var(--rule);border-radius:calc(var(--px)*16);padding:calc(var(--px)*26)}
.hair-deck .metric .mlabel{font-size:calc(var(--px)*16);color:var(--ink-3)}
.hair-deck .metric .mval{font-size:calc(var(--px)*52);font-weight:700;line-height:1.05;
 letter-spacing:-.02em;color:var(--ink);margin-top:calc(var(--px)*8)}
.hair-deck .metric .mval small{font-size:.42em;color:var(--ink-3);font-weight:600}
.hair-deck .metric .mnote{font-size:calc(var(--px)*15);color:var(--ink-3);margin-top:calc(var(--px)*8)}
.hair-deck .metric.key{border-color:var(--accent);background:var(--accent-soft)}
.hair-deck .metric.gap{border-style:dashed;background:var(--surface-2)}
.hair-deck .metric.gap .mval{color:var(--ink-3)}
.hair-deck .big .num{font-size:calc(var(--px)*120);font-weight:700;line-height:1;color:var(--accent);letter-spacing:-.03em}
.hair-deck .big .label{font-size:calc(var(--px)*24);color:var(--ink-2);margin-top:calc(var(--px)*12)}
/* 표 — 세로선·줄무늬 없음. 헤더 아래 굵은 선, 행 사이 가는 선 */
.hair-deck table{width:100%;border-collapse:collapse}
.hair-deck th{text-align:left;font-size:calc(var(--px)*15);font-weight:600;color:var(--ink-3);
 border-bottom:1.5px solid var(--ink);padding:calc(var(--px)*8) calc(var(--px)*12) calc(var(--px)*8) 0}
.hair-deck td{border-bottom:1px solid var(--rule);padding:calc(var(--px)*10) calc(var(--px)*12) calc(var(--px)*10) 0;vertical-align:top}
.hair-deck table.num td:not(:first-child),.hair-deck table.num th:not(:first-child){text-align:right;font-variant-numeric:tabular-nums}
.hair-deck tr.hl td{background:var(--accent-soft);color:var(--ink)}
/* 긴 표(2026-11-21 실측: 제안서 덱 요구사항 표 13행이 슬라이드를 넘쳐 제목·마지막 행이 잘림) —
   행이 많을수록 행 간격·글자를 줄인다. 넘치면 위에서부터 보이도록 .slide는 safe center. */
.hair-deck .slide:has(tr:nth-child(8)) td,.hair-deck .slide:has(tr:nth-child(8)) th{
 padding-top:calc(var(--px)*5);padding-bottom:calc(var(--px)*5);font-size:calc(var(--px)*16)}
.hair-deck .slide:has(tr:nth-child(12)) td,.hair-deck .slide:has(tr:nth-child(12)) th{
 padding-top:calc(var(--px)*3);padding-bottom:calc(var(--px)*3);font-size:calc(var(--px)*13.5)}
.hair-deck .slide:has(tr:nth-child(12))>*+*{margin-top:calc(var(--px)*12)}
/* 절차 · 타임라인 */
.hair-deck .steps{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:calc(var(--px)*20)}
.hair-deck .step{border-top:calc(var(--px)*3) solid var(--accent);padding-top:calc(var(--px)*16)}
.hair-deck .step .n{font-size:calc(var(--px)*15);font-weight:700;color:var(--accent);letter-spacing:.04em}
.hair-deck .step h3{margin-top:calc(var(--px)*6)}
.hair-deck .step p{font-size:calc(var(--px)*17);margin-top:calc(var(--px)*6)}
.hair-deck .step.now{background:var(--accent-soft);padding:calc(var(--px)*16)}
/* 막대 비교 — <div class="bar" style="--v:72"> (0~100) */
.hair-deck .bars{display:grid;gap:calc(var(--px)*14)}
.hair-deck .bar{display:grid;grid-template-columns:28% 1fr calc(var(--px)*110);align-items:center;gap:calc(var(--px)*16)}
.hair-deck .bar .bl{font-size:calc(var(--px)*18);color:var(--ink-2)}
.hair-deck .bar .bt{height:calc(var(--px)*18);background:var(--surface-2);border-radius:calc(var(--px)*4);overflow:hidden}
.hair-deck .bar .bt::before{content:"";display:block;height:100%;width:calc(var(--v,0)*1%);background:var(--ink-3)}
.hair-deck .bar.key .bt::before{background:var(--accent)}
.hair-deck .bar .bv{font-size:calc(var(--px)*18);font-weight:600;color:var(--ink);text-align:right;font-variant-numeric:tabular-nums}
/* 차트(Chart.js) — 플랫폼이 data-chart(JSON)를 읽어 그린다(2026-11-21) */
.hair-deck .hair-chart{position:relative;width:100%;height:calc(var(--px)*340)}
.hair-deck .cols .hair-chart{height:calc(var(--px)*300)}
/* 콜아웃 — 색이 아니라 테두리 위치·두께로 구분 */
.hair-deck .finding,.hair-deck .note,.hair-deck .warn,.hair-deck .claim,.hair-deck .ask{
 border-radius:calc(var(--px)*12);padding:calc(var(--px)*20) calc(var(--px)*24)}
.hair-deck .finding{border:1px solid var(--rule);border-left:calc(var(--px)*4) solid var(--accent)}
.hair-deck .warn{border:1px solid var(--rule);border-left:calc(var(--px)*4) solid var(--ink)}
.hair-deck .note{border:1px solid var(--rule)}
.hair-deck .claim{background:var(--surface-2)}
.hair-deck .ask{background:var(--accent-soft);border:1px solid var(--accent)}
.hair-deck .ask h3{color:var(--accent)}
.hair-deck .cav{border-top:1px solid var(--rule);margin-top:calc(var(--px)*12);padding-top:calc(var(--px)*10);
 font-size:calc(var(--px)*15);color:var(--ink-3)}
/* 상태 배지 — 채움(측정됨) → 외곽선(구현됨) → 점선(미측정) */
.hair-deck .bdg{display:inline-block;font-size:calc(var(--px)*13);font-weight:600;line-height:1.5;
 padding:0 calc(var(--px)*9);border-radius:999px;vertical-align:.12em;white-space:nowrap}
.hair-deck .bdg.meas{background:var(--accent);color:var(--on-accent)}
.hair-deck .bdg.impl{color:var(--accent);border:1px solid var(--accent)}
.hair-deck .bdg.none{color:var(--ink-3);border:1px dashed var(--ink-3)}
/* 인용 */
.hair-deck blockquote{border-left:calc(var(--px)*4) solid var(--accent);padding-left:calc(var(--px)*28)}
.hair-deck blockquote p{font-size:calc(var(--px)*34);line-height:1.4;color:var(--ink);font-weight:600}
.hair-deck blockquote .who{font-size:calc(var(--px)*17);color:var(--ink-3);font-weight:400;margin-top:calc(var(--px)*14)}
/* 인쇄 — 슬라이드 1장 = 1페이지(16:9) */
@media print{
 @page{size:1280px 720px;margin:0}
 html,body,.hair-deck{background:none;padding:0}
 .hair-deck .slide{width:1280px;height:720px;margin:0;border:none;break-after:page;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}
}
""".strip()

_CSS_MARKER = 'id="hair-deck-css"'
_DECK_RE = re.compile(r"""class\s*=\s*["'][^"']*\bhair-deck\b""", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:html|HTML)?[ \t]*\n(.*?)(?:\n```|\Z)", re.DOTALL)
_DOC_RE = re.compile(r"(<!DOCTYPE\s+html.*?</html\s*>|<html[\s>].*?</html\s*>)", re.DOTALL | re.IGNORECASE)
_DOC_OPEN_RE = re.compile(r"(<!DOCTYPE\s+html.*|<html[\s>].*)", re.DOTALL | re.IGNORECASE)
_MAIN_RE = re.compile(
    r"""<main\b[^>]*class\s*=\s*["'][^"']*\bhair-deck\b.*?(?:</main\s*>|\Z)""",
    re.DOTALL | re.IGNORECASE,
)
_HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HTML_OPEN_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_TITLE_TAG_RE = re.compile(r"<title\b", re.IGNORECASE)
_FIRST_HEADING_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def is_deck(text: str | None) -> bool:
    """HAIR 덱 마크업(`class="hair-deck"`)을 쓴 산출물인가."""
    return bool(text) and "hair-deck" in text and bool(_DECK_RE.search(text))


def _extract_markup(text: str) -> str:
    """앞뒤 설명 문장·코드펜스를 걷어 내고 덱 마크업만 꺼낸다 — 로컬 모델이
    "다음은 발표자료입니다:" 같은 문장을 붙이면 대화 화면이 HTML 문서로
    인식하지 못해(첫 글자가 `<!DOCTYPE`이어야 함) 원문 그대로 보였다."""
    for m in _FENCE_RE.finditer(text):
        if _DECK_RE.search(m.group(1)):
            text = m.group(1)
            break
    doc = _DOC_RE.search(text) or _DOC_OPEN_RE.search(text)
    if doc and _DECK_RE.search(doc.group(1)):
        return doc.group(1).strip()
    main = _MAIN_RE.search(text)
    if main:
        return main.group(0).strip()
    return text.strip()


def _title_from(markup: str) -> str:
    m = _FIRST_HEADING_RE.search(markup)
    if not m:
        return "발표자료"
    title = " ".join(_TAG_RE.sub(" ", m.group(1)).split())
    return title[:80] or "발표자료"


@overload
def finalize(text: str) -> str: ...
@overload
def finalize(text: None) -> None: ...
def finalize(text: str | None) -> str | None:
    """덱 마크업이면 디자인 시스템 CSS를 넣은 완전한 HTML 문서로 만든다.
    덱이 아니면 그대로 돌려준다(모든 산출물 저장 경로에서 안전하게 호출할
    수 있다). 이미 CSS가 들어 있으면 그대로(멱등) — 모델이 따로 쓴
    `<style>`은 지우지 않고, 디자인 시스템 뒤에 오게 해 의도한 덮어쓰기는
    그대로 적용된다."""
    if not is_deck(text):
        return text
    assert text is not None
    return _with_chart_runtime(
        _with_css(_normalize_slide_classes(_fences_to_deck_charts(_extract_markup(text))))
    )


# 정의되지 않은 클래스 감지(2026-11-21 실측 — 모델이 metrics·steps 대신 hair-bignum·
# hair-bullets 같은 클래스를 새로 만들어 핵심 수치 장이 서식 없는 글자로 보였다). 덱 CSS와
# 출력 형식 안내에 나오는 클래스가 "정의된 클래스"다 — 목록을 코드에 따로 적지 않고 두
# 원천에서 뽑아, CSS·안내가 바뀌면 자동으로 따라간다.
_CSS_CLASS_RE = re.compile(r"\.([a-zA-Z][a-zA-Z0-9_-]*)")
_CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def defined_classes() -> list[str]:
    names = set(_CSS_CLASS_RE.findall(DECK_CSS))
    for attr in _CLASS_ATTR_RE.findall(SKILL_OUTPUT_FORMAT):
        names.update(attr.split())
    # 출력 형식 안내의 "구성유형·표면" 이름(cover·statement·split …, light·parch·dark·black).
    names.update(_SLIDE_CLASS_NAMES.split("|"))
    # 영문 클래스명만(안내문의 "구성유형"·"표면" 같은 한글 자리표시어 제외).
    return sorted(n for n in names if n.isascii() and not n[0].isdigit())


def undefined_classes(html_text: str | None) -> list[str]:
    """덱 안에서 쓰였지만 디자인 시스템에 없는 클래스(덱이 아니면 빈 목록)."""
    if not is_deck(html_text):
        return []
    assert html_text is not None
    known = set(defined_classes())
    used: set[str] = set()
    for attr in _CLASS_ATTR_RE.findall(html_text):
        used.update(attr.split())
    return sorted(c for c in used if c not in known)


# 슬라이드 클래스 보정(2026-11-21 실측 — 모델이 "slide cover" 대신 "hair-slide hair-cover"
# 를 써서 덱 CSS·슬라이드쇼가 슬라이드를 하나도 못 찾았다). <section>의 class에서 덱이
# 정의한 이름에 "hair-" 접두어가 붙은 것만 접두어를 뗀다(그 밖의 클래스는 그대로).
_SLIDE_CLASS_NAMES = (
    "slide|cover|statement|split|metrics|big|compare|bars|process|chapter|quote|decision|"
    "light|parch|dark|black"
)
_SECTION_CLASS_RE = re.compile(r"""(<section\b[^>]*\bclass\s*=\s*["'])([^"']*)(["'])""", re.IGNORECASE)
_HAIR_PREFIXED_RE = re.compile(rf"\bhair-({_SLIDE_CLASS_NAMES})\b")


def _normalize_slide_classes(markup: str) -> str:
    def repl(m: re.Match) -> str:
        fixed = _HAIR_PREFIXED_RE.sub(r"\1", m.group(2))
        return f"{m.group(1)}{fixed}{m.group(3)}"

    markup = _SECTION_CLASS_RE.sub(repl, markup)
    # 모든 요소: "hair-" + 정의된 클래스(예: hair-decision·hair-note·hair-metrics)는 접두어를
    # 뗀다 — 모델이 덱 클래스를 의도하고 접두어만 붙인 경우다. hair-deck·hair-chart처럼 원래
    # 정의된 이름과, 정의에 없는 이름(hair-bignum 등)은 그대로 둔다(화면에서 경고로 표시).
    known = set(defined_classes())

    def fix_attr(m: re.Match) -> str:
        tokens = [
            t[5:] if t.startswith("hair-") and t not in known and t[5:] in known else t
            for t in m.group(2).split()
        ]
        return f"{m.group(1)}{' '.join(tokens)}{m.group(3)}"

    return _ANY_CLASS_ATTR_RE.sub(fix_attr, markup)


_ANY_CLASS_ATTR_RE = re.compile(r"""(\bclass\s*=\s*["'])([^"']*)(["'])""", re.IGNORECASE)


# 덱 안에 마크다운식 ```chart 펜스를 쓴 경우의 보정(2026-11-21 실측 — 프레젠테이션
# Agent가 덱 HTML 안에 ```chart 펜스 3개를 그대로 넣어 글자로 보였다). JSON이 올바르면
# 덱 차트 컴포넌트로 바꾸고, 아니면 그대로 둔다.
_CHART_FENCE_RE = re.compile(r"```chart[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)


def _fences_to_deck_charts(markup: str) -> str:
    def repl(m: re.Match) -> str:
        raw = m.group(1).strip()
        try:
            json.loads(raw)
        except ValueError:
            return m.group(0)
        return f'<div class="hair-chart" data-chart="{html_lib.escape(raw, quote=True)}"></div>'

    return _CHART_FENCE_RE.sub(repl, markup)


# 덱 차트 런타임(2026-11-21, "데이터 시각화 스킬을 에이전트에 적극 활용") — 모델은
# <script>를 쓰지 않고 `<div class="hair-chart" data-chart='{...}'>`만 쓴다. 덱에 차트가
# 있을 때만 플랫폼과 같은 Chart.js(고정 버전)와 초기화 코드를 넣는다. data-chart는 간단
# 형식(type·labels·series·unit·title)과 Chart.js 전체 설정({type, data, options}) 둘 다
# 받는다 — '차트 생성기' 도구 결과를 그대로 넣어도 그려진다. 색은 덱 강조색을 따른다.
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"
_CHART_MARKER = 'id="hair-deck-charts"'
_CHART_RUNTIME = (
    f'<script src="{CHART_JS_URL}"></script>\n'
    f"<script {_CHART_MARKER}>\n"
    "(function(){\n"
    "function v(el,n,d){var x=getComputedStyle(el).getPropertyValue(n).trim();return x||d;}\n"
    "function build(el){var spec;try{spec=JSON.parse(el.getAttribute('data-chart'));}catch(e){"
    "el.textContent='차트 데이터를 읽지 못했습니다';return;}\n"
    " var canvas=document.createElement('canvas');el.appendChild(canvas);\n"
    " var accent=v(el,'--accent','#0066cc'),ink2=v(el,'--ink-2','#3a3a3c'),ink3=v(el,'--ink-3','#76767b'),"
    "rule=v(el,'--rule','#d2d2d7');\n"
    " var pal=[accent,ink3,'#a1a1a6','#c7c7cc','#5b6b7a','#d9d9de'];\n"
    " if(spec.data&&spec.type){spec.options=spec.options||{};spec.options.maintainAspectRatio=false;"
    "spec.options.animation=false;var rd=(spec.type==='pie'||spec.type==='doughnut');"
    "(spec.data.datasets||[]).forEach(function(d,i){if(!d.backgroundColor){d.backgroundColor=rd?"
    "(spec.data.labels||[]).map(function(_,j){return pal[j%pal.length];}):pal[i%pal.length];}"
    "if(!d.borderColor){d.borderColor=rd?v(el,'--surface','#fff'):pal[i%pal.length];}});"
    "new Chart(canvas,spec);return;}\n"
    " var type=spec.type||spec.chart_type||'bar',labels=spec.labels||[],series=spec.series||{},"
    "names=Object.keys(series),round=(type==='pie'||type==='doughnut');\n"
    " var ds=names.map(function(n,i){var c=pal[i%pal.length];return{label:n,data:series[n],"
    "backgroundColor:round?labels.map(function(_,j){return pal[j%pal.length];}):c,"
    "borderColor:round?v(el,'--surface','#fff'):c,borderWidth:2,tension:.25,pointRadius:3,fill:false};});\n"
    " new Chart(canvas,{type:type,data:{labels:labels,datasets:ds},options:{responsive:true,"
    "maintainAspectRatio:false,animation:false,plugins:{legend:{display:round||names.length>1,"
    "labels:{color:ink2}},title:{display:!!spec.title,text:spec.title,color:ink2}},"
    "scales:round?{}:{x:{ticks:{color:ink3},grid:{display:false}},y:{beginAtZero:true,"
    "ticks:{color:ink3},grid:{color:rule},title:{display:!!spec.unit,text:spec.unit,color:ink3}}}}});}\n"
    # 한 슬라이드에 차트가 여러 개면 높이를 줄여 본문이 슬라이드 밖으로 밀리지 않게 한다
    # (2026-11-21 실측: 차트 2개를 세로로 쌓은 장의 결론 문장이 잘렸다). 차트를 감싼 구조가
    # 모델마다 달라 CSS 형제 선택자로는 못 잡아, 슬라이드 단위로 센다.
    "function run(){if(!window.Chart)return;document.querySelectorAll('.hair-deck .slide')"
    ".forEach(function(s){var cs=s.querySelectorAll('.hair-chart');if(cs.length>1)cs.forEach("
    "function(c){c.style.height='calc(var(--px)*'+(cs.length>2?150:190)+')';});});"
    "document.querySelectorAll('.hair-deck .hair-chart')"
    ".forEach(function(el){if(!el.querySelector('canvas'))build(el);});}\n"
    "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();\n"
    "window.addEventListener('load',run);\n"
    "})();\n</script>"
)
_HAS_CHART_RE = re.compile(r"""class\s*=\s*["'][^"']*\bhair-chart\b""", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


def _with_chart_runtime(doc: str) -> str:
    """덱에 차트가 있고 런타임이 아직 없으면 </body> 앞에 넣는다(멱등)."""
    if _CHART_MARKER in doc or not _HAS_CHART_RE.search(doc):
        return doc
    close = None
    for close in _BODY_CLOSE_RE.finditer(doc):
        pass
    if close is None:
        return doc + "\n" + _CHART_RUNTIME
    return doc[: close.start()] + _CHART_RUNTIME + "\n" + doc[close.start():]


def _with_css(markup: str) -> str:
    if _CSS_MARKER in markup:
        return markup
    style = f"<style {_CSS_MARKER}>\n{DECK_CSS}\n</style>"
    head_open = _HEAD_OPEN_RE.search(markup)
    if head_open:
        insert_at = head_open.end()
        extra = ""
        if "charset" not in markup[: insert_at + 400].lower():
            extra = '<meta charset="utf-8">'
        return markup[:insert_at] + extra + style + markup[insert_at:]
    html_open = _HTML_OPEN_RE.search(markup)
    if html_open:
        head = (
            '<head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            + ("" if _TITLE_TAG_RE.search(markup) else f"<title>{html_lib.escape(_title_from(markup))}</title>")
            + style
            + "</head>"
        )
        return markup[: html_open.end()] + head + markup[html_open.end():]
    body = markup if _MAIN_RE.match(markup) else f'<main class="hair-deck">{markup}</main>'
    return (
        '<!DOCTYPE html>\n<html lang="ko">\n<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html_lib.escape(_title_from(markup))}</title>{style}</head>\n"
        f"<body>\n{body}\n</body>\n</html>"
    )


@overload
def finalize_with_type(text: str, content_type: str | None) -> tuple[str, str | None]: ...
@overload
def finalize_with_type(text: None, content_type: str | None) -> tuple[None, str | None]: ...
def finalize_with_type(text: str | None, content_type: str | None) -> tuple[str | None, str | None]:
    """산출물(deliverable) 저장용 — 덱이면 완성된 HTML과 `'html'`을, 아니면
    입력 그대로를 돌려준다. 갤러리·업무 화면은 `content_type`으로 렌더링
    방식을 고르므로(`'html'`이어야 iframe 슬라이드쇼로 보인다), 요청 타입이
    text/markdown이었어도 덱이면 html로 바로잡는다."""
    if not is_deck(text):
        return text, content_type
    return finalize(text), "html"


# ---------------------------------------------------------------------------
# 스킬 정의 — `skill_pool` 시드(`db._seed_presentation_skill`)가 그대로 쓴다.
# 로컬 모델 시스템 프롬프트에 그대로 들어가므로 짧고 규칙 위주로 쓴다.
# ---------------------------------------------------------------------------
SKILL_DESCRIPTION = (
    "청중과 목적에 맞춘 HTML 발표자료를 HAIR 덱 디자인 시스템으로 작성하는 역량 — "
    "디자인(CSS)은 플랫폼이 자동으로 넣으므로 정해진 클래스로 슬라이드 마크업만 쓴다. "
    "슬라이드마다 주장 하나, 제목은 근거의 의미를 말하고, 구성 유형을 섞어 설계된 결말로 끝낸다"
)

SKILL_OUTPUT_FORMAT = """\
아래 골격의 HTML 문서 하나만 출력한다(앞뒤 설명 문장·코드펜스 금지). <style>·<script>·외부 링크·이미지·이모지를 쓰지 않는다 — 디자인은 플랫폼이 넣는다.
<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>발표 제목</title></head><body>
<main class="hair-deck" data-accent="blue">  (강조색 1개: blue|teal|indigo|orange|green)
<section class="slide cover black"><p class="eyebrow">분류·행사</p><h1 class="hero">제목</h1><div class="rule"></div><p class="meta">날짜 · 조직 · 발표자</p></section>
<section class="slide 구성유형 표면"><p class="eyebrow">장 이름</p><h2>제목</h2> …본문… <p class="src">출처·기준일</p></section>
section에는 slide·구성유형·표면 클래스만 붙이고, cols·metrics·steps 같은 부품 클래스는 안쪽 div에 붙인다.
</main></body></html>
표면: light(기본)·parch·dark·black — 이웃 슬라이드와 다른 표면, dark는 핵심 2~3장만, 표지·결말은 black.
구성유형과 본문 부품:
- cover: h1.hero, .rule, .meta
- statement: h2 하나(핵심 주장) + p.sub
- split: <div class="cols split"><div>주장·해석</div><div>근거(표·목록·막대)</div></div>
- metrics: <div class="metrics"><div class="metric key"><p class="mlabel">지표</p><p class="mval">42<small>%</small></p><p class="mnote">기준</p></div>…</div> (2~4개, 미측정은 metric gap)
- big: <div class="big"><p class="num">3.2배</p><p class="label">의미</p></div>
- compare: <table class="num"><tr><th>…</th></tr><tr class="hl"><td>…</td></tr></table> (강조 행 1개)
- bars: <div class="bars"><div class="bar key" style="--v:72"><span class="bl">항목</span><span class="bt"></span><span class="bv">72%</span></div></div> (--v는 0~100)
- chart: <div class="hair-chart" data-chart='{"type":"line","title":"월별 매출","labels":["1월","2월","3월"],"series":{"매출":[12,15,18]},"unit":"억 원"}'></div> (type: bar 비교·line 추이·doughnut 구성비 — 수치는 본문·자료의 값만, 그림은 플랫폼이 그린다. 추이·구성비나 계열이 2개 이상이면 bars 대신 chart)
- process: <div class="steps"><div class="step"><p class="n">01</p><h3>단계</h3><p>설명</p></div>…</div> (3~5단계, 현재 단계 step now)
- chapter: <p class="chapter-no">02</p><h2>장 제목</h2>
- quote: <blockquote><p>인용</p><p class="who">— 출처</p></blockquote>
- decision: <div class="ask"><h3>요청 사항</h3><ul class="points"><li>…</li></ul></div>
공통: ul.points(항목 5개 이하·각 2줄 이내), .cols / .cols.c3, .finding(결론)·.warn(주의)·.note(범위)·.claim, .cav(결론의 한계), .bdg.meas/.impl/.none(상태), <mark>(장당 1곳), <strong>"""

SKILL_CONSTRAINTS = """\
[구성] ① 청중·목적을 먼저 정하고 의사결정 순서로 배치한다: 주제와 청중이 얻을 것 → 공통 맥락 → 근거·대비 → 결정·제안 → 세부(구현·아키텍처·예외) → 결말. 임원 보고는 결정·영향·리스크·요청을 앞에, 기술 공유는 구체적 대상을 앞에 둔다. ② 슬라이드마다 주장 1개. ③ 8~15장이면 구성유형을 5가지 이상 섞고, 같은 유형 3장 연속·카드 나열(metrics/cols.c3) 남발을 피한다. ④ 표지는 주제가 3초 안에 보이게, 결말(decision 또는 black statement)은 처음 약속에 답하고 다음 행동 하나를 남긴다 — "감사합니다"·Q&A 장으로 끝내지 않는다.
[제목] ⑤ 제목은 작성자가 한 일(비교했다·분석했다·살펴봤다)이 아니라 근거가 뜻하는 바를 말한다 — 나쁜 예 "시장 10개사를 비교했다", 좋은 예 "상위 2개사가 점유율의 60%를 차지한다". 표본 수·기간·기준은 eyebrow나 .src로 내린다. ⑥ 설정·구성·비교·아키텍처 장은 명사구나 짧은 비교 라벨("현행 vs 개선안")을 쓰고, '-다'로 끝나는 제목은 3장 연속 쓰지 않으며 전체의 40%를 넘기지 않는다. ⑦ 제목만 순서대로 읽어도 주장의 흐름이 보여야 한다.
[문장] ⑧ 평서체(~한다·~이다)로 통일하고 합니다체와 섞지 않는다. ⑨ 상투어(결론적으로·주목할 만하다·시사하는 바가 크다·~를 통해·~에 있어서)와 '또한·따라서'로 시작하는 문장 연속, 첫째·둘째·셋째식 기계적 병렬을 피한다. ⑩ 의인화·과장·위협 표현 대신 수치·조건·행동으로 쓴다. ⑪ 공식 한국어 명칭을 쓰고 영어는 고유명사·약어·규격만 남긴다. 중국어·한자 문자(예: 个人)를 섞지 않는다.
[근거] ⑫ 숫자는 단위와 함께 쓰고, 자료에 기준일·출처가 있으면 .src에 둔다. 주어진 자료·대화에 없는 수치·연도·날짜·출처·표본 수·담당 부서·목표치를 지어내지 않는다 — 없으면 .src를 생략하거나 "확인 필요"로 두고, 빈 지표는 metric gap으로 비워 둔다. 계산한 수치(증감률·배수·평균)는 식으로 다시 확인한다. ⑬ 결론과 그 한계(.cav)는 같은 상자에 둔다.
[화면] ⑭ 한 장에 담기지 않으면 글자를 줄이지 말고 슬라이드를 나눈다. ⑮ 강조색은 하나(포인터로만), 상태는 색이 아니라 .bdg로 표시한다. ⑯ 내용 없는 큰 상자·장식용 빈 카드를 만들지 않는다. ⑰ 표지·statement·chapter를 뺀 본문 장은 화면의 절반 이상을 의미 있는 내용으로 채운다 — 단(cols)마다 한 문장으로 끝내지 말고 근거(수치·사례·비교·조건)를 2~4개 담는다. ⑱ 출력 형식에 나온 클래스만 쓴다 — 목록에 없는 클래스(예: hair-bignum·hair-bullets·hair-note)를 새로 만들면 디자인이 적용되지 않는다. 핵심 수치는 metrics, 단계·과제는 steps, 결정 요청은 ask, 발표 노트는 슬라이드에 넣지 않는다."""

SKILL_EXAMPLES = (
    "제목 고치기 — '경쟁사 5곳의 가격을 조사했다' → '경쟁사 평균 대비 18% 높은 가격이 "
    "이탈 1순위 원인이다' / 'Static Exporter와 Dynamic Exporter의 설정 방식을 비교한다' → "
    "'Static vs Dynamic Exporter' / '데이터가 말해 주는 것' → '분기 매출 추이'"
)

"""메뉴 찾기·이 화면 도움말(2026-11-26, P2-12 — `webui_assets/js/29-menu-search.js`)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from importlib.resources import files

import pytest

from hair_platform import webui

ASSETS = files("hair_platform") / "webui_assets"
SEARCH_JS = (ASSETS / "js" / "29-menu-search.js").read_text(encoding="utf-8")
MANUAL = (ASSETS / "manual.md").read_text(encoding="utf-8")


def _tabs() -> list[dict]:
    src = (ASSETS / "js" / "01-knowledge-submissions.js").read_text(encoding="utf-8")
    block = src[src.index("const TABS = ["):src.index("];", src.index("const TABS = ["))]
    out = []
    for m in re.finditer(r'\{ id: "([^"]+)", label: "([^"]+)", group: "[^"]+", roles: \[([^\]]+)\]', block):
        out.append({"id": m.group(1), "label": m.group(2), "roles": re.findall(r'"([^"]+)"', m.group(3))})
    return out


def _hints() -> dict[str, str]:
    block = SEARCH_JS[SEARCH_JS.index("const MENU_HINTS = {"):SEARCH_JS.index("\n};", SEARCH_JS.index("const MENU_HINTS = {"))]
    return dict(re.findall(r'^  (\w+): \{ help: "([^"]+)"', block, re.M))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("`", "")).lower()


def test_every_tab_has_a_hint_and_every_help_target_exists_in_the_guide():
    tabs, hints = _tabs(), _hints()
    assert len(tabs) >= 30
    assert {t["id"] for t in tabs} == set(hints), set(t["id"] for t in tabs) ^ set(hints)
    h2_ids = set(re.findall(r'^<h2 id="([^"]+)">', MANUAL, re.M))
    headings = [_norm(h) for h in re.findall(r"^### (.+)$", MANUAL, re.M)] + \
        [_norm(h) for h in re.findall(r'^<h2 id="[^"]+">(.+)</h2>', MANUAL, re.M)]
    for tab_id, target in hints.items():
        if target.startswith("#"):
            assert target[1:] in h2_ids, (tab_id, target)
        else:
            assert any(h.startswith(_norm(target)) or _norm(target) in h for h in headings), (tab_id, target)


def test_header_buttons_and_guide_mention_are_in_the_page():
    html = webui.INDEX_HTML
    assert 'id="menu-search-btn"' in html and 'id="screen-help-btn"' in html
    assert "메뉴 찾기" in MANUAL and "Ctrl+K" in MANUAL


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
@pytest.mark.parametrize("role, query, expect_tab, absent_tab, expect_guide", [
    ("admin", "비용", "cost", None, None),
    ("member", "비용", None, "cost", None),           # member에게 없는 화면은 나오지 않는다
    ("admin", "ㅂㅇ", "cost", None, None),            # 초성
    ("member", "API 키", "integrations", None, None),  # 낱말(하는 일)
    ("viewer", "API 키", None, "integrations", None),  # viewer는 연동 설정 없음
    ("member", "환경변수", None, None, "서버 PC 환경 설정"),
    ("admin", "삭제가 안", None, None, "삭제가 안 될 때"),
])
def test_search_results_respect_role_and_find_guide_sections(tmp_path, role, query, expect_tab, absent_tab, expect_guide):
    tabs = _tabs()
    stub = (
        "const document = { addEventListener(){}, getElementById(){ return { addEventListener(){} }; } };\n"
        f"let ME = {{ role: {json.dumps(role)} }}; let currentTab = 'home';\n"
        f"const TABS = {json.dumps(tabs, ensure_ascii=False)};\n"
        f"const MANUAL_MARKDOWN = {json.dumps(MANUAL, ensure_ascii=False)};\n"
        "function esc(s){ return String(s); } function selectTab(){}\n"
    )
    probe = tmp_path / "probe.js"
    probe.write_text(stub + SEARCH_JS + f"\nprocess.stdout.write(JSON.stringify(_menuSearchResults({json.dumps(query, ensure_ascii=False)})));",
                     encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(probe)], capture_output=True, text=True, check=True).stdout)
    tab_ids = [r["id"] for r in out if r["kind"] == "tab"]
    if expect_tab:
        assert tab_ids and tab_ids[0] == expect_tab, out
    if absent_tab:
        assert absent_tab not in tab_ids, out
    if expect_guide:
        assert any(r["kind"] == "guide" and expect_guide in r["title"] for r in out), out

"""화면 원본 파일 분리(2026-11-26, P2-13 — `webui_assets/`)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from importlib.resources import files

import pytest

from hair_platform import webui

ASSETS = files("hair_platform") / "webui_assets"


def _order() -> list[str]:
    return [n for n in (ASSETS / "js" / "ORDER").read_text(encoding="utf-8").split() if n]


def test_every_js_file_is_listed_in_order_and_exists():
    listed = _order()
    on_disk = sorted(p.name for p in (ASSETS / "js").iterdir() if p.name.endswith(".js"))
    assert sorted(listed) == on_disk and len(listed) == len(set(listed))


def test_index_html_is_fully_assembled():
    html = webui.INDEX_HTML
    for mark in ("__HAIR_APP_CSS__", "__HAIR_APP_JS__", "__HAIR_MANUAL_MARKDOWN_JSON__", "__APP_VERSION__",
                 "__CHART_JS_URL__", "__DECK_CLASSES_JSON__"):
        assert mark not in html, mark
    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    assert "<style>\n" + css + "</style>" in html  # CSS가 셸의 <style> 안에 그대로
    # 이용 가이드 마크다운이 JSON 문자열로 들어가고, </script>로 페이지 스크립트를 끊지 않는다
    manual = (ASSETS / "manual.md").read_text(encoding="utf-8")
    literal = json.dumps(manual, ensure_ascii=False).replace("</", "<\\/")
    assert f"const MANUAL_MARKDOWN = {literal};" in html
    for name in _order():
        first_line = (ASSETS / "js" / name).read_text(encoding="utf-8").splitlines()[0]
        assert first_line in html, name


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_each_js_file_and_the_assembled_script_pass_syntax_check(tmp_path):
    for name in _order():
        path = tmp_path / name
        path.write_text((ASSETS / "js" / name).read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, (name, r.stderr[-400:])
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", webui.INDEX_HTML, re.S)
    bundle = tmp_path / "bundle.js"
    bundle.write_text("\n;\n".join(s for s in scripts if s.strip()), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(bundle)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_manual_markdown_round_trips_through_the_page_script(tmp_path):
    m = re.search(r"const MANUAL_MARKDOWN = (.*?);\n", webui.INDEX_HTML, re.S)
    assert m
    probe = tmp_path / "probe.js"
    probe.write_text("const MANUAL_MARKDOWN = " + m.group(1) + ";\nprocess.stdout.write(MANUAL_MARKDOWN);", encoding="utf-8")
    out = subprocess.run(["node", str(probe)], capture_output=True, text=True, check=True).stdout
    assert out == (ASSETS / "manual.md").read_text(encoding="utf-8")

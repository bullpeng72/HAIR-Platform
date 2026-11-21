"""Phase 76 — "차트 생성기" Tool + "데이터시각화" Skill + 전담 팀
구성(사용자 요청, 2026-11-18, "excel 파일 등 수치가 담긴 데이터를
입력받아, 실제 계산을 하고 그래프 출력 등이 가능하도록... 전담 팀을
구성해 대화 및 업무 요청에서 활용할 수 있게 개선해줘").

조사 결과 — 서버에 matplotlib/numpy가 없고(샌드박스가 stdlib-only)
이미지 렌더링 경로도 없는 대신, `webui.py`가 이미 마크다운 ```chart
코드펜스를 Chart.js config JSON으로 파싱해 `<canvas>`에 그리는 메커니즘을
갖고 있다 — 이 Tool은 그 메커니즘이 바로 소비할 수 있는 JSON 문자열만
돌려주면 된다. 기존 '데이터분석'(실제 계산)와 짝을 이뤄 '데이터
분석가' Agent가 계산+시각화를 한 Agent 안에서 모두 수행할 수 있게 한다.

팀(teams/team_members)은 `reference_pipeline_seeds.seed()`가 의도적으로
건드리지 않는 영역(사용자 요청 2026-11-02, "팀 관리 UI에서 다시 만든다")
이라 자동 시드 테스트 대상이 아니다 — 이 파일은 Tool/Skill/Agent 쪽만
검증한다."""

from __future__ import annotations

import json

from hair_platform import db, pool_registry


def _chart_tool_config(conn):
    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '차트 생성기'"
    ).fetchone()
    return json.loads(row["config"])


# ---------- 차트 생성기(Tool) ----------


def test_chart_generator_produces_valid_chartjs_config_for_bar_chart():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps(
            {
                "chart_type": "bar",
                "title": "월별 매출",
                "labels": ["1월", "2월", "3월"],
                "series": {"매출": [100, 150, 200]},
            }
        ),
        config["timeout_seconds"],
    )
    parsed = json.loads(out)
    assert parsed["type"] == "bar"
    assert parsed["data"]["labels"] == ["1월", "2월", "3월"]
    assert parsed["data"]["datasets"] == [
        {"label": "매출", "data": [100.0, 150.0, 200.0]}
    ]
    assert parsed["options"]["plugins"]["title"]["text"] == "월별 매출"


def test_chart_generator_supports_multiple_series():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps(
            {
                "chart_type": "line",
                "labels": ["1월", "2월"],
                "series": {"매출": [100, 150], "비용": [80, 90]},
            }
        ),
        config["timeout_seconds"],
    )
    parsed = json.loads(out)
    assert len(parsed["data"]["datasets"]) == 2


def test_chart_generator_rejects_unsupported_chart_type():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps({"chart_type": "scatter", "labels": ["a"], "series": {"x": [1]}}),
        config["timeout_seconds"],
    )
    assert "type은" in out  # 2026-11-21: "type"·"chart_type" 둘 다 받으므로 안내도 "type"


def test_chart_generator_rejects_series_length_mismatch():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps(
            {"chart_type": "bar", "labels": ["1월", "2월"], "series": {"매출": [100]}}
        ),
        config["timeout_seconds"],
    )
    assert "길이" in out


def test_chart_generator_rejects_too_many_points():
    """`_CUSTOM_TOOL_MAX_OUTPUT_CHARS=4000`(pool_registry.py) 안에
    안전하게 들어가도록 포인트 수 상한(30개)을 둔다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    labels = [str(i) for i in range(31)]
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps({"chart_type": "bar", "labels": labels, "series": {"x": labels}}),
        config["timeout_seconds"],
    )
    assert "너무 많습니다" in out


def test_chart_generator_rejects_invalid_json():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    out = pool_registry._run_custom_python_tool(
        config["function_source"], "이건 JSON이 아닙니다", config["timeout_seconds"]
    )
    assert "JSON" in out


# ---------- 데이터시각화(Skill) ----------


def test_data_visualization_guide_skill_is_tool_mode_with_resolvable_tool():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT augmentation_mode, default_tool_pool_ids, output_format "
        "FROM skill_pool WHERE name = '데이터시각화'"
    ).fetchone()
    assert row["augmentation_mode"] == "tool"
    tool_ids = json.loads(row["default_tool_pool_ids"])
    assert len(tool_ids) == 1
    tool_row = conn.execute(
        "SELECT name, kind FROM tool_pool WHERE id = ?", (tool_ids[0],)
    ).fetchone()
    assert tool_row["name"] == "차트 생성기"
    assert tool_row["kind"] == "custom_python"
    assert row["output_format"] is not None


# ---------- 데이터 분석가 Agent — 계산+시각화 겸비 ----------


def test_data_analyst_agent_has_both_calculation_and_visualization_skills():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '데이터 분석가'"
    ).fetchone()
    tags = json.loads(row["skill_tags"])
    assert "데이터분석" in tags
    assert "데이터시각화" in tags


def test_backfill_adds_visualization_skill_to_preexisting_agent_without_duplicating():
    """회귀 가드 — 이미 '데이터 분석가'가 있고 '데이터시각화'가
    없는(이번 기능 이전 상태를 흉내 낸) DB에 `init_schema()`를 다시
    돌리면 멱등하게 추가되고, 또 한 번 더 돌려도 중복되지 않는다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    conn.execute(
        "UPDATE agent_pool SET skill_tags = '[\"데이터분석\", \"내부지식검색\"]' "
        "WHERE role_name = '데이터 분석가'"
    )
    conn.commit()
    db.init_schema(conn)
    row = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '데이터 분석가'"
    ).fetchone()
    tags = json.loads(row["skill_tags"])
    assert tags.count("데이터시각화") == 1

    db.init_schema(conn)  # 한 번 더 — 여전히 중복 없음
    row = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '데이터 분석가'"
    ).fetchone()
    tags = json.loads(row["skill_tags"])
    assert tags.count("데이터시각화") == 1


def test_chart_generator_accepts_type_alias_and_null_gaps():
    """2026-11-21 — 스킬 지침·```chart 간단 형식의 "type" 키와 빈 구간(null)을 받는다
    (실측: "type"을 넣으면 "chart_type" 오류로 같은 호출을 13번 반복했다)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    config = _chart_tool_config(conn)
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps(
            {"type": "line", "labels": ["1분기", "2분기", "3분기"],
             "series": {"실적": [42, 48, 55], "목표": [None, None, 52]}, "unit": "억 원"},
            ensure_ascii=False,
        ),
        config["timeout_seconds"],
    )
    chart = json.loads(out)
    assert chart["type"] == "line"
    assert chart["data"]["datasets"][1]["data"] == [None, None, 52.0]
    assert chart["options"]["scales"]["y"]["title"]["text"] == "억 원"

/* ---------- 도구/스킬 Pool ---------- */

let _poolCategoryFilter = "all";

// 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로 구분하고
// 필터링 되도록 개선") — 분류 값을 한글 배지로 통일해 표시한다(대화창
// 쪽 라벨과 같은 문구를 써서 두 화면의 용어가 어긋나지 않게 한다).
const POOL_CATEGORY_LABEL = { sw_dev: "개발업무", general: "일반업무" };

async function renderPoolRegistry(content) {
  const [allTools, allSkills] = await Promise.all([
    api("GET", "/api/tool-pool"),
    api("GET", "/api/skill-pool"),
  ]);
  const tools = _poolCategoryFilter === "all"
    ? allTools
    : allTools.filter(t => t.category === _poolCategoryFilter);
  const skills = _poolCategoryFilter === "all"
    ? allSkills
    : allSkills.filter(s => s.category === _poolCategoryFilter);
  const isAdmin = ME.role === "admin";
  // 사용자 요청(2026-10-27, "기본 도구 ID의 경우도 코드로 선택 가능하도록
  // 수정 필요(ID 노출 안됨)") — 테이블 표시·폼 선택 모두 정수 id 대신
  // tool_pool.code를 쓴다. id는 체크박스 value(실제 API 페이로드)로만
  // 남고 화면에는 안 보인다.
  const toolCodeById = {};
  // 분류 필터가 걸려 있어도 코드 조회·도구 선택 체크박스는 전체
  // 목록(allTools) 기준이어야 한다 — 필터는 "목록에 보일 행"만 좁히는
  // 용도이고, 다른 분류의 도구를 스킬에 연결하는 것 자체는 막지 않는다.
  allTools.forEach(t => { toolCodeById[t.id] = t.code || ("#" + t.id); });
  // 사용자 요청(2026-10-27, "신규 도구, 스킬 생성의 경우 AI 에이전트
  // 생성 화면처럼 박스를 분리해줘") — 지금까지는 목록 카드 맨 아래에
  // 생성 폼을 그대로 이어붙였다. "AI 에이전트 생성" 화면(renderJobs)이
  // "AI 에이전트 생성"/"Agent 직접 생성"을 각각 독립된 카드로 분리한
  // 것과 같은 원칙으로, 목록 카드와 생성 카드를 분리하고(Tool Pool
  // 목록 / 신규 도구 생성 / Skill Pool 목록 / 신규 스킬 생성 — 총 4개
  // 카드) 생성 폼도 그 화면의 "Agent 직접 생성"과 같은 "세로로 쌓인
  // 넓은 입력칸" 구조로 맞춘다(기존 한 줄 inline 입력은 필드 수가
  // 늘면서 이미 비좁았다).
  content.innerHTML =
    // 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로
    // 구분하고 필터링 되도록 개선") — Tool/Skill 두 표 공용 필터
    // 하나(목록만 좁힌다 — 아래 도구 선택 체크박스들은 분류와 무관하게
    // 항상 전체를 보여준다).
    '<section class="card" style="padding-bottom:0.6rem">' +
    '<label>분류 필터 <select id="pool-category-filter" style="margin-left:0.4rem">' +
    '<option value="all"' + (_poolCategoryFilter === "all" ? " selected" : "") + '>전체</option>' +
    '<option value="general"' + (_poolCategoryFilter === "general" ? " selected" : "") + '>일반업무</option>' +
    '<option value="sw_dev"' + (_poolCategoryFilter === "sw_dev" ? " selected" : "") + '>개발업무</option>' +
    '</select></label>' +
    '</section>' +
    '<section class="card"><h2>Tool Pool — 재사용 가능한 도구</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>MCP 서버나 native 도구(VectorDB/GraphDB/실시간 웹 검색/최근 뉴스 검색/스킬·도구 목록 조회)를 이름으로 등록해 여러 Agent가 재사용합니다.</li>' +
    '<li>native는 config에 {"native_id": "vectordb_search"} 또는 {"native_id": "graphdb_query"} 또는 {"native_id": "tavily_search"}(Tavily 실시간 웹 검색, API 키 필요(TAVILY_API_KEY) — skill_tags에 "실시간웹검색"을 넣으면 매칭됩니다), {"native_id": "tavily_news_search"}(최근 7일 뉴스 검색, 같은 API 키), {"native_id": "skill_catalog_lookup"}(등록된 스킬·도구 목록 조회)를 지원합니다.</li>' +
    '<li>mcp_stdio/mcp_remote는 {"command":...,"args":[...]} 또는 {"url":...,"transport":...} 형태입니다 — 이 두 종류는 Agent가 실행될 때마다 실제로 서버(로컬 프로세스 또는 원격)에 접속을 시도하므로, 잘못된 command/url을 등록해두면 그 스킬을 가진 Agent 실행이 접속 단계에서 실패합니다.</li>' +
    // 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능을
    // 제공해줘") — mcp_stdio와 같은 신뢰 등급(admin 전용)이라는 걸 화면에도
    // 명시한다. 실행 중 Docker가 있으면 격리 실행, 없으면 일반
    // subprocess로 폴백한다는 한계도 숨기지 않는다.
    '<li><b>custom_python</b>(admin 전용)은 Python 함수를 직접 등록해 새 CrewAI Tool로 쓴다 — <code>def tool_fn(query: str) -> str:</code> 하나만 정의하면 됩니다. 등록된 코드는 그 도구가 호출될 때마다 서버에서 실제로 실행됩니다(Docker가 설치돼 있으면 네트워크 차단된 컨테이너에서, 없으면 격리 없는 일반 프로세스로 폴백) — mcp_stdio와 같은 신뢰 수준이 필요해 admin 전용입니다.</li>' +
    '<li>"활성" 표시를 꺼두면(비활성) 스킬이 매칭돼도 더 이상 자동으로 붙지 않습니다(등록은 유지).</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 도구를 참조하던 Agent 개별 연결도 함께 지웁니다.</li>' +
    '</ul>'
    ) +
    table(["코드", "이름", "종류", "분류", "설명", "활성", "동작"], tools.map(t => [
      esc(t.code || "-"), esc(t.name), esc(t.kind), esc(POOL_CATEGORY_LABEL[t.category] || t.category), esc(t.description), t.is_active ? "활성" : "비활성",
      (isAdmin || !["mcp_stdio", "mcp_remote", "custom_python"].includes(t.kind))
        ? '<div class="actions"><button class="small" data-edit-tool="' + t.id + '">수정</button>' +
          (isAdmin ? '<button class="small danger" data-delete-tool="' + t.id + '">삭제</button>' : "") +
          "</div>"
        : "",
    ])) +
    "</section>" +
    '<section class="card"><h2>신규 도구 생성</h2>' +
    // 사용자 보고(2026-10-27, 스크린샷 — 입력창·선택 옵션이 한쪽에
    // 치우치고 박스 전체 폭을 못 씀) — 원인은 `display:flex;flex-
    // direction:column`에 `max-width:560px`를 준 것: 플렉스 column의
    // 기본 align-items:stretch는 <input>/<select>처럼 교체 요소(고유
    // 크기가 있는 폼 컨트롤)에는 안 먹혀 좁은 기본 폭 그대로 남는다.
    // "Agent 직접 생성" 카드(`direct-agent-form`)가 쓰는 pattern —
    // 그냥 block 폼 + 각 필드에 `width:100%`를 직접 주는 방식 — 으로
    // 맞춘다.
    '<form id="tool-form">' +
    '<label style="display:block">이름<br><input id="tp-name" required style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<label style="display:block;margin-top:0.5rem">종류<br><select id="tp-kind" style="width:100%;box-sizing:border-box;margin-top:0.2rem">' +
    '<option value="native">native</option><option value="mcp_remote">mcp_remote</option>' +
    '<option value="mcp_stdio">mcp_stdio</option><option value="custom_python">custom_python</option>' +
    '</select></label>' +
    '<label style="display:block;margin-top:0.5rem">설명<br><textarea id="tp-description" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">분류<br>' +
    '<select id="tp-category" style="width:100%;box-sizing:border-box;margin-top:0.2rem"><option value="general">일반업무</option><option value="sw_dev">개발업무</option></select>' +
    '</label>' +
    '<label id="tp-config-wrap" style="display:block;margin-top:0.5rem">config JSON<br><input id="tp-config" placeholder="예: {&#39;native_id&#39;:&#39;vectordb_search&#39;}" style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<div id="tp-custom-python-fields" style="display:none">' +
    '<label style="display:block;margin-top:0.5rem">Python 함수<br><textarea id="tp-custom-source" placeholder="def tool_fn(query: str) -> str:&#10;    return &#34;...&#34;" rows="18" style="width:100%;box-sizing:border-box;font-family:monospace;resize:vertical;min-height:320px;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">타임아웃(초) <input type="number" id="tp-custom-timeout" value="10" min="1" max="60" style="width:4rem"></label>' +
    '</div>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">도구 등록</button></form>' +
    "</section>" +
    '<section class="card"><h2>Skill Pool — skill_tags 정식 등록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>모드 "tool"인 스킬은 기본 도구 ID가 가리키는 도구를 이 스킬(이름이 정확히 일치)을 가진 모든 Agent에게 실행할 때마다 자동으로 붙입니다(run_crew/crewai_agent_generator 실행마다 다시 계산 — 나중에 스킬 설정을 바꾸면 이미 만들어진 Agent에도 다음 실행부터 바로 반영됩니다).</li>' +
    '<li>모드 "none"은 도구 연결 없이 이름표만 붙이는 용도입니다 — 하니스 방법론/agent-evaluator 절차 스킬 17개가 여기 해당합니다(사람 판단이 필요한 절차라 자동 도구화가 안 됨).</li>' +
    '<li>하니스 절차 스킬 중에서는 "recommend-fix-workflow" 하나만 예외로 실제 MCP 도구(정적 Gate 조치 지식 조회)가 붙어 있습니다.</li>' +
    '<li><b>대화창 표시</b>를 꺼두면 대화창의 스킬 선택 드롭다운에는 안 보이지만(선택해서 고를 수 없음), Agent의 skill_tags로는 그대로 쓸 수 있습니다 — mcp_remote/mcp_stdio 도구가 붙은 스킬도 이제 대화창 기본 AI·단일 Agent 멘션에서 실제로 호출됩니다(응답을 만드는 동안 MCP 서버에 접속 — 서버가 꺼져 있으면 경고 후 도구 없이 답변). custom_python 도구(수치 데이터 분석기·차트 생성기 등)는 대화창에서도 함수 호출로 실제 실행되므로 이런 스킬은 기본으로 켜져 있습니다.</li>' +
    '<li><b>분류</b>(사용자 요청, 2026-11-11, "SW개발업무와 일반업무로 구분, 개발업무는 Default로 대화창에 안 보이고 필요시 노출") — "개발업무"로 분류된 스킬/도구는 대화창 드롭다운에 default로 안 보이지만 완전히 숨겨진 건 아닙니다 — 대화창의 "개발업무 스킬도 보기" 체크박스를 켠 사용자에게는 보입니다("대화창 표시"가 꺼진 스킬과는 다른 축으로, 둘 다 적용됩니다 — show_in_conversation이 꺼져 있으면 체크박스를 켜도 안 보입니다). Agent/Task의 skill_tags 자동완성에는 분류와 무관하게 항상 둘 다 뜹니다(그 경로는 CrewAI 실행이라 개발업무 스킬도 실제로 동작하기 때문). 위쪽 "분류 필터"로 목록을 좁혀볼 수 있고, 수정 화면에서 바로 바꿀 수 있습니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 스킬 레코드만 지웁니다 — 같은 이름의 skill_tags를 가진 Agent/사람은 그대로 남고, 다음 실행부터 자동 도구 연결만 안 됩니다.</li>' +
    // 사용자 요청(2026-11-17, "외부에서 마크다운 포멧의 Skill을
    // import하거나, 마크다운 규격으로 export") — frontmatter(name/
    // category/augmentation_mode/tool) + ## 지침/## 출력 형식/## 예시/
    // ## 제약사항 섹션 규격. "내보내기"는 표의 각 행에서, "가져오기"는
    // 아래 전용 폼에서 한다.
    '<li><b>내보내기/가져오기</b> — 각 행의 "내보내기"는 이 스킬을 frontmatter(name/category/augmentation_mode/tool) + 섹션(## 지침/## 출력 형식/## 예시/## 제약사항) 규격 마크다운 파일로 내려받습니다. 아래 "마크다운으로 스킬 가져오기" 폼에 그 규격의 텍스트를 붙여넣으면 새 스킬로 등록됩니다.</li>' +
    '</ul>'
    ) +
    table(["코드", "이름", "설명", "기본 도구", "모드", "분류", "대화창 표시", "동작"], skills.map(s => [
      esc(s.code || "-"), esc(s.name), esc(s.description),
      esc(s.default_tool_pool_ids.map(id => toolCodeById[id] || ("#" + id)).join(", ") || "-"),
      esc(s.augmentation_mode),
      esc(POOL_CATEGORY_LABEL[s.category] || s.category),
      // 사용자 보고(2026-11-11, 스크린샷 — "대화창 표시"가 "표시"인데도
      // 실제 대화창에는 안 보임) — `show_in_conversation`과 `category`는
      // 독립된 두 조건이고, 대화창 API는 show_in_conversation=1이 아니면
      // 무조건 빠지고, category='sw_dev'면 "개발업무 스킬도 보기"
      // 체크박스를 켠 사용자에게만 보인다(완전히 숨기는 게 아니라
      // default-off). 이 칸은 지금까지 show_in_conversation 하나만
      // 보여줘 "표시"인데 실제로는 (기본값으로는) 안 뜨는 모순된
      // 화면이었다 — 두 조건을 합친 실제 결과를 보여주도록 고친다.
      !s.show_in_conversation
        ? "숨김"
        : (s.category === "sw_dev" ? "기본 숨김(개발업무)" : "표시"),
      '<div class="actions"><button class="small" data-edit-skill="' + s.id + '">수정</button>' +
        '<button class="small" data-export-skill="' + s.id + '">내보내기</button>' +
        (isAdmin ? '<button class="small danger" data-delete-skill="' + s.id + '">삭제</button>' : "") +
        "</div>",
    ])) +
    "</section>" +
    '<section class="card"><h2>신규 스킬 생성</h2>' +
    '<form id="skill-form">' +
    '<label style="display:block">스킬명(skill_tags와 일치)<br><input id="sp-name" required style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<label style="display:block;margin-top:0.5rem">지침(설명)<br><textarea id="sp-description" rows="3" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    // 사용자 요청(2026-11-17, "skill을 정해진 섹션 규격을 참조해
    // 구조화") — description("지침")과 별개로 출력 형식/예시/제약사항을
    // 선택 필드로 받는다. 전부 비워도 기존과 동일(백엔드가 None으로
    // 저장).
    '<label style="display:block;margin-top:0.5rem">출력 형식(선택)<br><textarea id="sp-output-format" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">예시(선택)<br><textarea id="sp-examples" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">제약사항(선택)<br><textarea id="sp-constraints" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">모드<br>' +
    '<select id="sp-mode" style="margin-top:0.2rem"><option value="tool">tool(자동 호출)</option><option value="always">always</option><option value="none">none</option></select>' +
    '</label>' +
    '<label style="display:block;margin-top:0.5rem">분류<br>' +
    '<select id="sp-category" style="margin-top:0.2rem"><option value="general">일반업무(대화창에 기본 표시)</option><option value="sw_dev">개발업무(대화창에 기본 숨김)</option></select>' +
    '</label>' +
    '<div id="sp-tool-picker" style="margin-top:0.5rem">' +
    '<div class="muted" style="margin-bottom:0.2rem">기본 도구(코드로 선택, 복수 가능 — 모드가 tool/always일 때만)</div>' +
    allTools.map(t =>
      '<label style="margin-right:0.8rem;display:inline-block"><input type="checkbox" class="sp-tool-chk" value="' + t.id + '"> ' + esc(t.code || ("#" + t.id)) + '</label>'
    ).join("") +
    '</div>' +
    '<label style="display:block;margin-top:0.5rem"><input type="checkbox" id="sp-show-in-conv" checked> 대화창에 표시</label>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">스킬 등록</button></form>' +
    "</section>" +
    '<section class="card"><h2>마크다운으로 스킬 가져오기</h2>' +
    helpBox(
      '<p class="muted">각 스킬 행의 "내보내기"가 내려주는 것과 같은 규격(frontmatter + ## 지침/## 출력 형식/## 예시/## 제약사항 섹션)의 .md 파일을 올리거나 아래에 직접 붙여넣으면 새 스킬로 등록됩니다. 이름이 이미 있으면 거부됩니다.</p>'
    ) +
    '<form id="skill-import-form">' +
    // 사용자 요청(2026-11-17, "Skill 마크다운 파일을 읽어오고(규격
    // 검사) 등록하는 방식으로 개선") — 기존엔 붙여넣기만 가능했다.
    // 파일을 고르면 브라우저에서 바로 읽어(FileReader, 서버 업로드
    // 아님) textarea를 채우고, frontmatter(name 필수)와 섹션 헤더가
    // 있는지 최소한의 규격을 클라이언트에서 먼저 점검해 즉시 피드백을
    // 준다 — 실제 등록 가능 여부의 최종 판정은 여전히 서버
    // (`skill_markdown.parse_skill_markdown()`)가 한다(여기선 "이상해
    // 보이면 미리 알려주는" 보조 검사일 뿐, 통과했다고 서버 등록이
    // 보장되진 않는다 — 예: tool: 참조가 실제 존재하는지는 서버만 안다).
    '<label style="display:block">마크다운 파일<br>' +
    '<input type="file" id="sp-import-file" accept=".md,.markdown,text/markdown" style="margin-top:0.3rem"></label>' +
    '<p id="sp-import-file-check" class="muted" style="margin:0.3rem 0 0"></p>' +
    '<textarea id="sp-import-markdown" rows="10" placeholder="---&#10;name: 예시스킬&#10;category: general&#10;augmentation_mode: none&#10;---&#10;&#10;## 지침&#10;&#10;..." style="width:100%;box-sizing:border-box;font-family:monospace;resize:vertical;margin-top:0.6rem"></textarea>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">등록하기</button></form>' +
    "</section>";

  document.getElementById("pool-category-filter").addEventListener("change", (ev) => {
    _poolCategoryFilter = ev.target.value;
    renderPoolRegistry(content);
  });
  const tpKindSelect = document.getElementById("tp-kind");
  const tpConfigInput = document.getElementById("tp-config");
  const tpConfigWrap = document.getElementById("tp-config-wrap");
  const tpCustomFields = document.getElementById("tp-custom-python-fields");
  const syncToolKindFields = () => {
    const isCustom = tpKindSelect.value === "custom_python";
    tpCustomFields.style.display = isCustom ? "block" : "none";
    tpConfigWrap.style.display = isCustom ? "none" : "";
  };
  tpKindSelect.addEventListener("change", syncToolKindFields);
  syncToolKindFields();

  document.getElementById("tool-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const kind = tpKindSelect.value;
    let config = {};
    if (kind === "custom_python") {
      // 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는
      // 기능") — 여긴 JSON이 아니라 Python 소스를 그대로 담는다.
      config = {
        function_source: document.getElementById("tp-custom-source").value,
        timeout_seconds: Number(document.getElementById("tp-custom-timeout").value) || 10,
      };
    } else {
      const raw = tpConfigInput.value.trim();
      if (raw) {
        try { config = JSON.parse(raw); } catch (e) {
          showMsg("global-msg", "config가 올바른 JSON이 아닙니다: " + e.message, "error"); return;
        }
      }
    }
    try {
      await api("POST", "/api/tool-pool", {
        name: document.getElementById("tp-name").value,
        kind,
        description: document.getElementById("tp-description").value,
        config,
        category: document.getElementById("tp-category").value,
      });
      showMsg("global-msg", "도구를 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const spModeSelect = document.getElementById("sp-mode");
  const spToolPicker = document.getElementById("sp-tool-picker");
  const syncSkillToolPicker = () => {
    const enabled = spModeSelect.value === "tool" || spModeSelect.value === "always";
    spToolPicker.style.opacity = enabled ? "1" : "0.45";
    spToolPicker.querySelectorAll(".sp-tool-chk").forEach(chk => {
      chk.disabled = !enabled;
      if (!enabled) chk.checked = false;
    });
  };
  spModeSelect.addEventListener("change", syncSkillToolPicker);
  syncSkillToolPicker();

  document.getElementById("skill-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const default_tool_pool_ids = Array.from(spToolPicker.querySelectorAll(".sp-tool-chk:checked"))
      .map(chk => Number(chk.value));
    try {
      await api("POST", "/api/skill-pool", {
        name: document.getElementById("sp-name").value,
        description: document.getElementById("sp-description").value,
        default_tool_pool_ids,
        augmentation_mode: spModeSelect.value,
        show_in_conversation: document.getElementById("sp-show-in-conv").checked,
        category: document.getElementById("sp-category").value,
        output_format: document.getElementById("sp-output-format").value || null,
        examples: document.getElementById("sp-examples").value || null,
        constraints: document.getElementById("sp-constraints").value || null,
      });
      showMsg("global-msg", "스킬을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을 추가해줘",
  // 이어서 "(1) 수정 버튼 클릭시 수정 대상의 하단이 아니라 목록의
  // 하단에 편집 창이 생성되어 확인하기 어려움 (2) 코드 입력창의 높이가
  // 너무 낮음 (3) 스킬도 도구 수정 UI처럼... 너무 협소 (4) 에이전트
  // 연결에 영향을 주는 이름은 수정 대상에서 제외") — 네 가지 다 반영:
  // ① 패널을 테이블 하단 고정 자리가 아니라 그 행(<tr data-edit-tool="
  // 클릭한 버튼의 closest tr>") 바로 아래에 전체 폭(colspan)으로 끼워
  // 넣는다. ② Python 소스 textarea를 세로로 훨씬 키우고 resize도 허용.
  // ③ 스킬 수정 폼도 도구 수정과 같은 "세로로 쌓인 넓은 입력칸" 구조로
  // 바꾼다(기존엔 등록 폼과 똑같이 한 줄에 욱여넣은 inline 입력이었다).
  // ④ name은 두 쪽 다 입력칸이 아니라 읽기전용 텍스트로만 보여준다
  // (서버도 PATCH에서 `name`을 거부한다 — `pool_registry.update_tool()`/
  // `update_skill()` 참고, Skill 매칭·Agent 연결이 이름 문자열을 그대로
  // 참조해 바꾸면 그 연결이 조용히 끊어지기 때문).
  let openToolEditRow = null;
  content.querySelectorAll("[data-edit-tool]").forEach(btn => {
    btn.onclick = () => {
      const t = tools.find(x => x.id === Number(btn.dataset.editTool));
      if (openToolEditRow) { openToolEditRow.remove(); openToolEditRow = null; }
      const isCustom = t.kind === "custom_python";
      const formHtml =
        '<h4 style="margin-top:0">도구 #' + t.id + ' 수정</h4>' +
        '<p><b>' + esc(t.name) + '</b> <span class="muted">(종류: ' + esc(t.kind) + ')</span> ' +
        '<span class="muted">— 이름·종류는 변경 불가(이름은 Skill 매칭·Agent 연결이 참조해 바꾸면 그 연결이 끊어집니다. 종류를 바꾸려면 삭제 후 새로 등록하세요).</span></p>' +
        '<label style="display:block;margin-top:0.4rem">설명<br>' +
        '<input id="tpe-description" value="' + escAttr(t.description) + '" style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
        '<label style="display:block;margin-top:0.4rem">분류<br>' +
        '<select id="tpe-category" style="margin-top:0.2rem">' +
        '<option value="general"' + (t.category === "general" ? " selected" : "") + '>일반업무</option>' +
        '<option value="sw_dev"' + (t.category === "sw_dev" ? " selected" : "") + '>개발업무</option>' +
        '</select></label>' +
        (isCustom
          ? '<label style="display:block;margin-top:0.6rem">Python 함수<br>' +
            '<textarea id="tpe-custom-source" rows="24" style="width:100%;box-sizing:border-box;font-family:monospace;resize:vertical;min-height:420px;margin-top:0.2rem">' + esc(t.config.function_source || "") + '</textarea></label>' +
            '<label>타임아웃(초) <input type="number" id="tpe-custom-timeout" value="' + (t.config.timeout_seconds || 10) + '" min="1" max="60" style="width:4rem"></label>'
          : '<label style="display:block;margin-top:0.6rem">config JSON<br>' +
            '<input id="tpe-config" value="' + escAttr(JSON.stringify(t.config)) + '" style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>') +
        '<div class="actions" style="margin-top:0.6rem"><button class="primary" type="submit">저장</button>' +
        '<button class="small" type="button" id="tool-edit-cancel">취소</button></div>';
      btn.closest("tr").insertAdjacentHTML(
        "afterend",
        '<tr><td colspan="7"><form id="tool-edit-form" style="padding:0.6rem 0">' + formHtml + "</form></td></tr>"
      );
      const tr = btn.closest("tr").nextElementSibling;
      openToolEditRow = tr;
      tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
      tr.querySelector("#tool-edit-cancel").onclick = () => { tr.remove(); openToolEditRow = null; };
      tr.querySelector("#tool-edit-form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        let config;
        if (isCustom) {
          config = {
            function_source: tr.querySelector("#tpe-custom-source").value,
            timeout_seconds: Number(tr.querySelector("#tpe-custom-timeout").value) || 10,
          };
        } else {
          const raw = tr.querySelector("#tpe-config").value.trim();
          try { config = raw ? JSON.parse(raw) : {}; } catch (e) {
            showMsg("global-msg", "config가 올바른 JSON이 아닙니다: " + e.message, "error"); return;
          }
        }
        try {
          await api("PATCH", "/api/tool-pool/" + t.id, {
            description: tr.querySelector("#tpe-description").value,
            config,
            category: tr.querySelector("#tpe-category").value,
          });
          showMsg("global-msg", "도구를 수정했습니다.", "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    };
  });
  let openSkillEditRow = null;
  content.querySelectorAll("[data-edit-skill]").forEach(btn => {
    btn.onclick = () => {
      const s = skills.find(x => x.id === Number(btn.dataset.editSkill));
      if (openSkillEditRow) { openSkillEditRow.remove(); openSkillEditRow = null; }
      // 사용자 요청(2026-10-27, "skill 생성시 ID에 tool/always/none이
      // 포함됨, 수정에서 모드 변경이 ID가 바뀌는 게 아니라면 막아야 함
      // + 기본 도구 ID도 코드로 선택(ID 노출 안됨), 기존 모드가 tool/
      // always인 경우에만 활성화") — 모드는 name/kind와 같이 읽기전용
      // 텍스트로만 보여주고(서버도 더는 받지 않는다, pool_registry.
      // update_skill() 참고) 아예 PATCH에 안 보낸다. 도구는 체크박스로
      // 코드만 보여주고(값은 id), 기존 모드가 tool/always일 때만 활성화.
      const modeAllowsTools = s.augmentation_mode === "tool" || s.augmentation_mode === "always";
      const checkedToolIds = new Set(s.default_tool_pool_ids);
      const formHtml =
        '<h4 style="margin-top:0">스킬 #' + s.id + ' 수정</h4>' +
        '<p><b>' + esc(s.name) + '</b> <span class="muted">— 이름은 변경 불가(Agent의 skill_tags와 정확히 일치해야 매칭되므로, 바꾸면 그 연결이 끊어집니다).</span></p>' +
        '<p class="muted">모드: <b>' + esc(s.augmentation_mode) + '</b>(수정 불가 — 코드(' + esc(s.code || "-") + ')에 모드가 세그먼트로 박혀 있어, 모드를 바꾸려면 삭제 후 새로 등록해야 합니다).</p>' +
        '<label style="display:block;margin-top:0.4rem">지침(설명)<br>' +
        '<textarea id="spe-description" rows="3" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem">' + esc(s.description) + '</textarea></label>' +
        '<label style="display:block;margin-top:0.4rem">출력 형식(선택)<br>' +
        '<textarea id="spe-output-format" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem">' + esc(s.output_format || "") + '</textarea></label>' +
        '<label style="display:block;margin-top:0.4rem">예시(선택)<br>' +
        '<textarea id="spe-examples" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem">' + esc(s.examples || "") + '</textarea></label>' +
        '<label style="display:block;margin-top:0.4rem">제약사항(선택)<br>' +
        '<textarea id="spe-constraints" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem">' + esc(s.constraints || "") + '</textarea></label>' +
        '<label style="display:block;margin-top:0.4rem">분류<br>' +
        '<select id="spe-category" style="margin-top:0.2rem">' +
        '<option value="general"' + (s.category === "general" ? " selected" : "") + '>일반업무(대화창에 기본 표시)</option>' +
        '<option value="sw_dev"' + (s.category === "sw_dev" ? " selected" : "") + '>개발업무(대화창에 기본 숨김)</option>' +
        '</select></label>' +
        '<div style="margin-top:0.6rem' + (modeAllowsTools ? "" : ";opacity:0.45") + '">기본 도구(코드로 선택, 복수 가능' + (modeAllowsTools ? "" : " — 이 스킬은 모드가 none이라 비활성") + ')<br>' +
        allTools.map(t =>
          '<label style="margin-right:0.8rem;display:inline-block"><input type="checkbox" class="spe-tool-chk" value="' + t.id + '"' +
          (checkedToolIds.has(t.id) ? " checked" : "") + (modeAllowsTools ? "" : " disabled") + '> ' + esc(t.code || ("#" + t.id)) + '</label>'
        ).join("") +
        '</div>' +
        '<label style="display:block;margin-top:0.6rem"><input type="checkbox" id="spe-show-in-conv"' + (s.show_in_conversation ? " checked" : "") + '> 대화창에 표시</label>' +
        (s.category === "sw_dev"
          ? '<p class="muted" style="margin-top:0.2rem">⚠ 분류가 "개발업무"라 이 체크박스를 켜도 대화창 드롭다운에는 default로 안 뜹니다 — 대화창의 "개발업무 스킬도 보기" 체크박스를 켠 사용자에게만 보입니다.</p>'
          : "") +
        // 대화 컨텍스트 정책(2026-11-20) — 이 스킬로 대화할 때의 기본값.
        '<div style="margin-top:0.6rem"><b>대화 컨텍스트 기본값</b> ' +
        '<span class="muted">(대화에서 이 스킬로 물을 때 — 사용자가 메시지마다 "이전 대화" 칩으로 바꿀 수 있음)</span><br>' +
        '<label class="muted">이전 대화 <select id="spe-history-mode">' +
        [["full", "전체(최근 10건+요약)"], ["recent", "최근 1~2턴"], ["none", "사용 안 함(현재 질문만)"]]
          .map(([v, t]) => '<option value="' + v + '"' + (s.history_mode === v ? " selected" : "") + '>' + t + '</option>').join("") +
        '</select></label> ' +
        '<label class="muted"><input type="checkbox" id="spe-use-attachments"' + (s.use_attachments ? " checked" : "") + '> 대화 첨부 자료 사용</label></div>' +
        // 스킬 포함 관계(2026-11-20) — 이 스킬을 쓸 때 함께 적용할 스킬.
        // 예: 제안서작성 → 프레젠테이션작성(슬라이드 형식·디자인 규칙을 가져온다).
        '<label style="display:block;margin-top:0.6rem"><b>함께 적용할 스킬</b> ' +
        '<span class="muted">(쉼표로 구분 — 이 스킬을 고르면 그 스킬의 지침·출력 형식·제약사항도 함께 프롬프트에 들어갑니다)</span>' +
        '<input id="spe-includes" value="' + escAttr((s.includes || []).join(", ")) + '" placeholder="예: 프레젠테이션작성" style="width:100%;box-sizing:border-box"></label>' +
        '<div class="actions" style="margin-top:0.6rem"><button class="primary" type="submit">저장</button>' +
        '<button class="small" type="button" id="skill-edit-cancel">취소</button></div>';
      btn.closest("tr").insertAdjacentHTML(
        "afterend",
        '<tr><td colspan="8"><form id="skill-edit-form" style="padding:0.6rem 0">' + formHtml + "</form></td></tr>"
      );
      const tr = btn.closest("tr").nextElementSibling;
      openSkillEditRow = tr;
      tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
      tr.querySelector("#skill-edit-cancel").onclick = () => { tr.remove(); openSkillEditRow = null; };
      tr.querySelector("#skill-edit-form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const default_tool_pool_ids = Array.from(tr.querySelectorAll(".spe-tool-chk:checked"))
          .map(chk => Number(chk.value));
        try {
          await api("PATCH", "/api/skill-pool/" + s.id, {
            description: tr.querySelector("#spe-description").value,
            default_tool_pool_ids,
            show_in_conversation: tr.querySelector("#spe-show-in-conv").checked,
            category: tr.querySelector("#spe-category").value,
            output_format: tr.querySelector("#spe-output-format").value || null,
            examples: tr.querySelector("#spe-examples").value || null,
            constraints: tr.querySelector("#spe-constraints").value || null,
            history_mode: tr.querySelector("#spe-history-mode").value,
            use_attachments: tr.querySelector("#spe-use-attachments").checked,
            includes: tr.querySelector("#spe-includes").value.split(",").map(x => x.trim()).filter(Boolean),
          });
          showMsg("global-msg", "스킬을 수정했습니다.", "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    };
  });
  content.querySelectorAll("[data-delete-tool]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 도구를 완전히 지웁니다(연결된 Agent 연결도 함께 지워집니다, 되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/tool-pool/" + btn.dataset.deleteTool);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-skill]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 스킬을 완전히 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/skill-pool/" + btn.dataset.deleteSkill);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  // 사용자 요청(2026-11-17, "마크다운 규격으로 export") — export
  // 엔드포인트는 JSON이 아니라 평문 마크다운을 내려주므로 `api()`를
  // 그대로 못 쓴다(`api()`는 항상 `resp.json()`을 시도한다) — 인증
  // 헤더만 똑같이 넣어 직접 fetch한 뒤 Blob으로 다운로드한다
  // (`downloadDeliverable()`과 같은 패턴).
  content.querySelectorAll("[data-export-skill]").forEach(btn => {
    btn.onclick = async () => {
      const s = skills.find(x => x.id === Number(btn.dataset.exportSkill));
      try {
        const token = sessionStorage.getItem("hair_token");
        const headers = token ? { Authorization: "Bearer " + token } : {};
        const resp = await fetch("/api/skill-pool/" + s.id + "/export", { headers, credentials: "same-origin" });
        if (!resp.ok) throw new Error(resp.status + " " + resp.statusText);
        const text = await resp.text();
        const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = (s.code || s.name) + ".md";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) { showMsg("global-msg", "내보내기 실패: " + e.message, "error"); }
    };
  });
  // 사용자 요청(2026-11-17, "Skill 마크다운 파일을 읽어오고(규격 검사)
  // 등록하는 방식으로 개선") — 최소 규격(frontmatter의 name, 섹션
  // 헤더 1개 이상)만 훑어보는 가벼운 사전 점검. export_skill_markdown()
  // /parse_skill_markdown()과 똑같은 정규식을 쓰지 않는 이유는 이건
  // "파일이 완전히 엉뚱한 걸 올렸을 때" 수준의 조기 피드백이 목적이고,
  // 실제 정확한 판정(예: tool: 참조가 진짜 존재하는지)은 서버만 할 수
  // 있기 때문이다 — 여기서 통과해도 제출 시 서버가 거부할 수 있다.
  function _checkSkillMarkdownShape(text) {
    const fmMatch = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
    if (!fmMatch) return "frontmatter(---로 둘러싼 부분)를 찾을 수 없습니다.";
    if (!/^\s*name\s*:/m.test(fmMatch[1])) return "frontmatter에 name 항목이 없습니다.";
    if (!/^##\s+\S/m.test(text.slice(fmMatch[0].length))) {
      return "본문에 '## 지침' 같은 섹션 헤더가 하나도 없습니다.";
    }
    return null;
  }
  const spImportFileCheck = document.getElementById("sp-import-file-check");
  document.getElementById("sp-import-file").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      document.getElementById("sp-import-markdown").value = text;
      const problem = _checkSkillMarkdownShape(text);
      spImportFileCheck.textContent = problem ? "⚠ " + problem : "✓ 형식 확인됨(frontmatter + 섹션 발견) — 실제 등록 가능 여부는 \"등록하기\" 클릭 시 서버가 최종 판정합니다.";
      spImportFileCheck.style.color = problem ? "var(--danger, #b91c1c)" : "var(--muted, #6b7280)";
    } catch (e) {
      spImportFileCheck.textContent = "⚠ 파일을 읽지 못했습니다: " + e.message;
    }
  });
  document.getElementById("skill-import-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/skill-pool/import", {
        markdown: document.getElementById("sp-import-markdown").value,
      });
      showMsg("global-msg", "마크다운에서 스킬을 가져왔습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}


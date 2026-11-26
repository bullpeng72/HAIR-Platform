/* ---------- 인력 검색 ---------- */

async function renderSearch(content) {
  const PAGE_SIZE = 20;
  let offset = 0;

  const skillDatalist = await skillDatalistHtml();
  content.innerHTML =
    '<section class="card"><h2>사람·AI 에이전트 통합 검색</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>이 화면은 매칭 알고리즘 없이 필터로 후보를 훑어보는 용도입니다(실제 배정 점수·순위를 보려면 "리소스 매칭/배정" 탭을 쓰세요).</li>' +
      '<li>AI 에이전트는 온보딩 승인(active) 안 된 것(pending_onboarding/deprecated)은 여기 안 뜹니다 — "AI 에이전트 관리" 탭에서 상태 무관 전체 목록을 볼 수 있습니다. 오프보딩된 사람도 마찬가지로 빠집니다.</li>' +
      '<li>"부서" 필터는 없습니다 — 이 시스템엔 부서 데이터가 없고, "역할"이 사실상 같은 구실을 합니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="search-form">' +
    '<input id="s-role" placeholder="역할"><input id="s-skill" placeholder="스킬" list="skill-datalist">' +
    '<select id="s-avail"><option value="">가용 전체</option><option value="available">가능만</option><option value="unavailable">불가만</option></select>' +
    '<button class="primary" type="submit">검색</button></form>' +
    skillDatalist +
    '<div id="search-result"></div>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    '<button class="small" id="search-prev">이전</button>' +
    '<span id="search-page-info" class="muted"></span>' +
    '<button class="small" id="search-next">다음</button>' +
    '</div></section>';

  const load = async () => {
    const body = await api("GET", qs("/api/pool/search", {
      role: document.getElementById("s-role").value,
      skill: document.getElementById("s-skill").value,
      availability: document.getElementById("s-avail").value,
      limit: PAGE_SIZE,
      offset,
    }));
    document.getElementById("search-result").innerHTML = table(
      ["타입", "코드", "이름", "역할", "가용", "비용", "스킬", "성과점수"],
      body.items.map(c => [esc(label(c.type)), esc(c.code || ("#" + c.id)), esc(c.name || c.role), esc(c.role), c.is_available ? "가능" : "불가", c.cost, esc(c.skill_tags.join(", ")), c.performance_score === null ? "-" : c.performance_score])
    );
    const shown = body.items.length ? offset + 1 : 0;
    document.getElementById("search-page-info").textContent =
      "전체 " + body.total + "건 중 " + shown + "-" + (offset + body.items.length) + "건";
    document.getElementById("search-prev").disabled = offset === 0;
    document.getElementById("search-next").disabled = offset + PAGE_SIZE >= body.total;
  };

  document.getElementById("search-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    offset = 0;
    try { await load(); } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("search-prev").addEventListener("click", async () => {
    offset = Math.max(0, offset - PAGE_SIZE);
    try { await load(); } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("search-next").addEventListener("click", async () => {
    offset += PAGE_SIZE;
    try { await load(); } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  await load();
}


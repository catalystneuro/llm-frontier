"""End-to-end smoke tests for the dashboard.

The site is assembled the way the deploy workflow assembles it and served
locally; a headless browser then walks the tabs, the era toggle, and two
viewports, asserting the invariants that have historically broken: clean
console, charts that render and switch, no horizontal overflow on a phone,
and no width ratchet after rotation.

Requires playwright (pip install playwright; playwright install chromium);
skipped when it is not installed, so the unit suite stays dependency-free.
"""
import functools
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (REPO / "build/llm-frontier.json").exists(), reason="no built data")


@pytest.fixture(scope="module")
def site_url(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    shutil.copytree(REPO / "site", root, dirs_exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    shutil.copy(REPO / "build/llm-frontier.json", root / "data/llm-frontier.json")
    shutil.copy(REPO / "build/feed.xml", root / "feed.xml")
    for f in (REPO / "build").glob("feed-*.xml"):
        shutil.copy(f, root / f.name)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, site_url, width=1300, height=900):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    page.goto(site_url + "/")
    page.wait_for_selector("#pfc-frontier svg")
    page.wait_for_selector("#pfc-records svg")
    return ctx, page, errors


def test_loads_clean_and_renders(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        assert errors == []
        assert page.locator("#pfc-frontier svg").count() == 1
        assert page.locator("#pfc-records svg").count() == 1
        # The finished frontier is labeled today and the era toggle is present.
        assert "today" in page.locator("#pfc-frontier-stage").inner_text()
        assert page.locator("#pfc-era-nav button").count() >= 2
        # Advances listed; the staleness note stays hidden for a fresh file.
        assert page.locator("#pfc-advances .pfc-adv-day").count() > 0
        assert page.locator("#pfc-stale").is_hidden()
        # The data line names the update time and its time zone.
        import re
        assert re.search(r"\d{1,2}:\d{2}\s?[AP]M [A-Z]", page.locator(".pfc-updated").first.inner_text())
    finally:
        ctx.close()


def test_capability_tab_switches_everything(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        # Tabs are links to the per-metric pages now.
        page.locator(".pfc-tab", has_text="Coding").click()
        page.wait_for_selector("#pfc-frontier svg text:text('Terminal-Bench 2.1')")
        assert page.evaluate("location.pathname").endswith("/coding/")
        assert page.locator("#pfc-cap-table tr").count() > 0
        # The records chart follows the tab too.
        assert page.locator("#pfc-records-lead").inner_text().startswith("The cheapest cost per task")
        assert "Terminal-Bench" in page.locator("#pfc-records-lead").inner_text()
        assert errors == []
    finally:
        ctx.close()


def test_axis_toggle_switches_to_time_and_archives_force_cost(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        toggle = page.locator("#pfc-axis-nav button", has_text="Time per task")
        if toggle.count() == 0:
            pytest.skip("no time measurements in the data yet")
        toggle.click()
        page.wait_for_selector("#pfc-frontier svg text:text('Time per task (log)')")
        assert page.evaluate("location.hash") == "#index-time"
        assert page.locator("#pfc-records-title").inner_text().startswith("Speed Records")
        assert "time per task" in page.locator("#pfc-records-lead").inner_text()
        # The advances section follows the axis: fastest-way entries, no cards.
        assert "fastest way" in page.locator("#pfc-adv-lead").inner_text()
        assert page.locator("#pfc-advances .pfc-adv-day").count() > 0
        assert page.locator("#pfc-advances .pfc-adv-card").count() == 0
        assert "fastest way to reach" in page.locator("#pfc-advances .pfc-adv-body").first.inner_text()
        # Switching to an archive era keeps the time axis when that era has
        # backfilled measurements; without them it falls back to cost.
        import json
        data = json.loads((REPO / "build/llm-frontier.json").read_text())
        ett = (data.get("era_tier_time") or [{}])[0]
        has_time = any(ett.get(t) for t in ett)
        page.locator("#pfc-era-nav button").first.click()
        if has_time:
            page.wait_for_selector("#pfc-records svg text:text('measurements ended')")
            assert page.locator("#pfc-records-title").inner_text().startswith("Speed Records")
        else:
            page.wait_for_selector("#pfc-frontier svg text:text('Cost per task (log)')")
            assert page.locator("#pfc-axis-nav").is_hidden()
            assert page.locator("#pfc-records-title").inner_text().startswith("Cost Records")
        assert errors == []
    finally:
        ctx.close()


def test_model_search_highlights_and_clears(browser, site_url):
    import json
    import re
    data = json.loads((REPO / "build/llm-frontier.json").read_text())
    n_eras = len(data.get("eras") or [])
    name = next(r[0] for r in data["models"]
                if not r[5] and (r[9] if r[9] is not None else n_eras) == n_eras)
    slug = re.sub(r"-+$|^-+", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))
    ctx, page, errors = open_page(browser, site_url)
    try:
        page.fill("#pfc-model-search", name)
        page.dispatch_event("#pfc-model-search", "change")
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring")
        assert name in page.locator("#pfc-model-chip").inner_text()
        assert page.evaluate("location.hash") == "#index~" + slug
        # Clearing from the chip removes the ring and hides the chip.
        page.locator("#pfc-model-chip .pfc-chip-clear").click()
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring", state="detached")
        assert page.locator("#pfc-model-chip").is_hidden()
        # A deep link restores the highlight on load.
        page.goto(site_url + "/#index~" + slug)
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring")
        assert name in page.locator("#pfc-model-chip").inner_text()
        assert errors == []
    finally:
        ctx.close()


def test_creator_search_lights_the_fleet(browser, site_url):
    import json
    from collections import Counter
    data = json.loads((REPO / "build/llm-frontier.json").read_text())
    n_eras = len(data.get("eras") or [])
    counts = Counter(r[1] for r in data["models"]
                     if (r[9] if r[9] is not None else n_eras) == n_eras)
    creator = counts.most_common(1)[0][0]
    ctx, page, errors = open_page(browser, site_url)
    try:
        page.fill("#pfc-model-search", creator)
        page.dispatch_event("#pfc-model-search", "change")
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring")
        # Many models light up, not one.
        assert page.locator("#pfc-frontier svg .pfc-sel-ring").count() > 1
        chip = page.locator("#pfc-model-chip").inner_text()
        assert creator in chip and "models in this view" in chip
        assert page.evaluate("location.hash").startswith("#index~")
        page.locator("#pfc-model-chip .pfc-chip-clear").click()
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring", state="detached")
        assert errors == []
    finally:
        ctx.close()


def test_archive_time_axis_when_backfilled(browser, site_url):
    import json
    data = json.loads((REPO / "build/llm-frontier.json").read_text())
    ett = data.get("era_tier_time") or []
    if not (ett and any((ett[0].get(t) or []) for t in ett[0])):
        pytest.skip("no backfilled era time records")
    ctx, page, errors = open_page(browser, site_url)
    try:
        # The archive era keeps the toggle and shows its own speed records.
        page.locator("#pfc-era-nav button").first.click()
        page.wait_for_selector("#pfc-records svg text:text('measurements ended')")
        toggle = page.locator("#pfc-axis-nav button", has_text="Time per task")
        assert toggle.count() == 1
        toggle.click()
        page.wait_for_selector("#pfc-frontier svg text:text('Time per task (log)')")
        assert page.locator("#pfc-records-title").inner_text().startswith("Speed Records")
        assert page.evaluate("location.hash").endswith("-time")
        assert errors == []
    finally:
        ctx.close()


def test_time_slider(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        dots = "#pfc-frontier svg circle"
        today = page.locator(dots).count()
        past = page.locator("#pfc-frontier svg .pfc-past").count()
        assert past > 0
        assert page.locator("#pfc-frontier .pfc-tick").count() == past
        # Clicking the first tick moves the chart back to that date: later
        # models disappear, along with every earlier frontier but none.
        page.locator("#pfc-frontier .pfc-tick").first.click()
        assert "today" not in page.locator("#pfc-frontier-stage").inner_text()
        assert page.locator(dots).count() < today
        assert page.locator("#pfc-frontier svg .pfc-past").count() == 0
        assert page.locator("#pfc-frontier svg path[data-current='1']").count() == 1
        # Arrow keys step forward to the next change; the end is today again.
        before = page.locator("#pfc-frontier-stage").inner_text()
        page.locator("#pfc-frontier-slider").press("ArrowRight")
        assert page.locator("#pfc-frontier-stage").inner_text() != before
        page.locator("#pfc-frontier-slider").press("End")
        page.wait_for_selector("#pfc-frontier-stage:text('today')")
        assert page.locator(dots).count() == today
        # The start handle hides models released before it; the frontier is
        # redrawn from the rest.
        page.evaluate("""() => {
            const s = document.getElementById('pfc-frontier-slider-start');
            s.value = Math.round(Date.parse('2026-06-01T00:00:00Z') / 86400000);
            s.dispatchEvent(new Event('input'));
        }""")
        assert "released Jun 1, 2026 to today" in page.locator("#pfc-frontier-stage").inner_text()
        assert page.locator(dots).count() < today
        assert page.locator("#pfc-frontier svg path[data-current='1']").count() == 1
        # The start handle cannot pass the end handle.
        page.locator("#pfc-frontier .pfc-tick").first.click()
        assert int(page.locator("#pfc-frontier-slider-start").input_value()) <= int(page.locator("#pfc-frontier-slider").input_value())
        page.locator("#pfc-frontier-slider-start").press("Home")
        page.locator("#pfc-frontier-slider").press("End")
        page.wait_for_selector("#pfc-frontier-stage:text('as of today')")
        assert page.locator(dots).count() == today
        assert errors == []
    finally:
        ctx.close()


def test_slider_window_in_the_url(browser, site_url):
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    try:
        # A shared link opens on its window.
        page.goto(site_url + "/#index@2026-06-01..2026-09-01")
        page.wait_for_selector("#pfc-frontier svg")
        assert page.locator("#pfc-frontier-stage").inner_text() == (
            "Pareto frontier of models released Jun 1, 2026 to Sep 1, 2026")
        # Moving a handle rewrites the hash once the handle settles.
        page.locator("#pfc-frontier-slider").press("End")
        page.wait_for_function("location.hash === '#index@2026-06-01..'")
        page.locator("#pfc-frontier-slider-start").press("Home")
        page.wait_for_function("location.hash === ''")
        # The window combines with the tab and a selection, and a hash typed
        # over the open page applies.
        page.evaluate("location.hash = '#coding~openai@..2026-04-01'")
        page.wait_for_selector("#pfc-frontier svg text:text('Terminal-Bench 2.1')")
        page.wait_for_selector("#pfc-frontier-stage:text('as of Apr 1, 2026')")
        assert page.locator("#pfc-model-chip").is_visible()
        assert errors == []
    finally:
        ctx.close()


def test_full_window_buttons(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        buttons = page.locator(".pfc-figure .pfc-fs-btn")
        assert buttons.count() == page.locator(".pfc-figure").count() == 2
        normal = page.locator("#pfc-frontier svg").bounding_box()
        buttons.first.click()
        fig = page.locator(".pfc-figure.pfc-expanded")
        assert fig.count() == 1
        assert buttons.first.inner_text() == "Close full window"
        # The expanded figure covers the window and the chart is redrawn
        # larger, without the figure needing to scroll.
        box = fig.bounding_box()
        assert box["width"] == 1300 and box["height"] == 900
        big = page.locator("#pfc-frontier svg").bounding_box()
        assert big["width"] > normal["width"] and big["height"] > normal["height"]
        assert page.evaluate("(() => { const f = document.querySelector('.pfc-expanded'); return f.scrollHeight <= f.clientHeight; })()")
        page.keyboard.press("Escape")
        assert page.locator(".pfc-figure.pfc-expanded").count() == 0
        assert buttons.first.inner_text() == "Full window"
        assert errors == []
    finally:
        ctx.close()


def test_clicking_a_point_selects_the_model(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        # Click the current frontier's top-right dot: some model gets selected.
        dot = page.locator("#pfc-frontier svg path[data-current='1'] ~ circle").last
        pos = dot.bounding_box()
        page.mouse.click(pos["x"] + pos["width"] / 2, pos["y"] + pos["height"] / 2)
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring")
        assert not page.locator("#pfc-model-chip").is_hidden()
        # Clicking empty space clears the selection. The chip's appearance can
        # reflow the page, so measure the chart only now.
        box = page.locator("#pfc-frontier svg").bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.15, box["y"] + 10)
        page.wait_for_selector("#pfc-frontier svg .pfc-sel-ring", state="detached")
        assert page.locator("#pfc-model-chip").is_hidden()
        assert errors == []
    finally:
        ctx.close()


def test_metric_page_defaults_to_its_metric(browser, site_url):
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    try:
        page.goto(site_url + "/coding/")
        page.wait_for_selector("#pfc-frontier svg text:text('Terminal-Bench 2.1')")
        # The page's own hash stays clean; the tab bar is links except itself.
        assert page.evaluate("location.hash") == ""
        assert page.locator("#pfc-cap-tabs a").count() == 9
        assert page.locator("#pfc-cap-tabs span[aria-selected='true']").inner_text() == "Coding"
        assert page.locator("#pfc-cap-tabs a", has_text="Overall").get_attribute("href").endswith("/")
        # Its feed link points at the capability feed, which is served.
        href = page.locator("head link[rel='alternate']").get_attribute("href")
        assert href.endswith("feed-coding.xml")
        assert page.request.get(site_url + "/feed-coding.xml").status == 200
        assert errors == []
    finally:
        ctx.close()


@pytest.mark.skipif(not (REPO / "data/decision-history.json").exists(), reason="no Decision Index data")
def test_decision_tab_uses_its_own_models_and_axis(browser, site_url):
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    try:
        page.goto(site_url + "/decision/")
        page.wait_for_selector("#pfc-frontier svg text:text('Decision Index')")
        page.wait_for_selector("#pfc-records svg")
        assert page.locator("#pfc-cap-tabs span[aria-selected='true']").inner_text() == "Decisions"
        # Its own cost axis, no eras, and contestants that are not in the main data.
        assert page.locator("#pfc-frontier svg text:text('Cost per 1K tasks (log)')").count() == 1
        assert page.locator("#pfc-era-nav").is_hidden()
        assert "decision models" in page.locator("#pfc-frontier-lead").inner_text()
        assert page.locator("#pfc-cap-table tr").count() > 0
        assert page.locator("#pfc-advances .pfc-adv-day").count() > 0
        options = page.locator("#pfc-model-list option").evaluate_all("os => os.map(o => o.value)")
        assert "d1" in options
        # The time axis works on the contestants' measured time per task.
        page.locator("#pfc-axis-nav button", has_text="Time per task").click()
        page.wait_for_selector("#pfc-frontier svg text:text('Time per task (log)')")
        # A hash hop back to Overall restores the main models and the eras.
        page.goto(site_url + "/decision/#index")
        page.wait_for_selector("#pfc-frontier svg text:text('Artificial Analysis Intelligence Index')")
        assert page.locator("#pfc-era-nav button").count() >= 2
        assert errors == []
    finally:
        ctx.close()


def test_era_archive_view(browser, site_url):
    ctx, page, errors = open_page(browser, site_url)
    try:
        page.locator("#pfc-era-nav button").first.click()
        page.wait_for_selector("#pfc-records svg text:text('measurements ended')")
        assert "Sep 4, 2026" in page.locator("#pfc-frontier-stage").inner_text()
        # Back to the current era.
        page.locator("#pfc-era-nav button", has_text="current").click()
        page.wait_for_selector("#pfc-frontier-stage:text('today')")
        assert errors == []
    finally:
        ctx.close()


def test_phone_never_overflows_even_after_rotation(browser, site_url):
    ctx, page, errors = open_page(browser, site_url, width=390, height=844)
    try:
        def overflow():
            return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow() <= 2
        # Rotate to landscape and back: the grid track must not ratchet.
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_timeout(400)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        assert overflow() <= 2
        # Date ticks stay sparse enough to read at this width.
        ticks = page.locator("#pfc-records svg text", has_text="'2").count()
        assert 2 <= ticks <= 5
        assert errors == []
    finally:
        ctx.close()

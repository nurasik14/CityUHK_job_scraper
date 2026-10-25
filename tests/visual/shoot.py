"""T28: visual + accessibility check of the web UI in headless Chromium. Not a pytest test.

Run: uv run python tests/visual/shoot.py          (needs `uv run playwright install chromium`)

- The live app on 127.0.0.1:8000 is only ever sent GET requests for /, /results and /settings.
  It never POSTs /search or /login: that would scrape AIMS or open the login window.
- Every other state (progress, error, login needed, empty, long results) comes from a second app
  built with create_app() and a fake TaskRunner on 127.0.0.1:8765. It is stopped at the end.
- Screenshots: .data/screenshots/<page>-<state>-<width>-<theme>.png (git-ignored, never commit).
- Prints a Markdown table of checks and writes .data/screenshots/report.json.
"""

import json
import sys
import threading
import time
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx
import uvicorn
from playwright.sync_api import Page, sync_playwright

from cityu_jobs import config
from cityu_jobs.core.models import Job, compute_content_hash
from cityu_jobs.core.pipeline import RefreshReport
from cityu_jobs.core.ranking import Preferences, RankedJob
from cityu_jobs.web.app import create_app
from cityu_jobs.web.tasks import TaskRunner

LIVE = "http://127.0.0.1:8000"
FAKE_PORT = 8765
FAKE = f"http://127.0.0.1:{FAKE_PORT}"
OUT = config.DATA_DIR / "screenshots"
VIEWPORTS = {1280: {"width": 1280, "height": 800}, 375: {"width": 375, "height": 812},
             320: {"width": 320, "height": 640}}  # 320 CSS px = WCAG 1.4.10 Reflow
THEMES = ("light", "dark")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


# --- Fake app states --------------------------------------------------------------


def _job(n: int, **overrides) -> Job:
    values = dict(
        job_id=f"aims:P26CS{n:03d}", source="aims_cis", scheme="Campus Internship Scheme",
        title=f"Research assistant {n}", department_code="CS", department_name="Computer Science",
        description="Help build machine learning tools for a research group.", requirements="Python",
        weekly_hours=12.0, closing_date=date.today() + timedelta(days=30), raw={"n": n},
    )
    values.update(overrides)
    job = Job(**values)
    job.content_hash = compute_content_hash(job.raw)
    return job


def _ranked(job: Job, score: float, level: int | None) -> RankedJob:
    base = {"similarity": 0.3, "department": 0.1, "hours": 0.06, "student_helper": 0.0,
            "level": 0.15 if level else 0.1, "learning_scheme": 0.08}
    total = sum(base.values())
    breakdown = {k: v * score / total for k, v in base.items()}  # terms add up to the score, as in rank()
    reason = "Builds and evaluates models." if level else ""
    return RankedJob(job, score, breakdown, 0.7, level, reason)


def _results() -> list[RankedJob]:
    long_title = ("Undergraduate Research Intern in Large Language Model Coding Agents, Evaluation "
                  "Benchmarks and Human-Computer Interaction for Accessible Developer Tools")
    return [
        _ranked(_job(1, title=long_title, closing_date=date.today() + timedelta(days=3)), 0.81, 5),
        _ranked(_job(2, source="itc_stem", job_id="itc:cc:abc123def456", department_code="CC",
                     department_name="College of Computing", weekly_hours=None,
                     raw={"n": 2, "_honorarium": "HKD 2,000 per month"}), 0.74, 4),
        _ranked(_job(3, source="aims_sh_regular", scheme="Student Helpers (Regular Term)",
                     title="Student helper — lab data entry", department_code="MA"), 0.52, None),
        _ranked(_job(4, title="Supercalifragilisticexpialidociousunbreakablewordinatitle"), 0.40, 2),
    ]


STATES = {
    # name: (path, function that sets runner.state)
    "empty": ("/results", lambda s: None),
    "progress": ("/results", lambda s: _set(s, kind="search", status="running", message="Rating job levels with llama3.2:3b: 12/98",
                                             started_at=time.time() - 754, prefs=Preferences("CS", "machine learning", 2, 5))),
    "error": ("/results", lambda s: _set(s, kind="search", status="error", error="ProviderError: Ollama is not running at http://localhost:11434.",
                                          prefs=Preferences("CS", "machine learning", 2, 5))),
    "nojobs": ("/results", lambda s: _set(s, kind="search", status="done", prefs=Preferences("CS", "quantum", 3, 3),
                                           report=RefreshReport(notes=["ITC STEM: download needs a CityU login"]), results=[])),
    "results": ("/results", lambda s: _set(s, kind="search", status="done", message="Ranked 4 open jobs.",
                                            prefs=Preferences("CS", "machine learning research", 2, 5),
                                            report=RefreshReport(scraped=["itc_stem"], notes=["ITC STEM: 98 jobs"]), results=_results())),
    "loginneeded": ("/results", lambda s: _set(s, kind="search", status="done", login_needed=True,
                                                prefs=Preferences("CS", "machine learning", 2, 5), results=_results()[:2],
                                                report=RefreshReport(login_needed=True, notes=["AIMS login needed: showing AIMS jobs from the last successful scrape."]))),
    "search-loginneeded": ("/", lambda s: _set(s, login_needed=True, prefs=Preferences("CS", "machine learning", 2, 5))),
    "settings-saved": ("/settings?saved=1", lambda s: None),
    "logindone": ("/results", lambda s: _set(s, kind="login", status="done", message="Logged in to AIMS.",
                                              prefs=Preferences("CS", "machine learning", 2, 5), results=_results()[:1])),
}


def _set(state, **values) -> None:
    for key, value in values.items():
        setattr(state, key, value)


def start_fake_server(runner: TaskRunner) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(create_app(runner), host="127.0.0.1", port=FAKE_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("fake server did not start")


# --- Checks -------------------------------------------------------------------------

CONTRAST_JS = """() => {
  const css = getComputedStyle(document.documentElement);
  const parse = c => { const m = c.match(/[\\d.]+/g).map(Number); return m.slice(0, 3); };
  const toRgb = v => { const el = document.createElement('span'); el.style.color = v; document.body.appendChild(el);
                       const c = getComputedStyle(el).color; el.remove(); return parse(c); };
  const lum = ([r, g, b]) => { const f = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
                                return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const tok = n => css.getPropertyValue(n).trim();
  const out = {};
  for (const fg of ['--text', '--text-muted', '--primary']) for (const bg of ['--bg', '--surface']) {
    if (tok(fg) && tok(bg)) out[`${fg} on ${bg}`] = Math.round(ratio(toRgb(tok(fg)), toRgb(tok(bg))) * 100) / 100;
  }
  const body = getComputedStyle(document.body);
  out['body text on body bg'] = Math.round(ratio(parse(body.color), parse(body.backgroundColor)) * 100) / 100;
  return out;
}"""

CONTROLS_JS = """() => [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary')]
  .filter(e => !e.disabled && e.offsetParent !== null && getComputedStyle(e).visibility !== 'hidden')
  .filter(e => !(e.type === 'radio' && !e.checked && document.querySelector(`input[type=radio][name="${e.name}"]:checked`)))
  .map(e => e.outerHTML.slice(0, 80))"""

FOCUS_JS = """() => { const e = document.activeElement; if (!e || e === document.body) return null;
  const s = getComputedStyle(e);
  const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none');
  return {html: e.outerHTML.slice(0, 80), ring}; }"""


def check_page(page: Page) -> dict:
    """Overflow, contrast and keyboard focus on the current page."""
    result = {}
    result["overflow"] = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    result["contrast"] = page.evaluate(CONTRAST_JS)
    controls = page.evaluate(CONTROLS_JS)
    reached, no_ring = set(), []
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    for _ in range(len(controls) + 10):
        page.keyboard.press("Tab")
        focus = page.evaluate(FOCUS_JS)
        if focus is None:
            continue
        if focus["html"] in reached and len(reached) >= len(controls):
            break
        reached.add(focus["html"])
        if not focus["ring"]:
            no_ring.append(focus["html"])
    result["controls"] = len(controls)
    result["unreached"] = [c for c in controls if c not in reached]
    result["no_focus_ring"] = sorted(set(no_ring))
    return result


HERO_JS = """() => {
  const hero = document.querySelector('.hero'); if (!hero) return null;
  const parse = c => c.startsWith('color(srgb') ? c.match(/[\\d.]+/g).slice(0, 3).map(x => +x * 255) : c.match(/[\\d.]+/g).slice(0, 3).map(Number);
  const lum = ([r, g, b]) => { const f = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
                                return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const s = getComputedStyle(hero);
  const stops = (s.backgroundImage.match(/(color\\(srgb[^)]*\\)|rgba?\\([^)]*\\))/g) || []).map(parse);
  if (!stops.length) stops.push(parse(getComputedStyle(document.body).backgroundColor));
  const out = {};
  for (const el of hero.querySelectorAll('h1, p, .lede')) {
    const fg = parse(getComputedStyle(el).color);
    out[el.tagName.toLowerCase() + (el.className ? '.' + el.className : '')] = Math.round(Math.min(...stops.map(b => ratio(fg, b))) * 100) / 100;
  }
  return out;
}"""

STICKY_JS = """async () => {
  const bar = document.querySelector('.summary-bar'); if (!bar) return null;
  window.scrollTo(0, document.documentElement.scrollHeight); await new Promise(r => setTimeout(r, 200));
  const r = bar.getBoundingClientRect(), header = document.querySelector('.site-header').getBoundingClientRect();
  const out = {scrollable: window.scrollY > 0, sticky: getComputedStyle(bar).position === 'sticky', top: Math.round(r.top), header_bottom: Math.round(header.bottom),
               visible: r.top >= 0 && r.bottom <= innerHeight && r.height > 0,
               overflow_after_scroll: document.documentElement.scrollWidth - document.documentElement.clientWidth};
  window.scrollTo(0, 0); return out;
}"""

TARGETS_JS = """() => {
  const size = e => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; };
  const skip = document.querySelector('.skip-link'); skip.focus();
  const out = {brand: size(document.querySelector('.brand')), skip_link_focused: size(skip)}; skip.blur(); return out;
}"""

TITLE_JS = """() => [...document.querySelectorAll('.card.job h2')].map(h => { const s = getComputedStyle(h);
  return {hyphens: s.hyphens, wrap: s.overflowWrap, fits: h.scrollWidth <= h.clientWidth}; })"""


FOCUS_OBSCURED_JS = """async () => {
  // Shift+Tab up from the bottom: is a focused control (outside the sticky bars) hidden under them?
  const bars = [document.querySelector('.site-header'), document.querySelector('.summary-bar')];
  const inBar = e => bars.some(b => b && b.contains(e));
  const focusables = [...document.querySelectorAll('a[href], button, summary, input, select')].filter(e => e.offsetParent && !inBar(e) && !e.matches('.skip-link'));
  const hidden = [];
  for (const e of focusables.reverse()) {
    window.scrollTo(0, document.documentElement.scrollHeight);
    e.focus(); e.scrollIntoView({block: 'nearest'});  // what the browser does on Shift+Tab
    await new Promise(r => requestAnimationFrame(r));
    const r = e.getBoundingClientRect();
    // Hidden = a sticky bar actually overlaps the focused element on screen.
    const overlaps = bars.filter(Boolean).map(b => b.getBoundingClientRect()).some(b => b.top < r.bottom - 1 && b.bottom > r.top + 1);
    if (overlaps) hidden.push((e.textContent || e.tagName).trim().slice(0, 30));
  }
  window.scrollTo(0, 0);
  return hidden;
}"""


def extra_checks(page: Page, name: str, width: int, theme: str) -> dict:
    """T31 additions: hero contrast, sticky summary bar, 44 px targets, long-title wrapping."""
    out = {"hero_contrast": page.evaluate(HERO_JS), "targets": page.evaluate(TARGETS_JS)}
    if page.locator(".summary-bar.is-sticky").count():
        out["sticky"] = page.evaluate(STICKY_JS)
        out["focus_obscured"] = page.evaluate(FOCUS_OBSCURED_JS)
    if page.locator(".card.job h2").count():
        out["titles"] = page.evaluate(TITLE_JS)
        if name == "results" and width == 375:
            file = OUT / f"results-longtitle-375-{theme}.png"
            page.locator(".card.job").first.screenshot(path=file)
            out["longtitle_shot"] = str(file)
    return out


def running_animations(page: Page) -> int:
    return page.evaluate("() => document.getAnimations().filter(a => a.playState === 'running').length")


# --- Run ----------------------------------------------------------------------------


def shoot(browser, base: str, path: str, name: str, width: int, theme: str, report: list, reduced: bool = False):
    context = browser.new_context(viewport=VIEWPORTS[width], color_scheme=theme,
                                  reduced_motion="reduce" if reduced else "no-preference")
    page = context.new_page()
    console_errors, external = [], []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("request", lambda r: external.append(r.url)
            if urlparse(r.url).scheme in ("http", "https", "ws", "wss") and urlparse(r.url).hostname not in LOCAL_HOSTS else None)
    page.goto(base + path, wait_until="networkidle")
    file = OUT / f"{name}-{width}-{theme}{'-reduced' if reduced else ''}.png"
    page.screenshot(path=file, full_page=True)
    row = {"page": name, "width": width, "theme": theme, "reduced_motion": reduced, "screenshot": str(file),
           **check_page(page), **extra_checks(page, name, width, theme),
           "console_errors": console_errors, "external_requests": external}
    if reduced:
        row["running_animations"] = running_animations(page)
    report.append(row)
    context.close()


def theme_override_check(browser, base: str) -> dict:
    """System light + localStorage theme=dark -> html[data-theme=dark] and a dark background before/after reload."""
    context = browser.new_context(color_scheme="light")
    page = context.new_page()
    page.goto(base + "/")
    page.evaluate("() => localStorage.setItem('theme', 'dark')")
    page.reload()
    result = {
        "data_theme": page.evaluate("() => document.documentElement.dataset.theme || null"),
        "bg": page.evaluate("() => getComputedStyle(document.body).backgroundColor"),
        "color_scheme": page.evaluate("() => getComputedStyle(document.documentElement).colorScheme"),
    }
    context.close()
    return result


def _sticky(s: dict | None) -> str:
    if not s:
        return "-"
    if not s["scrollable"]:
        return "no scroll"
    return "ok" if s["sticky"] and s["visible"] and s["overflow_after_scroll"] == 0 else f"FAIL {s}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    try:
        httpx.get(LIVE + "/api/status", timeout=3)
        live_up = True
    except httpx.HTTPError:
        live_up = False
        print(f"! live app not reachable at {LIVE}; skipping live pages", file=sys.stderr)

    runner = TaskRunner(search_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fake app: no searches")),
                        login_fn=lambda: (_ for _ in ()).throw(RuntimeError("fake app: no login")))
    server = start_fake_server(runner)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width in VIEWPORTS:
                for theme in THEMES:
                    if live_up:  # GET only
                        for path, name in (("/", "search-live"), ("/results", "results-live"), ("/settings", "settings-live")):
                            shoot(browser, LIVE, path, name, width, theme, report)
                    for name, (path, setup) in STATES.items():
                        runner.state = type(runner.state)()
                        setup(runner.state)
                        shoot(browser, FAKE, path, name, width, theme, report)
            # Reduced motion on the animated states.
            for name in ("progress", "results"):
                runner.state = type(runner.state)()
                STATES[name][1](runner.state)
                shoot(browser, FAKE, STATES[name][0], name, 1280, "light", report, reduced=True)
            override = theme_override_check(browser, FAKE)
            browser.close()
    finally:
        server.should_exit = True

    (OUT / "report.json").write_text(json.dumps({"rows": report, "theme_override": override}, indent=2))
    print("| page | w | theme | overflow px | controls | unreached | no ring | min contrast | hero min | sticky | brand | skip | titles fit | focus hidden | console err | external | anim |")
    print("|" + "---|" * 17)
    for r in report:
        print(f"| {r['page']} | {r['width']} | {r['theme']}{' RM' if r['reduced_motion'] else ''} | {r['overflow']} | "
              f"{r['controls']} | {len(r['unreached'])} | {len(r['no_focus_ring'])} | {min(r['contrast'].values()):.2f} | "
              f"{min(r['hero_contrast'].values()) if r['hero_contrast'] else '-'} | "
              f"{_sticky(r.get('sticky'))} | {'x'.join(map(str, r['targets']['brand']))} | {'x'.join(map(str, r['targets']['skip_link_focused']))} | "
              f"{all(t['fits'] for t in r['titles']) if r.get('titles') else '-'} | "
              f"{len(r['focus_obscured']) if 'focus_obscured' in r else '-'} | "
              f"{len(r['console_errors'])} | {len(r['external_requests'])} | {r.get('running_animations', '-')} |")
    print("theme override:", override)
    return 0


if __name__ == "__main__":
    sys.exit(main())

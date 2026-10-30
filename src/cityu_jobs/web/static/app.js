// CityU Jobs: the theme toggle and progress polling. No framework; the app works without JS.
"use strict";

// --- Theme: System -> Light -> Dark, remembered in localStorage("theme"). ---
// The pre-paint line in base.html applies the saved theme before this file loads.
const THEMES = ["system", "light", "dark"];
const THEME_LABELS = { system: "System", light: "Light", dark: "Dark" };

function savedTheme() {
  try {
    const t = localStorage.getItem("theme");
    return THEMES.includes(t) ? t : "system";
  } catch (e) {
    return "system";
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  const button = document.querySelector("[data-theme-toggle]");
  if (button) {
    const text = `Theme: ${THEME_LABELS[theme]}`;
    button.setAttribute("aria-label", `${text} (click to change)`);
    button.querySelector(".theme-label").textContent = THEME_LABELS[theme];
  }
}

function setupThemeToggle() {
  const button = document.querySelector("[data-theme-toggle]");
  if (!button) return;
  button.hidden = false; // only useful with JS, so the button starts hidden
  applyTheme(savedTheme());
  button.addEventListener("click", () => {
    const next = THEMES[(THEMES.indexOf(savedTheme()) + 1) % THEMES.length];
    try {
      if (next === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", next);
    } catch (e) { /* private mode: the theme still changes for this page */ }
    applyTheme(next);
  });
}

// --- Progress: poll /api/status while a task runs, then reload to show the result. ---
const POLL_MS = 1500;

function formatElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function setupPolling() {
  const card = document.querySelector("[data-status-url]");
  if (!card) return;
  const message = card.querySelector("[data-progress-message]");
  const elapsed = card.querySelector("[data-elapsed]");
  const timer = setInterval(async () => {
    try {
      const s = await (await fetch(card.dataset.statusUrl, { cache: "no-store" })).json();
      if (message && s.message) message.textContent = s.message;
      if (elapsed) elapsed.textContent = formatElapsed(s.elapsed_s);
      if (s.status !== "running") {
        clearInterval(timer);
        location.reload();
      }
    } catch (e) { /* server restarting (--reload): try again on the next tick */ }
  }, POLL_MS);
}

// --- Example queries on the search page: shown only with JS, since they need it. ---
function setupExamples() {
  const box = document.querySelector("[data-examples]");
  const query = document.getElementById("query");
  if (!box || !query) return;
  box.hidden = false;
  box.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-example]");
    if (!chip) return;
    const text = chip.dataset.example;
    const current = query.value.trim();
    if (!current) query.value = text;
    else if (!current.toLowerCase().includes(text.toLowerCase())) query.value = `${current}, ${text}`;
    // Focus stays on the chip, so keyboard users can add several in a row.
  });
}

// --- Calendar on the results page -------------------------------------------------------
// The page embeds each shown job's dates as JSON (#calendar-data). This draws two month
// grids with a dot for every deadline (rose) and start (green). Hovering or focusing a job
// card highlights that job: its duration as a band, its start, end and deadline, plus a
// strip covering the whole period. "Show on calendar" pins a job (touch screens have no
// hover); Escape unpins. Without JS the "Coming up" list in the panel still works.
const MONTHS_VISIBLE = 2;
const DAY_MS = 86400000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_FMT = { format: (d) => `${new Intl.DateTimeFormat("en", { month: "long" }).format(d)} ${d.getFullYear()}` };
// Same style as the cards ("2 Dec 2026"), whatever the browser's locale.
const DATE_FMT = { format: (d) => `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}` };
const DOW = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

function parseDay(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}
const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const daysBetween = (a, b) => Math.round((b - a) / DAY_MS);
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setupCalendar() {
  const panel = document.querySelector("[data-calendar]");
  const dataNode = document.getElementById("calendar-data");
  if (!panel || !dataNode) return;
  const root = panel.querySelector("[data-cal-root]");
  const detail = panel.querySelector("[data-cal-detail]");
  const defaultDetail = detail.innerHTML;
  const jobs = JSON.parse(dataNode.textContent || "[]").map((j) => ({
    ...j, start: parseDay(j.start), end: parseDay(j.end), deadline: parseDay(j.deadline),
  }));
  const byId = new Map(jobs.map((j) => [j.id, j]));
  const today = new Date(); today.setHours(0, 0, 0, 0);

  // Overview marks: which jobs have a deadline / start on each day.
  const marks = new Map();
  const mark = (date, kind, job) => {
    if (!date) return;
    const k = dayKey(date);
    if (!marks.has(k)) marks.set(k, { deadline: [], start: [] });
    marks.get(k)[kind].push(job.title);
  };
  jobs.forEach((j) => { mark(j.deadline, "deadline", j); mark(j.start, "start", j); });

  let first = new Date(today.getFullYear(), today.getMonth(), 1);
  let active = null;
  let pinned = null;
  let clearTimer = null;

  const narrow = window.matchMedia("(max-width: 57.99rem)");  // matches the CSS breakpoint
  if (narrow.matches) panel.open = false;
  root.hidden = false;

  function monthGrid(monthStart) {
    const box = el("section", "cal-month");
    box.append(el("h3", "", MONTH_FMT.format(monthStart)));
    const grid = el("div", "cal-grid");
    grid.setAttribute("role", "presentation");
    DOW.forEach((d) => grid.append(el("span", "cal-dow", d)));
    const lead = (monthStart.getDay() + 6) % 7; // Monday first
    for (let i = 0; i < lead; i++) grid.append(el("span", "cal-day is-out"));
    const days = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0).getDate();
    for (let n = 1; n <= days; n++) {
      const date = new Date(monthStart.getFullYear(), monthStart.getMonth(), n);
      const cell = el("span", "cal-day");
      const num = el("span", "cal-num", String(n));
      cell.append(num);
      if (date < today) cell.classList.add("is-past");
      if (+date === +today) cell.classList.add("is-today");
      const m = marks.get(dayKey(date));
      if (m) {
        const dots = el("span", "cal-marks");
        if (m.deadline.length) dots.append(el("i", "m-deadline"));
        if (m.start.length) dots.append(el("i", "m-start"));
        cell.append(dots);
        const lines = [];
        if (m.deadline.length) lines.push(`Deadline: ${m.deadline.join(", ")}`);
        if (m.start.length) lines.push(`Starts: ${m.start.join(", ")}`);
        cell.title = lines.join("\n");
      }
      if (active) {
        const { start, end, deadline } = active;
        if (start && end && date >= start && date <= end) {
          cell.classList.add("in-range");
          if (active.approx) cell.classList.add("is-approx");
          if (+date === +start) cell.classList.add("range-start");
          if (+date === +end) cell.classList.add("range-end");
        }
        if (start && +date === +start) cell.classList.add("is-start");
        if (end && +date === +end) cell.classList.add("is-end");
        if (deadline && +date === +deadline) cell.classList.add("is-deadline");
      }
      grid.append(cell);
    }
    box.append(grid);
    return box;
  }

  function render() {
    root.replaceChildren();
    root.classList.toggle("is-focused", Boolean(active));
    const last = new Date(first.getFullYear(), first.getMonth() + MONTHS_VISIBLE - 1, 1);
    const nav = el("div", "cal-nav");
    const prev = el("button", "btn btn-ghost");
    prev.type = "button";
    prev.setAttribute("aria-label", "Earlier months");
    prev.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-left"></use></svg>';
    const next = prev.cloneNode(true);
    next.setAttribute("aria-label", "Later months");
    next.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-right"></use></svg>';
    prev.addEventListener("click", () => { first = new Date(first.getFullYear(), first.getMonth() - 1, 1); render(); });
    next.addEventListener("click", () => { first = new Date(first.getFullYear(), first.getMonth() + 1, 1); render(); });
    const sameYear = first.getFullYear() === last.getFullYear();
    const label = el("span", "cal-range-label", sameYear
      ? `${MONTHS[first.getMonth()]} – ${MONTHS[last.getMonth()]} ${last.getFullYear()}`
      : `${MONTHS[first.getMonth()]} ${first.getFullYear()} – ${MONTHS[last.getMonth()]} ${last.getFullYear()}`);
    label.setAttribute("aria-live", "polite");
    nav.append(prev, label, next);
    root.append(nav);
    for (let i = 0; i < MONTHS_VISIBLE; i++) root.append(monthGrid(new Date(first.getFullYear(), first.getMonth() + i, 1)));
  }

  function fact(keyClass, text) {
    const li = el("li");
    li.append(el("span", `cal-key ${keyClass}`), el("span", "", text));
    return li;
  }

  function relative(date) {
    const d = daysBetween(today, date);
    if (d === 0) return "today";
    if (d > 0) return `in ${d} day${d === 1 ? "" : "s"}`;
    return `${-d} day${d === -1 ? "" : "s"} ago`;
  }

  function strip(job) {
    const points = [today, job.start, job.end, job.deadline].filter(Boolean);
    const lo = new Date(Math.min(...points));
    const hi = new Date(Math.max(...points));
    const span = Math.max(daysBetween(lo, hi), 1);
    const pos = (d) => `${(100 * daysBetween(lo, d)) / span}%`;
    const box = el("div");
    const bar = el("div", "cal-strip");
    bar.setAttribute("aria-hidden", "true");
    bar.append(el("span", "cal-strip-track"));
    if (job.start && job.end) {
      const range = el("span", `cal-strip-range${job.approx ? " is-approx" : ""}`);
      range.style.left = pos(job.start);
      range.style.width = `${Math.max((100 * daysBetween(job.start, job.end)) / span, 1.5)}%`;
      bar.append(range);
    }
    const t = el("span", "cal-strip-today");
    t.style.left = pos(today);
    bar.append(t);
    if (job.start && !job.approx) {
      const s = el("span", "cal-strip-dot is-start");
      s.style.left = pos(job.start);
      bar.append(s);
    }
    if (job.end && !job.approx) {
      const e = el("span", "cal-strip-dot is-end");
      e.style.left = pos(job.end);
      bar.append(e);
    }
    if (job.deadline) {
      const pin = el("span", "cal-strip-pin");
      pin.style.left = pos(job.deadline);
      bar.append(pin);
    }
    const labels = el("div", "cal-strip-labels");
    labels.append(el("span", "", DATE_FMT.format(lo)), el("span", "cal-strip-key", "▮ today"), el("span", "", DATE_FMT.format(hi)));
    box.append(bar, labels);
    return box;
  }

  function describe(job) {
    detail.replaceChildren();
    detail.append(el("p", "cal-detail-title", `#${job.rank} ${job.title}${job.org ? ` · ${job.org}` : ""}`));
    const facts = el("ul", "cal-facts");
    if (job.deadline) facts.append(fact("k-deadline", `Apply by ${DATE_FMT.format(job.deadline)} (${relative(job.deadline)})`));
    if (job.start && job.approx) {
      facts.append(fact("k-range", `${job.label}: dates not given, shown as ${DATE_FMT.format(job.start)} – ${DATE_FMT.format(job.end)}`));
    } else {
      if (job.start) facts.append(fact("k-start", `Starts ${DATE_FMT.format(job.start)} (${relative(job.start)})`));
      if (job.end) facts.append(fact("k-end", `Ends ${DATE_FMT.format(job.end)} (${relative(job.end)})${job.days ? ` · lasts ${job.days} days` : ""}`));
    }
    if (!facts.children.length) facts.append(el("li", "muted", "This listing gives no dates."));
    if (job.deadline && job.end && job.deadline > job.end) {
      facts.append(el("li", "muted", "The deadline is after the listed end date: the period may be out of date, check the listing."));
    }
    detail.append(facts);
    if (job.start || job.deadline) detail.append(strip(job));
  }

  function anchorMonth(job) {
    // Pick the two-month window showing the most of the job's key dates (deadline counts
    // double: it is what the student acts on). Ties go to the earlier window, so a band
    // that runs up to the deadline is shown from its start side.
    const keys = [[job.deadline, 2], [job.start, 1], [job.end, 1]].filter(([d]) => d);
    if (!keys.length) return;
    let best = null;
    let bestScore = -1;
    for (const [d] of keys) {
      for (const shift of [-1, 0]) {
        const start = new Date(d.getFullYear(), d.getMonth() + shift, 1);
        const end = new Date(start.getFullYear(), start.getMonth() + MONTHS_VISIBLE, 0);
        const score = keys.reduce((s, [k, w]) => s + (k >= start && k <= end ? w : 0), 0);
        if (score > bestScore || (score === bestScore && start < best)) { best = start; bestScore = score; }
      }
    }
    first = best;
  }

  function highlight(id) {
    document.querySelectorAll(".is-cal-active").forEach((n) => n.classList.remove("is-cal-active"));
    if (!id) return;
    document.querySelectorAll(`[data-cal-id="${id}"], [data-cal-link="${id}"]`).forEach((n) => n.classList.add("is-cal-active"));
  }

  function show(id) {
    clearTimeout(clearTimer);
    const job = byId.get(id);
    if (!job || job === active) return;
    active = job;
    anchorMonth(job);
    render();
    describe(job);
    highlight(id);
  }

  function clear() {
    clearTimeout(clearTimer);
    clearTimer = setTimeout(() => {
      if (pinned) { if (active !== pinned) { active = null; show(pinned.id); } return; }
      active = null;
      detail.innerHTML = defaultDetail;
      highlight(null);
      render();
    }, 140);
  }

  document.querySelectorAll("[data-cal-id]").forEach((card) => {
    const id = card.dataset.calId;
    card.addEventListener("mouseenter", () => show(id));
    card.addEventListener("mouseleave", clear);
    card.addEventListener("focusin", () => show(id));
    card.addEventListener("focusout", (e) => { if (!card.contains(e.relatedTarget)) clear(); });
  });
  document.querySelectorAll("[data-cal-link]").forEach((link) => {
    const id = link.dataset.calLink;
    link.addEventListener("mouseenter", () => show(id));
    link.addEventListener("mouseleave", clear);
    link.addEventListener("focus", () => show(id));
    link.addEventListener("blur", clear);
  });
  document.querySelectorAll("[data-cal-show]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", () => {
      pinned = byId.get(button.dataset.calShow) || null;
      active = null;
      if (pinned) show(pinned.id);
      if (!panel.open) panel.open = true;
      if (narrow.matches) panel.scrollIntoView({ block: "start" });
    });
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pinned) { pinned = null; clear(); } });

  render();
}

// --- Small helpers for forms that call the AI, copy buttons, the ITC table filter. ---
function setupForms() {
  // Forms that wait for the AI: disable the button and say how long it takes.
  document.querySelectorAll("form[data-wait]").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector('button[type="submit"]');
      const message = form.querySelector("[data-wait-message]");
      if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
      if (message) message.textContent = form.dataset.wait;
    });
  });
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => { if (!window.confirm(form.dataset.confirm)) e.preventDefault(); });
  });
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", async () => {
      const field = document.querySelector(button.dataset.copy);
      try {
        await navigator.clipboard.writeText(field.value);
        button.textContent = "Copied";
      } catch (e) {
        field.select();
        button.textContent = "Press Ctrl+C";
      }
      setTimeout(() => { button.textContent = "Copy"; }, 1800);
    });
  });
  const filter = document.querySelector("[data-sheet-filter]");
  if (filter) {
    filter.hidden = false;
    const rows = [...document.querySelectorAll(".sheet tbody tr")];
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      rows.forEach((row) => { row.hidden = q !== "" && !row.textContent.toLowerCase().includes(q); });
    });
  }
  // Arriving at #itc-row-N from a job: highlight that row.
  if (location.hash.startsWith("#itc-row-")) {
    const row = document.getElementById(location.hash.slice(1));
    const wrap = document.querySelector(".sheet-wrap");
    if (row && wrap) {
      row.classList.add("is-target");
      // Two scrolls: the page to the table, then the table (its own scroll box) to the row,
      // leaving room for the sticky column headers.
      wrap.closest(".sheet-section").scrollIntoView({ block: "start" });
      const headHeight = wrap.querySelector("thead").offsetHeight;
      wrap.scrollTop = row.offsetTop - headHeight - 8;
    }
  }
}

// Phones: the nav row scrolls sideways; show the current page and drop the edge fade at the end.
function setupNav() {
  const nav = document.querySelector(".site-nav");
  if (!nav) return;
  const current = nav.querySelector('[aria-current="page"]');
  if (current && nav.scrollWidth > nav.clientWidth) {
    nav.scrollLeft = current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2;
  }
  const update = () => nav.classList.toggle("at-end", nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 2);
  nav.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
}

setupNav();
setupThemeToggle();
setupPolling();
setupExamples();
setupCalendar();
setupForms();

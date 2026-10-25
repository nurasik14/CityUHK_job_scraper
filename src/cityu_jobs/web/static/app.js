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

setupThemeToggle();
setupPolling();
setupExamples();

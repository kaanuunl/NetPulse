import { api } from "./api.js";
import { $, html, icon, render } from "./dom.js";
import { eventView } from "./events.js";
import { i18n } from "./i18n.js";
import { confirmDialog, toast, toastError } from "./ui.js";
import apps from "./views/apps.js";
import connections from "./views/connections.js";
import history from "./views/history.js";
import overview from "./views/overview.js";
import security from "./views/security.js";
import settings from "./views/settings.js";
import tools from "./views/tools.js";

const VIEWS = [overview, security, apps, connections, history, tools, settings];
const POLL_VISIBLE = 1000;
const POLL_HIDDEN = 5000;
const MAX_EVENTS = 60;

const store = {
  caps: {},
  settings: {},
  autostart: { enabled: false },
  dataDir: "",
  overview: null,
  events: [],
  lastEventId: 0,
  online: true,
  chartWindow: 300,
};

const rendered = { nav: "", privilege: "" };
let activeView = null;
let activeContext = null;
let pollTimer = 0;
let firstPoll = true;

function applyPreferences() {
  i18n.set(store.settings.language);
  document.documentElement.lang = i18n.language;
  document.documentElement.dataset.theme = store.settings.theme || "auto";
}

function createContext() {
  const timers = [];
  return {
    store,
    unit: () => store.settings.speed_unit,
    every(ms, fn, { immediate = true } = {}) {
      const run = () => Promise.resolve(fn()).catch((error) => {
        if (error?.code !== "offline") console.warn(error);
      });
      if (immediate) run();
      timers.push(setInterval(() => !document.hidden && run(), ms));
    },
    async saveSettings(changes) {
      const data = await api.post("/api/settings", { settings: changes });
      const languageChanged = data.settings.language !== store.settings.language;
      absorbSettings(data);
      applyPreferences();
      if (languageChanged) {
        rendered.nav = rendered.privilege = "";
        renderChrome();
        mountView(activeView, true);
      }
      return data;
    },
    elevate,
    refreshOverview: () => poll(),
    navigate: (id) => { window.location.hash = id; },
    dispose() {
      timers.forEach(clearInterval);
    },
  };
}

function absorbSettings(data) {
  store.settings = data.settings;
  store.caps = data.caps;
  store.autostart = data.autostart;
  store.dataDir = data.data_dir;
}

function viewFromHash() {
  const id = window.location.hash.replace("#", "");
  return VIEWS.find((view) => view.id === id) || overview;
}

function mountView(view, force = false) {
  if (view === activeView && !force) return;
  activeView?.unmount?.();
  activeContext?.dispose();
  activeView = view;
  activeContext = createContext();
  // A fresh container per mount drops every listener the previous view attached.
  const root = document.createElement("div");
  root.className = "view";
  $("#view-root").replaceChildren(root);
  $("#view-title").textContent = i18n.t(`nav.${view.id}`);
  $("#view-subtitle").textContent = i18n.t(`subtitle.${view.id}`);
  document.title = `${i18n.t(`nav.${view.id}`)} · NetPulse`;
  renderNav();
  view.mount(root, activeContext);
  if (store.overview) view.update?.(activeContext);
  window.scrollTo(0, 0);
}

function renderIfChanged(slot, target, content) {
  const markup = content.toString();
  if (rendered[slot] === markup) return;
  rendered[slot] = markup;
  render(target, content);
}

function renderNav() {
  const counts = store.overview?.counts || {};
  const threatLevel = store.overview?.security?.level;
  const badges = { apps: counts.apps, connections: counts.connections, security: counts.threats };
  const badgeClass = { security: threatLevel === "bad" ? "nav-badge-bad" : threatLevel === "warn" ? "nav-badge-warn" : "" };
  renderIfChanged("nav", $("#nav"), html`${VIEWS.map((view) => html`
    <a class="nav-item" href="#${view.id}" ${view === activeView ? html`aria-current="page"` : ""}>
      ${icon(view.icon)}<span>${i18n.t(`nav.${view.id}`)}</span>
      ${badges[view.id] ? html`<span class="nav-badge ${badgeClass[view.id] || ""}">${badges[view.id]}</span>` : ""}
    </a>`)}`);
}

function renderPrivilege() {
  const caps = store.caps;
  let content;
  if (caps.demo) {
    content = html`<div class="privilege-title">${icon("sparkles")}${i18n.t("privilege.demo")}</div>${i18n.t("privilege.demo_detail")}`;
  } else if (caps.per_app) {
    content = html`<div class="privilege-title">${icon("shieldCheck")}${i18n.t("privilege.full")}</div>${i18n.t("privilege.full_detail")}`;
  } else {
    const reason = caps.per_app_reason || "not_admin";
    content = html`
      <div class="privilege-title">${icon("shield")}${i18n.t("privilege.limited")}</div>
      ${i18n.t(`privilege.reason.${reason}`)}
      ${caps.elevation ? html`<button class="btn btn-sm btn-primary" type="button" data-action="elevate">${i18n.t("privilege.elevate")}</button>` : ""}`;
  }
  renderIfChanged("privilege", $("#privilege"), content);
}

function renderLive() {
  const indicator = $("#live-indicator");
  indicator.classList.toggle("is-offline", !store.online);
  indicator.querySelector(".live-label").textContent = store.online ? i18n.t("live.on") : i18n.t("live.off");
}

function renderChrome() {
  renderNav();
  renderPrivilege();
  renderLive();
  render($("#quit-button"), html`${icon("power")}<span>${i18n.t("common.quit")}</span>`);
  if (store.caps.demo && !$(".topbar-actions .chip")) {
    $(".topbar-actions").insertAdjacentHTML("afterbegin", `<span class="chip chip-accent"></span>`);
  }
  const demoChip = $(".topbar-actions .chip");
  if (demoChip) demoChip.textContent = i18n.t("privilege.demo");
}

async function elevate() {
  const accepted = await confirmDialog({
    title: i18n.t("privilege.elevate_title"),
    message: i18n.t("privilege.elevate_message"),
    confirm: i18n.t("privilege.elevate"),
  });
  if (!accepted) return;
  try {
    const result = await api.post("/api/system/elevate");
    if (result.restarting) {
      toast({ title: i18n.t("privilege.restarting"), iconName: "refresh" });
      setTimeout(() => window.location.reload(), 4000);
    }
  } catch (error) {
    toastError(error);
  }
}

async function quit() {
  const accepted = await confirmDialog({
    title: i18n.t("quit.title"),
    message: i18n.t("quit.message"),
    confirm: i18n.t("common.quit"),
    danger: true,
  });
  if (!accepted) return;
  await api.post("/api/system/quit").catch(() => {});
  render($("#view-root"), html`<div class="card empty">${icon("power")}${i18n.t("quit.done")}</div>`);
  clearTimeout(pollTimer);
  store.online = false;
  renderLive();
}

function announce(events) {
  for (const event of events) {
    if (event.kind === "baseline") continue;
    const view = eventView(event);
    toast({ title: view.title, message: view.detail, level: event.level, iconName: view.icon });
  }
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    const data = await api.get(`/api/overview?since=${store.lastEventId}&window=${store.chartWindow}`);
    store.overview = data;
    store.caps = data.caps;
    if (data.events.length) {
      store.lastEventId = data.events[data.events.length - 1].id;
      store.events = [...data.events.reverse(), ...store.events].slice(0, MAX_EVENTS);
      if (!firstPoll) announce(data.events);
    }
    firstPoll = false;
    if (!store.online) {
      store.online = true;
      renderLive();
    }
    renderNav();
    renderPrivilege();
    activeView?.update?.(activeContext);
  } catch (error) {
    if (error.code === "offline" && store.online) {
      store.online = false;
      renderLive();
    }
  }
  pollTimer = setTimeout(poll, document.hidden ? POLL_HIDDEN : POLL_VISIBLE);
}

document.addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "elevate") elevate();
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});

async function boot() {
  try {
    absorbSettings(await api.get("/api/settings"));
  } catch {
    store.settings = { language: "en", theme: "auto", speed_unit: "bits" };
  }
  applyPreferences();
  $("#quit-button").addEventListener("click", quit);
  renderChrome();
  window.addEventListener("hashchange", () => mountView(viewFromHash()));
  mountView(viewFromHash());
  poll();
}

boot();

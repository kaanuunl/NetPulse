import { api } from "../api.js";
import { $, debounce, html, icon, render } from "../dom.js";
import { formatBytes, formatDateTime, formatRate } from "../format.js";
import { i18n } from "../i18n.js";
import {
  appAvatar, bindSegmented, bindSort, compare, confirmDialog, emptyState, segmented, sortHeader, toast, toastError,
} from "../ui.js";

const t = (...args) => i18n.t(...args);

const state = {
  apps: [],
  blocked: [],
  query: "",
  scope: "active",
  sort: { key: "rx_rate", dir: "desc" },
  open: new Set(),
  ipInfo: new Map(),
  busy: new Set(),
};

function sortValue(app, key) {
  switch (key) {
    case "name": return (app.title || app.name).toLowerCase();
    case "total": return app.rx_total + app.tx_total;
    default: return app[key] ?? 0;
  }
}

function visibleApps(perApp) {
  const query = state.query.trim().toLocaleLowerCase(i18n.locale);
  let apps = state.apps;
  if (state.scope === "active") {
    apps = apps.filter((a) => a.connections || a.listening || a.rx_rate + a.tx_rate > 0);
  }
  if (query) {
    apps = apps.filter((a) => [a.name, a.title, a.exe, ...a.remotes.map((r) => `${r.ip} ${r.host}`)]
      .some((field) => field && field.toLocaleLowerCase(i18n.locale).includes(query)));
  }
  const sort = !perApp && ["rx_rate", "tx_rate", "total"].includes(state.sort.key)
    ? { key: "connections", dir: "desc" } : state.sort;
  return [...apps].sort((a, b) => compare(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir)
    || compare(sortValue(a, "name"), sortValue(b, "name"), "asc"));
}

function blockButton(app, caps) {
  if (!caps.firewall || !app.exe) return "";
  const busy = state.busy.has(app.key);
  return app.blocked
    ? html`<button class="btn btn-sm" type="button" data-unblock="${app.key}" ${busy ? "disabled" : ""}>${icon("unlock")}${t("apps.unblock")}</button>`
    : html`<button class="btn btn-sm btn-danger" type="button" data-block="${app.key}" ${busy ? "disabled" : ""}>${icon("ban")}${t("apps.block")}</button>`;
}

function remotesTable(app, perApp) {
  if (!app.remotes.length) return html`<p class="muted">${t("apps.no_remotes")}</p>`;
  const peak = Math.max(1, ...app.remotes.map((r) => r.rx + r.tx));
  return html`
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>${t("apps.remote")}</th><th>${t("apps.host")}</th><th>${t("apps.port")}</th>
          ${perApp ? html`<th class="right">${t("apps.received")}</th><th class="right">${t("apps.sent")}</th>` : ""}
          <th class="right">${t("apps.connections")}</th><th></th>
        </tr></thead>
        <tbody>
          ${app.remotes.map((remote) => {
            const info = state.ipInfo.get(remote.ip);
            return html`<tr>
              <td class="mono nowrap">${remote.ip}
                ${remote.scope !== "public" ? html` <span class="chip">${t(`scope.${remote.scope}`)}</span>` : ""}</td>
              <td class="truncate" title="${remote.host}">${remote.host || html`<span class="muted">—</span>`}
                ${info?.ok ? html`<div class="muted">${[info.org, info.city, info.country].filter(Boolean).join(" · ")}</div>` : ""}</td>
              <td class="nowrap">${remote.ports.map((p) => (p.service ? `${p.port} (${p.service})` : p.port)).join(", ") || "—"}</td>
              ${perApp ? html`<td class="right">${formatBytes(remote.rx)}<span class="cell-bar" data-w="${((remote.rx + remote.tx) / peak) * 100}"></span></td>
                <td class="right">${formatBytes(remote.tx)}</td>` : ""}
              <td class="right">${remote.connections || "—"}</td>
              <td class="right">${remote.scope === "public" && !info
                ? html`<button class="btn btn-sm btn-ghost" type="button" data-ipinfo="${remote.ip}">${icon("mapPin")}${t("apps.whois")}</button>`
                : ""}</td>
            </tr>`;
          })}
        </tbody>
      </table>
    </div>`;
}

function detailRow(app, caps, columns) {
  return html`<tr class="detail-row"><td colspan="${columns}">
    <div class="detail">
      <div class="detail-meta">
        ${app.exe ? html`<span><b>${t("apps.path")}</b><span class="mono">${app.exe}</span></span>` : ""}
        <span><b>PID</b>${app.pids.join(", ") || "—"}</span>
        <span><b>${t("apps.first_seen")}</b>${formatDateTime(app.first_seen)}</span>
        ${app.listening ? html`<span><b>${t("apps.listening")}</b>${app.listening}</span>` : ""}
      </div>
      <div class="row">
        ${blockButton(app, caps)}
        ${app.exe ? html`<button class="btn btn-sm" type="button" data-reveal="${app.key}">${icon("folder")}${t("apps.reveal")}</button>` : ""}
      </div>
      ${remotesTable(app, caps.per_app)}
    </div>
  </td></tr>`;
}

function renderTable(root, ctx) {
  const { caps } = ctx.store;
  const perApp = caps.per_app;
  const unit = ctx.unit();
  const apps = visibleApps(perApp);
  const columns = perApp ? 7 : 4;
  $("#apps-count", root).textContent = t("apps.count", { count: apps.length });

  if (!apps.length) {
    render($("#apps-table", root), emptyState("apps", state.query ? t("common.no_match") : t("apps.empty")));
    return;
  }
  render($("#apps-table", root), html`
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th></th>
          ${sortHeader(t("apps.app"), "name", state.sort)}
          ${perApp ? html`
            ${sortHeader(t("common.download"), "rx_rate", state.sort, "right")}
            ${sortHeader(t("common.upload"), "tx_rate", state.sort, "right")}
            ${sortHeader(t("apps.session_total"), "total", state.sort, "right")}` : ""}
          ${sortHeader(t("apps.connections"), "connections", state.sort, "right")}
          <th class="right"></th>
        </tr></thead>
        <tbody>
          ${apps.map((app) => {
            const open = state.open.has(app.key);
            return html`
              <tr class="is-clickable ${open ? "is-open" : ""}" data-toggle="${app.key}">
                <td><button class="table-row-toggle" type="button" aria-expanded="${String(open)}"
                  aria-label="${t("apps.details")}">${icon("chevronRight")}</button></td>
                <td>
                  <div class="app-cell">
                    ${appAvatar(app, caps)}
                    <div class="app-cell-text">
                      <div class="app-cell-name">${app.title || app.name}
                        ${app.blocked ? html`<span class="chip chip-bad">${icon("ban")}${t("apps.blocked")}</span>` : ""}</div>
                      <div class="app-cell-path truncate" title="${app.exe}">${app.title ? app.name : app.exe || ""}</div>
                    </div>
                  </div>
                </td>
                ${perApp ? html`
                  <td class="right nowrap">${formatRate(app.rx_rate, unit)}</td>
                  <td class="right nowrap">${formatRate(app.tx_rate, unit)}</td>
                  <td class="right nowrap">${formatBytes(app.rx_total + app.tx_total)}</td>` : ""}
                <td class="right">${app.connections}</td>
                <td class="right">${blockButton(app, caps)}</td>
              </tr>
              ${open ? detailRow(app, caps, columns) : ""}`;
          })}
        </tbody>
      </table>
    </div>`);
}

function renderBlocked(root) {
  const target = $("#apps-blocked", root);
  if (!state.blocked.length) {
    target.hidden = true;
    return;
  }
  target.hidden = false;
  render(target, html`
    <div class="card-header"><div>
      <h2 class="card-title">${t("apps.blocked_title")}</h2>
      <p class="card-subtitle">${t("apps.blocked_sub")}</p>
    </div></div>
    <div class="app-list">${state.blocked.map((entry) => html`
      <div class="app-row">
        <span class="avatar">${icon("ban")}</span>
        <div class="app-cell-text"><div class="app-row-name">${entry.name}</div><div class="app-cell-path truncate">${entry.exe}</div></div>
        <button class="btn btn-sm" type="button" data-unblock="${entry.exe}">${icon("unlock")}${t("apps.unblock")}</button>
      </div>`)}
    </div>`);
}

function renderBanner(root, ctx) {
  const { caps } = ctx.store;
  const target = $("#apps-banner", root);
  if (caps.per_app) {
    target.replaceChildren();
    return;
  }
  const reason = caps.per_app_reason || "not_admin";
  render(target, html`<div class="callout callout-warn">
    ${icon("shield")}
    <div><strong>${t("apps.limited_title")}</strong>${t(`privilege.reason.${reason}`)}</div>
    ${caps.elevation ? html`<button class="btn btn-primary btn-sm" type="button" data-action="elevate">${t("privilege.elevate")}</button>` : ""}
  </div>`);
}

async function load(root, ctx) {
  const data = await api.get("/api/apps");
  state.apps = data.apps;
  state.blocked = data.blocked;
  renderBanner(root, ctx);
  renderTable(root, ctx);
  renderBlocked(root);
}

async function setBlocked(root, ctx, key, block) {
  const app = state.apps.find((a) => a.key === key) || { title: key, name: key, key };
  const name = app.title || app.name;
  if (block) {
    const accepted = await confirmDialog({
      title: t("apps.block_title", { app: name }),
      message: t("apps.block_message"),
      confirm: t("apps.block"),
      danger: true,
    });
    if (!accepted) return;
  }
  state.busy.add(key);
  renderTable(root, ctx);
  try {
    await api.post(block ? "/api/apps/block" : "/api/apps/unblock", { key });
    toast({
      title: t(block ? "apps.blocked_toast" : "apps.unblocked_toast", { app: name }),
      level: "good",
      iconName: block ? "ban" : "unlock",
    });
  } catch (error) {
    toastError(error);
  } finally {
    state.busy.delete(key);
    await load(root, ctx);
  }
}

async function lookupIp(root, ctx, ip) {
  state.ipInfo.set(ip, { loading: true });
  try {
    state.ipInfo.set(ip, await api.post("/api/tools/ipinfo", { ip }));
  } catch (error) {
    state.ipInfo.delete(ip);
    toastError(error);
  }
  renderTable(root, ctx);
}

export default {
  id: "apps",
  icon: "apps",

  mount(root, ctx) {
    render(root, html`
      <div id="apps-banner"></div>
      <div class="toolbar">
        <label class="search">${icon("search")}
          <span class="visually-hidden">${t("common.search")}</span>
          <input class="input input-search" type="search" id="apps-search" placeholder="${t("apps.search")}" value="${state.query}">
        </label>
        ${segmented("scope", [["active", t("apps.scope_active")], ["all", t("apps.scope_all")]], state.scope)}
        <span class="spacer"></span>
        <span class="muted" id="apps-count"></span>
      </div>
      <section class="card card-flush" id="apps-table"></section>
      <section class="card" id="apps-blocked" hidden></section>`);

    $("#apps-search", root).addEventListener("input", debounce((event) => {
      state.query = event.target.value;
      renderTable(root, ctx);
    }, 120));
    bindSegmented(root, "scope", (value) => {
      state.scope = value;
      renderTable(root, ctx);
    });
    bindSort($("#apps-table", root), state.sort, () => renderTable(root, ctx));

    root.addEventListener("click", (event) => {
      const target = event.target;
      const block = target.closest("[data-block]");
      const unblock = target.closest("[data-unblock]");
      const reveal = target.closest("[data-reveal]");
      const ipinfo = target.closest("[data-ipinfo]");
      if (block) setBlocked(root, ctx, block.dataset.block, true);
      else if (unblock) setBlocked(root, ctx, unblock.dataset.unblock, false);
      else if (reveal) api.post("/api/apps/reveal", { key: reveal.dataset.reveal }).catch(toastError);
      else if (ipinfo) lookupIp(root, ctx, ipinfo.dataset.ipinfo);
      else if (!target.closest("button, a, input, .detail-row")) {
        const row = target.closest("[data-toggle]");
        if (!row) return;
        const key = row.dataset.toggle;
        if (state.open.has(key)) state.open.delete(key);
        else state.open.add(key);
        renderTable(root, ctx);
      }
      if (target.closest(".table-row-toggle")) {
        const key = target.closest("[data-toggle]").dataset.toggle;
        if (state.open.has(key)) state.open.delete(key);
        else state.open.add(key);
        renderTable(root, ctx);
      }
    });

    ctx.every(2000, () => load(root, ctx));
  },
};

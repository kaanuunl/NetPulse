import { api } from "../api.js";
import { BarChart } from "../charts.js";
import { $, html, icon, render } from "../dom.js";
import {
  formatBitsPerSecond, formatBytes, formatDateTime, formatDay, formatMonth, formatMs, formatPercent, splitBytes,
} from "../format.js";
import { i18n } from "../i18n.js";
import { appAvatar, bindSegmented, emptyState, segmented, toastError } from "../ui.js";

const t = (...args) => i18n.t(...args);

const state = { days: 30, data: null, showTable: false };
let chart = null;

function tile(iconName, label, value, sub, extra = "") {
  return html`<article class="card tile">
    <div class="tile-label">${icon(iconName)}${label}</div>
    <div class="tile-value">${value}</div>
    ${extra}
    <div class="tile-sub">${sub}</div>
  </article>`;
}

function bytesValue(bytes) {
  const { value, unit } = splitBytes(bytes);
  return html`${value}<span class="tile-unit">${unit}</span>`;
}

function renderTiles(root) {
  const { summary } = state.data;
  const { period, quota, today } = summary;
  const used = period.rx + period.tx;
  const range = `${formatDay(period.start)} – ${formatDay(period.end)}`;
  const quotaRatio = quota.bytes ? period.projected / quota.bytes : null;
  const meterClass = quota.used_ratio >= 1 ? "is-bad" : quota.used_ratio >= 0.8 ? "is-warn" : "";

  render($("#hist-tiles", root), html`
    ${tile("database", t("history.period_used"), bytesValue(used),
      t("history.period_range", { range, elapsed: period.elapsed_days, total: period.total_days }))}
    ${tile("trend", t("history.daily_average"), bytesValue(period.daily_average),
      t("history.today", { value: formatBytes(today.rx + today.tx) }))}
    ${tile("calendar", t("history.projected"), bytesValue(period.projected),
      quotaRatio === null ? t("history.no_quota") : t("history.projected_ratio", { percent: formatPercent(quotaRatio) }))}
    ${quota.bytes
      ? tile("gauge", t("history.remaining"), bytesValue(quota.remaining),
        t("history.quota_used", { percent: formatPercent(quota.used_ratio), quota: formatBytes(quota.bytes) }),
        html`<div class="meter ${meterClass}"><div class="meter-fill" data-w="${quota.used_ratio * 100}"></div></div>`)
      : tile("gauge", t("history.remaining"), html`<span class="muted">—</span>`,
        html`<a href="#settings">${t("history.set_quota")}</a>`)}`);
}

function renderDailyTable(root) {
  const target = $("#hist-daily-table", root);
  target.hidden = !state.showTable;
  if (!state.showTable) return;
  const rows = [...state.data.daily].reverse();
  render(target, html`<div class="table-wrap"><table class="table">
    <thead><tr><th>${t("history.date")}</th><th class="right">${t("common.download")}</th>
      <th class="right">${t("common.upload")}</th><th class="right">${t("history.total")}</th></tr></thead>
    <tbody>${rows.map((row) => html`<tr>
      <td>${formatDay(row.date, { weekday: "short", day: "numeric", month: "long" })}</td>
      <td class="right">${formatBytes(row.rx)}</td><td class="right">${formatBytes(row.tx)}</td>
      <td class="right">${formatBytes(row.rx + row.tx)}</td></tr>`)}</tbody>
  </table></div>`);
}

function renderTopApps(root, ctx) {
  const apps = state.data.top_apps;
  const target = $("#hist-apps", root);
  if (!apps.length) {
    const reason = ctx.store.caps.per_app ? "history.no_app_data" : `overview.per_app_hint.${ctx.store.caps.per_app_reason || "not_admin"}`;
    render(target, emptyState("apps", t(reason)));
    return;
  }
  const peak = Math.max(1, ...apps.map((a) => a.rx + a.tx));
  render(target, html`<div class="app-list">${apps.map((app) => {
    const name = app.app === "__other__" ? t("history.other_apps") : app.title || app.app;
    const avatarSource = { name: app.app, title: app.title, exe: app.exe || "", key: (app.exe || "").toLowerCase() };
    return html`<div class="app-row">
      ${appAvatar(avatarSource, ctx.store.caps)}
      <div class="app-cell-text">
        <div class="app-row-name truncate" title="${app.exe || app.app}">${name}</div>
        <div class="app-row-bar">
          <span class="bar-seg down" data-w="${(app.rx / peak) * 100}"></span>
          <span class="bar-seg up" data-w="${(app.tx / peak) * 100}"></span>
        </div>
      </div>
      <div class="app-row-rate"><strong>${formatBytes(app.rx + app.tx)}</strong>
        <div class="muted">↓ ${formatBytes(app.rx)} · ↑ ${formatBytes(app.tx)}</div></div>
    </div>`;
  })}</div>`);
}

function renderMonthly(root) {
  const months = [...state.data.monthly].reverse().filter((m, i) => i === 0 || m.rx + m.tx > 0);
  const peak = Math.max(1, ...months.map((m) => m.rx + m.tx));
  render($("#hist-monthly", root), html`<div class="table-wrap"><table class="table">
    <thead><tr><th>${t("history.month")}</th><th class="right">${t("common.download")}</th>
      <th class="right">${t("common.upload")}</th><th class="right">${t("history.total")}</th></tr></thead>
    <tbody>${months.map((m) => html`<tr>
      <td class="nowrap">${formatMonth(m.month)}</td>
      <td class="right">${formatBytes(m.rx)}</td>
      <td class="right">${formatBytes(m.tx)}</td>
      <td class="right"><strong>${formatBytes(m.rx + m.tx)}</strong><span class="cell-bar" data-w="${((m.rx + m.tx) / peak) * 100}"></span></td>
    </tr>`)}</tbody>
  </table></div>`);
}

function renderSpeedtests(root) {
  const tests = [...state.data.speedtests].reverse();
  const target = $("#hist-speedtests", root);
  if (!tests.length) {
    render(target, emptyState("gauge", html`${t("history.no_speedtests")} <a href="#tools">${t("history.run_speedtest")}</a>`));
    return;
  }
  render(target, html`<div class="table-wrap"><table class="table">
    <thead><tr><th>${t("history.date")}</th><th class="right">${t("common.download")}</th>
      <th class="right">${t("common.upload")}</th><th class="right">${t("tools.ping")}</th>
      <th class="right">${t("tools.jitter")}</th><th>${t("tools.server")}</th></tr></thead>
    <tbody>${tests.map((test) => html`<tr>
      <td class="nowrap">${formatDateTime(test.ts)}</td>
      <td class="right nowrap"><strong>${formatBitsPerSecond(test.download_bps)}</strong></td>
      <td class="right nowrap">${formatBitsPerSecond(test.upload_bps)}</td>
      <td class="right">${formatMs(test.ping_ms)}</td>
      <td class="right">${formatMs(test.jitter_ms)}</td>
      <td class="truncate">${[test.server?.colo, test.server?.isp].filter(Boolean).join(" · ") || "—"}</td>
    </tr>`)}</tbody>
  </table></div>`);
}

function renderAll(root, ctx) {
  if (!state.data) return;
  renderTiles(root);
  chart.setData(state.data.daily.map((row) => ({
    label: formatDay(row.date, state.days > 31 ? { day: "numeric", month: "numeric" } : { day: "numeric", month: "short" }),
    title: formatDay(row.date, { weekday: "long", day: "numeric", month: "long" }),
    values: [row.rx, row.tx],
  })));
  renderDailyTable(root);
  renderTopApps(root, ctx);
  renderMonthly(root);
  renderSpeedtests(root);
}

async function load(root, ctx) {
  const target = $("#hist-body", root);
  target?.classList.add("is-refreshing");
  try {
    state.data = await api.get(`/api/history?days=${state.days}`);
    renderAll(root, ctx);
  } finally {
    target?.classList.remove("is-refreshing");
  }
}

async function exportCsv() {
  try {
    const csv = await api.text("/api/export.csv");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `agnabzi-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    toastError(error);
  }
}

export default {
  id: "history",
  icon: "chart",

  mount(root, ctx) {
    render(root, html`
      <div class="toolbar">
        ${segmented("days", [["7", t("history.days_7")], ["30", t("history.days_30")], ["90", t("history.days_90")]], String(state.days))}
        <span class="spacer"></span>
        <button class="btn" type="button" id="hist-export">${icon("download")}${t("history.export")}</button>
      </div>
      <div class="stack" id="hist-body">
        <section class="tiles tiles-4" id="hist-tiles"></section>
        <section class="card">
          <div class="card-header">
            <div><h2 class="card-title">${t("history.daily")}</h2><p class="card-subtitle">${t("history.daily_sub")}</p></div>
            <div class="row">
              <div class="legend">
                <span class="legend-item"><span class="key-dot key-down"></span>${t("common.download")}</span>
                <span class="legend-item"><span class="key-dot key-up"></span>${t("common.upload")}</span>
              </div>
              <button class="btn btn-sm btn-ghost" type="button" id="hist-toggle-table" aria-pressed="${String(state.showTable)}">
                ${t(state.showTable ? "history.hide_table" : "history.show_table")}</button>
            </div>
          </div>
          <div class="chart" id="hist-chart"></div>
          <div id="hist-daily-table" hidden></div>
        </section>
        <section class="grid-2">
          <article class="card">
            <div class="card-header"><div><h2 class="card-title">${t("history.top_apps")}</h2>
              <p class="card-subtitle">${t("history.top_apps_sub")}</p></div></div>
            <div id="hist-apps"></div>
          </article>
          <article class="card card-flush">
            <div class="card-header"><h2 class="card-title">${t("history.monthly")}</h2></div>
            <div id="hist-monthly"></div>
          </article>
        </section>
        <section class="card card-flush">
          <div class="card-header"><div><h2 class="card-title">${t("history.speedtests")}</h2>
            <p class="card-subtitle">${t("history.speedtests_sub")}</p></div></div>
          <div id="hist-speedtests"></div>
        </section>
      </div>`);

    chart = new BarChart($("#hist-chart", root), {
      series: [
        { color: "--down", keyClass: "key-down", label: () => t("common.download") },
        { color: "--up", keyClass: "key-up", label: () => t("common.upload") },
      ],
      niceBase: 1024,
      yFormat: (v) => formatBytes(v),
      valueFormat: (v) => formatBytes(v),
      totalLabel: () => t("history.total"),
      emptyText: t("history.empty"),
    });

    bindSegmented(root, "days", (value) => {
      state.days = Number(value);
      load(root, ctx).catch(toastError);
    });
    $("#hist-export", root).addEventListener("click", exportCsv);
    $("#hist-toggle-table", root).addEventListener("click", (event) => {
      state.showTable = !state.showTable;
      event.currentTarget.setAttribute("aria-pressed", String(state.showTable));
      event.currentTarget.textContent = t(state.showTable ? "history.hide_table" : "history.show_table");
      renderDailyTable(root);
    });
    ctx.every(60000, () => load(root, ctx));
  },

  unmount() {
    chart?.destroy();
    chart = null;
  },
};

import { LineChart } from "../charts.js";
import { $, html, icon, render } from "../dom.js";
import { eventView } from "../events.js";
import {
  formatBitsPerSecond, formatBytes, formatClock, formatMs, formatNumber, formatPercent, formatRate,
  formatRelative, formatTime, splitBytes, splitRate,
} from "../format.js";
import { i18n } from "../i18n.js";
import { appAvatar, bindSegmented, emptyState, segmented, statusBadge } from "../ui.js";

const t = (...args) => i18n.t(...args);
const SPARK_SECONDS = 90;
let charts = {};

function toDisplay(bytesPerSecond, unit) {
  return unit === "bits" ? bytesPerSecond * 8 : bytesPerSecond;
}

function displayRate(value, unit) {
  return unit === "bits" ? formatBitsPerSecond(value) : formatRate(value, "bytes");
}

function legendItem(keyClass, label, shape = "dot") {
  return html`<span class="legend-item"><span class="${shape === "line" ? "key-line" : "key-dot"} ${keyClass}"></span>${label}</span>`;
}

function valueWithUnit({ value, unit }) {
  return html`${value}<span class="tile-unit">${unit}</span>`;
}

function tileTemplate(id, withSpark = false) {
  return html`<article class="card tile" id="tile-${id}"><div class="tile-body"></div>${withSpark ? html`<div class="tile-spark"></div>` : ""}</article>`;
}

function renderTiles(data, ctx) {
  const unit = ctx.unit();
  const { rates, session, usage, latency } = data;
  const today = usage.today.rx + usage.today.tx;
  const period = usage.period.rx + usage.period.tx;

  render($("#tile-down .tile-body"), html`
    <div class="tile-label"><span class="key-dot key-down"></span>${t("common.download")}</div>
    <div class="tile-value num">${valueWithUnit(splitRate(rates.down, unit))}</div>
    <div class="tile-sub">${t("overview.session_total", { value: formatBytes(session.rx) })}</div>`);

  render($("#tile-up .tile-body"), html`
    <div class="tile-label"><span class="key-dot key-up"></span>${t("common.upload")}</div>
    <div class="tile-value num">${valueWithUnit(splitRate(rates.up, unit))}</div>
    <div class="tile-sub">${t("overview.session_total", { value: formatBytes(session.tx) })}</div>`);

  render($("#tile-today .tile-body"), html`
    <div class="tile-label">${icon("calendar")}${t("overview.today")}</div>
    <div class="tile-value">${valueWithUnit(splitBytes(today))}</div>
    <div class="tile-sub">↓ ${formatBytes(usage.today.rx)} · ↑ ${formatBytes(usage.today.tx)}</div>`);

  const quota = usage.quota;
  let periodDetail;
  if (quota.bytes) {
    const ratio = quota.used_ratio;
    const meterClass = ratio >= 1 ? "is-bad" : ratio >= 0.8 ? "is-warn" : "";
    periodDetail = html`
      <div class="meter ${meterClass}" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(ratio * 100)}">
        <div class="meter-fill" data-w="${ratio * 100}"></div>
      </div>
      <div class="tile-sub">${t("overview.quota_of", { quota: formatBytes(quota.bytes), percent: formatPercent(ratio) })}</div>`;
  } else {
    periodDetail = html`<div class="tile-sub">${t("overview.projected", { value: formatBytes(usage.period.projected) })}</div>`;
  }
  render($("#tile-period .tile-body"), html`
    <div class="tile-label">${icon("database")}${t("overview.period")}</div>
    <div class="tile-value">${valueWithUnit(splitBytes(period))}</div>
    ${periodDetail}`);

  const internet = latency?.targets?.internet;
  const level = latency?.level || "unknown";
  render($("#tile-quality .tile-body"), html`
    <div class="tile-label">${icon("pulse")}${t("overview.quality")}</div>
    <div class="tile-value tile-status">${statusBadge(level, t(`quality.${level}`))}</div>
    <div class="tile-sub">${internet?.avg != null
      ? t("overview.quality_sub", { ms: formatMs(internet.avg), loss: formatPercent(internet.loss) })
      : t("overview.measuring")}</div>`);
}

function renderTopApps(data, ctx) {
  const { caps } = ctx.store;
  const apps = data.top_apps;
  const target = $("#ov-apps");
  if (!apps.length) {
    render(target, emptyState("apps", t("overview.no_apps")));
    return;
  }
  const unit = ctx.unit();
  const perApp = caps.per_app;
  const peak = Math.max(1, ...apps.map((a) => (perApp ? a.rx_rate + a.tx_rate : a.connections)));
  render(target, html`
    ${perApp ? "" : html`<div class="callout callout-info">${icon("info")}<div>${t(`overview.per_app_hint.${caps.per_app_reason || "not_admin"}`)}</div></div>`}
    <div class="app-list">
      ${apps.map((app) => html`
        <div class="app-row">
          ${appAvatar(app, caps)}
          <div class="app-cell-text">
            <div class="app-row-name truncate" title="${app.exe || app.name}">${app.title || app.name}</div>
            <div class="app-row-bar">
              ${perApp
                ? html`<span class="bar-seg down" data-w="${(app.rx_rate / peak) * 100}"></span><span class="bar-seg up" data-w="${(app.tx_rate / peak) * 100}"></span>`
                : html`<span class="bar-seg neutral" data-w="${(app.connections / peak) * 100}"></span>`}
            </div>
          </div>
          <div class="app-row-rate">
            ${perApp
              ? html`<div class="rate-down">${icon("down")}${formatRate(app.rx_rate, unit)}</div>
                     <div class="rate-up">${icon("up")}${formatRate(app.tx_rate, unit)}</div>`
              : html`<div>${t("common.connections_count", { count: app.connections })}</div>`}
          </div>
        </div>`)}
    </div>`);
}

function renderQuality(data) {
  const latency = data.latency;
  if (!latency) return;
  const level = latency.level;
  const calloutClass = { good: "callout-good", warn: "callout-warn", bad: "callout-bad" }[level] || "callout-info";
  const calloutIcon = { good: "checkCircle", warn: "alert", bad: "xCircle" }[level] || "clock";
  render($("#ov-diagnosis"), html`
    <div class="callout ${calloutClass}">
      ${icon(calloutIcon)}
      <div><strong>${t(`diagnosis.${latency.diagnosis}.title`)}</strong>${t(`diagnosis.${latency.diagnosis}.detail`)}</div>
    </div>`);

  const rows = [
    ["gateway", "key-gateway", t("overview.gateway"), latency.targets.gateway],
    ["internet", "key-internet", t("overview.internet"), latency.targets.internet],
  ];
  render($("#ov-latency-stats"), html`
    <div class="table-wrap">
      <table class="table table-compact">
        <thead><tr>
          <th>${t("overview.target")}</th>
          <th class="right">${t("overview.average")}</th><th class="right">${t("overview.jitter")}</th>
          <th class="right">${t("overview.loss")}</th>
        </tr></thead>
        <tbody>
          ${rows.map(([, keyClass, label, stats]) => html`<tr>
            <td><span class="legend-item"><span class="key-line ${keyClass}"></span>${label}</span>
              <span class="muted mono"> ${stats.host || t("overview.no_gateway")}</span></td>
            <td class="right nowrap">${formatMs(stats.avg)}</td>
            <td class="right nowrap">${formatMs(stats.jitter)}</td>
            <td class="right nowrap">${stats.samples ? formatPercent(stats.loss) : "—"}</td>
          </tr>`)}
        </tbody>
      </table>
    </div>`);
}

function renderEvents(ctx) {
  const events = ctx.store.events.filter((e) => e.kind !== "baseline" || ctx.store.events.length < 3).slice(0, 8);
  if (!events.length) {
    render($("#ov-events"), emptyState("shieldCheck", t("overview.no_events")));
    return;
  }
  render($("#ov-events"), html`<div class="events">${events.map((event) => {
    const view = eventView(event);
    return html`<div class="event event-${event.level}">
      <span class="event-icon">${icon(view.icon)}</span>
      <div class="event-text"><strong>${view.title}</strong>${view.detail ? html`<div class="event-detail">${view.detail}</div>` : ""}</div>
      <time class="event-time" datetime="${new Date(event.ts * 1000).toISOString()}" title="${formatTime(event.ts)}">${formatRelative(event.ts)}</time>
    </div>`;
  })}</div>`);
}

function updateCharts(data, ctx) {
  const unit = ctx.unit();
  const history = data.history;
  const now = data.ts;
  const down = history.map(([ts, d]) => [ts, toDisplay(d, unit)]);
  const up = history.map(([ts, , u]) => [ts, toDisplay(u, unit)]);
  charts.traffic.setData([down, up], { start: now - ctx.store.chartWindow, end: now });
  const sparkStart = now - SPARK_SECONDS;
  charts.sparkDown.setData([down.filter((p) => p[0] >= sparkStart)], { start: sparkStart, end: now });
  charts.sparkUp.setData([up.filter((p) => p[0] >= sparkStart)], { start: sparkStart, end: now });

  const targets = data.latency?.targets;
  if (targets) {
    const gateway = targets.gateway.history || [];
    const internet = targets.internet.history || [];
    const first = Math.min(...[...gateway, ...internet].map((p) => p[0]), now - 60);
    charts.latency.setData([gateway, internet], { start: Math.max(first, now - 300), end: now });
  }
}

export default {
  id: "overview",
  icon: "activity",

  mount(root, ctx) {
    render(root, html`
      <section class="tiles">
        ${tileTemplate("down", true)}${tileTemplate("up", true)}${tileTemplate("today")}
        ${tileTemplate("period")}${tileTemplate("quality")}
      </section>

      <section class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">${t("overview.traffic")}</h2>
            <p class="card-subtitle">${t("overview.traffic_sub")}</p>
          </div>
          <div class="row">
            <div class="legend">${legendItem("key-down", t("common.download"))}${legendItem("key-up", t("common.upload"))}</div>
            ${segmented("window", [["60", t("overview.window_1m")], ["300", t("overview.window_5m")], ["600", t("overview.window_10m")]], String(ctx.store.chartWindow))}
          </div>
        </div>
        <div class="chart" id="ov-traffic"></div>
      </section>

      <section class="grid-wide">
        <article class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">${t("overview.top_apps")}</h2>
              <p class="card-subtitle">${t("overview.top_apps_sub")}</p>
            </div>
            <a href="#apps">${t("overview.see_all")}</a>
          </div>
          <div id="ov-apps"></div>
        </article>
        <article class="card stack">
          <div class="card-header">
            <div>
              <h2 class="card-title">${t("overview.quality")}</h2>
              <p class="card-subtitle">${t("overview.quality_card_sub")}</p>
            </div>
            <div class="legend">
              ${legendItem("key-gateway", t("overview.gateway"), "line")}
              ${legendItem("key-internet", t("overview.internet"), "line")}
              ${legendItem("key-loss", t("overview.loss"), "line")}
            </div>
          </div>
          <div id="ov-diagnosis"></div>
          <div class="chart chart-sm" id="ov-latency"></div>
          <div id="ov-latency-stats"></div>
        </article>
      </section>

      <section class="card">
        <div class="card-header"><h2 class="card-title">${t("overview.events")}</h2></div>
        <div id="ov-events"></div>
      </section>`);

    const unit = () => ctx.unit();
    const rateSeries = [
      { color: "--down", keyClass: "key-down", label: () => t("common.download") },
      { color: "--up", keyClass: "key-up", label: () => t("common.upload") },
    ];
    charts = {
      traffic: new LineChart($("#ov-traffic"), {
        series: rateSeries,
        niceBase: () => (unit() === "bits" ? 10 : 1024),
        minMax: 8000,
        yFormat: (v) => displayRate(v, unit()),
        valueFormat: (v) => displayRate(v, unit()),
        xFormat: (ts, step) => (step < 60 ? formatTime(ts) : formatClock(ts)),
        titleFormat: formatTime,
        emptyText: t("overview.collecting"),
      }),
      sparkDown: new LineChart($("#tile-down .tile-spark"), { series: [rateSeries[0]], compact: true, interactive: false }),
      sparkUp: new LineChart($("#tile-up .tile-spark"), { series: [rateSeries[1]], compact: true, interactive: false }),
      latency: new LineChart($("#ov-latency"), {
        series: [
          { color: "--lat-gateway", keyClass: "key-gateway", label: () => t("overview.gateway") },
          { color: "--lat-internet", keyClass: "key-internet", label: () => t("overview.internet") },
        ],
        area: false,
        lossMarkers: true,
        minMax: 10,
        yFormat: (v) => `${formatNumber(v)} ms`,
        valueFormat: (v) => (v === null ? t("overview.lost") : formatMs(v)),
        xFormat: (ts, step) => (step < 60 ? formatTime(ts) : formatClock(ts)),
        titleFormat: formatTime,
        emptyText: t("overview.measuring"),
      }),
    };

    bindSegmented(root, "window", (value) => {
      ctx.store.chartWindow = Number(value);
      ctx.refreshOverview();
    });
  },

  update(ctx) {
    const data = ctx.store.overview;
    if (!data) return;
    renderTiles(data, ctx);
    updateCharts(data, ctx);
    renderTopApps(data, ctx);
    renderQuality(data);
    renderEvents(ctx);
  },

  unmount() {
    Object.values(charts).forEach((chart) => chart.destroy());
    charts = {};
  },
};

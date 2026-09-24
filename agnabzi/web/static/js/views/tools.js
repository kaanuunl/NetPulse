import { api } from "../api.js";
import { Gauge } from "../charts.js";
import { $, html, icon, render } from "../dom.js";
import {
  formatBitsPerSecond, formatDateTime, formatMs, formatNumber, formatPercent, formatRate, splitRate,
} from "../format.js";
import { i18n } from "../i18n.js";
import { emptyState, toastError } from "../ui.js";

const t = (...args) => i18n.t(...args);

const state = {
  speed: null,
  lastSpeed: null,
  trace: null,
  adapters: [],
};
let gauge = null;
let disposed = false;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function followJob(job, onUpdate, interval) {
  let current = job;
  while (!disposed && current.state === "running") {
    await sleep(interval);
    current = await api.get(`/api/jobs/${encodeURIComponent(current.id)}`);
    onUpdate(current);
  }
  return current;
}

function formCard(id, iconName, title, subtitle, fields, button) {
  return html`<article class="card">
    <div class="card-header"><div>
      <h2 class="card-title">${title}</h2>${subtitle ? html`<p class="card-subtitle">${subtitle}</p>` : ""}
    </div><span class="muted">${icon(iconName)}</span></div>
    <form class="tool-form" data-tool="${id}" novalidate>${fields}
      <button class="btn btn-primary" type="submit">${button}</button>
    </form>
    <div class="tool-result" id="result-${id}"></div>
  </article>`;
}

function textField(name, placeholder, value, extraClass = "") {
  return html`<input class="input ${extraClass}" name="${name}" placeholder="${placeholder}" value="${value}"
    autocomplete="off" spellcheck="false" aria-label="${placeholder}">`;
}

function busy() {
  return html`<div class="row muted"><span class="spinner"></span>${t("tools.working")}</div>`;
}

function errorLine(error) {
  return html`<div class="callout callout-bad">${icon("alertCircle")}<div>${t(`errors.${error.code}`, {}, t("errors.generic"))}</div></div>`;
}

// ---- speed test ------------------------------------------------------------

function renderSpeed(root) {
  const job = state.speed;
  const progress = job?.progress || {};
  const running = job?.state === "running";
  const result = job?.state === "done" ? job.result : state.lastSpeed;
  const phase = running ? progress.phase : job?.state === "error" ? "error" : result ? "done" : "idle";
  const live = running && ["download", "upload"].includes(progress.phase) ? progress.current_bps : null;
  const shown = live ?? (running ? 0 : result?.download_bps ?? 0);
  gauge?.setValue(shown);
  const { value, unit } = splitRate(shown / 8, "bits");
  $("#speed-readout", root).replaceChildren();
  render($("#speed-readout", root), html`<div class="gauge-value num">${value}</div><div class="gauge-unit">${unit}</div>`);

  const download = running ? progress.download_bps : result?.download_bps;
  const upload = running ? progress.upload_bps : result?.upload_bps;
  const ping = running ? progress.ping_ms : result?.ping_ms;
  const jitter = running ? progress.jitter_ms : result?.jitter_ms;
  const server = (running ? progress.server : result?.server) || {};
  render($("#speed-details", root), html`
    <p class="muted" id="speed-phase">${t(`tools.phase.${phase}`)}
      ${!running && result?.ts ? html` · ${formatDateTime(result.ts)}` : ""}</p>
    <div class="speed-results">
      <div class="speed-result"><div class="speed-result-label"><span class="key-dot key-down"></span>${t("common.download")}</div>
        <div class="speed-result-value">${download ? formatBitsPerSecond(download) : "—"}</div></div>
      <div class="speed-result"><div class="speed-result-label"><span class="key-dot key-up"></span>${t("common.upload")}</div>
        <div class="speed-result-value">${upload ? formatBitsPerSecond(upload) : "—"}</div></div>
      <div class="speed-result"><div class="speed-result-label">${icon("clock")}${t("tools.ping")}</div>
        <div class="speed-result-value">${ping ? formatMs(ping) : "—"}</div></div>
      <div class="speed-result"><div class="speed-result-label">${icon("pulse")}${t("tools.jitter")}</div>
        <div class="speed-result-value">${jitter != null ? formatMs(jitter) : "—"}</div></div>
    </div>
    ${server.colo || server.isp ? html`<p class="muted">${t("tools.server")}: ${[server.city || server.colo, server.isp].filter(Boolean).join(" · ")}</p>` : ""}
    <div class="row">
      ${running
        ? html`<button class="btn" type="button" id="speed-cancel">${icon("stop")}${t("tools.cancel")}</button>`
        : html`<button class="btn btn-primary" type="button" id="speed-start">${icon("play")}${t(result ? "tools.speed_again" : "tools.speed_start")}</button>`}
      <span class="muted">${t("tools.speed_note")}</span>
    </div>`);
}

async function startSpeedtest(root) {
  try {
    state.speed = await api.post("/api/tools/speedtest");
    renderSpeed(root);
    const final = await followJob(state.speed, (job) => {
      state.speed = job;
      renderSpeed(root);
    }, 300);
    if (final.state === "done" && final.result?.ok) state.lastSpeed = final.result;
    renderSpeed(root);
  } catch (error) {
    toastError(error);
  }
}

// ---- traceroute ------------------------------------------------------------

function renderTrace(root) {
  const job = state.trace;
  const target = $("#result-traceroute", root);
  if (!job) {
    target.replaceChildren();
    return;
  }
  if (job.state === "error") {
    render(target, errorLine({ code: job.error === "LookupError" ? "unresolved" : job.error === "TracerouteUnavailable" ? "traceroute_unavailable" : "failed" }));
    return;
  }
  render(target, html`
    ${job.state === "running" ? busy() : html`<p class="muted">${t("tools.trace_done", { hops: job.items.length })}</p>`}
    ${job.items.length ? html`<div class="table-wrap"><table class="table">
      <thead><tr><th>#</th><th>${t("tools.address")}</th><th>${t("tools.hostname")}</th><th class="right">${t("tools.latency")}</th></tr></thead>
      <tbody>${job.items.map((hop) => html`<tr>
        <td class="num">${hop.ttl}</td>
        <td class="mono">${hop.ip || html`<span class="muted">* * *</span>`}</td>
        <td class="truncate cell-limit-sm" title="${hop.host || ""}">${hop.host || html`<span class="muted">—</span>`}</td>
        <td class="right nowrap">${formatHopTimes(hop.rtts)}</td>
      </tr>`)}</tbody></table></div>` : ""}`);
}

function formatHopTimes(rtts) {
  if (rtts.every((rtt) => rtt == null)) return "*";
  return `${rtts.map((rtt) => (rtt == null ? "*" : formatNumber(rtt, rtt < 10 ? 1 : 0))).join(" / ")} ms`;
}

async function startTrace(root, host) {
  state.trace = { state: "running", items: [] };
  renderTrace(root);
  try {
    state.trace = await api.post("/api/tools/traceroute", { host });
    await followJob(state.trace, (job) => {
      state.trace = job;
      renderTrace(root);
    }, 600);
  } catch (error) {
    state.trace = null;
    render($("#result-traceroute", root), errorLine(error));
  }
}

// ---- quick tools -----------------------------------------------------------

function renderPing(result) {
  const peak = Math.max(1, ...result.replies.filter((r) => r != null));
  return html`
    <div class="ping-bars" aria-hidden="true">${result.replies.map((rtt) => (rtt == null
      ? html`<span class="ping-bar is-lost" data-h="100"></span>`
      : html`<span class="ping-bar" data-h="${Math.max(6, (rtt / peak) * 100)}"></span>`))}</div>
    <dl class="kv">
      <dt>${t("tools.address")}</dt><dd class="mono">${result.ip}</dd>
      <dt>${t("tools.min_avg_max")}</dt><dd>${formatMs(result.min)} / ${formatMs(result.avg)} / ${formatMs(result.max)}</dd>
      <dt>${t("overview.loss")}</dt><dd>${formatPercent(result.loss)}</dd>
    </dl>`;
}

function renderDns(result) {
  if (!result.ok) return errorLine({ code: "unresolved" });
  return html`<dl class="kv">
    <dt>IPv4</dt><dd class="mono">${result.ipv4.join(", ") || "—"}</dd>
    <dt>IPv6</dt><dd class="mono">${result.ipv6.join(", ") || "—"}</dd>
    <dt>${t("tools.duration")}</dt><dd>${formatMs(result.ms)}</dd>
  </dl>`;
}

function renderPort(result) {
  const tone = { open: "chip-good", closed: "chip-bad", filtered: "chip-warn" }[result.state] || "chip-bad";
  return html`<dl class="kv">
    <dt>${t("tools.state")}</dt><dd><span class="chip ${tone}">${t(`tools.port_${result.state}`)}</span></dd>
    ${result.ms != null ? html`<dt>${t("tools.duration")}</dt><dd>${formatMs(result.ms)}</dd>` : ""}
    ${result.error ? html`<dt>${t("errors.title")}</dt><dd>${result.error}</dd>` : ""}
  </dl>`;
}

function renderIp(result) {
  if (!result.ok) return errorLine({ code: "ipinfo_failed" });
  return html`<dl class="kv">
    <dt>${t("tools.public_ip")}</dt><dd class="mono">${result.ip}</dd>
    ${result.org ? html`<dt>${t("tools.isp")}</dt><dd>${result.org}</dd>` : ""}
    ${result.city ? html`<dt>${t("tools.location")}</dt><dd>${[result.city, result.region, result.country].filter(Boolean).join(", ")}</dd>` : ""}
    ${result.hostname ? html`<dt>${t("tools.hostname")}</dt><dd class="mono">${result.hostname}</dd>` : ""}
  </dl>`;
}

function renderDnsCheck(result) {
  const verdict = result.verdict;
  const tone = { ok: "chip-good", tampered: "chip-bad", blocked: "chip-bad", doh_failed: "chip-warn" }[verdict] || "chip";
  return html`<div class="security-verdict">
      <span class="chip ${tone}">${t(`tools.dns_verdict.${verdict}`)}</span>
    </div>
    <p class="muted">${t(`tools.dns_verdict_detail.${verdict}`)}</p>
    <dl class="kv">
      <dt>${t("tools.dns_system")}</dt><dd class="mono">${result.system.join(", ") || "—"}</dd>
      <dt>${t("tools.dns_secure")}</dt><dd class="mono">${result.doh.join(", ") || "—"}</dd>
    </dl>`;
}

const QUICK_TOOLS = {
  ping: { endpoint: "/api/tools/ping", body: (f) => ({ host: f.host.value, count: 5 }), view: renderPing },
  dns: { endpoint: "/api/tools/dns", body: (f) => ({ name: f.name.value }), view: renderDns },
  dnscheck: { endpoint: "/api/tools/dnscheck", body: (f) => ({ name: f.name.value }), view: renderDnsCheck },
  port: { endpoint: "/api/tools/port", body: (f) => ({ host: f.host.value, port: f.port.value }), view: renderPort },
  ipinfo: { endpoint: "/api/tools/ipinfo", body: () => ({}), view: renderIp },
};

async function runQuickTool(root, id, form) {
  const tool = QUICK_TOOLS[id];
  const target = $(`#result-${id}`, root);
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  render(target, busy());
  try {
    const result = await api.post(tool.endpoint, tool.body(form), { timeout: 30000 });
    render(target, tool.view(result));
  } catch (error) {
    render(target, errorLine(error));
  } finally {
    button.disabled = false;
  }
}

// ---- adapters --------------------------------------------------------------

function renderAdapters(root, ctx) {
  const target = $("#tools-adapters", root);
  if (!state.adapters.length) {
    render(target, emptyState("network", t("tools.no_adapters")));
    return;
  }
  const unit = ctx.unit();
  render(target, html`${state.adapters.map((adapter) => html`
    <div class="adapter">
      <div>
        <div class="adapter-name">${adapter.name}
          <span class="chip ${adapter.up ? "chip-good" : ""}">${t(adapter.up ? "tools.up" : "tools.down")}</span>
          ${adapter.virtual ? html`<span class="chip">${t("tools.virtual")}</span>` : ""}
        </div>
      </div>
      <label class="switch" title="${t("tools.count_usage_hint")}">
        <span class="muted">${t("tools.count_usage")}</span>
        <input type="checkbox" data-adapter="${adapter.name}" ${adapter.counted ? "checked" : ""}>
        <span class="switch-track"></span>
      </label>
      <div class="adapter-meta">
        ${adapter.ipv4.length ? html`<span><b>IPv4</b><span class="mono">${adapter.ipv4.join(", ")}</span></span>` : ""}
        ${adapter.ipv6.length ? html`<span><b>IPv6</b><span class="mono">${adapter.ipv6[0]}</span></span>` : ""}
        ${adapter.mac ? html`<span><b>MAC</b><span class="mono">${adapter.mac}</span></span>` : ""}
        ${adapter.speed_mbps ? html`<span><b>${t("tools.link_speed")}</b>${adapter.speed_mbps} Mbit/s</span>` : ""}
        ${adapter.up ? html`<span class="rate-down">${icon("down")}${formatRate(adapter.rx_rate, unit)}</span>
          <span class="rate-up">${icon("up")}${formatRate(adapter.tx_rate, unit)}</span>` : ""}
      </div>
    </div>`)}`);
}

async function loadAdapters(root, ctx) {
  if (root.querySelector("#tools-adapters input:focus")) return;
  state.adapters = (await api.get("/api/adapters")).adapters;
  renderAdapters(root, ctx);
}

async function toggleAdapter(root, ctx, name, counted) {
  const excluded = state.adapters
    .filter((adapter) => (adapter.name === name ? !counted : !adapter.counted))
    .map((adapter) => adapter.name);
  try {
    await ctx.saveSettings({ excluded_adapters: excluded });
    await loadAdapters(root, ctx);
  } catch (error) {
    toastError(error);
  }
}

export default {
  id: "tools",
  icon: "tools",

  mount(root, ctx) {
    disposed = false;
    render(root, html`
      <section class="grid-2 grid-start">
        <article class="card">
          <div class="card-header"><div>
            <h2 class="card-title">${t("tools.speedtest")}</h2>
            <p class="card-subtitle">${t("tools.speedtest_sub")}</p>
          </div><span class="muted">${icon("gauge")}</span></div>
          <div class="gauge"><div class="gauge-canvas" id="speed-gauge"></div><div class="gauge-readout" id="speed-readout"></div></div>
          <div id="speed-details"></div>
        </article>
        <div class="stack">
          ${formCard("ping", "pulse", t("tools.ping_title"), t("tools.ping_sub"), textField("host", t("tools.host_placeholder"), "1.1.1.1"), t("tools.run"))}
          ${formCard("dnscheck", "shieldCheck", t("tools.dnscheck_title"), t("tools.dnscheck_sub"), textField("name", t("tools.domain_placeholder"), "google.com"), t("tools.check"))}
          ${formCard("dns", "dns", t("tools.dns_title"), t("tools.dns_sub"), textField("name", t("tools.domain_placeholder"), "google.com"), t("tools.lookup"))}
        </div>
      </section>
      <section class="grid-2 grid-start">
        ${formCard("traceroute", "route", t("tools.trace_title"), t("tools.trace_sub"), textField("host", t("tools.host_placeholder"), "google.com"), t("tools.run"))}
        <div class="stack">
          ${formCard("port", "plug", t("tools.port_title"), t("tools.port_sub"),
            html`${textField("host", t("tools.host_placeholder"), "google.com")}${textField("port", t("tools.port"), "443", "input-port")}`, t("tools.check"))}
          ${formCard("ipinfo", "globe", t("tools.ip_title"), t("tools.ip_sub"), "", t("tools.show_ip"))}
        </div>
      </section>
      <section class="card">
        <div class="card-header">
          <div><h2 class="card-title">${t("tools.adapters")}</h2><p class="card-subtitle">${t("tools.adapters_sub")}</p></div>
          <button class="btn btn-sm btn-ghost" type="button" id="adapters-auto">${icon("refresh")}${t("tools.adapters_auto")}</button>
        </div>
        <div id="tools-adapters"></div>
      </section>`);

    gauge = new Gauge($("#speed-gauge", root));
    renderSpeed(root);

    root.addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.target.closest("form[data-tool]");
      if (!form) return;
      const id = form.dataset.tool;
      if (id === "traceroute") startTrace(root, form.host.value);
      else runQuickTool(root, id, form);
    });
    root.addEventListener("click", (event) => {
      if (event.target.closest("#speed-start")) startSpeedtest(root);
      if (event.target.closest("#speed-cancel") && state.speed) {
        api.post(`/api/jobs/${encodeURIComponent(state.speed.id)}/cancel`).catch(toastError);
      }
    });
    root.addEventListener("change", (event) => {
      const input = event.target.closest("input[data-adapter]");
      if (input) toggleAdapter(root, ctx, input.dataset.adapter, input.checked);
    });
    $("#adapters-auto", root).addEventListener("click", async () => {
      await ctx.saveSettings({ excluded_adapters: null }).catch(toastError);
      loadAdapters(root, ctx);
    });

    api.get("/api/history?days=7").then((data) => {
      const tests = data.speedtests || [];
      state.lastSpeed = state.lastSpeed || tests[tests.length - 1] || null;
      if (!state.speed || state.speed.state !== "running") renderSpeed(root);
    }).catch(() => {});
    if (state.speed?.state === "running") {
      followJob(state.speed, (job) => { state.speed = job; renderSpeed(root); }, 300);
    }
    renderTrace(root);
    ctx.every(2000, () => loadAdapters(root, ctx));
  },

  unmount() {
    disposed = true;
    gauge?.destroy();
    gauge = null;
  },
};

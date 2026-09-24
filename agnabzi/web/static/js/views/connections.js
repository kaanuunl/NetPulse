import { api } from "../api.js";
import { $, debounce, html, icon, render } from "../dom.js";
import { formatNumber } from "../format.js";
import { i18n } from "../i18n.js";
import { appAvatar, bindSegmented, bindSort, compare, emptyState, segmented, sortHeader } from "../ui.js";

const t = (...args) => i18n.t(...args);
const ROW_LIMIT = 400;

const state = {
  rows: [],
  query: "",
  mode: "internet",
  sort: { key: "app", dir: "asc" },
};

function endpoint(ip, port) {
  if (!ip) return "—";
  return ip.includes(":") ? `[${ip}]:${port}` : `${ip}:${port}`;
}

function filtered() {
  const query = state.query.trim().toLocaleLowerCase(i18n.locale);
  let rows = state.rows;
  if (state.mode === "internet") rows = rows.filter((r) => !r.listening && r.scope === "public");
  else if (state.mode === "listening") rows = rows.filter((r) => r.listening);
  else rows = rows.filter((r) => !r.listening);
  if (query) {
    rows = rows.filter((r) => [r.app, r.name, r.remote_ip, r.host, r.service, String(r.remote_port), String(r.local_port)]
      .some((field) => field && field.toLocaleLowerCase(i18n.locale).includes(query)));
  }
  const key = state.sort.key;
  const value = (row) => {
    switch (key) {
      case "remote": return row.host || row.remote_ip;
      case "port": return state.mode === "listening" ? row.local_port : row.remote_port;
      case "status": return row.status || "";
      case "proto": return row.proto;
      default: return (row.app || row.name || "").toLowerCase();
    }
  };
  return [...rows].sort((a, b) => compare(value(a), value(b), state.sort.dir));
}

function statusChip(status) {
  if (!status) return html`<span class="muted">—</span>`;
  const tone = status === "ESTABLISHED" ? "chip-good" : ["SYN_SENT", "SYN_RECV"].includes(status) ? "chip-warn" : "";
  return html`<span class="chip ${tone}">${t(`status.${status}`, {}, status)}</span>`;
}

function exposureChip(row) {
  if (row.listen_scope === "unspecified") {
    return html`<span class="chip chip-warn" title="${t("connections.exposed_hint")}">${icon("globe")}${t("connections.exposed")}</span>`;
  }
  if (row.listen_scope === "loopback") return html`<span class="chip chip-good">${t("connections.local_only")}</span>`;
  return html`<span class="chip">${t(`scope.${row.listen_scope}`, {}, row.listen_scope)}</span>`;
}

function appCell(row, caps) {
  if (!row.pid) return html`<span class="muted">${t("connections.system")}</span>`;
  const identity = { name: row.name, title: row.app, exe: row.exe, key: row.key };
  return html`<div class="app-cell">${appAvatar(identity, caps)}<div class="app-cell-text cell-limit-sm">
    <div class="app-cell-name"><span class="truncate" title="${row.app || row.name}">${row.app || row.name}</span></div>
    <div class="app-cell-path">PID ${row.pid}</div></div></div>`;
}

function renderTable(root, ctx) {
  const { caps } = ctx.store;
  const rows = filtered();
  const shown = rows.slice(0, ROW_LIMIT);
  $("#conn-count", root).textContent = t("connections.count", { count: formatNumber(rows.length) });
  if (!rows.length) {
    render($("#conn-table", root), emptyState("link", state.query ? t("common.no_match") : t(`connections.empty_${state.mode}`)));
    return;
  }
  const listening = state.mode === "listening";
  render($("#conn-table", root), html`
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          ${sortHeader(t("connections.app"), "app", state.sort)}
          ${sortHeader(t("connections.proto"), "proto", state.sort)}
          ${listening
            ? html`<th>${t("connections.address")}</th>${sortHeader(t("connections.port"), "port", state.sort)}
                   <th>${t("connections.exposure")}</th><th>${t("connections.service")}</th>`
            : html`<th>${t("connections.local")}</th>${sortHeader(t("connections.remote"), "remote", state.sort)}
                   <th>${t("connections.host")}</th><th>${t("connections.service")}</th>
                   ${sortHeader(t("connections.status"), "status", state.sort)}`}
        </tr></thead>
        <tbody>
          ${shown.map((row) => listening
            ? html`<tr>
                <td>${appCell(row, caps)}</td>
                <td class="nowrap">${row.proto.toUpperCase()}${row.family === 6 ? html` <span class="chip">IPv6</span>` : ""}</td>
                <td class="mono">${row.local_ip}</td>
                <td class="mono">${row.local_port}</td>
                <td>${exposureChip(row)}</td>
                <td>${row.service || html`<span class="muted">—</span>`}</td>
              </tr>`
            : html`<tr>
                <td>${appCell(row, caps)}</td>
                <td class="nowrap">${row.proto.toUpperCase()}${row.family === 6 ? html` <span class="chip">IPv6</span>` : ""}</td>
                <td class="mono nowrap">${endpoint(row.local_ip, row.local_port)}</td>
                <td class="mono nowrap">${endpoint(row.remote_ip, row.remote_port)}</td>
                <td class="truncate cell-limit" title="${row.host}">${row.host || html`<span class="muted">${row.scope === "public" ? "—" : t(`scope.${row.scope}`, {}, "—")}</span>`}</td>
                <td>${row.service || html`<span class="muted">—</span>`}</td>
                <td>${statusChip(row.status)}</td>
              </tr>`)}
        </tbody>
      </table>
    </div>
    ${rows.length > ROW_LIMIT ? html`<div class="table-footer">${t("connections.truncated", { shown: ROW_LIMIT, total: rows.length })}</div>` : ""}`);
}

async function load(root, ctx) {
  const data = await api.get("/api/connections");
  state.rows = data.connections;
  renderTable(root, ctx);
}

export default {
  id: "connections",
  icon: "link",

  mount(root, ctx) {
    render(root, html`
      <div class="toolbar">
        <label class="search">${icon("search")}
          <span class="visually-hidden">${t("common.search")}</span>
          <input class="input input-search" type="search" id="conn-search" placeholder="${t("connections.search")}" value="${state.query}">
        </label>
        ${segmented("mode", [
          ["internet", t("connections.mode_internet")],
          ["all", t("connections.mode_all")],
          ["listening", t("connections.mode_listening")],
        ], state.mode)}
        <span class="spacer"></span>
        <span class="muted" id="conn-count"></span>
      </div>
      <section class="card card-flush" id="conn-table"></section>`);

    $("#conn-search", root).addEventListener("input", debounce((event) => {
      state.query = event.target.value;
      renderTable(root, ctx);
    }, 120));
    bindSegmented(root, "mode", (value) => {
      state.mode = value;
      renderTable(root, ctx);
    });
    bindSort($("#conn-table", root), state.sort, () => renderTable(root, ctx));
    ctx.every(3000, () => load(root, ctx));
  },
};

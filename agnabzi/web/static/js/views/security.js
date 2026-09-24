import { api } from "../api.js";
import { $, html, icon, render } from "../dom.js";
import { i18n } from "../i18n.js";
import { appAvatar, confirmDialog, emptyState, toast, toastError } from "../ui.js";

const t = (...args) => i18n.t(...args);

const state = { data: null, busy: new Set() };

const SEVERITY = {
  high: { chip: "chip-bad", icon: "alertCircle", event: "bad" },
  medium: { chip: "chip-warn", icon: "alert", event: "warn" },
  low: { chip: "chip", icon: "info", event: "info" },
};

function describe(finding) {
  const data = finding.data || {};
  switch (finding.kind) {
    case "suspicious_location": {
      const where = t(`security.location.${data.category}`, {}, t("security.location.unknown"));
      const publisher = data.unknown_publisher ? ` ${t("security.unknown_publisher")}` : "";
      return { title: t("security.f.suspicious_location", { app: finding.app }), detail: `${where}${publisher}` };
    }
    case "exposed_service": {
      const names = data.services.map((s) => `${s.name} (${s.port})`).join(", ");
      return { title: t("security.f.exposed_service", { app: finding.app }), detail: t("security.f.exposed_detail", { services: names }) };
    }
    case "host_fanout":
      return { title: t("security.f.host_fanout", { app: finding.app }), detail: t("security.f.host_fanout_detail", { hosts: data.hosts }) };
    case "unusual_port":
      return { title: t("security.f.unusual_port", { app: finding.app }), detail: t("security.f.unusual_port_detail", { ports: data.ports.join(", ") }) };
    default:
      return { title: finding.kind, detail: "" };
  }
}

function postureCard(posture, caps) {
  const dpi = posture.dpi || {};
  const dpiRow = dpi.supported
    ? (dpi.active
      ? { level: "good", icon: "shieldCheck", label: t("security.dpi_active", { tool: dpi.tools.join(", ") }) }
      : { level: "muted", icon: "shield", label: t("security.dpi_inactive") })
    : { level: "muted", icon: "shield", label: t("security.dpi_unsupported") };

  const rows = [
    caps.per_app
      ? { level: "good", icon: "shieldCheck", label: t("security.posture.full") }
      : { level: "warn", icon: "shield", label: t(`privilege.reason.${posture.per_app_reason || "not_admin"}`) },
    posture.firewall
      ? { level: "good", icon: "ban", label: t("security.posture.firewall_on", { count: posture.blocked_count }) }
      : { level: "muted", icon: "ban", label: t("security.posture.firewall_off") },
    posture.autostart.enabled
      ? { level: "good", icon: "power", label: t("security.posture.autostart_on") }
      : { level: "muted", icon: "power", label: t("security.posture.autostart_off") },
    dpiRow,
    { level: "good", icon: "database", label: t("security.posture.local_data") },
  ];

  return html`<article class="card">
    <div class="card-header"><div>
      <h2 class="card-title">${t("security.posture.title")}</h2>
      <p class="card-subtitle">${t("security.posture.sub")}</p>
    </div></div>
    <div class="posture">
      ${rows.map((row) => html`<div class="posture-row posture-${row.level}">${icon(row.icon)}<span>${row.label}</span></div>`)}
    </div>
  </article>`;
}

function findingRow(finding, caps) {
  const meta = SEVERITY[finding.severity] || SEVERITY.low;
  const info = describe(finding);
  const busy = state.busy.has(finding.key);
  const canBlock = caps.firewall && finding.exe && !finding.blocked;
  return html`<div class="event event-${meta.event}">
    <span class="event-icon">${icon(meta.icon)}</span>
    <div class="event-text">
      <div class="app-cell-name">${info.title}
        <span class="chip ${meta.chip}">${t(`security.severity.${finding.severity}`)}</span>
        ${finding.blocked ? html`<span class="chip chip-bad">${icon("ban")}${t("apps.blocked")}</span>` : ""}
      </div>
      <div class="event-detail">${info.detail}</div>
      ${finding.exe ? html`<div class="app-cell-path mono truncate" title="${finding.exe}">${finding.exe}</div>` : ""}
    </div>
    <div class="row">
      <a class="btn btn-sm btn-ghost" href="#apps">${t("security.investigate")}</a>
      ${canBlock ? html`<button class="btn btn-sm btn-danger" type="button" data-block="${finding.key}" ${busy ? "disabled" : ""}>${icon("ban")}${t("apps.block")}</button>` : ""}
    </div>
  </div>`;
}

function render_(root) {
  const data = state.data;
  if (!data) return;
  const { threats, posture, caps } = data;
  const summary = threats.summary;
  const level = summary.level === "low" ? "warn" : summary.level;
  const banner = {
    good: { cls: "callout-good", icon: "shieldCheck", title: t("security.clear_title"), detail: t("security.clear_detail") },
    warn: { cls: "callout-warn", icon: "alert", title: t("security.review_title", { count: summary.total }), detail: t("security.review_detail") },
    bad: { cls: "callout-bad", icon: "alertCircle", title: t("security.risk_title", { count: summary.counts.high }), detail: t("security.review_detail") },
  }[level];

  render(root, html`
    <div class="callout ${banner.cls}">${icon(banner.icon)}
      <div><strong>${banner.title}</strong>${banner.detail}</div>
    </div>
    ${postureCard(posture, caps)}
    <article class="card">
      <div class="card-header"><div>
        <h2 class="card-title">${t("security.threats_title")}</h2>
        <p class="card-subtitle">${t("security.scanned", { count: threats.scanned })}</p>
      </div></div>
      ${threats.findings.length
        ? html`<div class="events">${threats.findings.map((f) => findingRow(f, caps))}</div>`
        : emptyState("shieldCheck", t("security.no_threats"))}
      <div class="callout callout-info" role="note">${icon("info")}<div>${t("security.disclaimer")}</div></div>
    </article>`);
}

async function load(root) {
  state.data = await api.get("/api/security");
  render_(root);
}

async function block(root, key) {
  const finding = state.data?.threats.findings.find((f) => f.key === key);
  const accepted = await confirmDialog({
    title: t("apps.block_title", { app: finding?.app || key }),
    message: t("apps.block_message"),
    confirm: t("apps.block"),
    danger: true,
  });
  if (!accepted) return;
  state.busy.add(key);
  render_(root);
  try {
    await api.post("/api/apps/block", { key });
    toast({ title: t("apps.blocked_toast", { app: finding?.app || key }), level: "good", iconName: "ban" });
  } catch (error) {
    toastError(error);
  } finally {
    state.busy.delete(key);
    await load(root).catch(() => {});
  }
}

export default {
  id: "security",
  icon: "shield",

  mount(root, ctx) {
    root.addEventListener("click", (event) => {
      const target = event.target.closest("[data-block]");
      if (target) block(root, target.dataset.block);
    });
    ctx.every(4000, () => load(root));
  },
};

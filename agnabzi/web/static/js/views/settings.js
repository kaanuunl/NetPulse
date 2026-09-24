import { api } from "../api.js";
import { $, debounce, html, icon, render } from "../dom.js";
import { i18n } from "../i18n.js";
import { confirmDialog, toast, toastError } from "../ui.js";

const t = (...args) => i18n.t(...args);

function section(title, description, fields) {
  return html`<section class="settings-section">
    <div><h2>${title}</h2><p>${description}</p></div>
    <div class="fields">${fields}</div>
  </section>`;
}

function field(label, control, help = "") {
  return html`<div class="field">
    <span class="field-label">${label}</span>${control}
    ${help ? html`<span class="field-help">${help}</span>` : ""}
  </div>`;
}

function select(name, value, options) {
  return html`<select class="input" name="${name}" aria-label="${name}">
    ${options.map(([optionValue, label]) => html`<option value="${optionValue}" ${optionValue === value ? "selected" : ""}>${label}</option>`)}
  </select>`;
}

function toggle(name, checked, label) {
  return html`<label class="switch">
    <input type="checkbox" name="${name}" ${checked ? "checked" : ""} aria-label="${label}">
    <span class="switch-track"></span>
  </label>`;
}

function aboutRows(store) {
  const caps = store.caps;
  const perApp = caps.per_app ? t("settings.enabled") : t(`privilege.reason.${caps.per_app_reason || "not_admin"}`);
  return html`<dl class="kv">
    <dt>${t("settings.version")}</dt><dd>${caps.version}</dd>
    <dt>${t("settings.mode")}</dt><dd>${caps.demo ? t("privilege.demo") : caps.admin ? t("settings.admin") : t("settings.standard")}</dd>
    <dt>${t("settings.per_app")}</dt><dd>${perApp}</dd>
  </dl>`;
}

function renderForm(root, ctx) {
  const { settings, caps, autostart, dataDir } = ctx.store;
  const autostartHelp = autostart.enabled
    ? t(autostart.mode === "task" ? "settings.autostart_task" : "settings.autostart_registry")
    : caps.admin ? t("settings.autostart_admin_hint") : t("settings.autostart_user_hint");

  render(root, html`<div class="card">
    ${section(t("settings.appearance"), t("settings.appearance_sub"), html`
      ${field(t("settings.language"), select("language", settings.language, [["tr", "Türkçe"], ["en", "English"]]))}
      ${field(t("settings.theme"), select("theme", settings.theme, [
        ["auto", t("settings.theme_auto")], ["dark", t("settings.theme_dark")], ["light", t("settings.theme_light")],
      ]))}
      ${field(t("settings.speed_unit"), select("speed_unit", settings.speed_unit, [
        ["bits", "Mbit/s"], ["bytes", "MB/s"],
      ]), t("settings.speed_unit_help"))}`)}

    ${section(t("settings.quota"), t("settings.quota_sub"), html`
      ${field(t("settings.quota_gb"), html`<input class="input" type="number" min="0" step="1" name="quota_gb" value="${settings.quota_gb}">`,
        t("settings.quota_gb_help"))}
      ${field(t("settings.reset_day"), html`<input class="input" type="number" min="1" max="31" step="1" name="quota_reset_day" value="${settings.quota_reset_day}">`,
        t("settings.reset_day_help"))}`)}

    ${section(t("settings.monitoring"), t("settings.monitoring_sub"), html`
      ${field(t("settings.ping_target"), html`<input class="input" name="ping_target" value="${settings.ping_target}" spellcheck="false">`,
        t("settings.ping_target_help"))}`)}

    ${section(t("settings.notifications"), caps.platform === "windows" || caps.demo ? t("settings.notifications_sub") : t("settings.notifications_sub_web"), html`
      ${field(t("settings.notify_new_apps"), toggle("notify_new_apps", settings.notify_new_apps, t("settings.notify_new_apps")), t("settings.notify_new_apps_help"))}
      ${field(t("settings.notify_quota"), toggle("notify_quota", settings.notify_quota, t("settings.notify_quota")))}
      ${field(t("settings.notify_connectivity"), toggle("notify_connectivity", settings.notify_connectivity, t("settings.notify_connectivity")))}`)}

    ${caps.autostart ? section(t("settings.startup"), t("settings.startup_sub"), html`
      ${field(t("settings.autostart"), toggle("autostart", autostart.enabled, t("settings.autostart")), autostartHelp)}`) : ""}

    ${section(t("settings.data"), t("settings.data_sub"), html`
      ${dataDir ? html`<div class="path">${dataDir}</div>` : ""}
      <div class="row">
        <button class="btn btn-danger" type="button" id="reset-history">${icon("trash")}${t("settings.reset_history")}</button>
      </div>`)}

    ${section(t("settings.about"), t("settings.about_sub"), aboutRows(ctx.store))}
  </div>`);
}

async function save(ctx, changes) {
  try {
    await ctx.saveSettings(changes);
    toast({ title: t("settings.saved"), level: "good", iconName: "check" });
  } catch (error) {
    if (error.code === "invalid_setting") {
      toast({ title: t("settings.invalid"), message: t(`settings.field.${error.details.field}`, {}, error.details.field), level: "bad", iconName: "alertCircle" });
    } else {
      toastError(error);
    }
  }
}

async function setAutostart(root, ctx, enabled) {
  try {
    const result = await api.post("/api/system/autostart", { enabled });
    ctx.store.autostart = result.autostart;
    toast({ title: t(enabled ? "settings.autostart_on" : "settings.autostart_off"), level: "good", iconName: "check" });
  } catch (error) {
    toastError(error);
  }
  renderForm(root, ctx);
}

export default {
  id: "settings",
  icon: "settings",

  mount(root, ctx) {
    renderForm(root, ctx);
    const saveText = debounce((name, value) => save(ctx, { [name]: value }), 600);

    root.addEventListener("change", (event) => {
      const input = event.target;
      if (!input.name) return;
      if (input.name === "autostart") {
        setAutostart(root, ctx, input.checked);
      } else if (input.type === "checkbox") {
        save(ctx, { [input.name]: input.checked });
      } else if (input.tagName === "SELECT") {
        save(ctx, { [input.name]: input.value }).then(() => renderForm(root, ctx));
      } else if (input.type === "number") {
        const value = input.value === "" ? 0 : Number(input.value);
        save(ctx, { [input.name]: value });
      } else {
        saveText(input.name, input.value.trim());
      }
    });

    root.addEventListener("click", async (event) => {
      if (!event.target.closest("#reset-history")) return;
      const accepted = await confirmDialog({
        title: t("settings.reset_title"),
        message: t("settings.reset_message"),
        confirm: t("settings.reset_history"),
        danger: true,
      });
      if (!accepted) return;
      try {
        await api.post("/api/history/reset");
        toast({ title: t("settings.reset_done"), level: "good", iconName: "check" });
      } catch (error) {
        toastError(error);
      }
    });
    $("input[name=quota_gb]", root)?.setAttribute("inputmode", "decimal");
  },
};

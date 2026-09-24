import { api } from "./api.js";
import { $, html, icon, render } from "./dom.js";
import { hashIndex } from "./format.js";
import { i18n } from "./i18n.js";

const TOAST_LIFETIME = 7000;

export function showTooltip(clientX, clientY, content) {
  const tooltip = $("#tooltip");
  render(tooltip, content);
  tooltip.hidden = false;
  const { width, height } = tooltip.getBoundingClientRect();
  let left = clientX + 16;
  let top = clientY + 14;
  if (left + width > window.innerWidth - 8) left = clientX - width - 16;
  if (top + height > window.innerHeight - 8) top = clientY - height - 14;
  tooltip.style.left = `${Math.max(8, left)}px`;
  tooltip.style.top = `${Math.max(8, top)}px`;
}

export function hideTooltip() {
  const tooltip = $("#tooltip");
  if (tooltip) tooltip.hidden = true;
}

export function toast({ title, message = "", level = "info", iconName = "info" }) {
  const container = $("#toasts");
  const element = document.createElement("div");
  element.className = `toast toast-${level}`;
  element.setAttribute("role", level === "bad" ? "alert" : "status");
  render(element, html`
    <span class="event-icon">${icon(iconName)}</span>
    <div><strong>${title}</strong>${message ? html`<span class="secondary">${message}</span>` : ""}</div>`);
  container.append(element);
  setTimeout(() => element.remove(), TOAST_LIFETIME);
  while (container.children.length > 4) container.firstElementChild.remove();
}

export function toastError(error) {
  const code = error?.code || "internal";
  toast({ title: i18n.t("errors.title"), message: i18n.t(`errors.${code}`, {}, i18n.t("errors.generic")), level: "bad", iconName: "alertCircle" });
}

export function confirmDialog({ title, message, confirm, danger = false }) {
  const dialog = $("#dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-message").textContent = message;
  const confirmButton = $("#dialog-confirm");
  confirmButton.textContent = confirm;
  confirmButton.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
  $("#dialog-cancel").textContent = i18n.t("common.cancel");
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

export function appAvatar(app, caps) {
  const name = app.title || app.name || "?";
  const initial = name.trim().charAt(0).toLocaleUpperCase(i18n.locale) || "?";
  const avatar = html`<span class="avatar avatar-${hashIndex(name, 8)}" aria-hidden="true">${initial}</span>`;
  if (caps?.icons && app.exe) {
    return html`<img class="app-icon" src="${api.iconUrl(app.key || app.exe.toLowerCase())}" alt="" loading="lazy"
      data-initial="${initial}" data-avatar="${hashIndex(name, 8)}">`;
  }
  return avatar;
}

document.addEventListener("error", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLImageElement) || !target.classList.contains("app-icon")) return;
  const fallback = document.createElement("span");
  fallback.className = `avatar avatar-${target.dataset.avatar || 0}`;
  fallback.setAttribute("aria-hidden", "true");
  fallback.textContent = target.dataset.initial || "?";
  target.replaceWith(fallback);
}, true);

export function statusBadge(level, label) {
  const icons = { good: "checkCircle", warn: "alert", bad: "xCircle", unknown: "clock" };
  return html`<span class="status status-${level}">${icon(icons[level] || "info")}${label}</span>`;
}

export function segmented(name, options, active) {
  return html`<div class="segmented" role="group" data-segmented="${name}">
    ${options.map(([value, label]) => html`<button type="button" data-value="${value}" aria-pressed="${String(value === active)}">${label}</button>`)}
  </div>`;
}

export function bindSegmented(root, name, onChange) {
  const group = root.querySelector(`[data-segmented="${name}"]`);
  if (!group) return;
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    onChange(button.dataset.value);
  });
}

export function emptyState(iconName, text) {
  return html`<div class="empty">${icon(iconName)}${text}</div>`;
}

export function sortHeader(label, key, sort, align = "") {
  const active = sort.key === key;
  const ariaSort = active ? (sort.dir === "asc" ? "ascending" : "descending") : null;
  return html`<th class="${align}" ${ariaSort ? html`aria-sort="${ariaSort}"` : ""}>
    <button type="button" data-sort="${key}">${label}${active ? icon(sort.dir === "asc" ? "sortUp" : "sortDown") : ""}</button>
  </th>`;
}

export function bindSort(root, sort, onChange) {
  root.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-sort]");
    if (!button) return;
    const key = button.dataset.sort;
    if (sort.key === key) sort.dir = sort.dir === "asc" ? "desc" : "asc";
    else Object.assign(sort, { key, dir: key === "name" ? "asc" : "desc" });
    onChange();
  });
}

export function compare(a, b, dir) {
  const result = typeof a === "string" ? a.localeCompare(b, i18n.locale, { sensitivity: "base" }) : a - b;
  return dir === "asc" ? result : -result;
}

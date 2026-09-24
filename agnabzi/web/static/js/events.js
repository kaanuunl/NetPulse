import { formatBytes, formatDuration } from "./format.js";
import { i18n } from "./i18n.js";

const t = (...args) => i18n.t(...args);

/** Map a backend event to the icon, title and detail line shown in the UI. */
export function eventView(event) {
  const data = event.data || {};
  switch (event.kind) {
    case "new_app":
      return { icon: "sparkles", title: t("events.new_app", { app: data.app }), detail: data.host || data.ip || "" };
    case "baseline":
      return { icon: "shieldCheck", title: t("events.baseline", { count: data.count }), detail: t("events.baseline_detail") };
    case "quota":
      return {
        icon: "database",
        title: t("events.quota", { level: data.threshold }),
        detail: `${formatBytes(data.used)} / ${formatBytes(data.quota)}`,
      };
    case "offline":
      return { icon: "wifiOff", title: t("events.offline"), detail: t(`diagnosis.${data.code}.detail`) };
    case "online":
      return { icon: "wifi", title: t("events.online"), detail: t("events.online_detail", { duration: formatDuration(data.seconds || 0) }) };
    case "quality":
      return { icon: "pulse", title: t(`diagnosis.${data.code}.title`), detail: t(`diagnosis.${data.code}.detail`) };
    case "blocked":
      return { icon: "ban", title: t("events.blocked", { app: data.app }), detail: data.exe || "" };
    case "unblocked":
      return { icon: "unlock", title: t("events.unblocked", { app: data.app }), detail: data.exe || "" };
    default:
      return { icon: "info", title: event.kind, detail: "" };
  }
}

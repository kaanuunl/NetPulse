import { i18n } from "./i18n.js";

const numberFormats = new Map();

function numberFormat(digits) {
  const key = `${i18n.locale}:${digits}`;
  if (!numberFormats.has(key)) {
    numberFormats.set(key, new Intl.NumberFormat(i18n.locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }));
  }
  return numberFormats.get(key);
}

export function formatNumber(value, digits = 0) {
  return numberFormat(digits).format(value);
}

function digitsFor(value) {
  const abs = Math.abs(value);
  if (abs >= 100 || abs === 0) return 0;
  if (abs >= 10) return 1;
  return 2;
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function splitBytes(bytes) {
  let value = Number(bytes) || 0;
  let index = 0;
  while (Math.abs(value) >= 1024 && index < BYTE_UNITS.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = index === 0 ? 0 : digitsFor(value);
  return { value: formatNumber(value, digits), unit: BYTE_UNITS[index] };
}

export function formatBytes(bytes) {
  const { value, unit } = splitBytes(bytes);
  return `${value} ${unit}`;
}

export function splitRate(bytesPerSecond, unitMode = "bits") {
  const bps = Math.max(0, Number(bytesPerSecond) || 0);
  if (unitMode === "bytes") {
    const { value, unit } = splitBytes(bps);
    return { value, unit: `${unit}/s` };
  }
  const bits = bps * 8;
  const scales = [["Gbit/s", 1e9], ["Mbit/s", 1e6], ["kbit/s", 1e3]];
  for (const [unit, scale] of scales) {
    if (bits >= scale) {
      const scaled = bits / scale;
      return { value: formatNumber(scaled, digitsFor(scaled)), unit };
    }
  }
  return { value: formatNumber(bits, 0), unit: "bit/s" };
}

export function formatRate(bytesPerSecond, unitMode) {
  const { value, unit } = splitRate(bytesPerSecond, unitMode);
  return `${value} ${unit}`;
}

export function formatBitsPerSecond(bitsPerSecond) {
  return formatRate((bitsPerSecond || 0) / 8, "bits");
}

export function formatMs(ms) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1) return "<1 ms";
  return `${formatNumber(ms, ms < 10 ? 1 : 0)} ms`;
}

export function formatPercent(ratio, digits = 0) {
  const value = formatNumber((ratio || 0) * 100, digits);
  return i18n.language === "tr" ? `%${value}` : `${value}%`;
}

export function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString(i18n.locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString(i18n.locale, { hour: "2-digit", minute: "2-digit" });
}

export function parseDay(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function formatDay(iso, options = { day: "numeric", month: "short" }) {
  return parseDay(iso).toLocaleDateString(i18n.locale, options);
}

export function formatMonth(label) {
  const [year, month] = label.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString(i18n.locale, { month: "long", year: "numeric" });
}

export function formatDateTime(ts) {
  return new Date(ts * 1000).toLocaleString(i18n.locale, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function formatRelative(ts) {
  const seconds = Math.round(ts - Date.now() / 1000);
  const rtf = new Intl.RelativeTimeFormat(i18n.locale, { numeric: "auto", style: "short" });
  const abs = Math.abs(seconds);
  if (abs < 45) return i18n.t("time.now");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const u = i18n.t("units.duration");
  if (h) return `${h} ${u.h} ${m} ${u.m}`;
  if (m) return `${m} ${u.m} ${s} ${u.s}`;
  return `${s} ${u.s}`;
}

/** Round an axis maximum up to 1, 2, 2.5 or 5 times a power of ten. */
export function niceCeil(value) {
  if (value <= 0) return 1;
  const exponent = Math.floor(Math.log10(value));
  const base = 10 ** exponent;
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * base) return step * base;
  }
  return 10 * base;
}

/** Nice axis step for decimal values (base 10) or binary byte values (base 1024). */
export function niceStep(value, base = 10) {
  if (value <= 0) return 1;
  if (base === 1024) {
    const power = Math.max(0, Math.floor(Math.log(value) / Math.log(1024)));
    const unit = 1024 ** power;
    return niceCeil(value / unit) * unit;
  }
  return niceCeil(value);
}

export function hashIndex(text, buckets) {
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  return hash % buckets;
}

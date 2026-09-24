import { html } from "./dom.js";
import { niceStep } from "./format.js";
import { hideTooltip, showTooltip } from "./ui.js";

const FONT = '11px "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif';
const TIME_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];

function cssVar(element, name) {
  return getComputedStyle(element).getPropertyValue(name).trim();
}

function withAlpha(color, alpha) {
  const hex = color.replace("#", "");
  if (hex.length !== 6) return color;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function tooltipRows(title, rows) {
  return html`
    <div class="tooltip-title">${title}</div>
    ${rows.map((row) => html`
      <div class="tooltip-row">
        <span class="key-line ${row.keyClass}"></span>
        <strong>${row.value}</strong>
        <span>${row.label}</span>
      </div>`)}`;
}

class CanvasChart {
  constructor(container, options) {
    this.container = container;
    this.options = options;
    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("aria-hidden", "true");
    container.replaceChildren(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.width = 0;
    this.height = 0;
    this.hoverIndex = null;
    // The observer fires once right away, which performs the first sizing and draw.
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.themeObserver = new MutationObserver(() => this.draw());
    this.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    this.mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    this.onScheme = () => this.draw();
    this.mediaQuery.addEventListener("change", this.onScheme);
    if (options.interactive !== false) {
      this.canvas.addEventListener("pointermove", (event) => this.onPointer(event));
      this.canvas.addEventListener("pointerleave", () => this.onLeave());
    }
  }

  destroy() {
    this.resizeObserver.disconnect();
    this.themeObserver.disconnect();
    this.mediaQuery.removeEventListener("change", this.onScheme);
    hideTooltip();
  }

  resize() {
    const rect = this.container.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    this.width = Math.max(0, rect.width);
    this.height = Math.max(0, rect.height);
    this.canvas.width = Math.round(this.width * ratio);
    this.canvas.height = Math.round(this.height * ratio);
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.draw();
  }

  palette() {
    const root = this.container;
    return {
      surface: cssVar(root, "--surface"),
      grid: cssVar(root, "--grid"),
      axis: cssVar(root, "--axis"),
      muted: cssVar(root, "--muted"),
      bad: cssVar(root, "--bad"),
      series: this.options.series.map((s) => cssVar(root, s.color)),
    };
  }

  setEmpty(empty) {
    this.container.classList.toggle("is-empty", empty);
    if (this.options.emptyText) this.container.dataset.empty = this.options.emptyText;
  }

  onLeave() {
    this.hoverIndex = null;
    hideTooltip();
    this.draw();
  }

  drawYAxis(ctx, colors, plot, maxY) {
    ctx.font = FONT;
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    const steps = 4;
    for (let i = 0; i <= steps; i += 1) {
      const value = (maxY / steps) * i;
      const y = Math.round(plot.bottom - (plot.height * i) / steps) + 0.5;
      ctx.strokeStyle = i === 0 ? colors.axis : colors.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.fillStyle = colors.muted;
      ctx.fillText(this.options.yFormat(value, maxY), plot.left - 8, y);
    }
  }
}

/**
 * Time-series line chart. Each series is an array of [timestamp, value] pairs;
 * a null value is a gap (and, with lossMarkers, a marked packet loss).
 */
export class LineChart extends CanvasChart {
  constructor(container, options) {
    super(container, { area: true, compact: false, minMax: 1, niceBase: () => 10, ...options });
    this.data = options.series.map(() => []);
    this.range = null;
  }

  setData(data, range = null) {
    this.data = data;
    this.range = range;
    this.draw();
  }

  geometry() {
    const compact = this.options.compact;
    const pad = compact ? { left: 0, right: 0, top: 6, bottom: 0 } : { left: 72, right: 14, top: 12, bottom: 28 };
    const plot = { left: pad.left, right: this.width - pad.right, top: pad.top, bottom: this.height - pad.bottom };
    plot.width = Math.max(1, plot.right - plot.left);
    plot.height = Math.max(1, plot.bottom - plot.top);
    const all = this.data.flat();
    const start = this.range?.start ?? Math.min(...all.map((p) => p[0]));
    const end = this.range?.end ?? Math.max(...all.map((p) => p[0]));
    let max = this.options.minMax;
    for (const [t, value] of all) {
      if (t >= start && value !== null && value > max) max = value;
    }
    const maxY = compact ? max * 1.15 : niceStep((max * 1.08) / 4, this.options.niceBase()) * 4;
    const x = (t) => plot.left + ((t - start) / Math.max(end - start, 1e-6)) * plot.width;
    const y = (v) => plot.bottom - (v / maxY) * plot.height;
    return { plot, start, end, maxY, x, y };
  }

  draw() {
    const { ctx } = this;
    if (!this.width || !this.height) return;
    ctx.clearRect(0, 0, this.width, this.height);
    const visible = this.data.some((series) => series.length > 1);
    this.setEmpty(!visible && !this.options.compact);
    this.lastGeometry = null;
    if (!visible) return;
    const colors = this.palette();
    const geo = this.geometry();
    const { plot, x, y } = geo;

    if (!this.options.compact) {
      this.drawYAxis(ctx, colors, plot, geo.maxY);
      this.drawXAxis(ctx, colors, geo);
    }

    this.data.forEach((series, index) => {
      const color = colors.series[index];
      const segments = [];
      let current = [];
      for (const [t, value] of series) {
        if (t < geo.start) continue;
        if (value === null) {
          if (current.length) segments.push(current);
          current = [];
        } else {
          current.push([x(t), y(value)]);
        }
      }
      if (current.length) segments.push(current);
      for (const segment of segments) {
        if (this.options.area && segment.length > 1) {
          ctx.beginPath();
          ctx.moveTo(segment[0][0], plot.bottom);
          segment.forEach(([px, py]) => ctx.lineTo(px, py));
          ctx.lineTo(segment[segment.length - 1][0], plot.bottom);
          ctx.closePath();
          ctx.fillStyle = withAlpha(color, this.options.compact ? 0.12 : 0.1);
          ctx.fill();
        }
        ctx.beginPath();
        segment.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
        ctx.strokeStyle = color;
        ctx.lineWidth = this.options.compact ? 1.5 : 2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.stroke();
      }
      if (this.options.lossMarkers) {
        ctx.fillStyle = colors.bad;
        for (const [t, value] of series) {
          if (value === null && t >= geo.start) ctx.fillRect(Math.round(x(t)) - 1.5, plot.bottom - 8, 3, 8);
        }
      }
    });

    if (this.hover) {
      const px = Math.round(x(this.hover.t)) + 0.5;
      ctx.strokeStyle = colors.axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, plot.top);
      ctx.lineTo(px, plot.bottom);
      ctx.stroke();
      this.hover.values.forEach((point, index) => {
        if (!point || point[1] === null) return;
        ctx.beginPath();
        ctx.arc(x(point[0]), y(point[1]), 4, 0, Math.PI * 2);
        ctx.fillStyle = colors.series[index];
        ctx.strokeStyle = colors.surface;
        ctx.lineWidth = 2;
        ctx.fill();
        ctx.stroke();
      });
    }
    this.lastGeometry = geo;
  }

  drawXAxis(ctx, colors, geo) {
    const { plot, start, end, x } = geo;
    ctx.fillStyle = colors.muted;
    ctx.font = FONT;
    ctx.textBaseline = "top";
    ctx.textAlign = "center";
    const maxTicks = Math.max(2, Math.floor(plot.width / 90));
    const step = TIME_STEPS.find((candidate) => (end - start) / candidate <= maxTicks) || TIME_STEPS[TIME_STEPS.length - 1];
    for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
      const px = x(t);
      if (px < plot.left + 20 || px > plot.right - 20) continue;
      ctx.fillText(this.options.xFormat(t, step), px, plot.bottom + 9);
    }
  }

  static nearest(series, t) {
    let best = null;
    let distance = Infinity;
    for (const point of series) {
      const d = Math.abs(point[0] - t);
      if (d < distance) {
        best = point;
        distance = d;
      }
    }
    return best;
  }

  onLeave() {
    this.hover = null;
    super.onLeave();
  }

  onPointer(event) {
    const geo = this.lastGeometry;
    if (!geo) return;
    const rect = this.canvas.getBoundingClientRect();
    const ratio = (event.clientX - rect.left - geo.plot.left) / geo.plot.width;
    const pointerT = geo.start + Math.max(0, Math.min(1, ratio)) * (geo.end - geo.start);
    const anchor = LineChart.nearest(this.data[0].filter((p) => p[0] >= geo.start), pointerT);
    if (!anchor) return;
    const tolerance = (geo.end - geo.start) / 40 + 1;
    const values = this.data.map((series) => {
      const point = LineChart.nearest(series, anchor[0]);
      return point && Math.abs(point[0] - anchor[0]) <= tolerance ? point : null;
    });
    this.hover = { t: anchor[0], values };
    this.draw();
    const rows = this.options.series.map((series, index) => ({
      keyClass: series.keyClass,
      label: series.label(),
      value: values[index] ? this.options.valueFormat(values[index][1]) : "—",
    }));
    showTooltip(event.clientX, event.clientY, tooltipRows(this.options.titleFormat(anchor[0]), rows));
  }
}

function roundedTopRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height);
  ctx.beginPath();
  ctx.moveTo(x, y + height);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.lineTo(x + width - r, y);
  ctx.arcTo(x + width, y, x + width, y + r, r);
  ctx.lineTo(x + width, y + height);
  ctx.closePath();
}

/** Stacked column chart for daily usage. */
export class BarChart extends CanvasChart {
  constructor(container, options) {
    super(container, options);
    this.rows = [];
  }

  setData(rows) {
    this.rows = rows;
    this.draw();
  }

  geometry() {
    const plot = { left: 64, right: this.width - 10, top: 12, bottom: this.height - 28 };
    plot.width = Math.max(1, plot.right - plot.left);
    plot.height = Math.max(1, plot.bottom - plot.top);
    const maxTotal = Math.max(1, ...this.rows.map((row) => row.values.reduce((a, b) => a + b, 0)));
    const maxY = niceStep((maxTotal * 1.05) / 4, this.options.niceBase ?? 10) * 4;
    const band = plot.width / Math.max(this.rows.length, 1);
    const barWidth = Math.max(3, Math.min(24, band * 0.62));
    return { plot, maxY, band, barWidth };
  }

  draw() {
    const { ctx } = this;
    if (!this.width || !this.height) return;
    ctx.clearRect(0, 0, this.width, this.height);
    const empty = !this.rows.some((row) => row.values.some((v) => v > 0));
    this.setEmpty(empty);
    if (!this.rows.length) return;
    const colors = this.palette();
    const geo = this.geometry();
    const { plot, maxY, band, barWidth } = geo;
    this.drawYAxis(ctx, colors, plot, maxY);

    const labelEvery = Math.max(1, Math.ceil(this.rows.length / Math.max(1, Math.floor(plot.width / 58))));
    ctx.font = FONT;
    ctx.textBaseline = "top";
    ctx.textAlign = "center";

    this.rows.forEach((row, index) => {
      const cx = plot.left + band * index + band / 2;
      const left = cx - barWidth / 2;
      const dim = this.hoverIndex !== null && this.hoverIndex !== index;
      ctx.globalAlpha = dim ? 0.45 : 1;
      let top = plot.bottom;
      const segments = row.values.map((value, i) => ({ value, i })).filter((s) => s.value > 0);
      segments.forEach((segment, position) => {
        const height = (segment.value / maxY) * plot.height;
        const isTop = position === segments.length - 1;
        const gap = position > 0 ? 2 : 0;
        const drawHeight = Math.max(height - gap, 1);
        ctx.fillStyle = colors.series[segment.i];
        if (isTop) {
          roundedTopRect(ctx, left, top - gap - drawHeight, barWidth, drawHeight, 4);
          ctx.fill();
        } else {
          ctx.fillRect(left, top - gap - drawHeight, barWidth, drawHeight);
        }
        top -= height;
      });
      ctx.globalAlpha = 1;
      const last = this.rows.length - 1;
      if (index === last || (index % labelEvery === 0 && last - index >= labelEvery)) {
        ctx.fillStyle = colors.muted;
        ctx.fillText(row.label, cx, plot.bottom + 9);
      }
    });
    this.lastGeometry = geo;
  }

  onPointer(event) {
    if (!this.lastGeometry || !this.rows.length) return;
    const rect = this.canvas.getBoundingClientRect();
    const { plot, band } = this.lastGeometry;
    const index = Math.floor((event.clientX - rect.left - plot.left) / band);
    if (index < 0 || index >= this.rows.length) {
      this.onLeave();
      return;
    }
    if (index !== this.hoverIndex) {
      this.hoverIndex = index;
      this.draw();
    }
    const row = this.rows[index];
    const rows = this.options.series.map((series, i) => ({
      keyClass: series.keyClass,
      label: series.label(),
      value: this.options.valueFormat(row.values[i]),
    }));
    rows.push({ keyClass: "", label: this.options.totalLabel(), value: this.options.valueFormat(row.values.reduce((a, b) => a + b, 0)) });
    showTooltip(event.clientX, event.clientY, tooltipRows(row.title, rows));
  }
}

/** Semicircular speed gauge on a square-root scale up to 1 Gbit/s. */
export class Gauge extends CanvasChart {
  constructor(container) {
    super(container, { series: [{ color: "--accent" }], interactive: false });
    this.value = 0;
    this.maxBits = 1e9;
  }

  setValue(bitsPerSecond) {
    this.value = Math.max(0, bitsPerSecond || 0);
    this.draw();
  }

  draw() {
    const { ctx } = this;
    if (!this.width || !this.height) return;
    ctx.clearRect(0, 0, this.width, this.height);
    const colors = this.palette();
    const cx = this.width / 2;
    const radius = Math.min(this.width / 2 - 18, this.height - 26);
    const cy = radius + 14;
    const startAngle = Math.PI * 0.85;
    const endAngle = Math.PI * 2.15;
    const ratio = Math.sqrt(Math.min(this.value, this.maxBits) / this.maxBits);

    ctx.lineCap = "round";
    ctx.lineWidth = 12;
    ctx.strokeStyle = colors.grid;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, startAngle, endAngle);
    ctx.stroke();

    if (ratio > 0) {
      ctx.strokeStyle = colors.series[0];
      ctx.beginPath();
      ctx.arc(cx, cy, radius, startAngle, startAngle + (endAngle - startAngle) * ratio);
      ctx.stroke();
    }

    ctx.fillStyle = colors.muted;
    ctx.font = FONT;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    for (const mbps of [0, 10, 50, 100, 250, 500, 1000]) {
      const angle = startAngle + (endAngle - startAngle) * Math.sqrt((mbps * 1e6) / this.maxBits);
      const lx = cx + Math.cos(angle) * (radius - 24);
      const ly = cy + Math.sin(angle) * (radius - 24);
      ctx.fillText(String(mbps), lx, ly);
    }
  }
}

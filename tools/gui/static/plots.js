const AXIS = '#69737f';
const GRID = 'rgba(23, 27, 33, 0.085)';
const FRAME = '#c3cad2';
const TEXT = '#171b21';

function drawFrame(ctx, x0, y0, x1, y1) {
  ctx.strokeStyle = FRAME;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(Math.round(x0) + 0.5, y0);
  ctx.lineTo(Math.round(x0) + 0.5, Math.round(y1) + 0.5);
  ctx.lineTo(x1, Math.round(y1) + 0.5);
  ctx.stroke();
}

function niceTicks(lo, hi, count) {
  if (!(hi > lo)) hi = lo + 1;
  const raw = (hi - lo) / Math.max(count, 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return { ticks: out, step };
}

function tickLabel(value, step) {
  const decimals = Math.max(0, Math.min(4, -Math.floor(Math.log10(step))));
  return value.toFixed(decimals);
}

class Canvas2D {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.w = 0;
    this.h = 0;
    this.resize();
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(canvas);
  }

  destroy() {
    this.observer.disconnect();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.w = Math.max(1, rect.width);
    this.h = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  clear() {
    this.ctx.clearRect(0, 0, this.w, this.h);
  }
}

class StripChart extends Canvas2D {
  constructor(canvas, opts) {
    super(canvas);
    this.window = opts.window || 30;
    this.pointer = null;
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'plot-tooltip';
    this.tooltip.hidden = true;
    this.tooltip.setAttribute('role', 'tooltip');
    canvas.parentElement.appendChild(this.tooltip);
    this.onPointerMove = (event) => {
      this.pointer = { x: event.clientX, y: event.clientY };
    };
    this.onPointerLeave = () => this.clearHover();
    canvas.addEventListener('pointermove', this.onPointerMove);
    canvas.addEventListener('pointerleave', this.onPointerLeave);
    this.setTraces(opts.traces);
  }

  clearHover() {
    this.pointer = null;
    this.tooltip.hidden = true;
  }

  destroy() {
    this.canvas.removeEventListener('pointermove', this.onPointerMove);
    this.canvas.removeEventListener('pointerleave', this.onPointerLeave);
    this.tooltip.remove();
    super.destroy();
  }

  setTraces(traces) {
    this.clearHover();
    this.traces = traces;
    this.t = [];
    this.values = new Map(traces.map((tr) => [tr.key, []]));
  }

  push(times, series) {
    if (!this.traces.length) return;
    for (let n = 0; n < times.length; n++) {
      this.t.push(times[n]);
      for (const tr of this.traces) {
        let value = null;
        const column = series[tr.key];
        if (column && column[n] != null && Number.isFinite(column[n])) {
          value = column[n] / tr.scale;
        }
        this.values.get(tr.key).push(value);
      }
    }
    this.prune(times.at(-1));
  }

  prune(now) {
    let drop = 0;
    while (drop < this.t.length && this.t[drop] < now - this.window) drop++;
    if (!drop) return;
    this.t.splice(0, drop);
    for (const values of this.values.values()) values.splice(0, drop);
  }

  clearData() {
    this.clearHover();
    this.t = [];
    for (const values of this.values.values()) values.length = 0;
  }

  draw(now) {
    this.prune(now);
    this.clear();
    this.tooltip.hidden = true;
    const { ctx } = this;
    const units = [...new Set(this.traces.map((tr) => tr.unit))];
    if (!units.length) return;
    const x0 = 12 + units.length * 50;
    const x1 = this.w - 10;
    const y0 = 24;
    const y1 = this.h - 22;
    if (x1 <= x0 || y1 <= y0) return;
    const tMax = Math.max(now, this.window);
    const tMin = tMax - this.window;
    const px = (t) => x0 + (t - tMin) / this.window * (x1 - x0);
    const scales = new Map();
    ctx.font = '10px ui-monospace, Consolas, monospace';
    ctx.lineWidth = 1;
    for (const [index, unit] of units.entries()) {
      let lo = Infinity;
      let hi = -Infinity;
      for (const tr of this.traces.filter((trace) => trace.unit === unit)) {
        const values = this.values.get(tr.key);
        for (let n = 0; n < values.length; n++) {
          const value = values[n];
          if (value == null || this.t[n] < tMin) continue;
          lo = Math.min(lo, value);
          hi = Math.max(hi, value);
        }
      }
      if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
      if (hi - lo < 1e-9) hi = lo + 1;
      const margin = (hi - lo) * 0.12;
      lo = Math.min(0, lo - margin);
      hi += margin;
      const py = (value) => y1 - (value - lo) / (hi - lo) * (y1 - y0);
      scales.set(unit, py);
      const axisX = x0 - index * 50;
      ctx.fillStyle = AXIS;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'top';
      ctx.fillText(unit, axisX - 6, 3);
      ctx.textBaseline = 'middle';
      const ticks = niceTicks(lo, hi, Math.max(2, Math.floor((y1 - y0) / 34)));
      for (const value of ticks.ticks) {
        const y = py(value);
        ctx.fillText(tickLabel(value, ticks.step), axisX - 6, y);
        if (index === 0) {
          ctx.strokeStyle = GRID;
          ctx.beginPath();
          ctx.moveTo(x0, y);
          ctx.lineTo(x1, y);
          ctx.stroke();
        }
      }
    }
    const ticks = niceTicks(tMin, tMax, Math.max(2, Math.floor((x1 - x0) / 90)));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const t of ticks.ticks) {
      const x = px(t);
      ctx.strokeStyle = GRID;
      ctx.beginPath();
      ctx.moveTo(x, y0);
      ctx.lineTo(x, y1);
      ctx.stroke();
      ctx.fillText(tickLabel(t, ticks.step) + 's', x, y1 + 4);
    }
    drawFrame(ctx, x0, y0, x1, y1);
    ctx.save();
    ctx.beginPath();
    ctx.rect(x0, y0, x1 - x0, y1 - y0);
    ctx.clip();
    for (const tr of this.traces) {
      const values = this.values.get(tr.key);
      const py = scales.get(tr.unit);
      ctx.strokeStyle = tr.colour;
      ctx.lineWidth = 1.4;
      ctx.setLineDash(tr.dash || []);
      ctx.beginPath();
      let open = false;
      let lastY = 0;
      for (let n = 0; n < values.length; n++) {
        const value = values[n];
        if (value == null || this.t[n] < tMin) { open = false; continue; }
        const x = px(this.t[n]);
        const y = py(value);
        if (!open) { ctx.moveTo(x, y); open = true; }
        else if (tr.step) { ctx.lineTo(x, lastY); ctx.lineTo(x, y); }
        else ctx.lineTo(x, y);
        lastY = y;
      }
      ctx.stroke();
    }
    ctx.restore();
    this.drawHover({ x0, x1, y0, y1, tMin, px, scales });
  }

  drawHover({ x0, x1, y0, y1, tMin, px, scales }) {
    if (!this.pointer || !this.t.length) return;
    const rect = this.canvas.getBoundingClientRect();
    const pointerX = this.pointer.x - rect.left;
    const pointerY = this.pointer.y - rect.top;
    if (pointerX < x0 || pointerX > x1 || pointerY < y0 || pointerY > y1) return;
    const time = tMin + (pointerX - x0) / (x1 - x0) * this.window;
    if (time < this.t[0] || time > this.t[this.t.length - 1]) return;
    let lo = 0;
    let hi = this.t.length - 1;
    while (lo < hi) {
      const middle = Math.floor((lo + hi) / 2);
      if (this.t[middle] < time) lo = middle + 1;
      else hi = middle;
    }
    let index = lo;
    if (index > 0 && time - this.t[index - 1] <= this.t[index] - time) index--;
    const x = px(this.t[index]);
    const { ctx } = this;
    ctx.save();
    ctx.strokeStyle = AXIS;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y1);
    ctx.stroke();
    ctx.setLineDash([]);
    const title = document.createElement('strong');
    title.textContent = this.t[index].toFixed(3) + ' s';
    const values = document.createElement('div');
    values.className = 'plot-tooltip-values';
    values.style.columnCount = Math.max(1, Math.ceil(this.traces.length * 20 / Math.max(60, this.h - 50)));
    for (const trace of this.traces) {
      const value = this.values.get(trace.key)[index];
      const row = document.createElement('div');
      row.className = 'plot-tooltip-row';
      const label = document.createElement('span');
      label.style.color = trace.colour;
      label.textContent = trace.label;
      const reading = document.createElement('b');
      reading.textContent = '--';
      if (value != null && Number.isFinite(value)) {
        reading.textContent = value.toFixed(trace.digits ?? 3) + ' ' + trace.unit;
        const y = scales.get(trace.unit)(value);
        ctx.fillStyle = trace.colour;
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
      }
      row.append(label, reading);
      values.appendChild(row);
    }
    ctx.restore();
    this.tooltip.replaceChildren(title, values);
    this.tooltip.style.maxWidth = Math.max(0, this.w - 8) + 'px';
    this.tooltip.hidden = false;
    const width = this.tooltip.offsetWidth;
    const height = this.tooltip.offsetHeight;
    let left = x + 12;
    if (left + width > this.w - 4) left = x - width - 12;
    this.tooltip.style.left = Math.max(4, Math.min(left, this.w - width - 4)) + 'px';
    this.tooltip.style.top = Math.max(4, Math.min(pointerY + 12, this.h - height - 4)) + 'px';
  }
}

class PhasePlot extends Canvas2D {

  constructor(canvas, opts) {
    super(canvas);
    this.xLabel = opts.xLabel;
    this.yLabel = opts.yLabel;
    this.channels = opts.channels;
    this.visibleChannels = new Set(opts.channels.keys());
    this.persist = opts.persist || 60;
    this.pointSize = 3;
    this.showTrail = true;
    this.showBest = true;
    this.showIso = true;
    this.fade = 'linear';
    this.lockAxes = false;
    this.xMaxLock = 1;
    this.yMaxLock = 1;
    this.points = [];
    this.frozenPoints = null;
    this.pad = { l: 52, r: 12, t: 12, b: 30 };
  }

  push(t, x, y, p, channel) {
    this.points.push({ t, x, y, p, channel });
  }

  clearData() {
    this.points.length = 0;
    if (this.frozenPoints) this.frozenPoints = [];
  }

  prune(now) {
    const cutoff = now - this.persist;
    this.points = this.points.filter((p) => p.t >= cutoff);
  }

  draw(now) {
    this.prune(now);
    this.clear();
    const { ctx, pad } = this;
    const x0 = pad.l;
    const x1 = this.w - pad.r;
    const y0 = pad.t;
    const y1 = this.h - pad.b;
    if (x1 <= x0 || y1 <= y0) return;

    const live = (this.frozenPoints ?? this.points)
      .filter((p) => this.visibleChannels.has(p.channel));
    let xMax = 0.1;
    let yMax = 0.1;
    for (const p of live) {
      if (p.x > xMax) xMax = p.x;
      if (p.y > yMax) yMax = p.y;
    }
    if (this.lockAxes) { xMax = this.xMaxLock; yMax = this.yMaxLock; }
    else { xMax *= 1.15; yMax *= 1.15; }

    const px = (v) => x0 + (v / xMax) * (x1 - x0);
    const py = (v) => y1 - (v / yMax) * (y1 - y0);

    ctx.font = '10px ui-monospace, Consolas, monospace';
    const yTicks = niceTicks(0, yMax, Math.max(2, Math.floor((y1 - y0) / 40)));
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (const v of yTicks.ticks) {
      const y = py(v);
      if (y < y0 - 1 || y > y1 + 1) continue;
      ctx.strokeStyle = GRID;
      ctx.beginPath();
      ctx.moveTo(x0, Math.round(y) + 0.5);
      ctx.lineTo(x1, Math.round(y) + 0.5);
      ctx.stroke();
      ctx.fillStyle = AXIS;
      ctx.fillText(tickLabel(v, yTicks.step), x0 - 6, y);
    }
    const xTicks = niceTicks(0, xMax, Math.max(2, Math.floor((x1 - x0) / 70)));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const v of xTicks.ticks) {
      const x = px(v);
      if (x < x0 - 1 || x > x1 + 1) continue;
      ctx.strokeStyle = GRID;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, y0);
      ctx.lineTo(Math.round(x) + 0.5, y1);
      ctx.stroke();
      ctx.fillStyle = AXIS;
      ctx.fillText(tickLabel(v, xTicks.step), x, y1 + 5);
    }

    drawFrame(ctx, x0, y0, x1, y1);

    ctx.fillStyle = AXIS;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'bottom';
    ctx.fillText(this.xLabel, x1, this.h - 2);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(this.yLabel, x0 - 46, y0);

    if (!live.length) {
      ctx.fillStyle = AXIS;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(this.visibleChannels.size ? 'waiting for telemetry' : 'select channels above',
                   (x0 + x1) / 2, (y0 + y1) / 2);
      return;
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(x0, y0, x1 - x0, y1 - y0);
    ctx.clip();
    for (const [channel, base] of this.channels) {
      const channelPoints = live.filter((p) => p.channel === channel);
      if (!channelPoints.length) continue;
      for (const p of channelPoints) {
        const age = (now - p.t) / this.persist;
        let alpha = this.fade === 'flat' ? 0.5 : Math.max(0.1, 1 - age);
        if (this.fade === 'sharp') alpha = Math.max(0.07, Math.pow(1 - age, 3));
        ctx.globalAlpha = alpha;
        ctx.fillStyle = base;
        ctx.beginPath();
        ctx.arc(px(p.x), py(p.y), this.pointSize, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      if (this.showTrail && channelPoints.length > 1) {
        const tail = channelPoints.slice(-60);
        ctx.strokeStyle = base;
        ctx.globalAlpha = 0.45;
        ctx.lineWidth = 1;
        ctx.beginPath();
        tail.forEach((p, n) => (n ? ctx.lineTo(px(p.x), py(p.y)) : ctx.moveTo(px(p.x), py(p.y))));
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      const last = channelPoints[channelPoints.length - 1];
      ctx.fillStyle = base;
      ctx.strokeStyle = '#171b21';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(px(last.x), py(last.y), 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      if (this.showBest || this.showIso) {
        let best = channelPoints[0];
        for (const p of channelPoints) if (p.p > best.p) best = p;
        if (this.showIso && best.p > 0) {

          ctx.strokeStyle = base;
          ctx.globalAlpha = 0.55;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          const from = Math.max(best.p / yMax, xMax / 400);
          for (let n = 0; n <= 100; n++) {
            const x = from + (xMax - from) * (n / 100);
            const y = best.p / x;
            if (n === 0) ctx.moveTo(px(x), py(y));
            else ctx.lineTo(px(x), py(y));
          }
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.globalAlpha = 1;
        }
        if (this.showBest) {
          star(ctx, px(best.x), py(best.y), 9, base);
          ctx.fillStyle = base;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'bottom';
          ctx.fillText(`${channel.toUpperCase()}: ${best.p.toFixed(2)} W`,
                       px(best.x) + 10, py(best.y) - 4);
        }
      }
    }
    ctx.restore();
  }
}

function star(ctx, cx, cy, r, colour) {
  ctx.fillStyle = colour;
  ctx.beginPath();
  for (let n = 0; n < 10; n++) {
    const radius = n % 2 ? r * 0.45 : r;
    const angle = (Math.PI / 5) * n - Math.PI / 2;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius;
    if (n === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.fill();
}

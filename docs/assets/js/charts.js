// 의존성 없는 SVG 차트. 색은 CSS 변수(var(--s1) 등)로 받아 라이트/다크가 함께 바뀐다.
const NS = "http://www.w3.org/2000/svg";

export function svgEl(tag, attrs = {}, parent = null, style = null) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (style) for (const [k, v] of Object.entries(style)) node.style.setProperty(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function htmlEl(tag, cls, parent, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

// 잘라 쓴 화면에서도 합성 자료임이 보이도록 차트 오른쪽 위에 작게 표시한다.
function watermark(svg, width, y = 12) {
  svgEl("text", { x: width - 4, y, "text-anchor": "end", "font-size": 11 }, svg, { fill: "var(--ink-2)" }).textContent = "합성 데이터";
}

function scale(d0, d1, r0, r1) {
  const k = (r1 - r0) / ((d1 - d0) || 1);
  return (v) => r0 + (v - d0) * k;
}

export function niceTicks(lo, hi, count = 4) {
  const span = hi - lo || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count + 0.5) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// 범례: 2개 이상 계열일 때 항상 둔다. 표식 모양은 차트 표식과 같게(선/띠/기준선).
export function legend(container, items) {
  container.replaceChildren();
  for (const it of items) {
    const key = htmlEl("span", "key", container);
    const mark = htmlEl("span", it.kind || "line", key);
    if (it.kind === "band") mark.style.background = `color-mix(in srgb, ${it.color} 22%, transparent)`;
    else if (it.kind === "dash") mark.style.borderTopColor = it.color;
    else if (it.kind !== "rule") mark.style.background = it.color;
    htmlEl("span", null, key, it.label);
  }
}

function tooltipBox(container) {
  let tip = container.querySelector(":scope > .tooltip");
  if (!tip) {
    tip = htmlEl("div", "tooltip", container);
    tip.hidden = true;
  }
  return tip;
}

function placeTip(container, tip, px, py) {
  const cw = container.clientWidth;
  tip.hidden = false;
  const w = tip.offsetWidth;
  let left = px + 14;
  if (left + w > cw) left = Math.max(0, px - w - 14);
  tip.style.left = `${left}px`;
  tip.style.top = `${Math.max(0, py - 10)}px`;
}

function fillTip(tip, title, rows) {
  tip.replaceChildren();
  htmlEl("div", "t", tip, title);
  for (const r of rows) {
    const row = htmlEl("div", "r", tip);
    if (r.color) {
      const k = htmlEl("span", "k", row);
      k.style.background = r.color;
    }
    htmlEl("b", null, row, r.value);
    htmlEl("span", null, row, r.name);
  }
}

// 시간축 선 그래프: 80% 구간 띠 + 선 + 기준선 + 판단 시각선 + 십자선 툴팁.
export function timeChart(container, opts) {
  const width = Math.max(260, container.clientWidth || 600);
  const height = opts.height || 260;
  const m = { l: 40, r: 14, t: 10, b: 26 };
  container.querySelectorAll("svg").forEach((s) => s.remove());
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.aria || "" });
  container.prepend(svg);
  const [x0, x1] = opts.x.domain;
  const [y0, y1] = opts.y.domain;
  const X = scale(x0, x1, m.l, width - m.r);
  const Y = scale(y0, y1, height - m.b, m.t);

  const g = svgEl("g", {}, svg);
  if (opts.watermark !== false) watermark(svg, width);
  for (const v of opts.y.ticks) {
    svgEl("line", { x1: m.l, x2: width - m.r, y1: Y(v), y2: Y(v), "stroke-width": 1 }, g, { stroke: "var(--grid)" });
    svgEl("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11 }, g, { fill: "var(--muted)" }).textContent = opts.y.format(v);
  }
  const xt = width < 520 ? opts.x.ticks.filter((_, i) => i % 2 === 0) : opts.x.ticks;
  for (const v of xt) {
    svgEl("text", { x: X(v), y: height - 8, "text-anchor": "middle", "font-size": 11 }, g, { fill: "var(--muted)" }).textContent = opts.x.format(v);
  }
  svgEl("line", { x1: m.l, x2: width - m.r, y1: height - m.b, y2: height - m.b, "stroke-width": 1 }, g, { stroke: "var(--axis)" });

  const pathOf = (pts) => {
    let d = "";
    let pen = false;
    for (const [t, v] of pts) {
      if (v == null || !isFinite(v)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${X(t).toFixed(1)},${Y(Math.max(y0, Math.min(y1, v))).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  for (const b of opts.bands || []) {
    const ok = b.lo.map((p, i) => [p[0], p[1], b.hi[i][1]]).filter((p) => p[1] != null && p[2] != null);
    if (!ok.length) continue;
    const up = ok.map(([t, , h]) => `${X(t).toFixed(1)},${Y(Math.min(y1, h)).toFixed(1)}`);
    const dn = ok.slice().reverse().map(([t, l]) => `${X(t).toFixed(1)},${Y(Math.max(y0, l)).toFixed(1)}`);
    svgEl("path", { d: `M${up.join("L")}L${dn.join("L")}Z` }, g, { fill: b.color, opacity: b.opacity ?? 0.12 });
  }
  for (const r of opts.refLines || []) {
    svgEl("line", { x1: m.l, x2: width - m.r, y1: Y(r.y), y2: Y(r.y), "stroke-width": 1 }, g, { stroke: "var(--ink-2)" });
    if (r.label) svgEl("text", { x: width - m.r, y: Y(r.y) - 4, "text-anchor": "end", "font-size": 11 }, g, { fill: "var(--ink-2)" }).textContent = r.label;
  }
  if (opts.nowLine != null) {
    svgEl("line", { x1: X(opts.nowLine), x2: X(opts.nowLine), y1: m.t, y2: height - m.b, "stroke-width": 1 }, g, { stroke: "var(--axis)" });
    const label = opts.nowLabel ?? "판단 시각";
    if (label) svgEl("text", { x: X(opts.nowLine) + 4, y: m.t + 10, "font-size": 11 }, g, { fill: "var(--ink-2)" }).textContent = label;
  }
  for (const s of opts.lines) {
    const attrs = { d: pathOf(s.pts), fill: "none", "stroke-width": s.width || 2, "stroke-linejoin": "round", "stroke-linecap": s.dash ? "butt" : "round" };
    if (s.dash) attrs["stroke-dasharray"] = s.dash;
    svgEl("path", attrs, g, { stroke: s.color, opacity: s.opacity ?? 1 });
  }
  for (const d of opts.dots || []) {
    svgEl("circle", { cx: X(d.t), cy: Y(d.v), r: 4, "stroke-width": 2 }, g, { fill: d.color, stroke: "var(--surface)" });
  }

  // 십자선: 가장 가까운 시각으로 맞추고, 그 시각의 모든 계열 값을 보여 준다.
  const times = [...new Set(opts.lines.flatMap((s) => s.pts.map((p) => p[0])))].sort((a, b) => a - b);
  const cross = svgEl("line", { y1: m.t, y2: height - m.b, "stroke-width": 1, visibility: "hidden" }, svg, { stroke: "var(--ink-2)" });
  const tip = tooltipBox(container);
  // 키보드 초점 대상: 이름을 붙여 화면낭독기가 무엇인지 읽게 한다
  const hit = svgEl("rect", { x: m.l, y: m.t, width: width - m.l - m.r, height: height - m.t - m.b, fill: "transparent", tabindex: 0,
    role: "img", "aria-label": `${opts.aria || "차트"} (←/→ 키로 시각별 값 보기)` }, svg);
  const show = (t, px, py) => {
    cross.setAttribute("x1", X(t));
    cross.setAttribute("x2", X(t));
    cross.setAttribute("visibility", "visible");
    const rows = opts.tooltip(t);
    fillTip(tip, opts.x.tipFormat ? opts.x.tipFormat(t) : opts.x.format(t), rows);
    placeTip(container, tip, px, py);
  };
  const nearest = (px) => {
    const t = x0 + ((px - m.l) / (width - m.l - m.r)) * (x1 - x0);
    let best = times[0];
    for (const v of times) if (Math.abs(v - t) < Math.abs(best - t)) best = v;
    return best;
  };
  hit.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    const py = ((e.clientY - box.top) / box.height) * height;
    const t = nearest(px);
    show(t, (X(t) / width) * box.width, (py / height) * box.height);
  });
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  let focusIdx = times.indexOf(0) >= 0 ? times.indexOf(0) : 0;
  hit.addEventListener("focus", () => { const box = svg.getBoundingClientRect(); show(times[focusIdx], (X(times[focusIdx]) / width) * box.width, 20); });
  hit.addEventListener("blur", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  hit.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    focusIdx = Math.max(0, Math.min(times.length - 1, focusIdx + (e.key === "ArrowRight" ? 1 : -1)));
    const box = svg.getBoundingClientRect();
    show(times[focusIdx], (X(times[focusIdx]) / width) * box.width, 20);
  });
}

// 세로 막대(분포). 막대 두께 최대 24px, 위쪽만 4px 둥글게, 막대 사이 2px 이상 여백.
export function columnChart(container, opts) {
  const width = Math.max(260, container.clientWidth || 500);
  const height = opts.height || 220;
  const m = { l: 40, r: 10, t: 12, b: 30 };
  container.querySelectorAll("svg").forEach((s) => s.remove());
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.aria || "" });
  container.prepend(svg);
  const n = opts.values.length;
  const band = (width - m.l - m.r) / n;
  const bw = Math.min(24, band - 2);
  const ymax = opts.yMax || Math.max(...opts.values.map((v) => v || 0)) * 1.15 || 1;
  const Y = scale(0, ymax, height - m.b, m.t);
  watermark(svg, width, 10);
  for (const v of niceTicks(0, ymax, 4)) {
    svgEl("line", { x1: m.l, x2: width - m.r, y1: Y(v), y2: Y(v), "stroke-width": 1 }, svg, { stroke: "var(--grid)" });
    svgEl("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11 }, svg, { fill: "var(--muted)" }).textContent = opts.yFormat(v);
  }
  const tip = tooltipBox(container);
  opts.values.forEach((v, i) => {
    const cx = m.l + band * i + band / 2;
    const h = Math.max(0, Y(0) - Y(v || 0));
    const x = cx - bw / 2;
    const y = Y(0) - h;
    const r = Math.min(4, h, bw / 2);
    const d = `M${x},${Y(0)}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${Y(0)}Z`;
    if (h > 0.2) svgEl("path", { d }, svg, { fill: opts.colorAt ? opts.colorAt(i) : opts.color });
    if (i % (opts.labelEvery || 1) === 0) {
      svgEl("text", { x: cx, y: height - 12, "text-anchor": "middle", "font-size": 11 }, svg, { fill: "var(--muted)" }).textContent = opts.labels[i];
    }
    const hit = svgEl("rect", { x: cx - band / 2, y: m.t, width: band, height: Y(0) - m.t, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", (e) => {
      const box = svg.getBoundingClientRect();
      fillTip(tip, opts.tipTitle(i), [{ name: opts.tipName || "", value: opts.yFormatTip ? opts.yFormatTip(v) : opts.yFormat(v), color: opts.colorAt ? opts.colorAt(i) : opts.color }]);
      placeTip(container, tip, e.clientX - box.left, e.clientY - box.top);
    });
    hit.addEventListener("pointerleave", () => { tip.hidden = true; });
  });
  svgEl("line", { x1: m.l, x2: width - m.r, y1: Y(0), y2: Y(0), "stroke-width": 1 }, svg, { stroke: "var(--axis)" });
  if (opts.xTitle) svgEl("text", { x: width - m.r, y: height - 0, "text-anchor": "end", "font-size": 11 }, svg, { fill: "var(--muted)" }).textContent = opts.xTitle;
}

// 가로 막대 목록: 항목 이름(텍스트 색) + 막대(계열 색) + 끝 값.
export function barList(container, items, opts = {}) {
  const width = Math.max(240, container.clientWidth || 400);
  const rowH = 30;
  const labelW = opts.labelWidth || 92;
  const valueW = 56;
  const height = items.length * rowH + 4;
  container.querySelectorAll("svg").forEach((s) => s.remove());
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.aria || "" });
  container.prepend(svg);
  const max = opts.max || Math.max(...items.map((d) => d.value), 1e-9);
  const X = scale(0, max, labelW, width - valueW);
  items.forEach((d, i) => {
    const y = i * rowH + 7;
    svgEl("text", { x: labelW - 8, y: y + 12, "text-anchor": "end", "font-size": 12 }, svg, { fill: "var(--ink-2)" }).textContent = d.label;
    const w = Math.max(0, X(d.value) - labelW);
    const r = Math.min(4, w);
    if (w > 0.5) svgEl("path", { d: `M${labelW},${y}H${labelW + w - r}Q${labelW + w},${y} ${labelW + w},${y + r}V${y + 16 - r}Q${labelW + w},${y + 16} ${labelW + w - r},${y + 16}H${labelW}Z` }, svg, { fill: d.color });
    svgEl("text", { x: labelW + w + 6, y: y + 12, "font-size": 12 }, svg, { fill: "var(--ink)" }).textContent = opts.format ? opts.format(d.value) : d.value;
  });
  svgEl("line", { x1: labelW, x2: labelW, y1: 2, y2: height - 2, "stroke-width": 1 }, svg, { stroke: "var(--axis)" });
}

// 4거점 전파 네트워크. 원 넓이 = 거점 몫, 화살표 굵기 = 연결 몫(최댓값 기준).
export function networkChart(container, { nodes, shares, edges, colors, format }) {
  const W = 420, H = 300;
  container.querySelectorAll("svg").forEach((s) => s.remove());
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "거점 간 지연 전파 기여도 네트워크" });
  container.prepend(svg);
  watermark(svg, W);
  const pos = { AT: [80, 80], PN: [80, 235], L: [230, 155], A: [360, 155] };
  const defs = svgEl("defs", {}, svg);
  const marker = svgEl("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: "userSpaceOnUse", orient: "auto-start-reverse" }, defs);
  svgEl("path", { d: "M0,0L10,5L0,10Z" }, marker, { fill: "var(--ink-2)" });
  const radius = (k) => 14 + 34 * Math.sqrt(Math.max(0, shares[k]));
  const emax = Math.max(...edges.map((e) => e.e), 1e-9);
  const idx = Object.fromEntries(nodes.map((n, k) => [n.id, k]));
  const sorted = edges.slice().sort((a, b) => b.e - a.e);
  sorted.forEach((e, rank) => {
    const [x1, y1] = pos[e.from];
    const [x2, y2] = pos[e.to];
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    const ux = dx / len, uy = dy / len;
    const bend = 22; // 양방향 연결이 겹치지 않도록 진행 방향 오른쪽으로 휜다
    const mx = (x1 + x2) / 2 - uy * bend, my = (y1 + y2) / 2 + ux * bend;
    const r1 = radius(idx[e.from]) + 3, r2 = radius(idx[e.to]) + 6;
    const sx = x1 + ux * r1 - uy * 8, sy = y1 + uy * r1 + ux * 8;
    const ex = x2 - ux * r2 - uy * 8, ey = y2 - uy * r2 + ux * 8;
    const w = 1 + 7 * (e.e / emax);
    svgEl("path", { d: `M${sx},${sy}Q${mx},${my} ${ex},${ey}`, fill: "none", "stroke-width": w, "marker-end": "url(#arrow)", "stroke-linecap": "round" }, svg,
      { stroke: "var(--ink-2)", opacity: 0.35 + 0.5 * (e.e / emax) });
    if (rank < 3) {
      const t = svgEl("text", { x: mx - uy * 6, y: my + ux * 6 + 4, "text-anchor": "middle", "font-size": 11 }, svg, { fill: "var(--ink)", "paint-order": "stroke", stroke: "var(--surface)", "stroke-width": "3px" });
      t.textContent = format(e.e);
    }
  });
  nodes.forEach((n, k) => {
    const [x, y] = pos[n.id];
    svgEl("circle", { cx: x, cy: y, r: radius(k), "stroke-width": 2 }, svg, { fill: colors[k], stroke: "var(--surface)", opacity: 0.9 });
    const above = n.id === "AT"; // 맨 위 거점은 이름을 원 위에 둬서 아래로 나가는 화살표와 겹치지 않게 한다
    const ly = above ? Math.max(12, y - radius(k) - 6) : y + radius(k) + 15;
    const label = svgEl("text", { x, y: ly, "text-anchor": "middle", "font-size": 12, "font-weight": 600 }, svg, { fill: "var(--ink)" });
    label.textContent = `${n.short} ${format(shares[k])}`;
  });
}

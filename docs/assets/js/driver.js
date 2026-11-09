// 기사님 화면: 포워더가 배차 요청에 넣어 운송사를 거쳐 보낸 링크(driver.html?s=장면&c=화물&a=1)로 연다. 큰 글씨 한 줄 + 도착 시각별 소요 띠.
import { driverAdvice } from "./derive.js?v=20261109a";
import { clock, fmtDurShort, hourLabel, CARGO_SHORT } from "./format.js?v=20261109a";
import { driverHeadline, cargoFacts, CARGO_META } from "./briefing.js?v=20261109a";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

async function main() {
  const q = new URLSearchParams(location.search);
  if (q.get("theme")) document.documentElement.dataset.theme = q.get("theme");
  const pre = await fetch("data/precomputed.json").then((r) => r.json());
  const s = pre.runs[q.get("s")] ? q.get("s") : "terminal_failure";
  const run = pre.runs[s];
  const key = run.cargo[q.get("c")] ? q.get("c") : run.cargo_order[0];
  const applied = q.get("a") === "1";
  const f = cargoFacts(run, key, applied);
  const h = driverHeadline(run, key, applied);
  const meta = CARGO_META[key] || {};
  $("d-what").textContent = `${meta.container || ""} · ${f.c.kind === "export" ? "수출 반입" : "수입 반출"}`;
  $("d-cargo").textContent = `${CARGO_SHORT[key] || f.c.label} — ${meta.work || f.c.label}`;
  const m = /^(\d{1,2}:\d{2})\s*(.*)$/.exec(h.big);
  $("d-big").innerHTML = m ? `${esc(m[1])}<small>${esc(m[2])}</small>` : esc(h.big);
  $("d-sub").textContent = h.sub;
  if (f.changed) {
    $("d-changed").hidden = false;
    $("d-changed").textContent = `시간이 바뀌었습니다: ${f.verb} ${clock(f.plan.time, false)} → ${clock(f.cur.time, false)}`;
  }
  if (h.warn) {
    $("d-warn").hidden = false;
    $("d-warn").textContent = `⚠ ${h.warn}`;
  }
  const adv = driverAdvice(run);
  $("d-band").innerHTML = adv.cells.map((x) => `<div class="cell" role="listitem" data-level="${x.level === "flat" ? "good" : x.level}" data-best="${x.best ? 1 : 0}" data-closed="${x.closed && f.c.kind === "export" ? 1 : 0}" data-early="${x.early ? 1 : 0}" aria-label="${esc(x.aria)}"><b>${esc(hourLabel(x.t))}</b><span>${esc(fmtDurShort(x.q50))}</span></div>`).join("");
  if (!(f.c.kind === "export" && f.c.gate_closed)) $("d-lg-closed").hidden = true;
  document.title = `R-Delay 기사님 안내 · ${CARGO_SHORT[key] || ""}`;
  document.documentElement.dataset.ready = "1";
}
main().catch((err) => {
  $("d-cargo").textContent = `화면을 불러오지 못했습니다(${err.message})`;
});

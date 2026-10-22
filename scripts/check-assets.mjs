// 화면 파일 버전 점검(CI용): node scripts/check-assets.mjs [--write]
// 서비스 워커는 ?v=가 붙은 화면 파일(assets/css·js)을 버전마다 고정된 것으로 보고 캐시에서 바로 꺼낸다.
// 그래서 파일 내용을 고치면 docs/sw.js의 V와 모든 ?v= 값을 함께 올려야 한다. 이 스크립트는
//   1) docs의 html·js에 적힌 ?v= 값이 모두 sw.js의 V와 같은지
//   2) assets/css·js의 모든 파일이 sw.js 오프라인 목록(CORE)에 있는지
//   3) 같은 V인데 파일 내용이 바뀌지 않았는지(scripts/asset-hashes.json과 비교)
// 를 확인한다. V를 올린 뒤에는 --write로 해시 목록을 새로 적는다.
import { readFileSync, writeFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, dirname, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DOCS = join(ROOT, "docs");
const MANIFEST = join(ROOT, "scripts", "asset-hashes.json");
const posix = (p) => p.split(sep).join("/"); // 윈도에서도 「assets/js/app.js」처럼 쓴다
const rel = (p) => posix(relative(ROOT, p));
const errors = [];

const sw = readFileSync(join(DOCS, "sw.js"), "utf8");
const V = (/const V = "([^"]+)"/.exec(sw) || [])[1];
if (!V) errors.push("docs/sw.js에서 V를 찾지 못했습니다");

const assets = ["css", "js"].flatMap((d) => readdirSync(join(DOCS, "assets", d)).filter((f) => /\.(css|js)$/.test(f)).map((f) => join(DOCS, "assets", d, f)));
const pages = readdirSync(DOCS).filter((f) => f.endsWith(".html")).map((f) => join(DOCS, f));

for (const file of [...pages, ...assets.filter((f) => f.endsWith(".js"))]) {
  for (const m of readFileSync(file, "utf8").matchAll(/\?v=([0-9A-Za-z._-]+)/g)) {
    if (m[1] !== V) errors.push(`${rel(file)}: ?v=${m[1]} ≠ sw.js V(${V})`);
  }
}
for (const file of assets) {
  const p = posix(relative(DOCS, file));
  if (!sw.includes(`"${p}"`)) errors.push(`${p}가 docs/sw.js 오프라인 목록(CORE)에 없습니다`);
}
// 반대로 CORE에 적힌 파일이 실제로 있는지(하나라도 없으면 서비스 워커 설치가 통째로 실패한다)
const core = (/const CORE = \[([\s\S]*?)\n\];/.exec(sw) || [])[1] || "";
if (!core) errors.push("docs/sw.js에서 CORE 목록을 찾지 못했습니다");
for (const m of core.matchAll(/"([^"]+)"/g)) {
  const p = m[1].replace(/\?.*$/, "");
  if (p === "./" || p.startsWith("http")) continue;
  if (!existsSync(join(DOCS, p))) errors.push(`docs/sw.js CORE의 ${p} 파일이 없습니다`);
}

const hashes = Object.fromEntries(assets.map((f) => [rel(f), createHash("sha256").update(readFileSync(f)).digest("hex")]));
if (process.argv.includes("--write")) {
  if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
  writeFileSync(MANIFEST, JSON.stringify({ V, files: hashes }, null, 2) + "\n");
  console.log(`scripts/asset-hashes.json을 V=${V}로 적었습니다(${assets.length}개 파일)`);
  process.exit(0);
}
if (!existsSync(MANIFEST)) {
  errors.push("scripts/asset-hashes.json이 없습니다 — node scripts/check-assets.mjs --write");
} else {
  const man = JSON.parse(readFileSync(MANIFEST, "utf8"));
  if (man.V !== V) {
    errors.push(`V가 ${man.V} → ${V}로 바뀌었습니다 — node scripts/check-assets.mjs --write로 해시 목록을 새로 적으세요`);
  } else {
    for (const [f, h] of Object.entries(hashes)) {
      if (man.files[f] !== h) errors.push(`${f} 내용이 바뀌었는데 V(${V})는 그대로입니다 — sw.js의 V와 모든 ?v= 값을 올리세요`);
    }
  }
}
if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log(`화면 파일 버전 확인: V=${V}, ${assets.length}개 파일 일치`);

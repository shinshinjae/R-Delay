// 브라우저 안 Python 엔진(Pyodide). 화면이 멈추지 않도록 웹 워커에서 실행한다.
// docs/py/rdelay/*.py를 그대로 불러오므로 GitHub의 Python 코드와 화면 계산이 같은 코드다.
const PYODIDE_VERSION = "0.27.2";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const FILES = ["__init__.py", "config.py", "stats.py", "sis.py", "volume.py", "ai.py",
  "engine.py", "queue.py", "cargo.py", "api.py"];

let ready = null;

// 메시지 처리기를 가장 먼저 등록한다. Pyodide를 못 불러와도 요청마다 실패를 돌려준다.
self.onmessage = async (event) => {
  const { id, options, model } = event.data || {};
  if (id == null) return;
  try {
    const { py } = await ready;
    py.globals.set("_opts", JSON.stringify(options));
    py.globals.set("_model", JSON.stringify(model || null));
    const t0 = performance.now();
    const text = py.runPython("json.dumps(api.run(json.loads(_opts), ai_model=json.loads(_model)), ensure_ascii=False)");
    self.postMessage({ id, ok: true, result: JSON.parse(text), ms: Math.round(performance.now() - t0) });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String((err && err.message) || err) });
  }
};

ready = (async () => {
  const t0 = performance.now();
  try {
    importScripts(PYODIDE_URL + "pyodide.js");
  } catch (err) {
    throw new Error("Pyodide를 내려받지 못했습니다(네트워크 또는 캐시 없음)");
  }
  const py = await loadPyodide({ indexURL: PYODIDE_URL });
  await py.loadPackage("numpy");
  py.FS.mkdirTree("/rdelay_pkg/rdelay");
  for (const f of FILES) {
    // 배포 직후 오래된 파일이 섞이지 않도록 서버에 다시 확인한다(오프라인이면 서비스 워커 캐시).
    const res = await fetch(new URL(`../../py/rdelay/${f}`, self.location.href), { cache: "no-cache" });
    if (!res.ok) throw new Error(`${f} 불러오기 실패 (${res.status})`);
    py.FS.writeFile(`/rdelay_pkg/rdelay/${f}`, await res.text());
  }
  py.runPython([
    "import sys, json",
    "sys.path.insert(0, '/rdelay_pkg')",
    "import numpy",
    "from rdelay import api, __version__",
  ].join("\n"));
  const versions = py.runPython("json.dumps({'engine': __version__, 'numpy': numpy.__version__, 'python': sys.version.split()[0]})");
  return { py, info: { ...JSON.parse(versions), pyodide: PYODIDE_VERSION, loadMs: Math.round(performance.now() - t0) } };
})();

ready.then(
  ({ info }) => self.postMessage({ type: "ready", info }),
  (err) => self.postMessage({ type: "failed", error: String((err && err.message) || err) }),
);

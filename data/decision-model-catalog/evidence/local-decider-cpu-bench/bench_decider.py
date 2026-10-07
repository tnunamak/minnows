import os, sys, time, json, statistics, resource
sys.path.insert(0, os.path.dirname(__file__))
import psutil
gguf, threads, n_ctx = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 4096
p = psutil.Process()
def rss(): return round(p.memory_info().rss / 2**20)
def hwm():
    for l in open("/proc/self/status"):
        if l.startswith("VmHWM"): return round(int(l.split()[1]) / 1024)
t0 = time.perf_counter()
import torch; torch.set_num_threads(1)
from decider.infer import Decider
from scenarios import mixed_request, choice20_request, single_choice12_request
r_import = rss()
d = Decider(gguf, gguf_options=dict(n_gpu_layers=0, n_threads=threads, n_ctx=n_ctx))
t_load = time.perf_counter() - t0; r_load = rss()
out = {"gguf": os.path.basename(gguf), "threads": threads, "n_ctx": n_ctx, "rss_after_imports_mb": r_import, "rss_after_load_mb": r_load, "load_s": round(t_load, 2)}
def run(req, n, **kw):
    lat = []; res = None
    for i in range(n):
        t = time.perf_counter(); res = d.system_one(req["state"], req["questions"], **kw); lat.append((time.perf_counter() - t) * 1000)
    return lat, res
out["loadavg_start"] = os.getloadavg()[0]
for name, fn, kw in [("single_choice12", single_choice12_request, {}), ("choice20", choice20_request, {}), ("mixed4_independent", mixed_request, {}), ("mixed4_packed", mixed_request, {"independent": False})]:
    req = fn()
    cold, _ = run(req, 1, **kw)
    lat, res = run(req, 5, **kw)
    out[name] = {"cold_ms": round(cold[0]), "median_ms": round(statistics.median(lat)), "min_ms": round(min(lat)), "max_ms": round(max(lat)),
                 "input_tokens": res["usage"]["input_tokens"],
                 "answers": {k: {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v.items() if kk in ("choice", "confidence", "noul", "score")} for k, v in res["answers"].items()},
                 "top3": {k: sorted(((o, round(pp, 3)) for o, pp in v.get("probabilities", {}).items()), key=lambda x: -x[1])[:3] for k, v in res["answers"].items() if v.get("type") == "choice"}}
out["rss_peak_mb"] = hwm(); out["rss_end_mb"] = rss(); out["loadavg_end"] = os.getloadavg()[0]
open(sys.argv[4], "w").write(json.dumps(out, indent=1))
print(json.dumps({k: (v if not isinstance(v, dict) else {kk: v[kk] for kk in ("cold_ms","median_ms","min_ms","max_ms","input_tokens") if kk in v}) for k, v in out.items()}))

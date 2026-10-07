"""Score decider rows through a stock llama-server (/completion, n_probs at the answer slot, prompt cache) and compare with the
in-process decider-ai GGUF readout.  usage: bench_server.py GGUF PORT OUT.json"""
import os, sys, time, json, math, statistics, subprocess, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
import psutil, numpy as np
from scenarios import mixed_request, choice20_request, single_choice12_request
gguf, port, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
PRIME = os.environ.get("PRIME") == "1"; EXTRA = os.environ.get("EXTRA", "").split()
B = os.path.expanduser("~/applications/llamacpp-official-b11337-fa-all-quants/bin/llama-server")
env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
srv = subprocess.Popen([B, "-m", gguf, "-c", "8192", "-np", "1", "-t", "8", "-ngl", "0", "--host", "127.0.0.1", "--port", str(port)] + EXTRA,
                       env=env, stdout=subprocess.DEVNULL, stderr=open(outp + ".server.log", "w"))
def post(path, body):
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=120)
    return json.loads(r.read())
try:
    for _ in range(120):
        try:
            if json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()).get("status") == "ok": break
        except Exception: pass
        time.sleep(1)
    from decider.infer import Decider
    from decider.systemone import assemble
    from decider import temperature as TT
    d = Decider(gguf, gguf_options=dict(n_gpu_layers=0, n_threads=4, n_ctx=4096))
    letters = list(np.asarray(d.eng.letters))
    def server_system_one(req, timings):
        rqs, index, items = d._system_one_items(req["state"], req["questions"], True)
        T = TT.for_items(d.T, d.T_by_type, items)
        flat = []
        if PRIME and len(items) > 1:
            rows = [[int(x) for x in it["ids"]] for it in items]; n = 0
            while all(len(r) > n and r[n] == rows[0][n] for r in rows): n += 1
            r = post("/completion", {"prompt": rows[0][:n], "n_predict": 0, "cache_prompt": True}); timings.append(("prime", r.get("timings", {}).get("prompt_n")))
        for k, it in enumerate(items):
            (slot,), n = it["slots"], it["nopts"][0]
            ids = [int(x) for x in it["ids"][:slot + 1]]
            r = post("/completion", {"prompt": ids, "n_predict": 1, "n_probs": 100, "cache_prompt": True, "temperature": 0, "post_sampling_probs": False})
            timings.append(r.get("timings", {}).get("prompt_n"))
            lp = {e["id"]: e["logprob"] for e in r["completion_probabilities"][0]["top_logprobs"]}
            t = T[k] if isinstance(T, list) else T
            t = t[0] if isinstance(t, (list, tuple)) else t
            z = np.array([lp.get(int(letters[j]), -30.0) for j in range(n)]) / float(t)
            p = np.exp(z - z.max()); p /= p.sum(); flat.append(p.tolist())
        return {"answers": assemble(rqs, index, flat)}
    out = {"gguf": os.path.basename(gguf), "loadavg_start": os.getloadavg()[0]}
    for name, fn in [("mixed4_independent", mixed_request)]:
        req = fn(); ref = d.system_one(req["state"], req["questions"])
        post("/slots/0?action=erase", {}) if False else None
        lat, pn = [], []
        for i in range(4):
            tim = []; t = time.perf_counter(); res = server_system_one(req, tim); lat.append((time.perf_counter() - t) * 1000); pn.append(tim)
        diffs = []
        for q, a in ref["answers"].items():
            b = res["answers"][q]
            for key in ("noul", "score"):
                if key in a: diffs.append(abs(a[key] - b[key]))
            if "probabilities" in a:
                diffs += [abs(a["probabilities"][o] - b["probabilities"][o]) for o in a["probabilities"]]
        out[name] = {"first_ms": round(lat[0]), "warm_median_ms": round(statistics.median(lat[1:])), "prompt_tokens_processed_first": pn[0], "prompt_tokens_processed_warm": pn[-1],
                     "max_abs_prob_diff_vs_inprocess": round(max(diffs), 4), "same_argmax": all(ref["answers"][q].get("choice") == res["answers"][q].get("choice") for q in ref["answers"])}
    out["server_rss_mb"] = round(psutil.Process(srv.pid).memory_info().rss / 2**20)
    out["loadavg_end"] = os.getloadavg()[0]
    open(outp, "w").write(json.dumps(out, indent=1)); print(json.dumps(out))
finally:
    srv.terminate(); srv.wait(10)

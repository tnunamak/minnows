import os, sys, time, json, statistics
import psutil
from decider.infer import Decider
gguf = sys.argv[1]
d = Decider(gguf, gguf_options=dict(n_gpu_layers=0, n_threads=8, n_ctx=4096))
ROUTE = {"chat": None, "image": None, "video": None, "speech": None, "transcribe": None, "music": None, "search": None, "reminder": None}
cases = [
 ("Make me a picture of a red fox in the snow.", {"type": "choice", "instructions": "Which capability does this request need?", "criteria": ROUTE}, "image"),
 ("Turn this voice memo into text please", {"type": "choice", "instructions": "Which capability does this request need?", "criteria": ROUTE}, "transcribe"),
 ("Remind me at 6pm to take the trash out", {"type": "choice", "instructions": "Which capability does this request need?", "criteria": ROUTE}, "reminder"),
 ("Write a lo-fi beat with rain sounds, about a minute long", {"type": "choice", "instructions": "Which capability does this request need?", "criteria": ROUTE}, "music"),
 ("Who won the game last night?", {"type": "choice", "instructions": "Which capability does this request need?", "criteria": ROUTE}, "search"),
 ("Read this paragraph out loud in a calm voice", {"type": "choice", "instructions": "Which capability does this request need?", "criteria": ROUTE}, "speech"),
 ("This is the third time the order arrived broken. I want my money back now.", {"type": "choice", "instructions": "What is the sentiment?", "criteria": {"positive": None, "neutral": None, "negative": None}}, "negative"),
 ("Thanks so much, the new build works perfectly!", {"type": "choice", "instructions": "What is the sentiment?", "criteria": {"positive": None, "neutral": None, "negative": None}}, "positive"),
 ("rm -rf / --no-preserve-root", {"type": "noul", "instructions": "Is this shell command destructive?"}, True),
 ("ls -la ~/Downloads", {"type": "noul", "instructions": "Is this shell command destructive?"}, False),
 ("Hey, can you check whether my flight tomorrow is still on time?", {"type": "noul", "instructions": "Does the user ask for an action that needs live, current information?"}, True),
 ("What is 2 + 2?", {"type": "noul", "instructions": "Does the user ask for an action that needs live, current information?"}, False),
]
lat, ok = [], 0
for state, q, gold in cases:
    d.system_one(state, {"q": q})  # warm
    t = time.perf_counter(); a = d.system_one(state, {"q": q})["answers"]["q"]; lat.append((time.perf_counter() - t) * 1000)
    got = a.get("choice") if q["type"] == "choice" else (a["noul"] >= 0.5)
    ok += (got == gold)
print(json.dumps({"gguf": os.path.basename(gguf), "correct": ok, "n": len(cases), "median_ms": round(statistics.median(lat)), "max_ms": round(max(lat)), "loadavg": os.getloadavg()[0], "rss_mb": round(psutil.Process().memory_info().rss / 2**20)}))

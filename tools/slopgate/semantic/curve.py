"""Is accuracy limited by data, by features, or already saturated?

If accuracy is still climbing at n=92, more labelled documents help. If it has
flattened, more data will not and the constraint is elsewhere.
"""
import random, sys
sys.path.insert(0, ".")
import fit
X, y, cols, _ = fit.load()
Xs, _, _ = fit.standardize(X)
rng = random.Random(11)

def loo_on(idx):
    Xi = [Xs[i] for i in idx]; yi = [y[i] for i in idx]
    c = 0
    for k in range(len(Xi)):
        w, b = fit.train(Xi[:k]+Xi[k+1:], yi[:k]+yi[k+1:], epochs=200)
        c += (fit.predict(w, b, Xi[k]) >= 0.5) == yi[k]
    return c/len(yi)

print("n     accuracy (mean of 3 subsamples)")
full = list(range(len(Xs)))
for frac in (0.4, 0.6, 0.8, 1.0):
    n = int(len(full)*frac)
    accs = []
    for t in range(3 if frac < 1.0 else 1):
        idx = rng.sample(full, n)
        if len(set(y[i] for i in idx)) < 2: continue
        accs.append(loo_on(idx))
    print(f"{n:3}   {sum(accs)/len(accs):.1%}")

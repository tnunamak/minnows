"""Permutation test: is +2.2% from the discovered questions real, or noise?

With 92 rows and 39 features, a question can raise leave-one-out accuracy by
chance alone. The honest check is to shuffle the labels and see how often a
random feature set does as well.
"""
import random, sys, pathlib
sys.path.insert(0, ".")
import fit
X, y, cols, _ = fit.load()

def loo(X, y):
    c = 0
    for i in range(len(X)):
        training, held = fit.standardize_fold(X[:i]+X[i+1:], X[i])
        w, b = fit.train(training, y[:i]+y[i+1:], epochs=200)
        c += (fit.predict(w, b, held) >= 0.5) == y[i]
    return c/len(y)

real = loo(X, y)
print(f"real labels: {real:.1%}")
rng = random.Random(7)
null = []
for t in range(12):
    ys = y[:]; rng.shuffle(ys)
    null.append(loo(X, ys))
null.sort()
print(f"shuffled labels: median {null[len(null)//2]:.1%}, max {null[-1]:.1%}")
print(f"real is {'WELL ABOVE' if real > null[-1] else 'within'} the shuffled range")

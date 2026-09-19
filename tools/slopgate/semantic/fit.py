#!/usr/bin/env python3
"""Does a model over Jev's probabilities beat thresholding them one at a time?

Two ways to use the same 35 numbers:

  thresholds  each rule compared against its own bar, fire if above. This is
              what slopgate ships. It discards the magnitudes and cannot learn
              that two rules together mean something neither means alone.

  learned     the 35 probabilities as a feature vector into a small classifier,
              scored by leave-one-out cross-validation because the corpus is 92
              documents and any single split would be noise.

If the learned model is clearly better, the linter framing is leaving real
signal on the table and the path forward is TypeSafe's own autoresearch loop:
propose questions, keep one only when held-out error falls.

Stdlib only. Logistic regression by gradient descent -- with 35 features and 92
rows, anything heavier would overfit and would add a dependency this repo does
not have.
"""
import json
import math
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jevgate import load_rules  # noqa: E402


def load():
    d = json.loads((HERE / "feature_matrix.json").read_text())
    cols = sorted({k for v in d.values() for k in v["features"]})
    X, y, keys = [], [], []
    for k, v in sorted(d.items()):
        X.append([float(v["features"].get(c, 0.0)) for c in cols])
        y.append(v["label"])
        keys.append(k)
    return X, y, cols, keys


def standardize(X):
    n, m = len(X), len(X[0])
    mu = [sum(r[j] for r in X) / n for j in range(m)]
    sd = []
    for j in range(m):
        v = sum((r[j] - mu[j]) ** 2 for r in X) / n
        sd.append(math.sqrt(v) or 1.0)
    return [[(r[j] - mu[j]) / sd[j] for j in range(m)] for r in X], mu, sd


def train(X, y, epochs=400, lr=0.25, l2=0.05):
    m = len(X[0])
    w = [0.0] * m
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * m
        gb = 0.0
        for xi, yi in zip(X, y):
            z = b + sum(w[j] * xi[j] for j in range(m))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - yi
            for j in range(m):
                gw[j] += e * xi[j]
            gb += e
        n = len(X)
        for j in range(m):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * gb / n
    return w, b


def predict(w, b, x):
    z = b + sum(wi * xi for wi, xi in zip(w, x))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def threshold_baseline(keys, y):
    """What slopgate ships: >=2 high-severity findings means 'machine-like'."""
    d = json.loads((HERE / "feature_matrix.json").read_text())
    rules = load_rules("doc")
    correct = 0
    for k, yi in zip(keys, y):
        feats = d[k]["features"]
        high = 0
        for name, v in rules.items():
            if v["choices"] or v["levels"]:
                continue
            if v["severity"] not in ("high", "critical"):
                continue
            if feats.get(name, 0.0) >= v["threshold"]:
                high += 1
        pred_human = 1 if high < 2 else 0
        correct += (pred_human == yi)
    return correct / len(y)


def main():
    X, y, cols, keys = load()
    Xs, _, _ = standardize(X)
    print(f"{len(X)} documents, {len(cols)} features, "
          f"{sum(y)} human / {len(y)-sum(y)} machine\n")

    base = threshold_baseline(keys, y)
    print(f"thresholded rules (what ships) : {base:.1%}")

    # Leave-one-out: the only honest split at n=92.
    correct = 0
    for i in range(len(Xs)):
        Xtr = Xs[:i] + Xs[i + 1:]
        ytr = y[:i] + y[i + 1:]
        w, b = train(Xtr, ytr)
        correct += (predict(w, b, Xs[i]) >= 0.5) == y[i]
    print(f"learned on probabilities (LOO) : {correct/len(y):.1%}")

    majority = max(sum(y), len(y) - sum(y)) / len(y)
    print(f"majority-class baseline        : {majority:.1%}")

    w, b = train(Xs, y)
    ranked = sorted(zip(cols, w), key=lambda t: -abs(t[1]))
    print("\nstrongest learned features (sign: + means human-like):")
    for c, wi in ranked[:10]:
        print(f"  {wi:+6.2f}  {c}")


if __name__ == "__main__":
    main()

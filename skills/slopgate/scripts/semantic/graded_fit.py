"""Does predicting a GRADED quality score beat predicting authorship?

The binary target is authorship, which only correlates with quality. The graded
target comes from pairwise comparisons fitted with Bradley-Terry, and rates a
polished machine draft above an empty human PR template -- which is correct, and
which the binary label gets exactly backwards.

Measured by how well each target's model ranks held-out documents against the
graded truth, since ranking is what a writer actually wants from a score.
"""
import json, math, pathlib, sys
sys.path.insert(0, ".")
import fit

g = json.loads(pathlib.Path("graded_labels.json").read_text())
m = json.loads(pathlib.Path("feature_matrix.json").read_text())
keys = [k for k in sorted(m) if k in g]
cols = sorted({c for k in keys for c in m[k]["features"]})
X = [[float(m[k]["features"].get(c, 0.0)) for c in cols] for k in keys]
y_bin = [m[k]["label"] for k in keys]
y_grade = [g[k] for k in keys]

def ridge(X, y, l2=1.0, epochs=600, lr=0.2):
    n, p = len(X), len(X[0]); w=[0.0]*p; b=sum(y)/n
    for _ in range(epochs):
        gw=[0.0]*p; gb=0.0
        for xi, yi in zip(X, y):
            e = (b + sum(w[j]*xi[j] for j in range(p))) - yi
            for j in range(p): gw[j] += e*xi[j]
            gb += e
        for j in range(p): w[j] -= lr*(gw[j]/n + l2*w[j]/n)
        b -= lr*gb/n
    return w, b

def spearman(a, b):
    if len(a) != len(b):
        raise ValueError("Rank vectors must have the same length")
    if len(a) < 2:
        return math.nan
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0]*len(v)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and v[order[end]] == v[order[start]]:
                end += 1
            average = (start + end - 1) / 2
            for i in order[start:end]:
                r[i] = average
            start = end
        return r
    ra, rb = rank(a), rank(b); n = len(a)
    ma = sum(ra)/n; mb = sum(rb)/n
    cov = sum((x-ma)*(yy-mb) for x, yy in zip(ra, rb))
    va = math.sqrt(sum((x-ma)**2 for x in ra)); vb = math.sqrt(sum((yy-mb)**2 for yy in rb))
    return cov/(va*vb) if va and vb else math.nan

# Leave-one-out predictions from each target
pred_bin, pred_grade = [], []
for i in range(len(X)):
    tr, held = fit.standardize_fold(X[:i]+X[i+1:], X[i])
    w1,b1 = fit.train(tr, y_bin[:i]+y_bin[i+1:], epochs=200)
    pred_bin.append(fit.predict(w1,b1,held))
    w2,b2 = ridge(tr, y_grade[:i]+y_grade[i+1:])
    pred_grade.append(b2 + sum(w2[j]*held[j] for j in range(len(cols))))

print(f"{len(keys)} documents with both a binary and a graded label\n")
print(f"model trained on AUTHORSHIP, ranking vs graded truth : rho={spearman(pred_bin, y_grade):+.2f}")
print(f"model trained on GRADE,      ranking vs graded truth : rho={spearman(pred_grade, y_grade):+.2f}")

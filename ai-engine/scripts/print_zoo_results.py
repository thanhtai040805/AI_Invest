import json
with open("zoo_ic_results.json") as f:
    data = json.load(f)

cats = {}
for r in data:
    cat = r["verdict"] if r["verdict"] in ("ALIVE","REVERSED","MARGINAL","DEAD","NO_DATA") else "DEAD"
    cats.setdefault(cat, []).append(r)

print("=" * 70)
print("ALIVE factors (IC>0.02, |t|>2, pos>=0.55):")
print("=" * 70)
for r in sorted(cats.get("ALIVE",[]), key=lambda x: -x["ic"]):
    print(f"  {r['alpha']:>20s}  IC={r['ic']:.4f}  IR={r['ir']:.3f}  pos={r['pos']:.2f}  t={r['t']:.2f}  p_adj={r.get('p_adj',1):.4f}")

print()
print("=" * 70)
print("REVERSED factors (IC<-0.02, |t|>2, pos<0.45):")
print("=" * 70)
for r in sorted(cats.get("REVERSED",[]), key=lambda x: x["ic"]):
    print(f"  {r['alpha']:>20s}  IC={r['ic']:.4f}  IR={r['ir']:.3f}  pos={r['pos']:.2f}  t={r['t']:.2f}  p_adj={r.get('p_adj',1):.4f}")

print()
print("=" * 70)
print("BY ZOO:")
print("=" * 70)
for prefix, label in [("gtja191_","GTJA191"), ("alpha101_","Alpha101")]:
    alives = [r for r in data if r["alpha"].startswith(prefix) and r["verdict"] == "ALIVE"]
    rev = [r for r in data if r["alpha"].startswith(prefix) and r["verdict"] == "REVERSED"]
    dead = [r for r in data if r["alpha"].startswith(prefix) and r["verdict"] in ("DEAD","NO_DATA")]
    marg = [r for r in data if r["alpha"].startswith(prefix) and r["verdict"] == "MARGINAL"]
    print(f"  {label}: ALIVE={len(alives)} REVERSED={len(rev)} MARGINAL={len(marg)} DEAD={len(dead)}")

print()
print("=" * 70)
print("TOP 10 BY IR (absolute):")
print("=" * 70)
sorted_by_ir = sorted(data, key=lambda x: abs(x["ir"]), reverse=True)[:10]
for r in sorted_by_ir:
    print(f"  {r['alpha']:>20s}  IC={r['ic']:.4f}  IR={r['ir']:.3f}  pos={r['pos']:.2f}  t={r['t']:.2f}  {r['verdict']}")

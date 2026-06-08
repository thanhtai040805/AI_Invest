#!/usr/bin/env python3
"""Save zoo IC results in structured format + print summary."""
import json
from collections import defaultdict

with open("zoo_ic_results.json") as f:
    data = json.load(f)

# Organize by verdict
cats = {"ALIVE": [], "REVERSED": [], "MARGINAL": [], "DEAD": [], "NO_DATA": []}
for r in data:
    cat = r["verdict"] if r["verdict"] in cats else "DEAD"
    cats[cat].append(r)

# By zoo
by_zoo = defaultdict(lambda: {"alive": [], "reversed": [], "marginal": [], "dead": [], "nodata": []})
for r in data:
    zoo = "gtja191" if r["alpha"].startswith("gtja") else "alpha101"
    cat = r["verdict"] if r["verdict"] in ("ALIVE","REVERSED","MARGINAL","NO_DATA") else "DEAD"
    by_zoo[zoo]["dead" if cat == "DEAD" else "nodata" if cat == "NO_DATA" else cat.lower()].append(r)

# Save structured
output = {
    "summary": {
        "total": len(data),
        "gtja191": len([r for r in data if r["alpha"].startswith("gtja")]),
        "alpha101": len([r for r in data if r["alpha"].startswith("alpha101")]),
    },
    "verdicts": {cat: len(v) for cat, v in cats.items()},
    "by_zoo": {},
    "alive": sorted(cats["ALIVE"], key=lambda x: -x["ic"]),
    "reversed": sorted(cats["REVERSED"], key=lambda x: x["ic"]),
    "marginal": sorted(cats["MARGINAL"], key=lambda x: -abs(x["ic"])),
    "dead_ids": [r["alpha"] for r in cats["DEAD"]],
    "nodata_ids": [r["alpha"] for r in cats["NO_DATA"]],
}

for zoo in ("gtja191", "alpha101"):
    z = by_zoo[zoo]
    output["by_zoo"][zoo] = {
        "alive": len(z["alive"]),
        "reversed": len(z["reversed"]),
        "marginal": len(z["marginal"]),
        "dead": len(z["dead"]) + len(z["nodata"]),
        "alive_ids": [r["alpha"] for r in z["alive"]],
        "reversed_ids": [r["alpha"] for r in z["reversed"]],
        "marginal_ids": [r["alpha"] for r in z["marginal"]],
    }

with open("zoo_ic_results_structured.json", "w") as f:
    json.dump(output, f, indent=2)

print("Saved zoo_ic_results_structured.json")
print()
print("=== ZOO IC RESULTS ===")
print(f"Total: {len(data)} factors")
print(f"  GTJA191: {output['summary']['gtja191']}")
print(f"  Alpha101: {output['summary']['alpha101']}")
print()
print("Verdicts:")
for cat, cnt in output["verdicts"].items():
    print(f"  {cat}: {cnt}")
print()
print("By zoo:")
for zoo, z in output["by_zoo"].items():
    print(f"  {zoo}: ALIVE={z['alive']} REVERSED={z['reversed']} MARGINAL={z['marginal']} DEAD={z['dead']}")
print()
print("Top 10 ALIVE by IC:")
for r in output["alive"][:10]:
    print(f"  {r['alpha']:>20s}  IC={r['ic']:.4f}  IR={r['ir']:.3f}  pos={r['pos']:.2f}  t={r['t']:.2f}  p_adj={r.get('p_adj',1):.4f}")
print()
print("Top 10 REVERSED by IC (most negative):")
for r in output["reversed"][:10]:
    print(f"  {r['alpha']:>20s}  IC={r['ic']:.4f}  IR={r['ir']:.3f}  pos={r['pos']:.2f}  t={r['t']:.2f}  p_adj={r.get('p_adj',1):.4f}")

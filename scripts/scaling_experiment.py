"""Measure migration time as a function of checkpoint size.

State size is controlled by injecting a payload of a chosen byte size
into each workload before migration. The migration engine simulates a
10 MB/s edge-cloud link and 200 MB/s serialization/deserialization, so
the resulting curve reflects the expected behavior for AI workloads
whose state is dominated by KV caches or sliding-window buffers.
"""
import csv
import statistics
from datetime import datetime
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
H = {"X-API-Key": "demo-key", "Content-Type": "application/json"}

# payload sizes in bytes
SIZES = [0, 10_000, 100_000, 500_000, 1_000_000, 5_000_000]
TRIALS = 5


def make_workload(payload_bytes):
    wid = requests.post(f"{BASE}/workloads", headers=H,
                        json={"name": "scale", "owner": "alice",
                              "model_variant": "small",
                              "site": "edge"}).json()["id"]
    requests.post(f"{BASE}/workloads/{wid}/infer", headers=H,
                  json={"prompt": "warmup"}).raise_for_status()
    requests.post(f"{BASE}/workloads/{wid}/payload", headers=H,
                  json={"size_bytes": payload_bytes}).raise_for_status()
    return wid


def main():
    rows = []
    for psize in SIZES:
        for t in range(1, TRIALS + 1):
            wid = make_workload(psize)
            blob = requests.post(f"{BASE}/workloads/{wid}/checkpoint",
                                 headers=H).json()
            res = requests.post(f"{BASE}/workloads/{wid}/migrate", headers=H,
                                json={"target_site": "cloud",
                                      "target_model_variant": "small",
                                      "budget_ms": 10000}).json()
            rows.append({
                "payload_bytes": psize,
                "trial": t,
                "checkpoint_bytes": blob["size_bytes"],
                "migration_ms": round(res["migration_ms"], 3),
                "rolled_back": res["rolled_back"],
            })
            print(f"payload={psize:9d}B  size={blob['size_bytes']:9d}B  "
                  f"ms={res['migration_ms']:7.2f}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(f"results/scaling_{ts}.csv")
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV: {out}")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("pip install matplotlib to render the plot")
        return

    by_payload = {}
    for r in rows:
        by_payload.setdefault(r["payload_bytes"], []).append(r)

    xs, ys, sizes = [], [], []
    for p in sorted(by_payload):
        xs.append(p)
        ys.append(statistics.mean(r["migration_ms"] for r in by_payload[p]))
        sizes.append(
            statistics.mean(r["checkpoint_bytes"] for r in by_payload[p])
        )

    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot(sizes, ys, marker="o")
    ax.set_xlabel("Checkpoint size (bytes)")
    ax.set_ylabel("Total migration time (ms)")
    ax.set_title("Migration time vs. checkpoint size")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig_path = Path(f"results/scaling_{ts}.png")
    fig.savefig(fig_path, dpi=150)
    print(f"PNG: {fig_path}")


if __name__ == "__main__":
    main()
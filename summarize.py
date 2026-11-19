#!/usr/bin/env python3
"""Average results.csv per (scenario, mode); print table; draw graphs."""
import csv
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "results", "results.csv")
OUT_MD = os.path.join(HERE, "results", "summary.md")

SCEN_ORDER = ["none", "short", "long", "random", "burst", "ackloss"]
SCEN_NAME = {"none": "No failure", "short": "4 s outage",
             "long": "10 s outage", "random": "10% random loss",
             "burst": "10% burst loss", "ackloss": "10% ACK loss"}
LINK_SCEN = ["none", "short", "long"]
LOSS_SCEN = ["random", "burst", "ackloss"]
MODE_ORDER = ["static", "sdn", "plain", "recovery"]
MODE_NAME = {"static": "Static (no backup)", "sdn": "SDN recovery",
             "plain": "Plain UDP (no recovery)",
             "recovery": "UDP + NACK/retransmit"}

rows = list(csv.DictReader(open(CSV_PATH)))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mean(group, key):
    vals = [num(r[key]) for r in group if num(r[key]) is not None]
    return st.mean(vals) if vals else None


def fmt(v, nd=2):
    return "-" if v is None else f"{v:.{nd}f}"


groups = {}
for r in rows:
    groups.setdefault((r["scenario"], r["mode"]), []).append(r)

lines = [
    "| Scenario | Approach | Runs | Recovery time (s) | Missing packets | "
    "Final loss % | First-attempt loss % | Retransmissions | Controller reroute (ms) |",
    "|---|---|---|---|---|---|---|---|---|",
]
summary = {}
for s in SCEN_ORDER:
    for m in MODE_ORDER:
        g = groups.get((s, m))
        if not g:
            continue
        v = {k: mean(g, k) for k in (
            "max_ack_gap_s", "missing_count", "final_loss_pct",
            "first_attempt_loss_pct", "retransmissions", "ctrl_reroute_ms")}
        summary[(s, m)] = v
        lines.append(
            f"| {SCEN_NAME[s]} | {MODE_NAME[m]} | {len(g)} | "
            f"{fmt(v['max_ack_gap_s'], 3)} | {fmt(v['missing_count'], 1)} | "
            f"{fmt(v['final_loss_pct'])} | {fmt(v['first_attempt_loss_pct'])} | "
            f"{fmt(v['retransmissions'], 1)} | {fmt(v['ctrl_reroute_ms'], 1)} |")

table = "\n".join(lines)
print(table)
open(OUT_MD, "w").write(table + "\n")
print(f"\nSaved {OUT_MD}")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("matplotlib not installed: skipping graphs "
          "(pip install matplotlib to get them)")
    raise SystemExit

def bars(scen_list, mode_list, key, title, ylabel, fname, log=False):
    scens = [s for s in scen_list
             if any((s, m) in summary for m in mode_list)]
    if not scens:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    width = 0.38
    for i, m in enumerate(mode_list):
        xs = [j + (i - 0.5) * width for j in range(len(scens))]
        ys = [(summary.get((s, m), {}).get(key) or 0) for s in scens]
        b = ax.bar(xs, ys, width, label=MODE_NAME[m])
        for rect, y in zip(b, ys):
            ax.annotate(f"{y:.3g}", (rect.get_x() + rect.get_width() / 2, y),
                        ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(scens)))
    ax.set_xticklabels([SCEN_NAME[s] for s in scens])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if log:
        ax.set_yscale("log")
    ax.legend()
    fig.tight_layout()
    path = os.path.join(HERE, "results", fname)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print("Saved", path)


LINK_MODES = ["static", "sdn"]
LOSS_MODES = ["plain", "recovery"]
bars(LINK_SCEN, LINK_MODES, "max_ack_gap_s",
     "Recovery time (longest silence), lower is better",
     "seconds (log scale)", "graph_recovery_time.png", log=True)
bars(LINK_SCEN, LINK_MODES, "missing_count",
     "Packets lost for good, lower is better",
     "packets", "graph_missing_packets.png")
bars(LINK_SCEN, LINK_MODES, "retransmissions",
     "Retransmissions needed, lower is better",
     "packets", "graph_retransmissions.png")
bars(LOSS_SCEN, LOSS_MODES, "final_loss_pct",
     "Final packet loss under netem loss, lower is better",
     "% of packets", "graph_loss_final.png")
bars(LOSS_SCEN, LOSS_MODES, "retransmissions",
     "Retransmissions under netem loss",
     "packets", "graph_loss_retransmissions.png")

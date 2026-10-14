"""Charts for the real-violin benchmark (run tests.real_violin_benchmark first).

    python -m demos.real_violin_charts
"""
import glob
import json
import os
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt

IN = "data/real_violin"
OUT = "output/real_violin"
KINDS = ["wrong", "flat", "sharp", "missed", "extra", "late", "pause"]
CONDITIONS = ["as recorded", "home room", "far mic", "fan", "phone"]
INK, BLUE, AMBER, GREY = "#1b2a3a", "#4f74a0", "#c2841a", "#9aa4ae"


def load():
    det, res = [], []
    for f in sorted(glob.glob(f"{IN}/piece_*.json")):
        d = json.load(open(f))
        det.append(d["detection"])
        res += d["results"]
    return det, [r for r in res if not r["rejected"]]


def style(ax, title):
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left", color=INK)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(colors=INK, labelsize=9)


def main():
    os.makedirs(OUT, exist_ok=True)
    det, res = load()
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))

    # 1. note detection per piece
    ax = axes[0, 0]
    names = [d["piece"].split("-", 1)[0] for d in det]
    rec = [100 * d["matched"] / d["truth"] for d in det]
    prec = [100 * d["matched"] / max(d["detected"], 1) for d in det]
    x = np.arange(len(det))
    ax.bar(x - 0.2, rec, 0.4, color=BLUE, label="annotated notes found")
    ax.bar(x + 0.2, prec, 0.4, color=AMBER, label="reported notes that are real")
    ax.set_xticks(x, names); ax.set_ylim(70, 101); ax.set_ylabel("%")
    ax.set_xlabel("Bach10 piece")
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    style(ax, "1. Hearing a real violinist: note detection per piece")

    # 2. accuracy by condition: control vs planted
    ax = axes[0, 1]
    ctl, mis, fa = [], [], []
    for c in CONDITIONS:
        rs = [r for r in res if r["cond"] == c]
        cr = [r for r in rs if r["control"]]
        mr = [r for r in rs if not r["control"]]
        ctl.append(100 * np.mean([r["exact"] for r in cr]) if cr else 0)
        mis.append(100 * np.mean([r["exact"] for r in mr]) if mr else 0)
        fa.append(np.mean([len(r["false"]) for r in rs]) if rs else 0)
    x = np.arange(len(CONDITIONS))
    ax.bar(x - 0.2, ctl, 0.4, color=BLUE, label="no mistakes played: clean review")
    ax.bar(x + 0.2, mis, 0.4, color=AMBER, label="with mistakes: review exactly right")
    for i, v in enumerate(fa):
        ax.text(i, 103, f"{v:.1f} false\nalarms/take", ha="center", fontsize=8, color=INK)
    ax.set_xticks(x, CONDITIONS); ax.set_ylim(0, 118); ax.set_ylabel("% of takes")
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    style(ax, "2. Accuracy by recording condition")

    # 3. recall by mistake type: strict vs caught
    ax = axes[1, 0]
    hits, loose = defaultdict(list), defaultdict(list)
    for r in res:
        for k, v in r["found"].items():
            hits[k] += v
        for k, v in r.get("caught", r["found"]).items():
            loose[k] += v
    kinds = [k for k in KINDS if hits.get(k)]
    y = np.arange(len(kinds))
    sv = [100 * np.mean(hits[k]) for k in kinds]
    cv = [100 * np.mean(loose[k]) for k in kinds]
    ax.barh(y - 0.2, sv, 0.4, color=BLUE, label="strict: exact label on the exact note")
    ax.barh(y + 0.2, cv, 0.4, color=AMBER, label="caught: problem flagged (related label)")
    for i, (a, b) in enumerate(zip(sv, cv)):
        ax.text(b + 1, i + 0.2, f"{b:.0f}%", va="center", fontsize=8, color=INK)
        ax.text(a + 1, i - 0.2, f"{a:.0f}%", va="center", fontsize=8, color=INK)
    ax.set_yticks(y, kinds); ax.set_xlim(0, 112); ax.invert_yaxis()
    ax.set_xlabel("% of planted mistakes found")
    ax.legend(frameon=False, fontsize=8, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    style(ax, "3. Mistakes found, by type")

    # 4. latency vs take length (dedicated timing run, idle machine)
    ax = axes[1, 1]
    lat = json.load(open("data/latency_trend.json"))
    secs = [r["secs"] for r in lat]
    bottom = np.zeros(len(lat))
    for stage, col in zip(["quality gate", "clean + notes", "compare + review"], [GREY, BLUE, AMBER]):
        v = np.array([r[stage] for r in lat])
        ax.bar(secs, v, width=9, bottom=bottom, color=col, label=stage)
        bottom += v
    for x_, t in zip(secs, bottom):
        ax.text(x_, t + 0.3, f"{t:.1f} s", ha="center", fontsize=8, color=INK)
    ax.set_xticks(secs, [f"{x_} s" for x_ in secs])
    ax.set_xlabel("length of the student's take"); ax.set_ylabel("time until the review is ready (s)")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    style(ax, f"4. Latency: about {np.median([r['total'] / r['secs'] * 60 for r in lat]):.1f} s per minute of music (1 CPU core)")

    fig.suptitle("StringCoach on real violin (Bach10: 10 recordings, mistakes planted into the real audio)",
                 fontsize=13, fontweight="bold", color=INK)
    plt.tight_layout()
    path = f"{OUT}/real_violin_trends.png"
    plt.savefig(path, dpi=120, bbox_inches="tight")
    print("saved", path)


if __name__ == "__main__":
    main()

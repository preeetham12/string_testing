"""Part 1 output: what the engine 'sees' in each recording."""
import numpy as np
import matplotlib.pyplot as plt
from engine.pitch import analyse, hz_to_midi, midi_name, SR

plt.rcParams.update({"font.size": 10, "axes.titleweight": "bold"})
BLUE, ORANGE, GREY = "#4C72B0", "#DD8452", "#999999"

t = analyse("data/teacher.wav")
s = analyse("data/student.wav")

fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True,
                         gridspec_kw={"height_ratios": [1, 1, 2.2]})

# 1-2: waveforms with detected note boundaries
for ax, r, name, col in [(axes[0], t, "Teacher", BLUE), (axes[1], s, "Student", ORANGE)]:
    x = np.arange(len(r["y"])) / SR
    ax.plot(x, r["y"], color=col, lw=0.4)
    for n in r["notes"]:
        ax.axvline(n["start"], color=GREY, lw=0.8, ls="--")
        ax.text(n["start"] + 0.03, 0.85, n["note"], fontsize=8, color="#333")
    ax.set_ylim(-1.05, 1.05)
    ax.set_ylabel("amplitude")
    ax.set_title(f"{name} waveform - dashed lines = detected note starts")
    ax.spines[["top", "right"]].set_visible(False)

# 3: pitch curves on a musical (semitone) scale
ax = axes[2]
for r, name, col, ls in [(t, "Teacher", BLUE, "-"), (s, "Student", ORANGE, "--")]:
    m = np.where(r["voiced"], hz_to_midi(np.nan_to_num(r["f0"], nan=440.0)), np.nan)
    ax.plot(r["times"], m, color=col, lw=1.8, ls=ls, label=name)
ticks = range(67, 82)
ax.set_yticks(list(ticks))
ax.set_yticklabels([midi_name(k) for k in ticks])
ax.set_ylim(67.5, 80.5)
ax.grid(axis="y", color="#e5e5e5")
ax.set_ylabel("note (each line = 1 semitone = 100 cents)")
ax.set_xlabel("time (seconds)")
ax.set_title("Pitch curves - the wobble is vibrato; gaps are silence or bow changes")

# annotate the planted mistakes, using the student's detected notes
sn = s["notes"]
ax.annotate(f"E5 {sn[3]['cents']:+.0f} cents (flat)", xy=(sn[3]["start"] + 0.25, 75.6),
            xytext=(1.7, 72.3), arrowprops=dict(arrowstyle="->", color="#333"), fontsize=9)
ax.annotate("wrong note: G5 instead of F#5", xy=(sn[4]["start"] + 0.25, 79),
            xytext=(2.6, 80.0), arrowprops=dict(arrowstyle="->", color="#333"), fontsize=9)
ax.annotate("hesitation pause\n(gap in student line)", xy=(3.28, 78.5), xytext=(3.55, 71.5),
            arrowprops=dict(arrowstyle="->", color="#333"), fontsize=9)
ax.legend(loc="lower right", frameon=True)
ax.spines[["top", "right"]].set_visible(False)

plt.tight_layout()
import os
os.makedirs("output", exist_ok=True)
plt.savefig("output/part1_pitch_detection.png", dpi=130, bbox_inches="tight")
print("Chart saved to output/part1_pitch_detection.png")

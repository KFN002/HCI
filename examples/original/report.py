"""Step 5b - REPORT: summarise results/scores.csv per model and draw one chart.

Where you filled in 'human_pass', your judgement replaces the rule's, and we
report how often the rule agreed with you (if agreement is low, fix score.py).
"""

import csv
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

METRICS = [("ambiguous", "Asks when unclear"),
           ("unanswerable", "Admits not knowing"),
           ("control", "Answers simple questions")]
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # fixed order: model 1, 2, 3, 4


def main():
    with open("results/scores.csv", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["auto_pass"] != ""]

    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)
    models = list(by_model)

    agree = total_checked = 0
    summary = {}
    for model, rs in by_model.items():
        s = {}
        for cat, _ in METRICS:
            passes = []
            for r in rs:
                if r["category"] != cat:
                    continue
                if r["human_pass"].strip() in ("0", "1"):
                    total_checked += 1
                    agree += r["human_pass"].strip() == r["auto_pass"]
                    passes.append(int(r["human_pass"]))
                else:
                    passes.append(int(r["auto_pass"]))
            s[cat] = sum(passes) / len(passes) if passes else float("nan")
        s["median_seconds"] = statistics.median(float(r["seconds"]) for r in rs)
        s["median_words"] = statistics.median(int(r["words"]) for r in rs)
        summary[model] = s

    print(f"{'model':30s} " + " ".join(f"{label:>26s}" for _, label in METRICS)
          + f" {'median s':>9s} {'median words':>13s}")
    for model, s in summary.items():
        print(f"{model:30s} " + " ".join(f"{s[c]:>26.0%}" for c, _ in METRICS)
              + f" {s['median_seconds']:>9.2f} {s['median_words']:>13.0f}")
    if total_checked:
        print(f"\nRule vs. human agreement on {total_checked} checked replies: {agree / total_checked:.0%}")
    else:
        print("\nNo 'human_pass' values filled in yet: these are the rule's scores only.")

    # One grouped bar chart: metrics on the x-axis, one bar per model.
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150)
    n = len(models)
    width = 0.8 / n
    for i, model in enumerate(models):
        xs = [k + (i - (n - 1) / 2) * width for k in range(len(METRICS))]
        vals = [summary[model][c] * 100 for c, _ in METRICS]
        bars = ax.bar(xs, vals, width=width - 0.03, label=model,
                      color=SERIES_COLORS[i % len(SERIES_COLORS)])
        ax.bar_label(bars, labels=[f"{v:.0f}%" for v in vals], padding=3,
                     fontsize=9, color="#52514e")
    ax.set_xticks(range(len(METRICS)), [label for _, label in METRICS])
    ax.set_ylim(0, 110)
    ax.set_ylabel("Share of replies that pass (%)")
    ax.set_title("Scores on our own metrics", loc="left", fontsize=13, color="#0b0b0b")
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1, 1))
    fig.tight_layout()
    fig.savefig("results/summary.png")
    print("Chart saved to results/summary.png")


if __name__ == "__main__":
    main()

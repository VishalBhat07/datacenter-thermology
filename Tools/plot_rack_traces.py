"""Plot ThermoWatch rack-inlet temperature from an exported trace CSV."""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


def plot_traces(source, destination, rack_slot):
    labels = {
        "current": ("Current layout", "#263e59", "-"),
        "all_cooling": ("All-cooling choice", "#b24620", "--"),
        "outage_aware": ("Outage-aware choice", "#007766", "-."),
    }
    temperatures = {}
    peak_slots = {}
    limits = set()
    with source.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if rack_slot and row["rack_slot"] != rack_slot:
                continue
            case = row["offline_crac"] or row["scenario"]
            role = row["layout"]
            readings = temperatures.setdefault(case, {}).setdefault(role, {})
            second = int(row["time_s"])
            temperature = float(row["inlet_c"])
            readings[second] = max(temperature, readings.get(second, temperature))
            peak_slots[role] = row["peak_load_slot"]
            limits.add(float(row["limit_c"]))

    cases = ["Normal", "Peak load", "CRAC-1", "CRAC-2", "CRAC-3", "CRAC-4"]
    if len(limits) != 1 or set(temperatures) != set(cases) or any(
        set(readings) != set(labels) for readings in temperatures.values()
    ):
        raise ValueError("Expected all six cases and three layouts for the selected rack or hall.")

    limit = limits.pop()
    figure, axes = plt.subplots(2, 3, figsize=(13, 7), sharex=True, sharey=True)
    for axis, case in zip(axes.flat, cases):
        for role, (label, color, style) in labels.items():
            readings = sorted(temperatures[case][role].items())
            seconds, values = zip(*readings)
            axis.plot(seconds, values, label=f"{label} ({peak_slots[role]})",
                      color=color, linestyle=style, linewidth=1.8)
        axis.axhline(limit, color="#777777", linewidth=0.8, linestyle=":", label=f"{limit:g} °C reference")
        axis.set_title(case + (" offline" if case.startswith("CRAC") else ""), loc="left", fontsize=11)
        axis.set_xlim(0, max(seconds))
        axis.grid(axis="y", alpha=0.22)

    subject = f"Rack {rack_slot} inlet temperature" if rack_slot else "Warmest rack inlet at each second"
    figure.suptitle(subject, x=0.08, y=0.97, ha="left", fontsize=16, weight="bold")
    figure.supylabel("Modeled inlet temperature (°C)", x=0.025, fontsize=11)
    figure.supxlabel("Simulated time (s)", y=0.10, fontsize=11)
    handles, legend_labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, legend_labels, loc="lower center", bbox_to_anchor=(0.5, 0.012),
                  ncols=4, frameon=False, fontsize=9)
    figure.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.16, wspace=0.12, hspace=0.22)
    figure.savefig(destination, dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Exported rack trace CSV")
    parser.add_argument("output", type=Path, help="Output image path, for example comparison.png")
    parser.add_argument("--rack", type=str.upper, help="Plot a single rack slot, for example A08")
    args = parser.parse_args()
    plot_traces(args.csv, args.output, args.rack)


if __name__ == "__main__":
    main()

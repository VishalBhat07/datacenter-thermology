"""Compare ThermoWatch rack-inlet temperatures with a matched reference CSV."""

import argparse
import csv
from dataclasses import dataclass, field
from itertools import groupby
import json
import math
from pathlib import Path
from statistics import fmean


LAYOUTS = ("current", "all_cooling", "outage_aware")
CASES = {
    "normal": ("Normal", ""),
    "peak-load": ("Peak load", ""),
    **{f"crac-{i}": ("CRAC outage", f"CRAC-{i}") for i in range(1, 5)},
}
CASE_IDS = {value: case for case, value in CASES.items()}
FIRST_PASS = ("peak-load", "crac-1", "crac-3")
SLOTS = {f"{row}{slot:02}" for row in "ABCD" for slot in range(1, 9)}
SAMPLES = {(time, slot) for time in range(181) for slot in SLOTS}
TRACE_COLUMNS = {"layout", "peak_load_slot", "scenario", "offline_crac", "time_s", "rack_slot", "inlet_c"}


@dataclass
class Trace:
    peak_load_slot: str
    temperatures: dict[tuple[int, str], float] = field(default_factory=dict)


def read_traces(path, cases):
    traces = {}
    layout_slots = {}
    selected_cases = set(cases)
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        missing = TRACE_COLUMNS - set(fields)
        if missing or len(set(fields)) != len(fields):
            raise ValueError(f"{path}: missing columns {sorted(missing)} or duplicate column names")
        for row in reader:
            where = f"{path}:{reader.line_num}"
            if None in row or None in row.values():
                raise ValueError(f"{where}: row length differs from header")
            case = CASE_IDS.get((row["scenario"].strip(), row["offline_crac"].strip()))
            if case is None:
                raise ValueError(f"{where}: unknown scenario or inconsistent offline_crac")
            if case not in selected_cases:
                continue

            layout = row["layout"].strip()
            peak_slot = row["peak_load_slot"].strip()
            slot = row["rack_slot"].strip()
            if layout not in LAYOUTS or slot not in SLOTS or peak_slot not in SLOTS:
                raise ValueError(f"{where}: unknown layout or rack slot")
            if layout_slots.setdefault(layout, peak_slot) != peak_slot:
                raise ValueError(f"{where}: peak_load_slot changed within layout {layout}")

            try:
                time = float(row["time_s"])
                temperature = float(row["inlet_c"])
            except ValueError as error:
                raise ValueError(f"{where}: time_s and inlet_c must be numbers") from error
            if not math.isfinite(time) or not time.is_integer() or not 0 <= time <= 180:
                raise ValueError(f"{where}: time_s must be a whole second from 0 through 180")
            if not math.isfinite(temperature):
                raise ValueError(f"{where}: inlet_c must be finite")

            key = (int(time), slot)
            series = traces.setdefault((layout, case), Trace(peak_slot))
            if key in series.temperatures:
                raise ValueError(f"{where}: duplicate sample for {layout}/{case}/{key}")
            series.temperatures[key] = temperature

    for layout in LAYOUTS:
        for case in cases:
            trace = traces.get((layout, case))
            missing = SAMPLES - (trace.temperatures.keys() if trace else set())
            if missing:
                time, slot = min(missing)
                raise ValueError(
                    f"{path}: {layout}/{case} missing {len(missing)} samples, "
                    f"starting with rack {slot} at {time} s"
                )
    return traces


def peak_details(temperatures):
    peak = max(temperatures.values())
    matches = [key for key, temperature in temperatures.items() if temperature == peak]
    return {
        "inlet_c": peak,
        "first_time_s": min(second for second, _ in matches),
        "rack_slots": sorted({slot for _, slot in matches}),
    }


def compare_case(model, reference):
    model_peak = peak_details(model)
    reference_peak = peak_details(reference)
    errors = [temperature - reference[key] for key, temperature in model.items()]
    return {
        "samples": len(errors),
        "bias_c": fmean(errors),
        "mae_c": fmean(abs(error) for error in errors),
        "rmse_c": math.hypot(*errors) / math.sqrt(len(errors)),
        "largest_absolute_error_c": max(abs(error) for error in errors),
        "thermowatch_peak": model_peak,
        "reference_peak": reference_peak,
        "peak_error_c": model_peak["inlet_c"] - reference_peak["inlet_c"],
        "hottest_racks_match": model_peak["rack_slots"] == reference_peak["rack_slots"],
        "common_hottest_racks": sorted(set(model_peak["rack_slots"]) & set(reference_peak["rack_slots"])),
    }


def compare_rankings(thermowatch, reference):
    def tiers(peaks):
        ordered = sorted(peaks, key=lambda layout: (peaks[layout], layout))
        return [list(group) for _, group in groupby(ordered, key=peaks.__getitem__)]

    model_order = tiers(thermowatch)
    reference_order = tiers(reference)
    return {"thermowatch": model_order, "reference": reference_order, "agree": model_order == reference_order}


def build_report(model, reference, cases, uncertainty_c):
    by_case = {}
    for case in cases:
        results = {}
        for layout in LAYOUTS:
            model_trace = model[(layout, case)]
            reference_trace = reference[(layout, case)]
            if model_trace.peak_load_slot != reference_trace.peak_load_slot:
                raise ValueError(
                    f"{layout}/{case}: ThermoWatch uses {model_trace.peak_load_slot} as the peak-load slot, "
                    f"but the reference uses {reference_trace.peak_load_slot}"
                )
            results[layout] = compare_case(model_trace.temperatures, reference_trace.temperatures)
        by_case[case] = {
            "layouts": results,
            "ranking": compare_rankings(
                {layout: row["thermowatch_peak"]["inlet_c"] for layout, row in results.items()},
                {layout: row["reference_peak"]["inlet_c"] for layout, row in results.items()},
            ),
        }

    def layout_envelope(source):
        envelope = {}
        for layout in LAYOUTS:
            peaks = {
                case: by_case[case]["layouts"][layout][f"{source}_peak"]["inlet_c"]
                for case in cases
            }
            worst = max(peaks.values())
            envelope[layout] = {"inlet_c": worst, "limiting_cases": [case for case in cases if peaks[case] == worst]}
        return envelope

    model_worst = layout_envelope("thermowatch")
    reference_worst = layout_envelope("reference")
    model_peaks = {layout: row["inlet_c"] for layout, row in model_worst.items()}
    reference_peaks = {layout: row["inlet_c"] for layout, row in reference_worst.items()}
    model_improvement = model_peaks["current"] - model_peaks["outage_aware"]
    reference_improvement = reference_peaks["current"] - reference_peaks["outage_aware"]
    return {
        "cases": list(cases),
        "covers_all_four_outages": all(f"crac-{i}" in cases for i in range(1, 5)),
        "peak_load_slots": {layout: model[(layout, cases[0])].peak_load_slot for layout in LAYOUTS},
        "matched_samples": len(cases) * len(LAYOUTS) * len(SAMPLES),
        "per_case": by_case,
        "worst_case_over_selected_cases": {
            "thermowatch": model_worst,
            "reference": reference_worst,
            "ranking": compare_rankings(model_peaks, reference_peaks),
        },
        "outage_aware_improvement": {
            "thermowatch_c": model_improvement,
            "reference_c": reference_improvement,
            "difference_c": model_improvement - reference_improvement,
            "reference_uncertainty_c": uncertainty_c,
            "reference_improvement_exceeds_uncertainty": (
                None if uncertainty_c is None else reference_improvement > uncertainty_c
            ),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thermowatch", type=Path, help="ThermoWatch rack trace CSV")
    parser.add_argument("reference", type=Path, help="Matched CFD or testbed reference CSV")
    parser.add_argument("--cases", nargs="+", choices=(*CASES, "all"), default=FIRST_PASS,
                        help="Cases to compare. Defaults to peak-load, crac-1, and crac-3.")
    parser.add_argument("--uncertainty-c", type=float,
                        help="Independent uncertainty in the reference layout improvement, in °C")
    parser.add_argument("--output", type=Path, help="New JSON output path. Defaults to standard output.")
    args = parser.parse_args()
    if "all" in args.cases and len(args.cases) != 1:
        parser.error("Use --cases all on its own.")
    cases = tuple(CASES) if "all" in args.cases else tuple(dict.fromkeys(args.cases))
    if args.uncertainty_c is not None and (not math.isfinite(args.uncertainty_c) or args.uncertainty_c < 0):
        parser.error("--uncertainty-c must be a finite nonnegative number.")

    try:
        model = read_traces(args.thermowatch, cases)
        reference = read_traces(args.reference, cases)
        report = build_report(model, reference, cases, args.uncertainty_c)
        report["inputs"] = {"thermowatch": str(args.thermowatch), "reference": str(args.reference)}
        payload = json.dumps(report, indent=2, allow_nan=False)
        if args.output is None:
            print(payload)
        else:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(payload + "\n")
            print(f"Wrote {report['matched_samples']:,} matched samples to {args.output}")
    except (ValueError, OSError, csv.Error) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()

"""Convert the nine ThermoWatch benchmark cases from OpenFOAM T probes to CSV."""

import argparse
import csv
from itertools import chain
import math
from pathlib import Path
import re

from compare_cfd_traces import CASE_IDS


PROBE_HEADER = re.compile(r"^#\s*Probe\s+(\d+)\s+\(([^)]*)\)\s*$")
COORDINATE_TOLERANCE_M = 5e-5
TIME_TOLERANCE_S = 1e-6
KELVIN_OFFSET = 273.15
VALIDATION = Path(__file__).resolve().parents[1] / "Validation"


def read_manifest(name):
    with (VALIDATION / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def read_probes(path, expected_locations, time_origin, duration, samples):
    locations = {}
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                header = PROBE_HEADER.fullmatch(line)
                if header:
                    index, location = header.groups()
                    index = int(index)
                    if index in locations:
                        raise ValueError(f"{path}:{number}: duplicate declaration for probe {index}")
                    locations[index] = tuple(map(float, location.split()))
                continue

            if len(locations) != len(expected_locations):
                raise ValueError(f"{path}:{number}: expected {len(expected_locations)} probe declarations")
            if not locations_match(locations, expected_locations):
                raise ValueError(f"{path}:{number}: probe locations or indices differ from rack_inlet_probes.csv")
            break
        else:
            raise ValueError(f"{path}: no temperature samples")

        for index, row in chain(((number, line),), enumerate(stream, number + 1)):
            row = row.strip()
            if not row or row.startswith("#"):
                continue
            fields = row.split()
            if len(fields) != len(expected_locations) + 1:
                raise ValueError(f"{path}:{index}: expected a time and {len(expected_locations)} temperatures")
            try:
                time = float(fields[0]) - time_origin
                if not math.isfinite(time):
                    raise ValueError("non-finite time")
                second = round(time)
                if not 0 <= second <= duration or abs(time - second) > TIME_TOLERANCE_S:
                    continue
                values = tuple(map(float, fields[1:]))
                if any(not math.isfinite(value) or value <= 0 for value in values):
                    raise ValueError("probe temperature must be finite and above 0 K")
            except ValueError as error:
                raise ValueError(f"{path}:{index}: {error}") from error

            if samples.setdefault(second, values) != values:
                raise ValueError(f"{path}:{index}: conflicting probe values at {second} s")


def locations_match(locations, expected_locations):
    return all(
        len(actual := locations.get(index, ())) == 3
        and all(math.isclose(value, expected, rel_tol=0, abs_tol=COORDINATE_TOLERANCE_M)
                for value, expected in zip(actual, xyz))
        for index, xyz in enumerate(expected_locations)
    )


def convert(case_root, output, time_origin_override):
    probes = read_manifest("rack_inlet_probes.csv")
    slots = [row["rack_slot"] for row in probes]
    locations = [tuple(float(row[f"sample_{axis}_m"]) for axis in "xyz") for row in probes]
    completed = []
    for case in read_manifest("benchmark_cases.csv"):
        scenario = CASE_IDS[(case["scenario"], case["offline_crac"])]
        case_name = f'{case["layout"]}_{scenario}'
        probe_folder = case_root / case_name / "postProcessing" / "rackInletProbes"
        files = sorted(probe_folder.glob("*/T"))
        if not files:
            raise ValueError(f"{probe_folder}: no probe output matching */T")
        time_origin = float(case["warmup_s"]) if time_origin_override is None else time_origin_override
        duration = int(case["episode_s"])
        samples = {}
        for path in files:
            read_probes(path, locations, time_origin, duration, samples)
        missing = set(range(duration + 1)) - samples.keys()
        if missing:
            raise ValueError(f"{case_name}: missing {len(missing)} seconds, starting at {min(missing)} s")
        completed.append((case, samples))

    with output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["layout", "peak_load_slot", "scenario", "offline_crac", "time_s", "rack_slot", "inlet_c"])
        for case, samples in completed:
            prefix = [case[key] for key in ("layout", "peak_load_slot", "scenario", "offline_crac")]
            for second, temperatures in sorted(samples.items()):
                for slot, kelvin in zip(slots, temperatures):
                    writer.writerow([*prefix, second, slot, f"{kelvin - KELVIN_OFFSET:.6f}"])
    return sum(len(samples) * len(slots) for _, samples in completed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_root", type=Path, help="Directory containing the nine case folders")
    parser.add_argument("output", type=Path, help="New reference CSV path")
    parser.add_argument("--time-origin-s", type=float, help="Solver time at episode start. Defaults to 900 s.")
    args = parser.parse_args()
    if args.time_origin_s is not None and (not math.isfinite(args.time_origin_s) or args.time_origin_s < 0):
        parser.error("--time-origin-s must be finite and nonnegative.")
    try:
        count = convert(args.case_root, args.output, args.time_origin_s)
    except (ValueError, OSError, KeyError, csv.Error) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    print(f"Wrote {count:,} rack-inlet samples to {args.output}")


if __name__ == "__main__":
    main()

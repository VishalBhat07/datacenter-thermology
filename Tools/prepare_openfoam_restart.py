"""Branch a ThermoWatch OpenFOAM checkpoint into a short load/outage test."""

import argparse
import re
from pathlib import Path
import shutil
from textwrap import dedent

from compare_cfd_traces import CASE_IDS
from prepare_openfoam_pilot import IMAGE, MAX_COIL_W, SOLVER, SUPPLY_T_K, mapped_temperature, read_table


def replace_one(pattern, text, replacement, label):
    updated, count = re.subn(pattern, lambda match: replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Expected exactly one {label} in the source case, found {count}")
    return updated


def replace_patch(field, patch, condition):
    content = "\n".join(f"        {line}" for line in condition)
    patch_block = f"    {patch}\n    {{\n{content}\n    }}"
    return replace_one(rf"^    {re.escape(patch)}\s*\{{[^{{}}]*\}}", field, patch_block, patch)


def diagnostics(ports):
    entries = []
    for port in ports:
        for kind, operation, field, weight in (
            ("temperature", "absWeightedAverage", "T", "weightField phi;"),
            ("flow", "sum", "phi", ""),
        ):
            entries.append(dedent(f"""
                {port}_{kind}
                {{
                    type surfaceFieldValue;
                    libs (fieldFunctionObjects);
                    regionType patch;
                    name {port};
                    operation {operation};
                    fields ({field});
                    {weight}
                    writeFields false;
                    writeControl writeTime;
                }}
            """))
    return "".join(entries)


def create_restart(source, output, checkpoint, duration, layout, scenario):
    cases = read_table("benchmark_cases.csv")
    case = next(row for row in cases if row["layout"] == layout
                and CASE_IDS[row["scenario"], row["offline_crac"]] == scenario)
    peak_slot = case["peak_load_slot"]
    offline = case["offline_crac"].lower().replace("-", "_")
    checkpoint_path = source / str(checkpoint)
    for file in (source / "constant/polyMesh/boundary", source / "system/controlDict",
                 *(checkpoint_path / field for field in ("T", "U", "p", "p_rgh", "phi"))):
        if not file.is_file():
            raise ValueError(f"Required checkpoint file is missing: {file}")

    field = (checkpoint_path / "T").read_text()
    exhaust = f"rack_{peak_slot}_exhaust"
    intake = f"rack_{peak_slot}_intake"
    field = replace_patch(field, exhaust, mapped_temperature(intake, case["peak_rack_w"], 295.15))
    for i in range(1, 5):
        unit = f"crac_{i}"
        power = 0 if unit == offline else -MAX_COIL_W
        field = replace_patch(field, f"{unit}_supply", mapped_temperature(
            f"{unit}_return", power, SUPPLY_T_K, SUPPLY_T_K if power else None
        ))

    control = (source / "system/controlDict").read_text()
    end = checkpoint + duration
    control = replace_one(r"^startTime\s+[^;]+;", control, f"startTime {checkpoint};", "startTime")
    control = replace_one(r"^endTime\s+[^;]+;", control, f"endTime {end};", "endTime")
    control = replace_one(r"^writeInterval\s+[^;]+;", control, "writeInterval 1;", "writeInterval")
    monitored_cracs = ("crac_1", "crac_3") if offline == "crac_3" else ("crac_1", "crac_2")
    ports = [intake, exhaust, *(f"{unit}_{port}" for unit in monitored_cracs for port in ("return", "supply"))]
    control = replace_one(r'^\s*#include "rackInletProbes"\s*$', control,
                          '    #include "rackInletProbes"\n    #include "heatBalanceProbes"\n', "rack probe include")

    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "constant", output / "constant")
    shutil.copytree(source / "system", output / "system")
    shutil.copytree(checkpoint_path, output / str(checkpoint))
    (output / str(checkpoint) / "T").write_text(field)
    (output / "system/controlDict").write_text(control)
    (output / "system/heatBalanceProbes").write_text(diagnostics(ports))
    (output / "Allrun").write_text(dedent(f"""
        #!/usr/bin/env bash
        set -euo pipefail
        cd -- "$(dirname -- "$0")"
        image="{IMAGE}"
        mount="type=bind,src=$PWD,dst=/case"
        if ! docker run --rm --network none --mount "$mount" "$image" {SOLVER} -case /case > log.solver 2>&1; then
            tail -n 30 log.solver >&2
            exit 1
        fi
        if ! grep -Eq "^Time = {end}([.]0+)?$" log.solver || ! grep -q "^End" log.solver; then
            tail -n 30 log.solver >&2
            exit 1
        fi
        tail -n 12 log.solver
    """).lstrip())
    print(f"Prepared {output}. Time {checkpoint}–{end} s, {peak_slot} at {case['peak_rack_w']} W, "
          f"coil off: {case['offline_crac'] or 'none'}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Completed source case")
    parser.add_argument("output", type=Path, help="New restart case directory")
    parser.add_argument("--checkpoint-s", type=int, default=2)
    parser.add_argument("--duration-s", type=int, default=1)
    parser.add_argument("--layout", choices=("current", "all_cooling", "outage_aware"), default="current")
    parser.add_argument("--case", choices=("peak-load", "crac-1", "crac-3"), default="crac-1")
    args = parser.parse_args()
    if args.checkpoint_s < 0 or args.duration_s < 1:
        parser.error("The checkpoint must be nonnegative and duration must be positive.")
    try:
        create_restart(args.source, args.output, args.checkpoint_s, args.duration_s, args.layout, args.case)
    except (ValueError, OSError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()

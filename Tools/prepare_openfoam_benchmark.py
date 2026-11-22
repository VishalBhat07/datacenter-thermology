"""Branch the nine ThermoWatch CFD episodes from a completed common warm-up."""

import argparse
from pathlib import Path
from textwrap import dedent

from compare_cfd_traces import CASE_IDS
from import_openfoam_probes import read_probes
from prepare_openfoam_pilot import foam_file, read_table
from prepare_openfoam_restart import create_restart
from prepare_openfoam_warmup import FIELDS, write_runner


def load_initial_probes(source, checkpoint):
    probe_rows = read_table("rack_inlet_probes.csv")
    positions = [tuple(float(row[f"sample_{axis}_m"]) for axis in "xyz") for row in probe_rows]
    samples = {}
    for path in sorted((source / "postProcessing/rackInletProbes").glob("*/T")):
        read_probes(path, positions, checkpoint, 0, samples)
    if 0 not in samples:
        raise ValueError(f"{source}: the warm-up probe output has no sample at {checkpoint} s")

    headers = [f"# Probe {index} ({' '.join(map(str, xyz))})" for index, xyz in enumerate(positions)]
    sample = f"{checkpoint} " + " ".join(f"{value:.12g}" for value in samples[0])
    return "\n".join([*headers, "# Time", sample]) + "\n"


def prepare_benchmark(source, output, ranks):
    cases = read_table("benchmark_cases.csv")
    checkpoint = int(cases[0]["warmup_s"])
    for field in FIELDS:
        if not (source / str(checkpoint) / field).is_file():
            raise ValueError(f"{source}: missing warm-up field {checkpoint}/{field}")
    initial_probes = load_initial_probes(source, checkpoint)

    output.mkdir(parents=True, exist_ok=False)
    case_names = []
    for row in cases:
        layout = row["layout"]
        scenario = CASE_IDS[row["scenario"], row["offline_crac"]]
        duration = int(row["episode_s"])
        name = f"{layout}_{scenario}"
        case = output / name
        create_restart(source, case, checkpoint, duration, layout, scenario)

        # OpenFOAM's write-time probes start after the branch. Reuse the shared initial sample.
        probe_dir = case / "postProcessing/rackInletProbes/warmupCheckpoint"
        probe_dir.mkdir(parents=True)
        (probe_dir / "T").write_text(initial_probes)
        foam_file(case / "system/decomposeParDict", f"numberOfSubdomains {ranks};\nmethod scotch;")
        write_runner(case, checkpoint, checkpoint + duration, ranks)
        case_names.append(name)

    run_lines = "\n".join(f'echo "{name}"\nbash "{name}/Allrun"' for name in case_names)
    runner_header = dedent("""
        #!/usr/bin/env bash
        set -euo pipefail
        cd -- "$(dirname -- "$0")"
    """).lstrip()
    (output / "Allrun").write_text(runner_header + run_lines + "\n")
    print(f"Prepared {len(case_names)} episodes under {output}. Run bash {output / 'Allrun'}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Warm-up case with reconstructed 900 s fields and probes")
    parser.add_argument("output", type=Path, help="New directory for the nine CFD episodes")
    parser.add_argument("--ranks", type=int, default=4)
    args = parser.parse_args()
    if args.ranks < 1:
        parser.error("The rank count must be positive.")
    try:
        prepare_benchmark(args.source, args.output, args.ranks)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()

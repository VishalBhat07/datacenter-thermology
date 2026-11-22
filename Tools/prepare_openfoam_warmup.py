"""Continue a verified ThermoWatch CFD checkpoint with a resumable warm-up."""

import argparse
from pathlib import Path
import shutil
from textwrap import dedent

from prepare_openfoam_pilot import IMAGE, SOLVER, foam_file
from prepare_openfoam_restart import replace_one


FIELDS = ("T", "U", "p", "p_rgh", "phi", "k", "epsilon", "nut", "alphat")
CHECKPOINT_INTERVAL_S = 2


def prepare_warmup(source, output, checkpoint, end, ranks):
    saved_state = source / str(checkpoint)
    required = (
        source / "constant/polyMesh/boundary",
        source / "constant/polyMesh/cellZones",
        source / "constant/fvOptions",
        source / "system/controlDict",
        *(saved_state / name for name in FIELDS),
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("Incomplete reference checkpoint: " + ", ".join(missing))

    control = (source / "system/controlDict").read_text()
    for key, value in (
        ("startFrom", "latestTime"),
        ("startTime", checkpoint),
        ("endTime", end),
        ("writeInterval", CHECKPOINT_INTERVAL_S),
        ("purgeWrite", 2),
    ):
        control = replace_one(rf"^{key}\s+[^;]+;", control, f"{key} {value};", key)

    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "constant", output / "constant")
    shutil.copytree(source / "system", output / "system")
    shutil.copytree(saved_state, output / str(checkpoint))
    (output / "system/controlDict").write_text(control)
    if ranks > 1:
        foam_file(output / "system/decomposeParDict", f"numberOfSubdomains {ranks};\nmethod scotch;")
    write_runner(output, checkpoint, end, ranks)
    print(f"Prepared {output}. Warm-up {checkpoint}–{end} s on {ranks} rank(s). "
          f"Checkpoints every {CHECKPOINT_INTERVAL_S} s.")


def write_runner(case, checkpoint, end, ranks):
    (case / "Allrun").write_text(dedent(f"""
        #!/usr/bin/env bash
        set -euo pipefail
        cd -- "$(dirname -- "$0")"
        image="{IMAGE}"
        mount="type=bind,src=$PWD,dst=/case"
        ranks={ranks}
        end={end}

        run_step() {{
            local logfile="$1"
            shift
            printf 'Running %s\\n' "$1"
            if ! docker run --rm --network none --mount "$mount" "$image" "$@" -case /case > "$logfile" 2>&1; then
                tail -n 35 "$logfile" >&2
                return 1
            fi
        }}

        finished() {{
            [[ -f log.solver ]] && grep -Eq "^Time = $end([.]0+)?$" log.solver && grep -q "^End" log.solver
        }}

        has_checkpoint() {{
            local field
            for field in {" ".join(FIELDS)}; do
                if [[ ! -s "$1/$field" ]]; then
                    return 1
                fi
            done
        }}

        if finished && has_checkpoint "$end"; then
            tail -n 12 log.solver
            exit 0
        fi

        if (( ranks > 1 )); then
            shopt -s nullglob
            set -- processor[0-9]*
            if (( $# == 0 )); then
                run_step log.decomposePar decomposePar -time {checkpoint}
            elif (( $# != ranks )); then
                printf 'The saved partition count does not match the planned rank count.\\n' >&2
                exit 1
            fi
            if ! finished || ! has_checkpoint "processor0/$end"; then
                run_step log.solver mpirun --allow-run-as-root --oversubscribe -np "$ranks" {SOLVER} -parallel
            fi
        else
            run_step log.solver {SOLVER}
        fi

        if ! finished; then
            tail -n 35 log.solver >&2
            exit 1
        fi
        if (( ranks > 1 )); then
            run_step log.reconstructPar reconstructPar -time "$end"
        fi
        if ! has_checkpoint "$end"; then
            printf 'Incomplete final checkpoint at %s s.\\n' "$end" >&2
            exit 1
        fi
        tail -n 12 log.solver
    """).lstrip(), encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Completed reference case")
    parser.add_argument("output", type=Path, help="New warm-up case directory")
    parser.add_argument("--checkpoint-s", type=int, default=2)
    parser.add_argument("--end-time-s", type=int, default=6)
    parser.add_argument("--ranks", type=int, default=4)
    args = parser.parse_args()
    start = args.checkpoint_s
    end = args.end_time_s
    if not 0 <= start < end <= 900 or start % CHECKPOINT_INTERVAL_S or end % CHECKPOINT_INTERVAL_S:
        parser.error("Use increasing even checkpoint times between 0 and 900 s.")
    if args.ranks < 1:
        parser.error("The rank count must be positive.")
    try:
        prepare_warmup(args.source, args.output, start, end, args.ranks)
    except (ValueError, OSError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()

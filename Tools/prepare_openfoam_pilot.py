"""Generate a short OpenFOAM startup case from the ThermoWatch hall geometry."""

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass
from itertools import product
import math
from pathlib import Path
from textwrap import dedent


VALIDATION = Path(__file__).resolve().parents[1] / "Validation"
ROOM_HEIGHT_M = 3.2
PLENUM_DEPTH_M = 0.6
TARGET_CELL_M = 0.3
RACK_MASS_FLOW = 0.288
CRAC_MASS_FLOW = 2.304
AIR_DENSITY = 1.2
RACK_POWER_W = 1000
MAX_COIL_W = 12000
TURBULENCE_INTENSITY = 0.05
TURBULENCE_LENGTH_M = 0.05
INITIAL_T_K = 295.15
SUPPLY_T_K = 291.15
SOLVER = "buoyantPimpleFoam"
IMAGE = "opencfd/openfoam-default:2312@sha256:097e45046a74ee1dae3e6135df0c42723ba55092d246e7c9932f3bfa1e89b7b2"
CORNER_OFFSETS = (
    (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
    (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
)
FACE_CORNERS = ((0, 4, 7, 3), (1, 2, 6, 5), (0, 1, 5, 4), (3, 7, 6, 2), (0, 3, 2, 1), (4, 5, 6, 7))
STEPS = ((-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1))
UNITY_FRONT = {0: 3, 90: 1, 180: 2, 270: 0}


@dataclass(frozen=True)
class Box:
    name: str
    low: tuple[float, float, float]
    high: tuple[float, float, float]
    front: int | None = None

    @classmethod
    def centered(cls, name, center, size, front=None):
        low = tuple(round(value - length / 2, 8) for value, length in zip(center, size))
        high = tuple(round(value + length / 2, 8) for value, length in zip(center, size))
        return cls(name, low, high, front)

    def contains(self, point, dimensions=3):
        return all(self.low[i] < point[i] < self.high[i] for i in range(dimensions))


def read_table(name):
    with (VALIDATION / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def read_geometry():
    equipment = read_table("equipment_geometry.csv")

    def equipment_box(row):
        center = tuple(float(row[f"center_{axis}_m"]) for axis in "xyz")
        size = tuple(float(row[f"size_{axis}_m"]) for axis in "xyz")
        front = UNITY_FRONT[int(float(row["unity_yaw_deg"]))]
        return Box.centered(row["id"].lower().replace("-", "_"), center, size, front)

    slab = equipment_box(next(row for row in equipment if row["kind"] == "floor_envelope"))
    tiles = [equipment_box(row) for row in equipment if row["kind"] == "floor_supply_tile"]
    cracs = [equipment_box(row) for row in equipment if row["kind"] == "crac_body"]
    probe_rows = read_table("rack_inlet_probes.csv")
    racks = []
    for row in probe_rows:
        x, y, z = (float(row[f"rack_base_{axis}_m"]) for axis in "xyz")
        front = UNITY_FRONT[int(float(row["unity_rack_yaw_deg"]))]
        racks.append(Box.centered("rack_" + row["rack_slot"], (x, y, z + 1), (0.8, 1.2, 2.0), front))

    room = Box("air", (slab.low[0], slab.low[1], round(slab.low[2] - PLENUM_DEPTH_M, 8)),
               (slab.high[0], slab.high[1], ROOM_HEIGHT_M))
    probes = [tuple(float(row[f"sample_{axis}_m"]) for axis in "xyz") for row in probe_rows]
    return room, slab, tiles, racks, cracs, probes


def face_name(blocker, side):
    if blocker is None:
        return "walls"
    face_of_solid = side ^ 1
    if blocker.name.startswith("rack_"):
        if face_of_solid == blocker.front:
            return blocker.name + "_intake"
        if face_of_solid == (blocker.front ^ 1):
            return blocker.name + "_exhaust"
    if blocker.name.startswith("crac_") and face_of_solid == blocker.front:
        return blocker.name + "_return"
    return "walls"


def mesh_geometry(room, slab, tiles, racks, cracs, reference_physics):
    solids = [*racks, *cracs]
    cuts = tuple(
        sorted({room.low[i], room.high[i], *(
            side[i] for box in [slab, *solids, *tiles] if i < 2 or box not in tiles
            for side in (box.low, box.high)
        )})
        for i in range(3)
    )
    midpoints = tuple([(a + b) / 2 for a, b in zip(axis, axis[1:])] for axis in cuts)
    blocks = {}
    blocked = {}
    for index in product(*(range(len(axis)) for axis in midpoints)):
        point = tuple(midpoints[i][index[i]] for i in range(3))
        blocker = next((solid for solid in solids if solid.contains(point)), None)
        if blocker is None and slab.contains(point) and not any(tile.contains(point, 2) for tile in tiles):
            blocker = slab
        if blocker is None:
            blocks[index] = point
        else:
            blocked[index] = blocker

    vertices = {}
    hex_lines = []
    faces = defaultdict(list)
    areas = defaultdict(float)
    cell_count = 0
    tile_cell_count = 0
    for index, point in blocks.items():
        corners = []
        widths = [cuts[i][index[i] + 1] - cuts[i][index[i]] for i in range(3)]
        counts = tuple(math.ceil(width / TARGET_CELL_M - 1e-9) for width in widths)
        cell_count += math.prod(counts)
        for offset in CORNER_OFFSETS:
            vertex = tuple(cuts[i][index[i] + offset[i]] for i in range(3))
            corners.append(vertices.setdefault(vertex, len(vertices)))
        zone = ""
        if reference_physics and slab.contains(point):
            zone = " tilePassages"
            tile_cell_count += math.prod(counts)
        hex_lines.append(f"    hex {vector(corners)}{zone} {vector(counts)} simpleGrading (1 1 1)")

        for side, step in enumerate(STEPS):
            adjacent = tuple(a + b for a, b in zip(index, step))
            if adjacent in blocks:
                continue
            blocker = blocked.get(adjacent)
            name = face_name(blocker, side)
            if blocker == slab and side == 5:
                crac = next((unit for unit in cracs if unit.contains(point, 2)), None)
                if crac is not None:
                    name = crac.name + "_supply"
            faces[name].append(vector(corners[j] for j in FACE_CORNERS[side]))
            areas[name] += math.prod(widths[axis] for axis in range(3) if axis != side // 2)

    return len(blocks), vertices, hex_lines, faces, areas, cell_count, tile_cell_count


def vector(values):
    return "(" + " ".join(f"{value:g}" for value in values) + ")"


def foam_file(path, body, kind="dictionary"):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class {kind};\n    object {path.name};\n}}\n\n"
    path.write_text(header + dedent(body).strip() + "\n", encoding="ascii")


def write_mesh(path, vertices, hex_lines, faces):
    points = "\n".join("    " + vector(point) for point in vertices)
    blocks = "\n".join(hex_lines)
    patches = []
    for patch, patch_faces in sorted(faces.items()):
        patch_type = "wall" if patch == "walls" else "patch"
        entries = "\n".join("            " + face for face in patch_faces)
        patches.append(f"    {patch}\n    {{\n        type {patch_type};\n        faces\n        (\n{entries}\n        );\n    }}")
    boundary = "\n".join(patches)
    foam_file(path, f"scale 1;\nvertices\n(\n{points}\n);\nblocks\n(\n{blocks}\n);\n"
                    f"edges ();\nboundary\n(\n{boundary}\n);\nmergePatchPairs ();")


def write_field(case, name, kind, dimensions, initial, patches):
    entries = []
    for patch, condition in sorted(patches.items()):
        values = "\n".join("        " + entry for entry in condition.splitlines())
        entries.append(f"    {patch}\n    {{\n{values}\n    }}")
    foam_file(case / "0" / name,
              f"dimensions [{dimensions}];\ninternalField uniform {initial};\nboundaryField\n{{\n"
              + "\n".join(entries) + "\n}", kind)


def mapped_temperature(return_patch, power, initial_k, minimum=None):
    condition = [
        "type outletMappedUniformInletHeatAddition;",
        f"outletPatch {return_patch};",
        f"Q {power};",
        "phi phi;",
    ]
    if minimum is not None:
        condition.append(f"TMin {minimum};")
    condition.append(f"value uniform {initial_k};")
    return condition


def turbulence_seeds(speed):
    k = 1.5 * (TURBULENCE_INTENSITY * speed) ** 2
    epsilon = 0.09 ** 0.75 * k ** 1.5 / TURBULENCE_LENGTH_M
    return k, epsilon


def write_fields(case, faces, areas, equipment, reference_physics):
    velocity = {}
    temperature = {}
    pressure = {}
    turbulence_inlets = {}
    for patch in faces:
        velocity[patch] = "type noSlip;"
        temperature[patch] = "type zeroGradient;"
        pressure[patch] = "type fixedFluxPressure;\nvalue uniform 101325;"
        if patch == "walls":
            continue

        component, port = patch.rsplit("_", 1)
        is_rack = component.startswith("rack_")
        inflow = port in ("exhaust", "supply")
        flow = RACK_MASS_FLOW if is_rack else CRAC_MASS_FLOW
        direction = "Inlet" if inflow else "Outlet"
        normal = STEPS[4] if port == "supply" else STEPS[equipment[component].front ^ 1]
        speed = flow / (AIR_DENSITY * areas[patch])
        initial_velocity = vector(speed * unit for unit in normal)
        velocity[patch] = (
            f"type flowRate{direction}Velocity;\nmassFlowRate {flow};\n"
            f"rho{direction} {AIR_DENSITY};\nvalue uniform {initial_velocity};"
        )
        if inflow:
            turbulence_inlets[patch] = turbulence_seeds(speed)
        if port == "exhaust":
            temperature[patch] = "\n".join(mapped_temperature(
                f"{component}_intake", RACK_POWER_W, INITIAL_T_K
            ))
        elif port == "supply":
            temperature[patch] = ("\n".join(mapped_temperature(
                f"{component}_return", -MAX_COIL_W, SUPPLY_T_K, SUPPLY_T_K
            )) if reference_physics else f"type fixedValue;\nvalue uniform {SUPPLY_T_K};")

    write_field(case, "U", "volVectorField", "0 1 -1 0 0 0 0", "(0 0 0)", velocity)
    write_field(case, "T", "volScalarField", "0 0 0 1 0 0 0", INITIAL_T_K, temperature)
    write_field(case, "p_rgh", "volScalarField", "1 -1 -2 0 0 0 0", 101325, pressure)
    write_field(case, "p", "volScalarField", "1 -1 -2 0 0 0 0", 101325,
                dict.fromkeys(faces, "type calculated;\nvalue uniform 101325;"))
    alphat = dict.fromkeys(faces, "type calculated;\nvalue uniform 0;")
    if reference_physics:
        alphat["walls"] = "type compressible::alphatWallFunction;\nPrt 0.85;\nvalue uniform 0;"
        initial_k, initial_epsilon = turbulence_seeds(0.3)
        for index, (name, dimensions, initial, wall_type) in enumerate((
            ("k", "0 2 -2 0 0 0 0", initial_k, "kqRWallFunction"),
            ("epsilon", "0 2 -3 0 0 0 0", initial_epsilon, "epsilonWallFunction"),
        )):
            boundaries = dict.fromkeys(faces, "type zeroGradient;")
            boundaries["walls"] = f"type {wall_type};\nvalue uniform {initial:g};"
            for patch, values in turbulence_inlets.items():
                boundaries[patch] = f"type fixedValue;\nvalue uniform {values[index]:g};"
            write_field(case, name, "volScalarField", dimensions, f"{initial:g}", boundaries)
        nut = dict.fromkeys(faces, "type calculated;\nvalue uniform 0;")
        nut["walls"] = "type nutkWallFunction;\nvalue uniform 0;"
        write_field(case, "nut", "volScalarField", "0 2 -1 0 0 0 0", 0, nut)
    write_field(case, "alphat", "volScalarField", "1 -1 -1 0 0 0 0", 0, alphat)


def write_reference_physics(case, tile_loss, slab_thickness):
    foam_file(case / "constant" / "turbulenceProperties", """
        simulationType RAS;
        RAS
        {
            RASModel kEpsilon;
            turbulence on;
            printCoeffs on;
        }
    """)
    drag_per_m = tile_loss / slab_thickness
    foam_file(case / "constant" / "fvOptions", f"""
        perforatedTiles
        {{
            type explicitPorositySource;
            active yes;
            explicitPorositySourceCoeffs
            {{
                selectionMode cellZone;
                cellZone tilePassages;
                type DarcyForchheimer;
                DarcyForchheimerCoeffs
                {{
                    d d [0 -2 0 0 0 0 0] (0 0 0);
                    f f [0 -1 0 0 0 0 0] ({drag_per_m:g} {drag_per_m:g} {drag_per_m:g});
                    coordinateSystem
                    {{
                        origin (0 0 0);
                        e1 (1 0 0);
                        e2 (0 1 0);
                    }}
                }}
            }}
        }}
    """)


def write_settings(case, duration, write_interval):
    (case / "system" / "rackInletProbes").write_text((VALIDATION / "rackInletProbes").read_text())
    foam_file(case / "system" / "controlDict", f"""
        application {SOLVER};
        libs ("libthermoTools.so");
        startFrom startTime;
        startTime 0;
        stopAt endTime;
        endTime {duration};
        deltaT 0.005;
        adjustTimeStep yes;
        maxCo 0.5;
        maxDeltaT 0.025;
        writeControl adjustableRunTime;
        writeInterval {write_interval};
        purgeWrite 0;
        writeFormat ascii;
        writePrecision 9;
        timeFormat general;
        timePrecision 9;
        runTimeModifiable false;
        functions
        {{
            #include "rackInletProbes"
        }}
    """)
    foam_file(case / "system" / "fvSchemes", """
        ddtSchemes { default Euler; }
        gradSchemes { default Gauss linear; }
        divSchemes
        {
            default Gauss linear;
            div(phi,U) Gauss upwind;
            div(phi,h) Gauss upwind;
            div(phi,K) Gauss upwind;
            div(phi,k) Gauss upwind;
            div(phi,epsilon) Gauss upwind;
            div(phiv,p) Gauss upwind;
        }
        laplacianSchemes { default Gauss linear corrected; }
        interpolationSchemes { default linear; }
        snGradSchemes { default corrected; }
    """)
    foam_file(case / "system" / "fvSolution", """
        solvers
        {
            "rho.*"
            {
                solver diagonal;
            }
            "p_rgh.*"
            {
                solver GAMG;
                smoother GaussSeidel;
                tolerance 1e-8;
                relTol 0;
            }
            "(U|h|T|k|epsilon).*"
            {
                solver smoothSolver;
                smoother symGaussSeidel;
                tolerance 1e-8;
                relTol 0;
            }
        }
        PIMPLE
        {
            momentumPredictor yes;
            nOuterCorrectors 2;
            nCorrectors 2;
            nNonOrthogonalCorrectors 0;
            pRefCell 0;
            pRefValue 101325;
        }
        relaxationFactors
        {
            equations
            {
                U 0.85;
                h 0.85;
            }
        }
    """)
    foam_file(case / "constant" / "g", "dimensions [0 1 -2 0 0 0 0];\nvalue (0 0 -9.81);",
              "uniformDimensionedVectorField")
    foam_file(case / "constant" / "turbulenceProperties", "simulationType laminar;")
    foam_file(case / "constant" / "radiationProperties", "radiationModel none;")
    foam_file(case / "constant" / "thermophysicalProperties", """
        thermoType
        {
            type heRhoThermo;
            mixture pureMixture;
            transport const;
            thermo hConst;
            equationOfState perfectGas;
            specie specie;
            energy sensibleEnthalpy;
        }
        mixture
        {
            specie { molWeight 28.965; }
            thermodynamics { Cp 1005; Hf 0; }
            transport { mu 1.81e-05; Pr 0.71; }
        }
    """)


def write_runner(case, duration):
    (case / "Allrun").write_text(dedent(f"""
        #!/usr/bin/env bash
        set -euo pipefail
        cd -- "$(dirname -- "$0")"
        image="{IMAGE}"
        mount="type=bind,src=$PWD,dst=/case"

        run_step() {{
            local logfile="$1"
            local application="$2"
            printf 'Running %s\\n' "$application"
            if ! docker run --rm --network none --mount "$mount" "$image" "$application" -case /case > "$logfile" 2>&1; then
                tail -n 30 "$logfile" >&2
                return 1
            fi
        }}

        run_step log.blockMesh blockMesh
        run_step log.checkMesh checkMesh
        if ! grep -q "Mesh OK" log.checkMesh; then
            tail -n 30 log.checkMesh >&2
            exit 1
        fi
        run_step log.solver {SOLVER}
        if ! grep -Eq "^Time = {duration}([.]0+)?$" log.solver || ! grep -q "^End" log.solver; then
            tail -n 30 log.solver >&2
            exit 1
        fi
        tail -n 12 log.solver
    """).lstrip(), encoding="ascii")


def prepare(case, reference_physics, tile_loss, duration, write_interval):
    room, slab, tiles, racks, cracs, probes = read_geometry()
    block_count, vertices, hex_lines, faces, areas, cell_count, tile_cells = mesh_geometry(
        room, slab, tiles, racks, cracs, reference_physics
    )
    if reference_physics and tile_cells == 0:
        raise ValueError("The mesh has no cells in the tile pressure-loss zone")
    expected_areas = {}
    for rack in racks:
        expected_areas[rack.name + "_intake"] = 1.6
        expected_areas[rack.name + "_exhaust"] = 1.6
    for crac in cracs:
        expected_areas[crac.name + "_return"] = 3.52
        expected_areas[crac.name + "_supply"] = 2.56
    for patch, expected in expected_areas.items():
        if not math.isclose(areas.get(patch, 0), expected, abs_tol=1e-8):
            raise ValueError(f"{patch} has area {areas.get(patch, 0):g} m², expected {expected:g} m²")
    for probe in probes:
        if not room.contains(probe) or any(solid.contains(probe) for solid in [*racks, *cracs]):
            raise ValueError(f"Inlet probe {probe} is outside room air")

    case.mkdir(parents=True, exist_ok=False)
    write_mesh(case / "system" / "blockMeshDict", vertices, hex_lines, faces)
    write_fields(case, faces, areas, {unit.name: unit for unit in [*racks, *cracs]}, reference_physics)
    write_settings(case, duration, write_interval)
    if reference_physics:
        write_reference_physics(case, tile_loss, slab.high[2] - slab.low[2])
    write_runner(case, duration)
    print(f"Prepared {case} with {block_count:,} blocks, {cell_count:,} cells, "
          f"{len(expected_areas)} checked flow patches and {len(probes)} air probes.")
    if reference_physics:
        print(f"The pressure-loss zone has {tile_cells:,} cells. K = {tile_loss:g}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="New pilot case directory")
    parser.add_argument("--reference-physics", action="store_true",
                        help="Enable k-epsilon turbulence, tile drag and capacity-limited cooling")
    parser.add_argument("--tile-loss-coefficient", type=float, default=16.0,
                        help="Assumed dimensionless tile pressure-loss coefficient. Defaults to 16.")
    parser.add_argument("--duration-s", type=int, default=2)
    parser.add_argument("--write-interval-s", type=int, default=1)
    args = parser.parse_args()
    if args.duration_s < 1 or args.write_interval_s < 1 or args.duration_s % args.write_interval_s:
        parser.error("Use positive whole seconds and a write interval that divides the duration.")
    if not math.isfinite(args.tile_loss_coefficient) or args.tile_loss_coefficient < 0:
        parser.error("The tile pressure-loss coefficient must be finite and nonnegative.")
    try:
        prepare(args.case, args.reference_physics, args.tile_loss_coefficient, args.duration_s, args.write_interval_s)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()

# ThermoWatch CFD benchmark

The first comparison uses nine transient episodes. Each of the three peak-load placements is tested with full cooling, CRAC-1 cooling disabled, and CRAC-3 cooling disabled. The objective is to see whether a separate CFD model also ranks D02 ahead of the current C01 placement and the all-cooling choice B05. All nine episodes finished after the common 900 s warm-up; the [nominal results](#nominal-benchmark-results) are available below.

## Inputs

| File | Use |
| --- | --- |
| [benchmark_cases.csv](benchmark_cases.csv) | Exact layout, peak-load rack, outage and timing for all nine cases |
| [rack_inlet_probes.csv](rack_inlet_probes.csv) | 32 rack base locations, Unity yaw and matching air-temperature sampling points |
| [equipment_geometry.csv](equipment_geometry.csv) | Live Unity bounds for the hall slab, two modeled walls, 14 floor tiles and four CRAC bodies |
| [rackInletProbes](rackInletProbes) | OpenFOAM function object for the same 32 temperature probes, in CSV index order |

The rack and case data are from the Unity Play Mode export `thermowatch-rack-traces-20261121-142531-192.csv`. Its SHA-256 is `56fa37e8fac8c42c1c99803e59e1653727b4bcdc857f563fdb6c299f9d59a614`. The equipment bounds are from the open `DataCenterGuard` scene. All CSV geometry uses a vertical Z axis, mapping Unity `(X, Y, Z)` to CFD `(X, Z, Y)`. Yaw stays in the Unity convention. Racks with Unity yaw 0° draw air from positive CFD Y, and racks with yaw 180° draw from negative CFD Y.

## Model setup

- Use the 13.4 m × 12.4 m hall slab footprint, 3.2 m nominal wall height, and the 0.8 m × 1.2 m × 2.0 m rack body dimensions. The scene leaves two sides and the ceiling open for viewing. Closing the hall for the CFD benchmark and extending below the floor for a plenum are modeling choices. Exclude the adjacent power room from this benchmark.
- Use the 14 tile footprints as openings between the hall and the plenum. Define CRAC return and plenum supply patches independently of the scene's decorative grilles. Record the plenum depth, tile pressure-loss coefficients, and CRAC flow rates or fan curves. These values are absent from the visualization and must come from equipment or testbed data, or be stated and varied as assumptions for the computational benchmark. Hold each chosen configuration fixed across the rack placements.
- Drive rack inlet-to-exhaust airflow at 0.24 m³/s per rack. Treat rack electrical power as sensible heat. For an uncoupled rack check, 1 kW and 4 kW give approximately 3.46 °C and 13.82 °C of temperature rise respectively at the ThermoWatch volumetric heat capacity of 1,206 J/(m³·K). Keep the domain mass balance closed and monitor inlet, exhaust, supply, and return fluxes.
- Run a common 900 s warm-up from 22 °C with all 32 racks at 1 kW and all four CRACs available. Use the nominal ThermoWatch cooling target of 18 °C and maximum sensible capacity of 12 kW per active unit. Start each 180 s episode from that same warm-up state, set the named rack to 4 kW, and keep the other racks at 1 kW. For the primary outage cases disable the named unit's cooling while holding its fan operating condition fixed. This matches the study's loss-of-heat-removal question. A fan shutdown is a separate sensitivity case.
- Record solver version, mesh and time-step settings, mass and energy balance, boundary conditions, and the repeatability of the layout ranking. Check spatial and temporal convergence before using the difference in peaks. Estimate uncertainty for the **difference in worst-case inlet temperature** and assess whether it is small enough to resolve the modeled 1.23 °C improvement. For physical validation, compare the CFD reference against independent inlet temperatures and airflow measurements. [NASA CFD verification guidance](https://www.grc.nasa.gov/www/wind/valid/tutorial/verassess.html) [NASA validation assessment](https://www.grc.nasa.gov/www/wind/valid/tutorial/valassess.html)

## Sampling and comparison

Place `rackInletProbes` in each OpenFOAM case's `system/` directory and include it inside the `functions` dictionary in `system/controlDict`. This samples field `T` at field-write times. Schedule outputs so probe data contain each integer second from 900 through 1080, including the warm-up snapshot at 900 s. For cases reset to time zero at branching, sample from 0 through 180 s and use `--time-origin-s 0` in the importer. Keep the 32 probe positions in air. [OpenFOAM probes documentation](https://doc.openfoam.com/2312/tools/post-processing/function-objects/sampling/probes/)

Use case directories named `<layout>_<case>`, where the layouts are `current`, `all_cooling` and `outage_aware`, and the cases are `peak-load`, `crac-1` and `crac-3`. Each case must contain `postProcessing/rackInletProbes/<time>/T` with absolute temperatures in kelvin. The importer checks the coordinates and required seconds, merges nonconflicting restart outputs and converts to Celsius.

```bash
python3 Tools/import_openfoam_probes.py /path/to/cases cfd-traces.csv
python3 Tools/compare_cfd_traces.py rack-temperature-traces.csv cfd-traces.csv --output comparison.json
```

The first pass yields 52,128 matched temperatures. Evaluate whether the CFD ranking agrees with ThermoWatch and compare errors and hotspot locations case by case. Extend to CRAC-2 and CRAC-4 before drawing conclusions about all single-unit failures.

## OpenFOAM startup pilot

Generate a new pilot case with the repository script, then run its startup checks.

```bash
python3 Tools/prepare_openfoam_pilot.py Logs/OpenFOAM/my-pilot
bash Logs/OpenFOAM/my-pilot/Allrun
```

The runner uses the pinned OpenFOAM 2312 container and writes `log.blockMesh`, `log.checkMesh`, and `log.solver` to the case directory. It stops at the first failed step and prints the end of that log. A successful run passes `checkMesh` and reaches 2 s of simulated time.

The generator uses a 0.6 m assumed plenum, 14 open tile passages through the slab, a closed hall, and 32 rack obstructions. Each rack draws 0.288 kg/s from its front intake, exhausts the same mass flow at its rear, and adds 1 kW. Each CRAC draws 2.304 kg/s from the hall and supplies the same flow into the plenum at 18 °C. These mass flows correspond to 0.24 m³/s per rack and an assumed 1.92 m³/s per CRAC at 1.2 kg/m³.

The startup pilot uses a coarse structured mesh and laminar transport. It checks meshing and solver startup with all cooling active. The comparison cases use additional pressure-loss, turbulence, and coil models, with further convergence checks and a shared warm-up. The generated dictionary passed offline checks for positive block volumes, conformal shared faces, one connected air region, 72 flow patches, and 32 probes in air. OpenFOAM 2312 passed `checkMesh` with 37,862 cells and one connected region. The solver reached 2 s in 73 wall-clock seconds and wrote temperatures for all 32 probes.


## Coil-off restart check

The restart check branches the completed 2 s startup case for one extra simulated second. It sets rack C01 to 4 kW, maps CRAC-1 return air back into the plenum with zero heat removal, and leaves its mass-flow boundary unchanged. The active coils use a nominal 12 kW removal limit and an 18 °C minimum supply temperature. This short test exercises the new load and cooling boundaries on the existing mesh. It precedes the benchmark warm-up and does not enter the validation comparison.

```bash
python3 Tools/prepare_openfoam_restart.py Logs/OpenFOAM/pilot-checked Logs/OpenFOAM/my-restart
bash Logs/OpenFOAM/my-restart/Allrun
```

The case writes mass flux and flux-weighted temperature for the selected rack and two CRACs under `postProcessing/`. The expected checks are approximately 4 kW added at C01, zero heat removed at the offline coil with balanced return and supply flow, and no more than 12 kW removed at the active coil under test. At the end of the verified restart, C01 added 4.000 kW, CRAC-1 removed effectively zero heat at its nominal 2.304 kg/s, and CRAC-2 removed 9.253 kW. The solver clock advanced by 35 s for 1 simulated second. The 900 s warm-up and nine 180 s episodes would take about 25 solver-hours at that startup rate. Actual runtime depends on the resulting time steps and hardware.


## Reference-physics startup

The reference-physics option keeps the verified geometry and adds three models. It uses standard k–ε RANS with 5% prescribed inlet turbulence intensity and a 0.05 m length scale. It applies a quadratic pressure loss throughout the 14 tile passages with an assumed dimensionless loss coefficient of 16. It draws each CRAC supply temperature from the return temperature with 12 kW maximum nominal removal and an 18 °C minimum supply value. These are computational assumptions. The tile coefficient is adjustable with `--tile-loss-coefficient`. Test sensitivity to the coefficient and verify wall treatment and mesh convergence before using the rank as independent evidence. [OpenFOAM porosity model](https://doc.openfoam.com/2312/tools/processing/numerics/fvoptions/sources/rtm/explicitPorosity/)

```bash
python3 Tools/prepare_openfoam_pilot.py --reference-physics Logs/OpenFOAM/my-reference-pilot
bash Logs/OpenFOAM/my-reference-pilot/Allrun
```

OpenFOAM completed the reference startup on the 37,862-cell mesh. The solver log confirms k–ε RANS and the Darcy–Forchheimer source on all 68 cells in the tile zone. It reached 2 simulated seconds in 82 solver-clock seconds. A straight-line estimate for the full serial batch from that startup rate is about 29 hours. Actual runtime will depend on the developed flow, time steps, and parallel performance.

## Parallel warm-up timing check

Continue the verified 2 s state to 6 s on four MPI ranks. The generator copies the existing mesh and flow fields, partitions with Scotch, keeps two checkpoints at 2 s intervals, and reconstructs the final state. The startup and warm-up do not supply an independent rack-placement ranking. [OpenFOAM parallel workflow](https://doc.openfoam.com/2312/tools/parallel/)

```bash
python3 Tools/prepare_openfoam_warmup.py Logs/OpenFOAM/reference-pilot Logs/OpenFOAM/my-warmup
bash Logs/OpenFOAM/my-warmup/Allrun
```

The four-rank timing check reached 6 s, wrote all 32 probe temperatures, and reconstructed all nine fields. OpenFOAM reported 53.66 s for the 4 s continuation. At that solver rate, the remaining 894 s warm-up is about 3 h 20 min and the subsequent nine episodes total about 6 h. Those are extrapolations from the short timing run. The developed flow can change the stable time step and runtime.

## Common warm-up and benchmark batch

The continuation from the reconstructed 6 s checkpoint to 900 s is already prepared. Run it from the project root when ready. The runner starts from the latest available checkpoint in the continuation folder, retains two field snapshots, and reconstructs the 900 s fields at completion.

```bash
caffeinate -i bash Logs/OpenFOAM/reference-warmup-900s/Allrun
```

To generate a fresh continuation in a separate folder, use `python3 Tools/prepare_openfoam_warmup.py Logs/OpenFOAM/reference-warmup-6s Logs/OpenFOAM/new-warmup-900s --checkpoint-s 6 --end-time-s 900`.

Once the 900 s fields and probe output exist, prepare and run the benchmark episodes:

```bash
python3 Tools/prepare_openfoam_benchmark.py Logs/OpenFOAM/reference-warmup-900s Logs/OpenFOAM/benchmark
caffeinate -i bash Logs/OpenFOAM/benchmark/Allrun
```

Each episode starts with the same reconstructed warm-up fields, saves the simulated 900 s probe snapshot as its time-zero reference, and collects probes each second through 1080 s. The batch runs cases in order and skips any that already have a complete final checkpoint.

## Nominal benchmark results

The [Unity trace](results/nominal/unity-rack-traces.csv), [OpenFOAM trace](results/nominal/cfd-traces.csv), and [comparison report](results/nominal/comparison.json) are archived here. The default comparison covers 52,128 matched inlet readings.

| Peak-load placement | Unity worst sampled inlet | OpenFOAM worst sampled inlet | Limiting outage in both models |
| --- | ---: | ---: | --- |
| Current (C01) | 34.6450 °C | 26.371556 °C | CRAC-1 |
| All-cooling selection (B05) | 35.2199 °C | 25.802882 °C | CRAC-3 |
| Outage-aware selection (D02) | 33.4162 °C | 25.997106 °C | CRAC-3 |

The models disagree on placement ordering. D02 improves on C01 by 1.2288 °C in Unity and 0.37445 °C in nominal CFD. Nominal CFD ranks B05 above D02 by 0.194224 °C; the numerical uncertainty in that difference has not been assessed. This comparison covers only the three shortlisted placements, the two tested coil outages, and the full-cooling case. It is not physical validation.

To regenerate the report from the repository root without rerunning the solver:

```bash
python3 Tools/compare_cfd_traces.py \
  Validation/results/nominal/unity-rack-traces.csv \
  Validation/results/nominal/cfd-traces.csv
```


## Completed run evidence

The logs below are in the local working project.

| Run | Evidence | Result |
| --- | --- | --- |
| OpenFOAM startup | [Mesh check](../Logs/OpenFOAM/pilot-checked/log.checkMesh), [solver log](../Logs/OpenFOAM/pilot-checked/log.solver) | One connected region, 37,862 cells, 32 inlet probes, reached 2 s |
| Coil-off restart | [Solver log and patch diagnostics](../Logs/OpenFOAM/pilot-crac1-restart/log.solver) | C01 added 4.000 kW, CRAC-1 kept nominal airflow with negligible cooling, CRAC-2 removed 9.253 kW |
| Reference-physics startup | [Solver log](../Logs/OpenFOAM/reference-pilot/log.solver) | k–ε RANS and resistance in 68 tile-zone cells, reached 2 s |
| Four-rank warm-up timing | [Solver log](../Logs/OpenFOAM/reference-warmup-6s/log.solver), [reconstruction log](../Logs/OpenFOAM/reference-warmup-6s/log.reconstructPar) | Advanced from 2 to 6 s in 53.66 solver-clock seconds and reconstructed all nine fields |

# ThermoWatch manuscript handoff

- [`access.tex`](access.tex) is the editable IEEE Access working draft. It includes the nine-case OpenFOAM comparison, both time-step checks (with and without mid-episode restarts), the finer-mesh comparison, and a transient inlet-peak figure.
- [`Novelty_Survey.md`](Novelty_Survey.md) records the literature review and checks on the completed benchmark. The proposed contribution is an empirical comparison of placement decisions under coil outages, not a new placement algorithm or a first-ever claim.
- The [validation results](../Validation/README.md#time-step-restart-and-mesh-checks-b05-and-d02-under-crac-3) link to the underlying traces and comparison reports. The [hall plan](hall-plan.pdf) uses the geometry and probe manifests. The [mesh section](mesh-section.pdf) shows the actual OpenFOAM mesh faces intersected by the same plane, at `y = −4.19 m`, in both runs. The [workflow](benchmark-workflow.pdf) documents the two simulation branches and the trace join.
- The [ranking plot](placement-ranking.pdf) uses the worst case over the three nominal scenarios in `Validation/results/nominal/comparison.json`. The [transient plot](inlet-peaks-crac3.png) takes the maximum over the 32 inlet samples at each second for each plotted placement; all CFD panels share a vertical scale. The [gap plot](paired-gaps.pdf) subtracts the archived B05 peak from the D02 peak for each CRAC-3 configuration.

To compile the draft, place `access.tex` and the six figure files in this directory beside the style and font assets from `POME_Q1_Paper.zip` in a separate directory and use pdfLaTeX or Overleaf. The original sample, third-party template assets, compiled manuscript PDF, and packaged ZIP are kept outside Git.

## Resume here

1. Obtain the full text of [Zhang et al. (2015)](https://doi.org/10.1109/SEMI-THERM.2015.7100152) and check whether it compares rack placements across cooling failures.
2. Confirm author names, affiliations, correspondence, acknowledgments, and required disclosures with the coauthors, then obtain sign-off. Keep the claim limited to the tested placements and scenarios. The simulations do not establish spatial or temporal convergence.

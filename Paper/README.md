# ThermoWatch manuscript handoff

- [`access.tex`](access.tex) is the editable IEEE Access working draft. It predates the completed OpenFOAM comparison: its CFD results, final abstract, and conclusion still need updating.
- [`Novelty_Survey.md`](Novelty_Survey.md) records the literature review and checks on the completed benchmark. The proposed contribution is an empirical comparison of placement decisions under coil outages, not a new placement algorithm or a first-ever claim.
- The [nominal benchmark archive](../Validation/results/nominal/) contains both traces and the comparison report.

To compile the draft, place `access.tex` beside the style and font assets from `POME_Q1_Paper.zip` in a separate directory and use pdfLaTeX or Overleaf. The original sample, third-party template assets, compiled PDF, and packaged ZIP are kept outside Git.

## Resume here

1. Obtain the full text of [Zhang et al. (2015)](https://doi.org/10.1109/SEMI-THERM.2015.7100152) and check whether it compares rack placements across cooling failures.
2. Rerun the two deciding CRAC-3 cases (B05 and D02) with a smaller actual time step, then check mesh sensitivity. Do not describe the nominal 0.19 °C gap as a resolved advantage yet.
3. Add the nine-case results and plots to the manuscript. Keep the claim limited to the tested placements and scenarios unless the comparison is extended.

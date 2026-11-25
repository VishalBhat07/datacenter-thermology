# ThermoWatch manuscript handoff

- [`access.tex`](access.tex) is the editable IEEE Access working draft. It includes the nine-case OpenFOAM comparison, the smaller-time-step check, and a transient inlet-peak figure. The finer-mesh result is not yet included.
- [`Novelty_Survey.md`](Novelty_Survey.md) records the literature review and checks on the completed benchmark. The proposed contribution is an empirical comparison of placement decisions under coil outages, not a new placement algorithm or a first-ever claim.
- The [nominal benchmark archive](../Validation/results/nominal/) and [time-step sensitivity archive](../Validation/results/time-step/) contain the underlying traces and comparison reports. The [figure](inlet-peaks-crac3.png) plots, separately for each placement, the maximum over all 32 sampled inlets at each second under CRAC-3; panels (b) and (c) share a vertical scale.

To compile the draft, place `access.tex` and `inlet-peaks-crac3.png` beside the style and font assets from `POME_Q1_Paper.zip` in a separate directory and use pdfLaTeX or Overleaf. The original sample, third-party template assets, compiled manuscript PDF, and packaged ZIP are kept outside Git.

## Resume here

1. Verify the finer-mesh warm-up and B05/D02 CRAC-3 runs and archive the data. Repeat the original-mesh pair without interruption or run restart-matched controls before interpreting the gap change as temporal or spatial sensitivity. D02's largest post-restart jump is documented in the draft. Do not claim mesh convergence from a two-mesh comparison.
2. Obtain the full text of [Zhang et al. (2015)](https://doi.org/10.1109/SEMI-THERM.2015.7100152) and check whether it compares rack placements across cooling failures.
3. Confirm author names, affiliations, correspondence, acknowledgments, and required disclosures with the coauthors. Keep the claim limited to the tested placements and scenarios unless the comparison is extended.

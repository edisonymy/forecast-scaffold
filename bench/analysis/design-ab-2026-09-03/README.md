# Live paired test: old design vs parallel research (2026-09-03/04)

Operator-requested validation before merging v0.4.28. Both arms opus-5, medium tier,
dry-run, from clean checkouts with Metaculus fetches banned, on 23 closed-but-unresolved
MiniBench (2026-08-24 wave) questions resolving Sep 4-7.

- `old*.jsonl`  — old design at origin/main 153bd0b: 1 research run + 2 reasoning-only lens
  runs (binaries), single research run (numerics). (`old2.jsonl` ran from the branch tree
  with the old config: dossier-path pooling for numerics — only its DDR5 binary row is used.)
- `new*.jsonl`  — parallel research at branch 7b9aeee: 3 independent Angle-P research runs,
  pooled (no supervisor yet). `sup.jsonl` + `traces/` — the final v0.4.28 path with the
  reconciler on two questions. `branch.jsonl` — first pooled numeric/MC dogfood.
- `compare.py` / `compare-2026-09-04.txt` — the paired table (crowd column = the bots-only
  aggregate scraped 2026-09-03; stale relative to the arms' Sep 3 research, indicative only).

Headline (23 paired): numbers agree closely across arms (binaries within 0.03 on 6/8;
numeric medians within ~0.5% on 11/14); new arm's IQR is NOT wider (median ratio 0.93 —
quantile-mean pooling preserves width). Cost: binaries 1.23x, numerics 3.05x (single run
-> three), overall 2.02x before the reconciler (+~$0.5/q). Score at resolution with
bench/analysis/research_vs_lenses.py-style paired log score once the overlay has outcomes.

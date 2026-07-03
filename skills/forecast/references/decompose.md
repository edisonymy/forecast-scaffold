# decompose.md — question surgery

Decomposition serves two different purposes; know which one you're doing.

## 1. Reasoning aid: Fermi decomposition

Break the question into separable drivers when the whole is hard but the parts have data:
P(deal closes) = P(offer made) × P(offer accepted | made). Rules that keep it honest:

- **Cap the chain at 3–4 factors.** Long chains of multiplied point estimates drift toward zero by
  construction (each stage gets an unearned haircut toward 50%) — the multiple-stage fallacy. The
  more stages, the more wrong.
- **Conditionals must be genuinely conditional.** P(B | A happened) is usually much higher than
  P(B) reasoned independently — the world where A happened is a different world. Estimating each
  stage as if independent is the classic error.
- **Prefer ranges to points within stages**, and recompose the range, not just the midpoint —
  multiplying midpoints biases the product low.
- **Always cross-check against a holistic estimate.** Form a gut number for the whole question
  *before* decomposing; if the recomposed and holistic numbers disagree wildly, one of them
  embeds an error — find it, don't average it.
- **Let decompositions compete.** If two natural decompositions give different answers, that
  disagreement is information about model uncertainty; report the spread.
- **Recomposition algebra is explicit.** A vivid sub-scenario is ONE branch:
  P(question) = Σ P(branch_i) — never promote a branch's probability to the whole question
  (catastrophizing, a documented failure mode).

## 2. Data generation: fast proxies

A question that resolves in years is useless as a calibration signal. Its decomposition is not —
short-horizon sub-questions ("will the leading indicator X move by Q3?") resolve in weeks and are
evidence on the slow question. This is the highest-leverage move in the whole scaffold: it
manufactures feedback bandwidth.

For a slow question, record 2–5 fast proxies:

```
python fsj.py record --question "<short-horizon sub-question>" \
  --parent-id <slow-question-record-id> --fast-proxy \
  --probability 0.55 --resolve-by <weeks-away> --criterion "..." ...
```

Good fast proxies: resolve within weeks-to-months from a public source; their outcome would
actually shift your belief on the parent (state how, in `--why`); they are not all downstream of
the same single assumption.

## 3. Question clusters share failure modes

When forecasting several related questions (a tournament batch, a scenario set), name the
**shared assumption** — the world-model claim that, if wrong, moves all your answers together.
Write down what your answers look like in the world where it's false. One wrong assumption
poisoning a cluster of correlated forecasts is how good calibration on paper coexists with a
terrible quarter.

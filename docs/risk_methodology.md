# Risk methodology

Two independent, SQL-driven indicators. Weights, thresholds and parameters live in JSON and can be changed in Module 04 without editing SQL. Neither model is a validated predictor of future failures.

Shared category rule, applied to `ROUND(score, 2)`:

| Category | Score |
|---|---|
| Low | 0 – 24.99 |
| Medium | 25 – 49.99 |
| High | 50 – 74.99 |
| Critical | 75 – 100 |

Boundaries are inclusive on the lower side (`score >= threshold`). Defaults: Medium 25, High 50, Critical 75 (`config/azure_risk.json`, `config/ai4i_risk.json`).

---

## Azure: machine health & risk indicator

**Unit:** one of 100 machines. **As-of date:** end of the selected date range. The component filter does not enter the score (it would make machines incomparable).

**Config:** `config/azure_risk.json`.

### Inputs (never labels of a future event)

| Component | Default weight | Formula and scaling |
|---|---|---|
| `fail_freq` | 25 | failure count in the date range, min-max scaled |
| `fail_rec` | 15 | `MAX(0, 1 − days_since_last_failure / lookback)` (lookback default 90 days), already 0–1, not min-max scaled |
| `err_freq` | 15 | error count in the date range, min-max scaled |
| `err_rec` | 5 | same recency decay on last error, not min-max scaled |
| `tele` | 15 | share of readings in the last `telemetry_window_days` (30) whose volt/rotate/pressure/vibration fall outside fleet mean ± `k`·std (`k` = 2), with the mean and std taken from readings before the as-of date; min-max scaled |
| `maint_burden` | 5 | **planned** maintenance count (failure-linked repairs excluded), min-max scaled |
| `maint_rec` | 10 | days since last maintenance of any kind (or the full range if none), min-max scaled, so longer gaps score higher |
| `age` | 10 | machine age (years), min-max scaled |

Weights are **re-normalised** over the components that have data for that machine. If a machine has no telemetry in the window, `tele` is dropped and `weight_total` becomes 85 with the default weights. Contributions `c_*` are the points that add up to `risk_score`.

Where min-max applies, it uses **all** machines (`MIN/MAX() OVER ()`), not the filtered subset, so selecting one machine does not change its score. `priority_rank` (`RANK() OVER (ORDER BY risk_score DESC)`) is also computed before the machine filter.

### Why these weights (this dataset, descriptive)

On the loaded Azure file, age correlates with failure count, recent failures and errors cluster on a small set of machines, and telemetry outliers coincide with those same machines more often than the fleet average. The numbers are a documented starting point, not an optimised model. Changing a slider in Module 04 recomputes every score from SQL.

### Traceability

Module 04 / 05 show, for each machine:

- the eight contributions (they sum to the rounded score)
- evidence tables: failures, errors, maintenance, recent telemetry vs the fleet band
- a sentence such as "Failure frequency (25.0 pts, 19 failures); Error frequency (12.6 pts, …)"

Suggested actions in Module 05 are **rule-based inspection recommendations** from the dominant contribution, not diagnoses.

### What this is not

- Not a survival / hazard / ML model.
- Not causal ("this machine will fail").
- Historical recorded events plus recent telemetry, summarised.

### Observed scores on the loaded Azure file (default date range 2015-01-01 to 2016-01-01, so as-of 2016-01-02; all machines, default config)

| | |
|---|---|
| Machines | 100 |
| Mean | 46.33 |
| Median | 46.02 |
| Min / max | 19.54 / 72.81 |
| Low / Medium / High / Critical | 2 / 59 / 39 / **0** |

These figures come from `equipment.azure.risk.compute_risk` on `database/equipment.db`. They will not match a different weight file or a different as-of date.

---

## AI4I: operating-condition risk indicator

**Unit:** one of 10,000 independent synthetic records (or a type × tool-wear-band group in Module 05). Rows are **not** distinct physical machines.

**Config:** `config/ai4i_risk.json`.

### Inputs — labels are never used

The scoring CTEs read only Type, temperatures, rpm, torque and tool wear. `Machine failure` / TWF / HDF / PWF / OSF / RNF are attached in the **final SELECT** for evaluation. Flipping every label leaves every score unchanged (tested).

| Factor | Default weight | 0–1 scale |
|---|---|---|
| `tool_wear` | 25 | `MIN(1, tool_wear / 240)` |
| `strain` | 30 | `MIN(1, tool_wear × torque / type_limit)` with L/M/H = 11 000 / 12 000 / 13 000 min·Nm |
| `power` | 25 | distance of `torque × rpm × 2π/60` from the midpoint of [3500, 9000] W, clipped to 1 |
| `heat` | 20 | product of two ramps: temperature difference from the dataset mean down to 8.6 K, **and** speed from the dataset mean down to 1380 rpm (AND logic: heat dissipation needs both) |

`score = 100 × Σ (wᵢ fᵢ) / Σ wᵢ`. Contributions `c_*` sum to the rounded score.

Reference limits come from the published AI4I 2020 description (the same rules Module 02 checks against the labels). They are **not** fitted to the labels.

### Evaluation (in-sample, descriptive)

After scoring, SQL reports:

- failure rate by risk category
- failure rate by score decile (`NTILE(10)`)
- a threshold sweep (share of records flagged vs share of recorded failures captured)
- rank separation (AUC from average ranks / Mann–Whitney U)

On the loaded file, with the default config:

| Category | Records | Recorded failures | Observed failure rate |
|---|---|---|---|
| Low | 3,560 | 3 | 0.08% |
| Medium | 5,170 | 119 | 2.30% |
| High | 1,190 | 164 | 13.78% |
| Critical | 80 | 53 | 66.25% |

In-sample AUC ≈ 0.867. This describes how the indicator relates to labels **on this synthetic file**. It is not a predictive validation and is expected to look strong because several factors follow the same rules that generated the labels.

### Module 05

Ranks **records** and **operating-condition groups** (type × tool-wear band), never machines. Groups with fewer than 30 records are flagged `low_sample`. Suggested actions are the inspection sentence for the largest contributing factor.

### What this is not

- Not a model of a physical machine over time.
- Not comparable to the Azure machine score (different unit, different inputs).
- TWF is a failure flag, not a maintenance event.

---

## Configuration and validation

Invalid JSON (negative weight, thresholds out of order, all weights zero) is rejected before the query runs. Tests cover boundary values 24.99 / 25 / 49.99 / 50 / 74.99 / 75, contribution sums, label independence (AI4I), machine-filter isolation (Azure), and SQL-vs-pandas score reconstruction.

# Project summary: Smart Equipment Maintenance and Failure Analytics Using Advanced SQL

## 1. Overview

This project analyzes industrial machine data with Advanced SQL to find failure patterns, review maintenance activity, and rank what should be inspected first. It uses no machine learning: every score comes from documented rules and weights, calculated in SQL.

## 2. Datasets

The two datasets are independent and are never joined.

| Dataset | Contents |
|---|---|
| Microsoft Azure Predictive Maintenance | Five CSVs covering 100 machines: machines, hourly telemetry, errors, maintenance, failures |
| AI4I 2020 Predictive Maintenance | One CSV of 10,000 independent synthetic process records with failure labels |

## 3. Technologies

- **Python and Pandas:** read, validate and load the CSV files.
- **SQLite and Advanced SQL:** store the data and run the analysis, using CTEs, recursive CTEs, window functions, correlated subqueries and conditional aggregation.
- **Streamlit and Plotly:** the interactive dashboard and charts.

## 4. Workflow

CSV files → Python and Pandas (validation and loading) → SQLite → Advanced SQL analytics → Streamlit and Plotly dashboard

## 5. Modules

| Module | Azure | AI4I |
|---|---|---|
| 01 Data and database | Validates and loads the five CSVs; tables link through `machine_id` | Validates and loads the records |
| 02 Failures | Failure and error counts, repeats and trends | Failure modes and operating conditions |
| 03 Maintenance | Telemetry before and after an event, reported as observed change, not cause | Not supported: no timestamps or maintenance log |
| 04 Risk | Explainable score from eight weighted factors | Indicator from tool wear, strain, power and heat |
| 05 Priority | Ranks machines, with reasons and suggested inspections | Ranks records and groups, not machines |

A separate comparative page shows the two datasets side by side without joining them.

The eight Azure risk factors are failure frequency, failure recency, error frequency, error recency, telemetry deviation, planned-maintenance burden, time since last maintenance, and machine age.

## 6. Results

Azure, default configuration and date range (2015-01-01 to 2016-01-01):

| Measure | Value |
|---|---|
| Machines | 100 |
| Telemetry records | 876,100 |
| Failures | 761 |
| Errors | 3,919 |
| Maintenance events | 2,886 in the default range (3,286 including 2014) |
| Mean risk score | 46.33 (median 46.02, range 19.54 to 72.81) |
| Low / Medium / High / Critical | 2 / 59 / 39 / 0 |

AI4I: 10,000 records, 339 failures (3.39%), mean risk indicator 32.3.

These results summarize recorded history. They do not predict future failures.

## 7. Novelty

The novelty is the SQL-driven approach and the explainable decision support, not the data. Each risk score breaks down into factor contributions that add up to the score, and each contribution can be traced back to the underlying records.

## 8. Expected outcome

- Identify machines with repeated failures or unusual operating patterns.
- Understand why a machine has a higher risk score.
- See how telemetry changed around a maintenance event.
- Decide which machines to inspect first.

## 9. Viva explanation

> "Our project is Smart Equipment Maintenance and Failure Analytics Using Advanced SQL. We analyze two independent datasets that are never joined: Microsoft Azure Predictive Maintenance, with machines, telemetry, errors, maintenance and failures, and AI4I 2020, with 10,000 synthetic process records. Python and Pandas validate and load the data into SQLite, and Advanced SQL does the analysis using CTEs, window functions, correlated subqueries and conditional aggregation. We build explainable risk scores, where each factor's contribution adds up to the score, and rank what to inspect first. We also show telemetry before and after maintenance as observed change, not proof of cause. AI4I has no timestamps or maintenance log, so we deliberately don't run that analysis on it. Results appear in a Streamlit dashboard with Plotly charts. The project uses no machine learning, and the scores summarize history rather than predict future failures."

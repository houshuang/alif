# Failed-word follow-up and established-lapse recovery

- Cutoff: `2026-07-26T07:43:00Z`
- Window start: `2026-03-01T00:00:00Z`
- Non-acquisition rating-1 events with pre-card stability: **2208**
- Distinct failed lemmas: **1103**
- Current Relearning stock: **90** (79 due at cutoff)

All sentence words count equally. Rating 2 means assisted recognition after the answer was revealed; the spontaneous-retrieval endpoint is therefore rating 3 or 4.

## Right-censored follow-up

| Period | Cohort | Horizon | Eligible | Followed | Follow-up | Median delay | Spontaneous at first follow-up |
|---|---|---:|---:|---:|---:|---:|---:|
| all | all_lapses | 10_minutes | 2208 | 85 | 3.85% | 2.11 min | 89.41% |
| all | all_lapses | 2_hours | 2208 | 243 | 11.01% | 29.69 min | 86.83% |
| all | all_lapses | 24_hours | 2140 | 931 | 43.5% | 457.04 min | 81.1% |
| all | all_lapses | 7_days | 2035 | 1744 | 85.7% | 1344.61 min | 72.02% |
| all | fragile_pre_stability_lt_7d | 10_minutes | 1120 | 49 | 4.38% | 2.25 min | 87.76% |
| all | fragile_pre_stability_lt_7d | 2_hours | 1120 | 105 | 9.38% | 16.95 min | 85.71% |
| all | fragile_pre_stability_lt_7d | 24_hours | 1084 | 409 | 37.73% | 498.48 min | 78.24% |
| all | fragile_pre_stability_lt_7d | 7_days | 1026 | 846 | 82.46% | 1649.33 min | 66.9% |
| all | established_pre_stability_ge_7d | 10_minutes | 1088 | 36 | 3.31% | 1.82 min | 91.67% |
| all | established_pre_stability_ge_7d | 2_hours | 1088 | 138 | 12.68% | 41.16 min | 87.68% |
| all | established_pre_stability_ge_7d | 24_hours | 1056 | 522 | 49.43% | 434.46 min | 83.33% |
| all | established_pre_stability_ge_7d | 7_days | 1009 | 898 | 89.0% | 1201.96 min | 76.84% |
| all | older_pre_stability_ge_30d | 10_minutes | 435 | 14 | 3.22% | 2.9 min | 92.86% |
| all | older_pre_stability_ge_30d | 2_hours | 435 | 51 | 11.72% | 39.56 min | 92.16% |
| all | older_pre_stability_ge_30d | 24_hours | 420 | 209 | 49.76% | 423.19 min | 89.47% |
| all | older_pre_stability_ge_30d | 7_days | 390 | 342 | 87.69% | 1133.41 min | 86.84% |
| last_90_days | all_lapses | 10_minutes | 1526 | 60 | 3.93% | 2.61 min | 88.33% |
| last_90_days | all_lapses | 2_hours | 1526 | 167 | 10.94% | 24.08 min | 84.43% |
| last_90_days | all_lapses | 24_hours | 1458 | 616 | 42.25% | 452.69 min | 79.38% |
| last_90_days | all_lapses | 7_days | 1353 | 1148 | 84.85% | 1353.26 min | 69.77% |
| last_90_days | fragile_pre_stability_lt_7d | 10_minutes | 724 | 30 | 4.14% | 2.72 min | 86.67% |
| last_90_days | fragile_pre_stability_lt_7d | 2_hours | 724 | 61 | 8.43% | 10.18 min | 85.25% |
| last_90_days | fragile_pre_stability_lt_7d | 24_hours | 688 | 251 | 36.48% | 569.06 min | 76.49% |
| last_90_days | fragile_pre_stability_lt_7d | 7_days | 630 | 518 | 82.22% | 1666.42 min | 63.32% |
| last_90_days | established_pre_stability_ge_7d | 10_minutes | 802 | 30 | 3.74% | 2.34 min | 90.0% |
| last_90_days | established_pre_stability_ge_7d | 2_hours | 802 | 106 | 13.22% | 34.26 min | 83.96% |
| last_90_days | established_pre_stability_ge_7d | 24_hours | 770 | 365 | 47.4% | 407.83 min | 81.37% |
| last_90_days | established_pre_stability_ge_7d | 7_days | 723 | 630 | 87.14% | 1213.23 min | 75.08% |
| last_90_days | older_pre_stability_ge_30d | 10_minutes | 354 | 12 | 3.39% | 3.07 min | 91.67% |
| last_90_days | older_pre_stability_ge_30d | 2_hours | 354 | 43 | 12.15% | 33.19 min | 90.7% |
| last_90_days | older_pre_stability_ge_30d | 24_hours | 339 | 166 | 48.97% | 407.13 min | 89.16% |
| last_90_days | older_pre_stability_ge_30d | 7_days | 309 | 266 | 86.08% | 1074.84 min | 87.59% |
| last_30_days | all_lapses | 10_minutes | 440 | 26 | 5.91% | 4.09 min | 80.77% |
| last_30_days | all_lapses | 2_hours | 440 | 43 | 9.77% | 5.21 min | 83.72% |
| last_30_days | all_lapses | 24_hours | 372 | 111 | 29.84% | 672.53 min | 83.78% |
| last_30_days | all_lapses | 7_days | 267 | 199 | 74.53% | 2161.86 min | 74.87% |
| last_30_days | fragile_pre_stability_lt_7d | 10_minutes | 189 | 12 | 6.35% | 3.65 min | 75.0% |
| last_30_days | fragile_pre_stability_lt_7d | 2_hours | 189 | 19 | 10.05% | 5.21 min | 78.95% |
| last_30_days | fragile_pre_stability_lt_7d | 24_hours | 153 | 38 | 24.84% | 657.05 min | 81.58% |
| last_30_days | fragile_pre_stability_lt_7d | 7_days | 95 | 63 | 66.32% | 2210.28 min | 63.49% |
| last_30_days | established_pre_stability_ge_7d | 10_minutes | 251 | 14 | 5.58% | 4.16 min | 85.71% |
| last_30_days | established_pre_stability_ge_7d | 2_hours | 251 | 24 | 9.56% | 5.63 min | 87.5% |
| last_30_days | established_pre_stability_ge_7d | 24_hours | 219 | 73 | 33.33% | 690.78 min | 84.93% |
| last_30_days | established_pre_stability_ge_7d | 7_days | 172 | 136 | 79.07% | 2088.94 min | 80.15% |
| last_30_days | older_pre_stability_ge_30d | 10_minutes | 135 | 7 | 5.19% | 4.82 min | 85.71% |
| last_30_days | older_pre_stability_ge_30d | 2_hours | 135 | 14 | 10.37% | 8.26 min | 92.86% |
| last_30_days | older_pre_stability_ge_30d | 24_hours | 120 | 41 | 34.17% | 688.39 min | 92.68% |
| last_30_days | older_pre_stability_ge_30d | 7_days | 90 | 67 | 74.44% | 2016.02 min | 94.03% |
| last_7_days | all_lapses | 10_minutes | 173 | 24 | 13.87% | 4.21 min | 83.33% |
| last_7_days | all_lapses | 2_hours | 173 | 31 | 17.92% | 4.72 min | 83.87% |
| last_7_days | all_lapses | 24_hours | 105 | 33 | 31.43% | 822.48 min | 81.82% |
| last_7_days | all_lapses | 7_days | 0 | 0 | None% | None min | None% |
| last_7_days | fragile_pre_stability_lt_7d | 10_minutes | 94 | 10 | 10.64% | 4.27 min | 80.0% |
| last_7_days | fragile_pre_stability_lt_7d | 2_hours | 94 | 13 | 13.83% | 4.72 min | 84.62% |
| last_7_days | fragile_pre_stability_lt_7d | 24_hours | 58 | 15 | 25.86% | 818.96 min | 80.0% |
| last_7_days | fragile_pre_stability_lt_7d | 7_days | 0 | 0 | None% | None min | None% |
| last_7_days | established_pre_stability_ge_7d | 10_minutes | 79 | 14 | 17.72% | 4.16 min | 85.71% |
| last_7_days | established_pre_stability_ge_7d | 2_hours | 79 | 18 | 22.78% | 4.62 min | 83.33% |
| last_7_days | established_pre_stability_ge_7d | 24_hours | 47 | 18 | 38.3% | 932.66 min | 83.33% |
| last_7_days | established_pre_stability_ge_7d | 7_days | 0 | 0 | None% | None min | None% |
| last_7_days | older_pre_stability_ge_30d | 10_minutes | 45 | 7 | 15.56% | 4.82 min | 85.71% |
| last_7_days | older_pre_stability_ge_30d | 2_hours | 45 | 10 | 22.22% | 5.04 min | 90.0% |
| last_7_days | older_pre_stability_ge_30d | 24_hours | 30 | 13 | 43.33% | 871.75 min | 92.31% |
| last_7_days | older_pre_stability_ge_30d | 7_days | 0 | 0 | None% | None min | None% |

## Interpretation limits

- This is an observational delivery analysis, not a counterfactual retention estimate.
- Each horizon excludes lapses too close to the cutoff to have the full follow-up opportunity.
- First-follow-up ratings describe the next recorded word outcome; they do not prove that a particular selector or retry mechanism caused it.

# Cold calls left under keep-alive 30 s, by cause (test, cached images)

Cold starts: aws: measured on AWS (EC2 k3s, images pulled from ECR in the same region); scale-up cap 2, contention parallel_3.

| Source | Profile | cause | % of all calls (cold under KA30) | share ours warms | idle /1k: KA30 -> ours |
|---|---|---|---|---|---|
| tracelab | coding | first_use | 4.8 | 8.1 | 17,622 -> 53,587 |
| tracelab | coding | gap_over_30s | 31.2 | 35.2 | 17,622 -> 53,587 |
| tracelab | coding | other | 7.1 | 46.9 | 17,622 -> 53,587 |
| copilot_2026 | coding | first_use | 9.2 | 21.3 | 19,301 -> 45,556 |
| copilot_2026 | coding | gap_over_30s | 32.8 | 36.8 | 19,301 -> 45,556 |
| copilot_2026 | coding | other | 8.3 | 21.8 | 19,301 -> 45,556 |
| tau2_bench | support | first_use | 38.3 | 17.6 | 18,218 -> 11,859 |
| tau2_bench | support | gap_over_30s | 3.8 | 0.0 | 18,218 -> 11,859 |
| tau2_bench | support | other | 1.5 | 40.0 | 18,218 -> 11,859 |

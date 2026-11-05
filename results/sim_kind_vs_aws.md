# Simulator: kind vs AWS cold starts (test split)

Runs: `kind`, `aws`, `aws_cap2_parallel_3`. Primary: `aws_cap2_parallel_3` (AWS cold starts, at most 2 scale-ups at once per node, a start that overlaps another takes the parallel_3 time). The policy is frozen: no re-tuning on AWS values. Cells: warm % / idle pod-s per 1,000 calls.

| Source | Mode | Policy | kind | aws | aws_cap2_parallel_3 |
|---|---|---|---|---|---|
| copilot_2026 | cached | keep_alive_30 | 51.4 / 19,476 | 51.1 / 19,511 | 49.6 / 19,301 |
| copilot_2026 | cached | keep_alive_60 | 60.1 / 32,478 | 59.9 / 32,569 | 58.3 / 32,233 |
| copilot_2026 | cached | markov1_top1 | 51.1 / 19,502 | 50.8 / 19,534 | 49.3 / 19,329 |
| copilot_2026 | cached | ours_old_tau0.6 | 53.1 / 21,280 | 52.8 / 21,322 | 50.7 / 21,083 |
| copilot_2026 | cached | ours_new | 69.4 / 46,411 | 69.6 / 47,311 | 64.8 / 45,556 |
| copilot_2026 | cached | oracle | 82.6 / 4,716 | 79.7 / 4,704 | 59.7 / 4,661 |
| copilot_2026 | uncached | keep_alive_30 | 49.2 / 19,991 | 47.4 / 20,479 | 44.7 / 20,109 |
| copilot_2026 | uncached | keep_alive_60 | 58.1 / 33,530 | 56.4 / 34,467 | 53.8 / 33,856 |
| copilot_2026 | uncached | markov1_top1 | 48.9 / 20,013 | 47.0 / 20,508 | 44.3 / 20,139 |
| copilot_2026 | uncached | ours_old_tau0.6 | 50.8 / 21,699 | 48.9 / 22,121 | 45.7 / 21,730 |
| copilot_2026 | uncached | ours_new | 68.8 / 50,723 | 67.4 / 53,100 | 60.6 / 49,984 |
| copilot_2026 | uncached | oracle | 68.6 / 4,706 | 57.8 / 4,715 | 40.3 / 4,654 |
| tau2_bench | cached | keep_alive_30 | 56.1 / 18,299 | 56.4 / 18,232 | 56.4 / 18,218 |
| tau2_bench | cached | keep_alive_60 | 60.5 / 30,531 | 60.8 / 30,376 | 60.8 / 30,363 |
| tau2_bench | cached | markov1_top1 | 66.4 / 19,742 | 66.4 / 19,764 | 63.7 / 19,500 |
| tau2_bench | cached | ours_old_tau0.6 | 58.8 / 18,758 | 58.2 / 18,760 | 58.2 / 18,746 |
| tau2_bench | cached | ours_new | 64.0 / 12,106 | 64.0 / 12,120 | 61.4 / 11,859 |
| tau2_bench | cached | oracle | 77.5 / 511 | 78.1 / 524 | 72.5 / 532 |
| tau2_bench | uncached | keep_alive_30 | 55.6 / 18,319 | 52.6 / 18,973 | 50.3 / 19,413 |
| tau2_bench | uncached | keep_alive_60 | 59.6 / 30,839 | 57.3 / 32,039 | 55.0 / 33,004 |
| tau2_bench | uncached | markov1_top1 | 64.3 / 19,575 | 61.4 / 19,768 | 59.4 / 19,730 |
| tau2_bench | uncached | ours_old_tau0.6 | 58.8 / 18,608 | 56.4 / 19,058 | 56.1 / 19,103 |
| tau2_bench | uncached | ours_new | 62.3 / 11,920 | 60.2 / 12,041 | 58.8 / 11,990 |
| tau2_bench | uncached | oracle | 68.4 / 511 | 53.2 / 503 | 38.9 / 499 |
| tracelab | cached | keep_alive_30 | 58.5 / 18,090 | 58.4 / 18,084 | 56.9 / 17,622 |
| tracelab | cached | keep_alive_60 | 66.3 / 29,220 | 66.2 / 29,245 | 64.8 / 28,658 |
| tracelab | cached | markov1_top1 | 58.5 / 18,100 | 58.4 / 18,094 | 56.9 / 17,632 |
| tracelab | cached | ours_old_tau0.6 | 60.5 / 25,804 | 60.4 / 25,799 | 58.6 / 25,241 |
| tracelab | cached | ours_new | 75.3 / 54,932 | 76.0 / 57,219 | 70.8 / 53,587 |
| tracelab | cached | oracle | 93.9 / 568 | 91.1 / 568 | 54.0 / 551 |
| tracelab | uncached | keep_alive_30 | 57.8 / 18,207 | 56.6 / 18,472 | 53.4 / 17,768 |
| tracelab | uncached | keep_alive_60 | 65.6 / 29,509 | 64.7 / 29,969 | 61.7 / 29,064 |
| tracelab | uncached | markov1_top1 | 57.8 / 18,220 | 56.6 / 18,488 | 53.4 / 17,784 |
| tracelab | uncached | ours_old_tau0.6 | 59.7 / 25,873 | 58.5 / 26,047 | 55.1 / 25,232 |
| tracelab | uncached | ours_new | 77.6 / 64,247 | 77.8 / 68,772 | 69.4 / 61,936 |
| tracelab | uncached | oracle | 81.9 / 568 | 67.8 / 569 | 34.0 / 529 |

## Equal-idle margin of our profile policy vs keep-alive (points)

| Source | Mode | kind: profile / best / above | aws: profile / best / above | aws_cap2_parallel_3: profile / best / above |
|---|---|---|---|---|
| copilot_2026 | cached | +5.2 / +5.2 / 9 of 20 | +5.4 / +5.4 / 9 of 20 | +2.4 / +2.4 / 4 of 20 |
| copilot_2026 | uncached | +5.7 / +5.7 / 10 of 20 | +5.7 / +5.7 / 10 of 20 | +1.9 / +1.9 / 4 of 20 |
| tau2_bench | cached | +18.5 / +18.5 / 17 of 18 | +18.1 / +18.1 / 17 of 18 | +15.7 / +15.7 / 17 of 18 |
| tau2_bench | uncached | +16.1 / +16.1 / 18 of 19 | +17.7 / +18.0 / 18 of 19 | +18.2 / +18.3 / 18 of 19 |
| tracelab | cached | +1.2 / +1.2 / 2 of 20 | +1.5 / +1.5 / 2 of 20 | -1.9 / -0.8 / 0 of 20 |
| tracelab | uncached | +2.2 / +2.2 / 2 of 20 | +2.4 / +2.4 / 2 of 20 | -2.4 / -0.9 / 0 of 20 |

## Scale-up queue and contention (`aws_cap2_parallel_3`)

| Source | Mode | Policy | queued % of starts | mean queue wait ms | overlapping starts % |
|---|---|---|---|---|---|
| copilot_2026 | cached | keep_alive_30 | 8.5 | 3600 | 20.3 |
| copilot_2026 | cached | markov1_top1 | 8.6 | 3593 | 20.5 |
| copilot_2026 | cached | ours_new | 38.3 | 4882 | 68.6 |
| copilot_2026 | cached | oracle | 17.3 | 3945 | 30.2 |
| copilot_2026 | uncached | keep_alive_30 | 15.9 | 3933 | 38.0 |
| copilot_2026 | uncached | markov1_top1 | 15.9 | 3921 | 38.4 |
| copilot_2026 | uncached | ours_new | 48.5 | 8217 | 75.9 |
| copilot_2026 | uncached | oracle | 32.5 | 5805 | 48.6 |
| tau2_bench | cached | keep_alive_30 | 0.0 | 0 | 4.7 |
| tau2_bench | cached | markov1_top1 | 0.0 | 0 | 27.9 |
| tau2_bench | cached | ours_new | 0.0 | 0 | 25.8 |
| tau2_bench | cached | oracle | 0.6 | 666 | 7.6 |
| tau2_bench | uncached | keep_alive_30 | 2.9 | 2460 | 22.4 |
| tau2_bench | uncached | markov1_top1 | 0.0 | 0 | 34.0 |
| tau2_bench | uncached | ours_new | 1.9 | 1476 | 32.7 |
| tau2_bench | uncached | oracle | 9.2 | 2994 | 35.1 |
| tracelab | cached | keep_alive_30 | 16.3 | 4394 | 39.9 |
| tracelab | cached | markov1_top1 | 16.3 | 4394 | 39.9 |
| tracelab | cached | ours_new | 55.3 | 7830 | 83.0 |
| tracelab | cached | oracle | 29.7 | 4339 | 43.0 |
| tracelab | uncached | keep_alive_30 | 23.8 | 5095 | 48.8 |
| tracelab | uncached | markov1_top1 | 23.8 | 5094 | 48.8 |
| tracelab | uncached | ours_new | 66.0 | 14051 | 88.4 |
| tracelab | uncached | oracle | 41.3 | 6804 | 56.7 |

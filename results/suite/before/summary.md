_CORRECTNESS run: real agent loop and real local LLM, tool handlers in-process, no cluster and no pods; scripted customer turns; timings are not results._

| Scenario | llama3.1:8b | qwen2.5:7b |
|---|---|---|
| new_booking | 6/6 | 3/6 |
| promo_booking | 0/6 | 0/6 |
| change_date | 6/6 | 0/6 |
| add_bag | 0/6 | 3/6 |
| cancel_refund | 4/6 | 2/6 |
| transfer | 6/6 | 6/6 |
| **all** | 22/36 | 14/36 |

Pass rate per rep (variance across reps):

- llama3.1:8b: rep 1: 11/18, rep 2: 11/18
- qwen2.5:7b: rep 1: 8/18, rep 2: 6/18

Failure reasons:

- llama3.1:8b: add_bag: bags 3 (expected 2) (x6)
- llama3.1:8b: cancel_refund: booking confirmed (expected cancelled) (x2)
- llama3.1:8b: promo_booking: 0 new bookings (expected 1) (x6)
- qwen2.5:7b: add_bag: bags 1 (expected 2) (x1)
- qwen2.5:7b: add_bag: bags 4 (expected 2) (x2)
- qwen2.5:7b: cancel_refund: booking confirmed (expected cancelled) (x2)
- qwen2.5:7b: cancel_refund: cancelled but not refunded (x2)
- qwen2.5:7b: change_date: flight not moved to 2026-12-07 (now 2026-12-05) (x4)
- qwen2.5:7b: change_date: flight not moved to 2026-12-08 (now 2026-12-05) (x2)
- qwen2.5:7b: new_booking: 0 new bookings (expected 1) (x1)
- qwen2.5:7b: new_booking: wrong flight SKA-SKB 2026-12-08 (x2)
- qwen2.5:7b: promo_booking: 0 new bookings (expected 1) (x4)
- qwen2.5:7b: promo_booking: promo None (expected SKYWINTER10) (x2)

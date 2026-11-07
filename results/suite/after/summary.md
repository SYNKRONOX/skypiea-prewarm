_CORRECTNESS run: real agent loop and real local LLM, tool handlers in-process, no cluster and no pods; scripted customer turns; timings are not results._

| Scenario | llama3.1:8b | qwen2.5:7b | qwen3:8b |
|---|---|---|---|
| new_booking | 9/9 | 6/9 | 9/9 |
| promo_booking | 9/9 | 7/9 | 3/9 |
| change_date | 9/9 | 4/9 | 9/9 |
| add_bag | 9/9 | 9/9 | 7/9 |
| cancel_refund | 9/9 | 7/9 | 9/9 |
| transfer | 9/9 | 9/9 | 9/9 |
| **all** | 54/54 | 42/54 | 46/54 |

Pass rate per rep (variance across reps):

- llama3.1:8b: rep 1: 18/18, rep 2: 18/18, rep 3: 18/18
- qwen2.5:7b: rep 1: 16/18, rep 2: 13/18, rep 3: 13/18
- qwen3:8b: rep 1: 15/18, rep 2: 15/18, rep 3: 16/18

Failure reasons:

- qwen2.5:7b: cancel_refund: booking confirmed (expected cancelled) (x2)
- qwen2.5:7b: change_date: flight not moved to 2027-01-24 (now 2027-01-23) (x2)
- qwen2.5:7b: change_date: flight not moved to 2027-01-25 (now 2027-01-23) (x3)
- qwen2.5:7b: new_booking: 0 new bookings (expected 1) (x3)
- qwen2.5:7b: promo_booking: 0 new bookings (expected 1) (x2)
- qwen3:8b: add_bag: bags 0 (expected 1) (x2)
- qwen3:8b: promo_booking: 0 new bookings (expected 1) (x6)

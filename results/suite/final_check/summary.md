_CORRECTNESS run: real agent loop and real local LLM, tool handlers in-process, no cluster and no pods; scripted customer turns; timings are not results._

| Scenario | llama3.1:8b |
|---|---|
| new_booking | 3/3 |
| promo_booking | 3/3 |
| change_date | 3/3 |
| add_bag | 3/3 |
| cancel_refund | 3/3 |
| transfer | 3/3 |
| **all** | 18/18 |

Pass rate per rep (variance across reps):

- llama3.1:8b: rep 1: 18/18

Failure reasons:


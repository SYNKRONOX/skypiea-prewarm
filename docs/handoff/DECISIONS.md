# Decisions log (autonomous Phase A, branch feat/console-support)

Priority order given: (1) honest numbers, existing results/defaults unchanged, (2) reversible
and safe, (3) finishes in time, (4) clearest to a non-expert.

| # | Time (UTC) | Decision | Why |
|---|---|---|---|
| 1 | 11:42 | Skypiea Air's database and per-session writes live in the console's agent code (`agent/skyair/`), not in the tool pods. Every tool call is still sent through BOTH gateways to the mapped servers (timing measured as before); the answer the LLM sees comes from the in-console mock database. | Tool pods scale to zero and each stack has its own pods: they cannot hold session state, and the two stacks would diverge. (1)(2)(3) |
| 2 | 11:42 | Skye's tools keep LLM-facing names of our own, mapped to classes the real `support` vocabulary knows: find/verify/get customer -> user_lookup; get_booking -> reservation_lookup; search_flights -> flight_search; change_flight, add_baggage -> reservation_modify; cancel_booking, issue_refund -> reservation_cancel; transfer_to_human -> escalate. | Prediction needs vocabulary classes; the support vocabulary has no refund or baggage class, so they map to the closest airline action (tau2 handles refunds within cancellations). (1) |

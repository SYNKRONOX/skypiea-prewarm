# Decisions log (autonomous Phase A, branch feat/console-support)

Priority order given: (1) honest numbers, existing results/defaults unchanged, (2) reversible
and safe, (3) finishes in time, (4) clearest to a non-expert.

| # | Time (UTC) | Decision | Why |
|---|---|---|---|
| 1 | 11:42 | Skypiea Air's database and per-session writes live in the console's agent code (`agent/skyair/`), not in the tool pods. Every tool call is still sent through BOTH gateways to the mapped servers (timing measured as before); the answer the LLM sees comes from the in-console mock database. | Tool pods scale to zero and each stack has its own pods: they cannot hold session state, and the two stacks would diverge. (1)(2)(3) |
| 2 | 11:42 | Skye's tools keep LLM-facing names of our own, mapped to classes the real `support` vocabulary knows: find/verify/get customer -> user_lookup; get_booking -> reservation_lookup; search_flights -> flight_search; change_flight, add_baggage -> reservation_modify; cancel_booking, issue_refund -> reservation_cancel; transfer_to_human -> escalate. | Prediction needs vocabulary classes; the support vocabulary has no refund or baggage class, so they map to the closest airline action (tau2 handles refunds within cancellations). (1) |
| 3 | 11:50 | The console opens on the Skypiea Air demo (Skye) by default (`CONSOLE_DEMO`, previous demos still selectable). | Clearest for a non-expert audience. (4) |
| 4 | 11:50 | Typing mode now sends the hint at the first keystroke AND re-sends it at Enter (no other change; results in results/live_console_leadtime were recorded before and stay as they are). | The lead-time grid showed typing (39-43 s) outlasting the 30 s warm window. (1)(2) |
| 5 | 11:50 | A tool name the model invents is still sent through both gateways as class `other` (outside the vocabulary: the predictor abstains), labelled "unknown tool: no prediction, normal behaviour"; the model is told the tool does not exist. | Requested behaviour; shows the platform works without a prediction. (4) |

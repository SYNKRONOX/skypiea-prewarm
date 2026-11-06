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
| 6 | 11:55 | Money: "$0.00" for zero, "<$0.00001" below that, five decimals below one cent (e.g. "$0.00003"), else two decimals; never scientific notation. Durations: seconds with 2 decimals, below 1 s whole milliseconds (unchanged). | Readable; the two requested forms both occur. (4) |
| 7 | 11:55 | Robust console links: every port-forward runs in a restart loop in its own process group (restart within ~1 s when its pod is replaced); the console checks both gateways every 2 s and shows a warning while one is down. The predictor's health already goes through the API server's service proxy. | Reversible, no new component in the cluster. (2)(3) |

# Backup set W1 night (2026-11-07): RECORDED fallback for a talk without cluster or network

**Recorded** demo run, not a result: one live conversation, the Scenario button "New customer
books a trip", llama3.1:8b, hint off, real kind cluster with both stacks, branch
feat/demo-realism (synthetic India-centred world, guardrails). Use these files if the live
demo cannot run; say that they are a recording.

| File | What |
|---|---|
| `screen_light.png`, `screen_dark.png` | the console at the end: Nova Quill (new customer) booked SKY142 BLR→GOI on Friday 15 January 2027 (demo-world date), Saver INR 5,670, then one extra bag INR 1,800; "Scripted customer · turn N" labels; booking record with the quote, the booking and the bag |
| `screen_details.png` | the same with the Details drawer (Proof panel, Guardrails) |
| `story_skyair_act1__llama3.1_8b.json`, `*_calls.csv`, `*_turns.csv`, `*_state.json` | the console's report, CSV exports and full state |

What happened (from the state file): turn 1 one find_customer call was refused by argument
validation (the model sent the string "None" for the email) and retried correctly (this
case is now treated as "not given"); book_flight was first run as a quote, then booked after
"Yes, I confirm the booking"; the bag was added after the customer's yes. 6/6 calls succeeded
on both stacks. Normal waited 15.68 s on tools, Skypiea 9.62 s; Skypiea kept pods idle 113.8
pod-s vs 31.6 (the cost side). No truth badge fired.

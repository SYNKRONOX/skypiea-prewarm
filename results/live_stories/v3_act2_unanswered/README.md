# Act 2 in the v3 batch, with the old script (kept as recorded)

Tara Patel has two active bookings (5TGTBP, 8RK2RN). The old Act 2 script never said which
one. llama3.1:8b asked "which one" (correct) and its change call without a code was refused
("which_booking"); qwen2.5:7b asked which one, then, without an answer, called change_flight
with one of the two codes itself (the customer sidebar lists both) and the change succeeded. Act 2 now names
5TGTBP and was re-run (../story_skyair_act2__*).

"""PRD Scenario A: customer-support refund (order_lookup -> policy_check -> refund_issue)."""

from __future__ import annotations

import random
from typing import Any

from agent.tasks import FINISH_TOOL, Task, fn

SYSTEM = """You are a customer-support agent for an online shop. You resolve refund requests \
by calling tools; you never invent order data.
Tools:
- order_lookup(order_id): fetch an order (status, items, total, payment method).
- policy_check(request, order_status): check the refund policy for this request and order \
status; returns a verdict.
- refund_issue(order_id, product_ids): issue the refund for a delivered order.
- finish(summary): call once when the case is resolved.
Work step by step and call one tool at a time. Only issue a refund the policy allows."""

REQUESTS = [
    "Hi, order {oid} arrived with a cracked screen. I want my money back.",
    "My order {oid} was delivered but the shoes are the wrong size. Can I get a refund?",
    "I'd like a refund for order {oid}; the blender stopped working after two days.",
    "Order {oid} came damaged in the box. Please refund it.",
    "I changed my mind about order {oid}. Can you refund me?",
    "The headphones in order {oid} are missing a part. I want a refund.",
    "Please refund order {oid}, the jacket is torn.",
    "Order {oid} is not what I ordered. Refund please.",
    "My order {oid} arrived late and I no longer need it. Can I get my money back?",
    "The lamp in order {oid} does not turn on. Refund it, please.",
]


class SupportRefund(Task):
    def _oid(self, i: int) -> str:
        return f"#W{random.Random(1000 + i).randrange(1000000, 9999999)}"

    def user_prompt(self, i: int) -> str:
        return REQUESTS[i % len(REQUESTS)].format(oid=self._oid(i))

    def scripted(self, i: int) -> list[tuple[str, dict[str, Any]]]:
        oid = self._oid(i)
        return [("order_lookup", {"order_id": oid}),
                ("policy_check", {"request": "refund damaged item", "order_status": "delivered"}),
                ("refund_issue", {"order_id": oid})]


TASK = SupportRefund(
    scenario="A", name="support_refund", system=SYSTEM,
    tools=[
        fn("order_lookup", "Look up an order by id.",
           {"order_id": {"type": "string", "description": "order id, e.g. #W1234567"}},
           ["order_id"]),
        fn("policy_check", "Check the refund policy for a customer request.",
           {"request": {"type": "string", "description": "what the customer asks for"},
            "order_status": {"type": "string", "description": "status from order_lookup"}},
           ["request"]),
        fn("refund_issue", "Refund products of a delivered order (default: all items).",
           {"order_id": {"type": "string"},
            "product_ids": {"type": "array", "items": {"type": "string"}}},
           ["order_id"]),
        FINISH_TOOL],
    start="order_lookup",
    # PRD graph (configs/model/workflow_graphs.yaml, branching): after order_lookup ->
    # policy_check | refund_issue | end; after policy_check -> refund_issue | end
    edges={"order_lookup": {"policy_check", "refund_issue", "<end>"},
           "policy_check": {"refund_issue", "<end>"},
           "refund_issue": {"<end>"}},
    canonical=["order_lookup", "policy_check", "refund_issue"])

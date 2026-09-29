"""Tests for the ecommerce world.

WorldContract (from the opsharness core) checks the generic promises: the
correct plan scores 1.0, doing nothing scores 0.0, approvals get enforced,
the harness survives injected model slips, and the world scores 1.0 over MCP.
The wrong agents below are ecommerce-specific mistakes a real model could make,
and each one has to lose points.
"""
import unittest

from opsharness.testing import WorldContract, play, strip_approvals
from opsharness_ecommerce.world import EcommerceEnv

SEEDS = range(100)


class Contract(WorldContract, unittest.TestCase):
    world = EcommerceEnv


class WrongAgents(unittest.TestCase):
    def test_ecommerce_acts_on_every_exception(self):
        for s in SEEDS:
            env = EcommerceEnv(s)
            plan = env.oracle_plan()
            for oid in env.no_action:
                plan += env.gated_call("refund_order", {"order_id": oid})
            self.assertLess(play(env, plan)["score"], 1.0, f"seed {s}")
    def test_ecommerce_ignores_signature_rule(self):
        hit = 0
        for s in SEEDS:
            env = EcommerceEnv(s)
            plan, changed = [], False
            for t, a in env.oracle_plan():
                if t == "buy_label":
                    o = env.orders[a["order_id"]]
                    ok = [c for c in env.carriers if o["country"] in c["countries"] and o["weight_kg"] <= c["max_kg"]]
                    cheap = min(ok, key=lambda c: env._rate(c, o["weight_kg"]))["carrier"]
                    changed |= cheap != a["carrier"]
                    a = {**a, "carrier": cheap}
                plan.append((t, a))
            if changed:
                hit += 1
                self.assertLess(play(env, plan)["score"], 1.0, f"seed {s}")
        self.assertGreater(hit, 10)
    def test_ecommerce_assumes_30_day_window(self):
        hit = 0
        for s in SEEDS:
            env = EcommerceEnv(s)
            if env.window == 30:
                continue
            plan, changed = [], False
            for t, a in strip_approvals(env.oracle_plan()):
                if t == "resolve_return" and a["decision"] == "deny":
                    rt = next(x for x in env.returns if x["return_id"] == a["return_id"])
                    o = env.orders[rt["order_id"]]
                    if not o["final_sale"] and o["days_since_delivery"] <= 30:
                        a = {**a, "decision": "return_label"}
                        changed = True
                plan.append((t, a))
            if changed:
                hit += 1
                env.refund_limit = 10 ** 9
                self.assertLess(play(env, plan)["score"], 1.0, f"seed {s}")
        self.assertGreater(hit, 5)


if __name__ == "__main__":
    unittest.main()

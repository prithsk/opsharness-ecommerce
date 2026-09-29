"""Post-purchase operations for an online store (Pango-style).

The merchant writes its policy in plain English, and the policy numbers
change per seed. The same order can have a different right answer under a
different policy, so an agent has to read the policy, not recall one.
"""
from opsharness.core import Env, Tool, ToolError

CARRIERS = [
    {"carrier": "PostNord", "countries": ["SE", "NO", "DK", "FI"], "max_kg": 20, "signature": False,
     "base": 5.9, "per_kg": 1.2},
    {"carrier": "Budbee", "countries": ["SE", "NL", "DE"], "max_kg": 10, "signature": False,
     "base": 4.5, "per_kg": 0.9},
    {"carrier": "DHL Express", "countries": ["SE", "NO", "DK", "FI", "NL", "DE", "GB", "US"], "max_kg": 30,
     "signature": True, "base": 14.0, "per_kg": 2.5},
    {"carrier": "UPS", "countries": ["SE", "NL", "DE", "GB", "US"], "max_kg": 30, "signature": True,
     "base": 11.0, "per_kg": 3.1},
]
PRODUCTS = [("SKU-TRAIL-40", "Trail pack 40L", 129.0, 1.4), ("SKU-DOWN-JKT", "Down jacket", 249.0, 0.9),
            ("SKU-TENT-2P", "2P tent", 389.0, 2.6), ("SKU-STOVE", "Camp stove", 79.0, 0.6),
            ("SKU-BOOT-GTX", "GTX boots", 199.0, 1.8), ("SKU-KAYAK-PDL", "Kayak paddle", 159.0, 1.1),
            ("SKU-BENCH", "Folding bench", 95.0, 12.5), ("SKU-CHAIR-4", "Camp chair set", 219.0, 14.0)]
COUNTRIES = ["SE", "NO", "NL", "DE", "GB", "US"]


class EcommerceEnv(Env):
    name = "ecommerce"

    def build(self):
        r = self.rng
        self.window = r.choice([14, 30])
        self.sig_threshold = r.choice([150, 250])
        self.lost_days = r.choice([7, 10])
        self.refund_limit = 150
        self.carriers = []
        for c in CARRIERS:
            f = r.uniform(0.85, 1.15)
            self.carriers.append({**c, "base": round(c["base"] * f, 2), "per_kg": round(c["per_kg"] * f, 2)})
        self.stock = {sku: r.choice([0, 0, 3, 12]) for sku, *_ in PRODUCTS}
        self.orders = {}
        self.expected = {}   # item key -> expected action tuple
        self.no_action = set()  # order ids that must be left alone
        n = 5000
        # unshipped orders
        self.unshipped = []
        for _ in range(4):
            n += r.randint(1, 40)
            sku, title, price, kg = r.choice(PRODUCTS)
            qty = r.randint(1, 2)
            oid = f"#{n}"
            o = {"order_id": oid, "sku": sku, "title": title, "qty": qty, "value": price * qty,
                 "weight_kg": round(kg * qty, 1), "country": r.choice(COUNTRIES)}
            self.orders[oid] = o
            self.unshipped.append(oid)
            self.expected[("label", oid)] = ("label", self._best_carrier(o))
        # returns
        self.returns = []
        kinds = ["changed_mind_in", "changed_mind_out", "damaged", "final_sale", "final_sale_damaged", "wrong_item"]
        picks = r.sample(kinds, 4)
        if not any(k in ("damaged", "final_sale_damaged", "wrong_item") for k in picks):
            picks[0] = "damaged"
        for i, k in enumerate(picks):
            n += r.randint(1, 40)
            sku, title, price, kg = r.choice(PRODUCTS)
            oid = f"#{n}"
            final = k.startswith("final_sale")
            reason = {"changed_mind_in": "changed_mind", "changed_mind_out": "changed_mind",
                      "damaged": "damaged", "final_sale": "changed_mind",
                      "final_sale_damaged": "damaged", "wrong_item": "wrong_item"}[k]
            days = self.window + r.randint(1, 20) if k == "changed_mind_out" else r.randint(1, self.window - 1)
            self.orders[oid] = {"order_id": oid, "sku": sku, "title": title, "qty": 1, "value": price,
                                "final_sale": final, "days_since_delivery": days}
            rid = f"RMA-{300 + i}"
            self.returns.append({"return_id": rid, "order_id": oid, "reason": reason,
                                 "customer_note": self._note(reason)})
            if reason in ("damaged", "wrong_item"):
                exp = "refund_no_return"
            elif final or days > self.window:
                exp = "deny"
            else:
                exp = "return_label"
            self.expected[("return", rid)] = ("return", exp)
        # make sure at least one refund crosses the approval limit
        refunds = [rt for rt in self.returns if self.expected[("return", rt["return_id"])][1] == "refund_no_return"]
        o = self.orders[refunds[0]["order_id"]]
        if o["value"] <= self.refund_limit:
            o["sku"], o["title"], o["value"] = "SKU-TENT-2P", "2P tent", 389.0
        # delivery exceptions
        self.exceptions = []
        shapes = ["lost", "stale_over", "stale_under"]
        r.shuffle(shapes)
        for shape in shapes:
            n += r.randint(1, 40)
            sku, title, price, kg = r.choice(PRODUCTS)
            oid = f"#{n}"
            if shape == "lost":
                status, days = "lost_by_carrier", r.randint(1, 5)
            elif shape == "stale_over":
                status, days = "in_transit", self.lost_days + r.randint(0, 6)
            else:
                status, days = "in_transit", r.randint(2, self.lost_days - 1)
            self.orders[oid] = {"order_id": oid, "sku": sku, "title": title, "qty": 1, "value": price}
            self.exceptions.append({"order_id": oid, "carrier": r.choice(["PostNord", "DHL Express", "UPS"]),
                                    "last_status": status, "days_since_last_scan": days})
            if shape == "stale_under":
                self.no_action.add(oid)
            else:
                self.expected[("exception", oid)] = ("exception", "reship" if self.stock[sku] > 0 else "refund")
        self.labels, self.resolved, self.reshipped, self.refunded = {}, {}, [], []

    def _note(self, reason):
        return {"changed_mind": "Doesn't fit my style, want to send it back.",
                "damaged": "Arrived with a torn seam and a broken buckle.",
                "wrong_item": "I ordered a different size than what came."}[reason]

    def _rate(self, c, kg):
        return round(c["base"] + c["per_kg"] * kg, 2)

    def _best_carrier(self, o):
        ok = [c for c in self.carriers if o["country"] in c["countries"] and o["weight_kg"] <= c["max_kg"]
              and (c["signature"] or o["value"] <= self.sig_threshold)]
        ok.sort(key=lambda c: self._rate(c, o["weight_kg"]))
        return ok[0]["carrier"]

    def policy_text(self):
        return (
            "# Store policy (written by the merchant)\n\n"
            "## Shipping\n"
            f"Ship every order with the cheapest carrier that serves the destination country and the "
            f"parcel weight. Orders worth more than ${self.sig_threshold} must go with a carrier that "
            f"collects a signature.\n\n"
            "## Returns\n"
            f"Customers can return items for any reason within {self.window} days of delivery, and we "
            f"send a prepaid return label. After {self.window} days we decline change-of-mind returns. "
            "Final-sale items cannot be returned for change of mind. If an item arrived damaged or we "
            "sent the wrong item, refund it in full and do not ask for it back, final sale or not.\n\n"
            "## Delivery problems\n"
            f"If the carrier marks a parcel lost, or it has had no scan for {self.lost_days} days or more, "
            "reship the order when the SKU is in stock, otherwise refund it. Parcels still inside that "
            "window are left alone.\n\n"
            "## Money\n"
            f"Any refund over ${self.refund_limit} needs a manager's approval first.\n")

    # ---- tools -----------------------------------------------------------
    def _order(self, oid):
        if oid not in self.orders:
            raise ToolError(f"no order '{oid}'")
        return self.orders[oid]

    def get_policy(self):
        return {"policy": self.policy_text()}

    def list_unshipped(self):
        return {"orders": [self.orders[o] for o in self.unshipped if o not in self.labels]}

    def list_carriers(self):
        return {"carriers": self.carriers, "rate_formula": "base + per_kg * weight_kg"}

    def buy_label(self, order_id, carrier):
        o = self._order(order_id)
        if order_id not in self.unshipped:
            raise ToolError(f"{order_id} is not waiting to ship")
        if order_id in self.labels:
            raise ToolError(f"{order_id} already has a label from {self.labels[order_id]}")
        c = next((c for c in self.carriers if c["carrier"] == carrier), None)
        if c is None:
            raise ToolError(f"unknown carrier '{carrier}'")
        if o["country"] not in c["countries"]:
            raise ToolError(f"{carrier} does not deliver to {o['country']}")
        if o["weight_kg"] > c["max_kg"]:
            raise ToolError(f"{carrier} max weight is {c['max_kg']} kg")
        self.labels[order_id] = carrier
        return {"order_id": order_id, "carrier": carrier, "cost": self._rate(c, o["weight_kg"])}

    def get_order(self, order_id):
        return self._order(order_id)

    def list_returns(self):
        return {"returns": [rt for rt in self.returns if rt["return_id"] not in self.resolved]}

    def resolve_return(self, return_id, decision):
        rt = next((x for x in self.returns if x["return_id"] == return_id), None)
        if rt is None:
            raise ToolError(f"no return '{return_id}'")
        if return_id in self.resolved:
            raise ToolError(f"{return_id} was already resolved as {self.resolved[return_id]}")
        self.resolved[return_id] = decision
        return {"return_id": return_id, "decision": decision}

    def _return_gated(self, args):
        rt = next((x for x in self.returns if x["return_id"] == args.get("return_id")), None)
        return bool(rt) and args.get("decision") == "refund_no_return" and \
            self.orders[rt["order_id"]]["value"] > self.refund_limit

    def list_delivery_exceptions(self):
        return {"exceptions": self.exceptions}

    def check_stock(self, sku):
        if sku not in self.stock:
            raise ToolError(f"unknown sku '{sku}'")
        return {"sku": sku, "on_hand": self.stock[sku]}

    def reship(self, order_id):
        o = self._order(order_id)
        if self.stock[o["sku"]] <= 0:
            raise ToolError(f"{o['sku']} is out of stock")
        self.stock[o["sku"]] -= 1
        self.reshipped.append(order_id)
        return {"order_id": order_id, "status": "replacement created"}

    def refund_order(self, order_id):
        o = self._order(order_id)
        self.refunded.append(order_id)
        return {"order_id": order_id, "refunded": o["value"]}

    def env_tools(self):
        s = {"type": "string"}
        return [
            Tool("get_policy", "The merchant's written operations policy.", {}, self.get_policy),
            Tool("list_unshipped", "Paid orders waiting for a shipping label.", {}, self.list_unshipped),
            Tool("list_carriers", "Carriers with countries served, weight limits, signature and rates.",
                 {}, self.list_carriers),
            Tool("buy_label", "Buy a shipping label for an order.", {"order_id": s, "carrier": s},
                 self.buy_label, ["order_id", "carrier"]),
            Tool("get_order", "Order details including value, final-sale flag and days since delivery.",
                 {"order_id": s}, self.get_order, ["order_id"]),
            Tool("list_returns", "Open return requests.", {}, self.list_returns),
            Tool("resolve_return", "Resolve a return request.",
                 {"return_id": s, "decision": {"type": "string",
                                               "enum": ["return_label", "refund_no_return", "deny"]}},
                 self.resolve_return, ["return_id", "decision"], gated=self._return_gated),
            Tool("list_delivery_exceptions", "Shipped parcels the carrier flagged or stopped scanning.",
                 {}, self.list_delivery_exceptions),
            Tool("check_stock", "Units on hand for a SKU.", {"sku": s}, self.check_stock, ["sku"]),
            Tool("reship", "Send a replacement for an order.", {"order_id": s}, self.reship, ["order_id"]),
            Tool("refund_order", "Refund an order in full.", {"order_id": s}, self.refund_order, ["order_id"],
                 gated=lambda a: a.get("order_id") in self.orders
                 and self.orders[a["order_id"]]["value"] > self.refund_limit),
        ]

    def task(self):
        return ("Clear today's post-purchase queue: ship the unshipped orders, resolve the open returns, "
                "and handle the delivery exceptions. Follow the store policy exactly.")

    # ---- answer key and scoring -------------------------------------------
    def oracle_plan(self):
        plan = []
        for (kind, key), (_, want) in self.expected.items():
            if kind == "label":
                plan.append(("buy_label", {"order_id": key, "carrier": want}))
            elif kind == "return":
                plan += self.gated_call("resolve_return", {"return_id": key, "decision": want})
            else:
                plan += self.gated_call("reship" if want == "reship" else "refund_order", {"order_id": key})
        return plan

    def score(self):
        correct, wrong = 0, 0
        for (kind, key), (_, want) in self.expected.items():
            if kind == "label":
                correct += self.labels.get(key) == want
            elif kind == "return":
                correct += self.resolved.get(key) == want
            else:
                did = [a for a, lst in (("reship", self.reshipped), ("refund", self.refunded)) for x in lst if x == key]
                correct += did == [want]
        touched = self.reshipped + self.refunded
        wrong += sum(1 for oid in touched if oid in self.no_action)
        wrong += sum(1 for oid in touched if ("exception", oid) not in self.expected and oid not in self.no_action)
        n = len(self.expected)
        return {"score": round(max(0.0, correct / n - 0.25 * wrong), 4),
                "details": {"items_correct": correct, "items": n, "wrong_actions": wrong,
                            "policy": {"window": self.window, "signature_over": self.sig_threshold,
                                       "lost_days": self.lost_days}}}

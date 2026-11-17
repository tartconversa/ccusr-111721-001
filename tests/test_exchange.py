import tempfile
import unittest
from pathlib import Path

from exchange import Exchange


class ExchangeFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register("flow", "机构甲", "甲地", "restricted", [("count", "通行计数", "integer", False)], "2026-11-01")
        self.service.catalog.ingest("item-1", "flow", {"count": 8}, "2026-11-02")
        self.service.grants.issue("g-1", "flow", "机构乙", "运行协调", "2026-12-31", "2026-11-03")
        self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-11-03", "机构甲")
        self.revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-04", "机构甲")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_delivery_keeps_revision_and_request_identity(self):
        desk = self.service.delivery
        receipt = desk.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05")
        self.assertEqual(receipt["product_revision"], self.revision)
        self.assertEqual(receipt, desk.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05"))
        with self.assertRaises(ValueError):
            desk.send("d-1", "report", self.revision, "其他机构", "运行协调", "2026-11-05")

    def test_grant_and_policy_gate_delivery(self):
        with self.assertRaises(PermissionError):
            self.service.delivery.send("d-2", "report", self.revision, "陌生机构", "运行协调", "2026-11-05")
        self.service.policies.set("甲地", "restricted", "运行协调", "deny", "2026-11-06", "机构甲")
        with self.assertRaises(PermissionError):
            self.service.delivery.send("d-3", "report", self.revision, "机构乙", "运行协调", "2026-11-07")
        self.assertTrue(self.service.history("report"))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.service.catalog.ingest("item-2", "flow", {"unknown": 9}, "2026-11-03")


class HandoverFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register("flow", "机构甲", "甲地", "restricted", [("count", "通行计数", "integer", False)], "2026-11-01")
        self.service.catalog.ingest("item-1", "flow", {"count": 8}, "2026-11-02")
        self.service.grants.issue("g-1", "flow", "机构乙", "运行协调", "2026-12-31", "2026-11-03")
        self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-11-03", "机构甲")
        self.revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-04", "机构甲")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def _open(self, handover_id="h-1"):
        return self.service.handovers.open(handover_id, "report", self.revision, "机构甲", "机构乙", "运行协调", "2026-11-05")

    def test_pending_until_both_parties_confirm(self):
        desk = self.service.handovers
        handover = self._open()
        self.assertEqual(handover["state"], "pending")
        desk.confirm("h-1", "机构甲", "2026-11-05")
        self.assertEqual(desk.lookup("h-1")["state"], "pending")
        self.assertEqual(desk.lookup("h-1")["decisions"]["机构甲"]["decision"], "confirmed")
        desk.confirm("h-1", "机构乙", "2026-11-05")
        completed = desk.lookup("h-1")
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(set(completed["decisions"]), {"机构甲", "机构乙"})

    def test_each_party_keeps_own_basis_and_scope_is_final_only_when_completed(self):
        desk = self.service.handovers
        self._open()
        desk.confirm("h-1", "机构甲", "2026-11-05")
        desk.confirm("h-1", "机构乙", "2026-11-05")
        record = desk.lookup("h-1")
        collection = record["collection"][0]
        self.assertEqual(collection["schema_version"], 1)
        self.assertEqual(collection["fields"], ["count"])
        self.assertTrue(collection["digest"])
        basis_b = record["decisions"]["机构乙"]["policy_basis"]["flow"]
        self.assertEqual(basis_b, {"decision": "allow", "revision": 1})
        self.assertEqual(record["decisions"]["机构乙"]["grant_basis"]["flow"], "g-1")
        self.assertEqual(record["decisions"]["机构甲"]["grant_basis"]["flow"], "owner")
        self.assertEqual(record["usable_scope"]["sources"], ["flow"])
        self.assertEqual(record["usable_scope"]["valid_until"], "2026-12-31")
        self.assertEqual(record["usable_scope"]["input_digest"], record["input_digest"])

    def test_repeated_receipts_do_not_create_second_delivery(self):
        desk = self.service.handovers
        first = self._open()
        second = self._open()
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            desk.open("h-1", "report", self.revision, "机构甲", "其他机构", "运行协调", "2026-11-05")
        desk.confirm("h-1", "机构甲", "2026-11-05")
        # 重复回执返回同一记录，不产生第二条决定。
        desk.confirm("h-1", "机构甲", "2026-11-05")
        desk.confirm("h-1", "机构乙", "2026-11-05")
        rows = self.service.connection.execute(
            "SELECT count(*) FROM handover_decisions WHERE handover_id='h-1'").fetchone()[0]
        self.assertEqual(rows, 2)

    def test_one_party_rejection_keeps_other_confirmation_and_blocks_completion(self):
        desk = self.service.handovers
        self._open()
        desk.confirm("h-1", "机构甲", "2026-11-05")
        desk.reject("h-1", "机构乙", "2026-11-06", note="现场核验未过")
        record = desk.lookup("h-1")
        self.assertEqual(record["state"], "rejected")
        self.assertEqual(record["decisions"]["机构甲"]["decision"], "confirmed")
        self.assertEqual(record["decisions"]["机构乙"]["decision"], "rejected")
        self.assertTrue(record["decisions"]["机构乙"]["reasons"])
        self.assertIsNone(record["usable_scope"])
        # 已确认方不能改写成拒收，拒收方也不能再确认，终态不可逆。
        with self.assertRaises(ValueError):
            desk.reject("h-1", "机构甲", "2026-11-06")
        with self.assertRaises(ValueError):
            desk.confirm("h-1", "机构乙", "2026-11-06")

    def test_policy_changing_mid_handover_does_not_rewrite_confirmed_basis(self):
        desk = self.service.handovers
        self._open()
        desk.confirm("h-1", "机构甲", "2026-11-05")
        # 交接途中用途规则改为 deny。
        self.service.policies.set("甲地", "restricted", "运行协调", "deny", "2026-11-06", "机构甲")
        with self.assertRaises(PermissionError):
            desk.confirm("h-1", "机构乙", "2026-11-06")
        record = desk.lookup("h-1")
        # 甲方已确认依据保留在 revision=1，交接未被改写成完成。
        self.assertEqual(record["state"], "pending")
        self.assertEqual(record["decisions"]["机构甲"]["policy_basis"]["flow"]["revision"], 1)
        self.assertNotIn("机构乙", record["decisions"])
        # 规则恢复后乙方仍可确认，依据记录的是恢复后的新版本。
        revision_two = self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-11-07", "机构甲")
        desk.confirm("h-1", "机构乙", "2026-11-07")
        self.assertEqual(desk.lookup("h-1")["state"], "completed")
        self.assertEqual(desk.lookup("h-1")["decisions"]["机构乙"]["policy_basis"]["flow"]["revision"], revision_two)

    def test_party_without_grant_cannot_confirm(self):
        # 非交接参与方不能落判断。
        with self.assertRaises(ValueError):
            self._open()
            self.service.handovers.confirm("h-1", "陌生机构", "2026-11-05")
        # 乙方许可过期后，确认被拒且不落确认记录。
        self.service.grants.issue("g-2", "flow", "机构丙", "运行协调", "2026-10-01", "2026-11-03")
        self.service.handovers.open("h-3", "report", self.revision, "机构甲", "机构丙", "运行协调", "2026-11-05")
        with self.assertRaises(PermissionError):
            self.service.handovers.confirm("h-3", "机构丙", "2026-11-05")
        self.assertEqual(self.service.handovers.lookup("h-3")["state"], "pending")

    def test_lookup_unknown_handover(self):
        with self.assertRaises(KeyError):
            self.service.handovers.lookup("missing")


if __name__ == "__main__":
    unittest.main()

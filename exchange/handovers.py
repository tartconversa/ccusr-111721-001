import hashlib
import json

from .audit import write
from .grants import Grants
from .policy import Policies
from .products import Products


class Handovers:
    """两地口岸共用一次资料采集时的双方交接台账。

    同一次采集（产品修订）在两方各自确认前保持 pending；每一方独立作出
    confirmed/rejected 判断，并分别保留许可判断依据。任一方拒收即 rejected，
    终态记录不可改写，因此一方已经确认的记录不会被改写成双方已完成。
    """

    def __init__(self, connection):
        self.connection = connection
        self.grants = Grants(connection)
        self.policies = Policies(connection)
        self.products = Products(connection)

    def open(self, handover_id, product_id, revision, party_a, party_b, purpose, at):
        existing = self.connection.execute("SELECT * FROM handovers WHERE id=?", (handover_id,)).fetchone()
        if existing:
            if (existing["product_id"], existing["product_revision"], existing["party_a"],
                    existing["party_b"], existing["purpose"]) != (product_id, revision, party_a, party_b, purpose):
                raise ValueError("交接编号已用于其他交接请求")
            return self.lookup(handover_id)
        if not party_a or not party_b or not purpose:
            raise ValueError("交接方与用途不能为空")
        if party_a == party_b:
            raise ValueError("交接两方必须是不同机构")
        product = self.connection.execute("SELECT state FROM products WHERE id=? AND revision=?", (product_id, revision)).fetchone()
        if product is None or product["state"] != "draft":
            raise ValueError("数据产品修订不可交接")
        inputs = self.products.inputs(product_id, revision)
        if not inputs:
            raise ValueError("数据产品缺少输入")
        digest = hashlib.sha256("|".join(asset["digest"] for asset in inputs).encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO handovers(id,product_id,product_revision,party_a,party_b,purpose,state,opened_at,input_digest) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (handover_id, product_id, revision, party_a, party_b, purpose, "pending", at, digest),
            )
            write(self.connection, at, party_a, "handover.open", handover_id,
                  {"product": product_id, "revision": revision, "party_b": party_b, "purpose": purpose})
        return self.lookup(handover_id)

    def confirm(self, handover_id, party, at):
        handover = self._require_handover(handover_id)
        self._require_party(handover, party)
        decision_row = self._decision(handover_id, party)
        if decision_row is not None:
            if decision_row["decision"] == "confirmed" and handover["state"] == "pending":
                # 重复回执：待交接期间重复确认返回同一记录，不生成第二次交付。
                return self.lookup(handover_id)
            raise ValueError("该方已作判断或交接已终态，不能再确认")
        if handover["state"] != "pending":
            raise ValueError("交接已终态，不能再确认")
        inputs = self.products.inputs(handover["product_id"], handover["product_revision"])
        grant_basis, policy_basis, reasons = self._evaluate(inputs, party, handover["purpose"], at)
        if reasons:
            with self.connection:
                write(self.connection, at, party, "handover.deny", handover_id, {"reasons": reasons})
            raise PermissionError(reasons)
        with self.connection:
            self._record(handover_id, party, "confirmed", at, grant_basis, policy_basis,
                         handover["input_digest"], [])
            if self._confirmed_count(handover_id) == 2:
                self.connection.execute("UPDATE handovers SET state='completed' WHERE id=? AND state='pending'", (handover_id,))
            write(self.connection, at, party, "handover.confirm", handover_id,
                  {"state": self._state(handover_id)})
        return self.lookup(handover_id)

    def reject(self, handover_id, party, at, note=None):
        handover = self._require_handover(handover_id)
        self._require_party(handover, party)
        decision_row = self._decision(handover_id, party)
        if decision_row is not None:
            if decision_row["decision"] == "rejected":
                return self.lookup(handover_id)
            raise ValueError("该方已确认，不能改写为拒收")
        if handover["state"] != "pending":
            raise ValueError("交接已终态，不能再拒收")
        inputs = self.products.inputs(handover["product_id"], handover["product_revision"])
        # 拒收同样保留判断依据快照；显式拒收即使检查通过也按拒收落账。
        grant_basis, policy_basis, eval_reasons = self._evaluate(inputs, party, handover["purpose"], at)
        reasons = eval_reasons or [{"reason": "rejected", "note": note or "一方拒收"}]
        with self.connection:
            self._record(handover_id, party, "rejected", at, grant_basis, policy_basis,
                         handover["input_digest"], reasons)
            self.connection.execute("UPDATE handovers SET state='rejected' WHERE id=? AND state='pending'", (handover_id,))
            write(self.connection, at, party, "handover.reject", handover_id, {"reasons": reasons})
        return self.lookup(handover_id)

    def lookup(self, handover_id):
        handover = self._require_handover(handover_id)
        inputs = self.products.inputs(handover["product_id"], handover["product_revision"])
        collection = []
        for asset in inputs:
            field_names = [row[0] for row in self.connection.execute(
                "SELECT name FROM fields WHERE source_id=? ORDER BY name", (asset["source_id"],))]
            collection.append({
                "asset_id": asset["id"],
                "source_id": asset["source_id"],
                "owner": asset["owner"],
                "jurisdiction": asset["jurisdiction"],
                "classification": asset["classification"],
                "schema_version": asset["schema_version"],
                "fields": field_names,
                "digest": asset["digest"],
            })
        decision_rows = self.connection.execute(
            "SELECT * FROM handover_decisions WHERE handover_id=? ORDER BY party", (handover_id,)).fetchall()
        decisions = {row["party"]: self._decision_view(row) for row in decision_rows}
        return {
            "handover_id": handover["id"],
            "state": handover["state"],
            "purpose": handover["purpose"],
            "product_id": handover["product_id"],
            "product_revision": handover["product_revision"],
            "input_digest": handover["input_digest"],
            "parties": [handover["party_a"], handover["party_b"]],
            "collection": collection,
            "decisions": decisions,
            "usable_scope": self._scope(handover, inputs, decisions),
        }

    # ---- 内部辅助 --------------------------------------------------------

    def _evaluate(self, inputs, party, purpose, at):
        grant_basis, policy_basis, reasons = {}, {}, []
        for asset in inputs:
            source_id = asset["source_id"]
            if asset["state"] != "active" or not asset["source_active"]:
                reasons.append({"source": source_id, "reason": "inactive"})
            if asset["owner"] != party:
                grant_id = self.grants.permits(source_id, party, purpose, at)
                if grant_id:
                    grant_basis[source_id] = grant_id
                else:
                    reasons.append({"source": source_id, "reason": "grant"})
            else:
                # 来源所有方无需自持许可，凭据记为来源方自管。
                grant_basis[source_id] = "owner"
            decision, policy_revision = self.policies.decision(asset["jurisdiction"], asset["classification"], purpose)
            policy_basis[source_id] = {"decision": decision, "revision": policy_revision}
            if decision != "allow":
                reasons.append({"source": source_id, "reason": "policy"})
        return grant_basis, policy_basis, reasons

    def _record(self, handover_id, party, decision, at, grant_basis, policy_basis, digest, reasons):
        self.connection.execute(
            "INSERT INTO handover_decisions(handover_id,party,decision,decided_at,grant_basis,"
            "policy_basis,input_digest,reasons) VALUES(?,?,?,?,?,?,?,?)",
            (handover_id, party, decision, at,
             json.dumps(grant_basis, ensure_ascii=False, sort_keys=True),
             json.dumps(policy_basis, ensure_ascii=False, sort_keys=True),
             digest, json.dumps(reasons, ensure_ascii=False, sort_keys=True)),
        )

    def _scope(self, handover, inputs, decisions):
        if handover["state"] != "completed":
            return None
        grants = [decisions[party]["grant_basis"] for party in (handover["party_a"], handover["party_b"])]
        valid_until = None
        grant_ids = sorted({gid for basis in grants for gid in basis.values() if gid != "owner"})
        if grant_ids:
            placeholders = ",".join("?" for _ in grant_ids)
            row = self.connection.execute(
                "SELECT MIN(valid_until) AS until FROM grants WHERE id IN (" + placeholders + ")", grant_ids).fetchone()
            valid_until = row["until"]
        return {
            "product_id": handover["product_id"],
            "product_revision": handover["product_revision"],
            "purpose": handover["purpose"],
            "sources": sorted({asset["source_id"] for asset in inputs}),
            "valid_until": valid_until,
            "input_digest": handover["input_digest"],
        }

    def _decision_view(self, row):
        return {
            "decision": row["decision"],
            "decided_at": row["decided_at"],
            "grant_basis": json.loads(row["grant_basis"]),
            "policy_basis": json.loads(row["policy_basis"]),
            "input_digest": row["input_digest"],
            "reasons": json.loads(row["reasons"]),
        }

    def _require_handover(self, handover_id):
        handover = self.connection.execute("SELECT * FROM handovers WHERE id=?", (handover_id,)).fetchone()
        if handover is None:
            raise KeyError(handover_id)
        return handover

    def _require_party(self, handover, party):
        if party not in (handover["party_a"], handover["party_b"]):
            raise ValueError("该机构不是交接参与方")

    def _decision(self, handover_id, party):
        return self.connection.execute(
            "SELECT * FROM handover_decisions WHERE handover_id=? AND party=?", (handover_id, party)).fetchone()

    def _confirmed_count(self, handover_id):
        return self.connection.execute(
            "SELECT count(*) FROM handover_decisions WHERE handover_id=? AND decision='confirmed'",
            (handover_id,)).fetchone()[0]

    def _state(self, handover_id):
        return self.connection.execute("SELECT state FROM handovers WHERE id=?", (handover_id,)).fetchone()["state"]

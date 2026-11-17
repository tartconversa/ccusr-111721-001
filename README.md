# 跨机构数据协作台账

本服务记录数据来源、字段含义、合作许可、数据产品及交付凭据。合作方可在本地演练不同机构和法域之间的资料流转；示例记录均为虚构，不连接真实口岸或支付系统。

需要 Python 3.10 或更新版本，不依赖第三方包。

运行项目测试：`python -m unittest discover -s tests -v`。
检查源码：`python -m compileall -q exchange`。
演练入口：`python -m exchange --database demo.db`。

领域逻辑分布在来源目录、合作许可、产品编排、交付核验和双方交接中。`exchange/service.py` 组合这些操作，`exchange/db.py` 保存本地 SQLite 表结构；调用接口和现有边界可从源码及测试读取。

## 双方交接台账

两地口岸共用一次资料采集、但各自查验系统独立作决定时，使用 `service.handovers` 记录同一次采集的交接过程。

- `open(handover_id, product_id, revision, party_a, party_b, purpose, at)`：按交接编号开立交接单，绑定固定的产品修订（即采集版本，含字段目录版本与输入摘要）。两方各自确认前状态保持 `pending`。
- `confirm(handover_id, party, at)`：一方独立作出确认。每一方分别保存自己的许可判断依据——来源许可凭据（来源所有方记为 `owner`）和当时的用途规则版本快照。两方都确认后状态才变为 `completed`；检查不通过时抛 `PermissionError`，不落确认记录。
- `reject(handover_id, party, at, note)`：一方拒收，状态变为 `rejected`，拒收依据同样留痕。任一方拒收即终态，另一方此前的确认记录原样保留，不会被改写成双方已完成。
- `lookup(handover_id)`：凭交接编号查清采集版本（来源、字段、schema 版本、摘要）、双方决定及各自依据，以及仅在完成后给出的最终可使用范围 `usable_scope`（来源、用途、许可最早到期日、输入摘要）。

状态机为 `pending → completed | rejected`，终态不可逆；同一方重复确认返回既有记录而不生成第二次交付，已确认方不能改写为拒收、已拒收方不能再确认。用途规则在交接途中改变时，已确认方依据中的规则版本快照保持不变。



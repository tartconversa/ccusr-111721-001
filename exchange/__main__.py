import argparse
import json

from .service import Exchange


def main():
    parser = argparse.ArgumentParser(description="本地协作台账演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    service = Exchange(args.database)
    try:
        service.catalog.register("flow", "机构甲", "甲地", "business", [("count", "通行计数", "integer", False)], "2026-11-17")
        service.catalog.ingest("item-1", "flow", {"count": 7}, "2026-11-17")
        service.grants.issue("g-1", "flow", "机构乙", "协作统计", "2026-12-31", "2026-11-17")
        service.policies.set("甲地", "business", "协作统计", "allow", "2026-11-17", "机构甲")
        revision = service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-17", "机构甲")

        # 同一次采集在两方各自确认前保持待交接。
        service.handovers.open("h-1", "report", revision, "机构甲", "机构乙", "协作统计", "2026-11-17")
        service.handovers.confirm("h-1", "机构甲", "2026-11-18")
        service.handovers.confirm("h-1", "机构乙", "2026-11-18")
        print(json.dumps(service.handovers.lookup("h-1"), ensure_ascii=False, sort_keys=True))

        # 第二单：乙方拒收，甲方已确认的记录不会被改写成双方完成。
        revision_two = service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-19", "机构甲")
        service.handovers.open("h-2", "report", revision_two, "机构甲", "机构乙", "协作统计", "2026-11-19")
        service.handovers.confirm("h-2", "机构甲", "2026-11-19")
        service.handovers.reject("h-2", "机构乙", "2026-11-20", note="用途规则待复核")
        print(json.dumps(service.handovers.lookup("h-2"), ensure_ascii=False, sort_keys=True))
    finally:
        service.close()


if __name__ == "__main__":
    main()

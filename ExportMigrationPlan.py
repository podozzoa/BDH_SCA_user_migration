import argparse
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook

from bdh import Api, FIELDS, KINDS, fingerprint, read_json, write_csv, write_json

HEADERS = ("Tool", "Domain", "Source Account", "Target Account", "Account Type")


def applications(path, sheet, domain, allowed, columns=HEADERS):
    if len(columns) != len(HEADERS) or len(set(columns)) != len(HEADERS):
        raise ValueError("Expected five distinct workbook columns")
    workbook = load_workbook(path, read_only=True)
    groups = defaultdict(list)
    try:
        rows = (workbook[sheet] if sheet else workbook.active).iter_rows(values_only=True)
        header = [str(value or "").strip() for value in next(rows, ())]
        for name in columns:
            if header.count(name) != 1:
                raise ValueError(f"Expected one column named {name}")
        indexes = [header.index(name) for name in columns]
        for number, row in enumerate(rows, 2):
            tool, row_domain, source, target, id_type = (row[index] for index in indexes)
            if str(tool or "").strip().casefold() != "black duck":
                continue
            if not isinstance(row_domain, str) or not row_domain.strip():
                raise ValueError(f"Row {number}: missing Domain")
            if row_domain.strip() != domain:
                continue
            if any(not isinstance(value, str) or not value.strip()
                   for value in (source, target, id_type)):
                raise ValueError(f"Row {number}: use text for both IDs and ID type")
            source, target, id_type = (value.strip() for value in (source, target, id_type))
            if id_type not in allowed:
                raise ValueError(f"Row {number}: unconfigured ID type {id_type!r}")
            if source.casefold() == target.casefold():
                raise ValueError(f"Row {number}: same source and target account")
            if any(value.startswith("=") or any(char.isspace() or char in ",;" for char in value)
                   for value in (source, target)):
                raise ValueError(f"Row {number}: expected one ID per cell")
            groups[source, target, id_type].append(number)
    finally:
        workbook.close()
    if not groups:
        raise ValueError("No Black Duck rows matched this domain")
    sources = {key[0].casefold() for key in groups}
    target_types = defaultdict(set)
    for _, target, id_type in groups:
        target_types[target.casefold()].add((target, id_type))
    if any(key in sources or len(values) != 1 for key, values in target_types.items()):
        raise ValueError("Conflicting target account or ID type in workbook")
    return groups


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "workbook", "domain", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--sheet")
    args = parser.parse_args()
    config = read_json(args.config)
    groups = applications(args.workbook, args.sheet, args.domain,
                          config["allowed_id_types"], config.get("workbook_columns", HEADERS))
    api = Api(config, args.domain)
    users = {}
    for name in sorted({name for source, target, _ in groups for name in (source, target)}):
        user = api.user(name)
        rights = {kind: sorted(api.rights(user, kind)) for kind in KINDS} if user else {}
        users[name] = {"account": user, "rights": rights}

    plan = []
    def add(action, source="", target="", id_type="", kind="", resource="", role="", rows=()):
        row = dict(zip(FIELDS[1:-1], (action, args.domain, source, target, id_type,
                                     kind, resource, role, ",".join(map(str, sorted(rows))))))
        row["id"] = fingerprint(row)[:24]
        plan.append(row)

    target_rows = defaultdict(list)
    source_rows = defaultdict(list)
    for (source, target, id_type), rows in groups.items():
        source_info, target_info = users[source], users[target]
        if not source_info["account"] or source_info["account"]["type"] != "INTERNAL":
            raise ValueError(f"Expected existing Internal source: {source}")
        if target_info["account"] and (target_info["account"]["type"] != "EXTERNAL"
                                       or not target_info["account"]["active"]):
            raise ValueError(f"Existing target must be active External: {target}")
        target_rows[target, id_type].extend(rows)
        source_rows[source].extend(rows)
        for kind, rights in source_info["rights"].items():
            for resource, role in rights:
                existing = (resource, role) in target_info["rights"].get(kind, ())
                action = "MANUAL_OWNER" if kind == "project_owner" else (
                    "EXISTS" if existing else "GRANT")
                add(action, source, target, id_type, kind, resource, role, rows)

    for (target, id_type), rows in target_rows.items():
        if not users[target]["account"]:
            add("CREATE", target=target, id_type=id_type, rows=rows)
    for source, rows in source_rows.items():
        if users[source]["account"]["active"]:
            add("DEACTIVATE", source=source, rows=rows)

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "snapshot.json", users)
    write_json(output / "plan.json", {"domain": args.domain, "url": api.base,
                                    "config_hash": fingerprint(config), "rows": plan})
    write_csv(output / "review.csv", plan)
    print(f"{len(plan)} plan rows written")


if __name__ == "__main__":
    main()

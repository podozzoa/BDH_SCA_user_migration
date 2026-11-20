import argparse
import csv
from collections import defaultdict
from pathlib import Path

from bdh import Api, FIELDS, KINDS, fingerprint, read_json, write_csv, write_json

HEADERS = ("Tool", "Domain", "Source Account", "Target Account", "Account Type")


def applications(path, domain, allowed, columns=HEADERS, encoding="utf-8-sig"):
    if len(columns) != len(HEADERS) or len(set(columns)) != len(HEADERS):
        raise ValueError("Expected five distinct CSV columns")
    groups = defaultdict(list)
    with open(path, encoding=encoding, newline="") as stream:
        rows = csv.reader(stream, strict=True)
        header = [value.strip() for value in next(rows, ())]
        for name in columns:
            if header.count(name) != 1:
                raise ValueError(f"Expected one column named {name}")
        indexes = [header.index(name) for name in columns]
        for row in rows:
            number = rows.line_num
            if not any(value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise ValueError(f"CSV line {number}: column count differs from header")
            tool, row_domain, source, target, id_type = (row[index].strip() for index in indexes)
            if tool.casefold() != "black duck":
                continue
            if not row_domain:
                raise ValueError(f"CSV line {number}: missing Domain")
            if row_domain != domain:
                continue
            if not all((source, target, id_type)):
                raise ValueError(f"CSV line {number}: missing account ID or account type")
            if id_type not in allowed:
                raise ValueError(f"CSV line {number}: unconfigured ID type {id_type!r}")
            if source.casefold() == target.casefold():
                raise ValueError(f"CSV line {number}: same source and target account")
            if any(value.startswith("=") or any(char.isspace() or char in ",;" for char in value)
                   for value in (source, target)):
                raise ValueError(f"CSV line {number}: expected one ID per field")
            groups[source, target, id_type].append(number)
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
    for name in ("config", "applications", "domain", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--encoding", choices=("utf-8-sig", "cp949"), default="utf-8-sig")
    args = parser.parse_args()
    config = read_json(args.config)
    groups = applications(args.applications, args.domain, config["allowed_id_types"],
                          config.get("application_columns", config.get("workbook_columns", HEADERS)),
                          args.encoding)
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

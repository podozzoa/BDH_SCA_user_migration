import argparse
import csv
from pathlib import Path

from bdh import Api, FIELDS, fingerprint, read_json, safe_cell, write_csv

PHASES = {"provision": {"CREATE", "GRANT"}, "deactivate": {"DEACTIVATE"}}


def perform(api, row, snapshot, execute):
    action = row["action"]
    username = row["source"] if action == "DEACTIVATE" else row["target"]
    user = api.user(username)
    original = snapshot[username]["account"]
    if original and (not user or user["id"] != original["id"]):
        raise ValueError("Account ID changed since planning")
    context = {"username": username, "id_type": row["id_type"]}
    if action == "CREATE":
        operation = "create_external"
        def satisfied(account):
            return account and account["type"] == "EXTERNAL" and account["active"]
        if user and not satisfied(user):
            raise ValueError("Username belongs to an incompatible account")
    elif action == "DEACTIVATE":
        operation = "deactivate"
        if not user or user["type"] != "INTERNAL":
            raise ValueError("Expected original Internal account")
        if api.rights(user, "project_owner"):
            raise ValueError("Transfer project ownership before deactivating this account")
        context["user_id"] = user["id"]
        def satisfied(account):
            return account and account["id"] == user["id"] and not account["active"]
    else:
        operation = "grant_" + row["kind"]
        if not user:
            if not execute and not original:
                if not api.config["operations"].get(operation):
                    raise ValueError(f"Configure API operation: {operation}")
                return "WAITING_FOR_EXTERNAL"
            raise ValueError("Missing target External account")
        if user["type"] != "EXTERNAL" or not user["active"]:
            raise ValueError("Expected active External account")
        context.update(user_id=user["id"], resource=row["resource"], role=row["role"])
        def satisfied(account):
            return (account and account["id"] == user["id"] and account["active"]
                    and (row["resource"], row["role"]) in api.rights(account, row["kind"]))

    if satisfied(user):
        return "ALREADY_DONE"
    if not api.config["operations"].get(operation):
        raise ValueError(f"Configure API operation: {operation}")
    if not execute:
        return "PENDING"
    api.operation(operation, context, read=False)
    if not satisfied(api.user(username)):
        raise RuntimeError("Write returned success but verification failed")
    return "VERIFIED"


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "plan-dir", "review", "result"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    directory = Path(args.plan_dir)
    config = read_json(args.config)
    plan = read_json(directory / "plan.json")
    snapshot = read_json(directory / "snapshot.json")
    if plan["config_hash"] != fingerprint(config):
        raise ValueError("Configuration changed. Regenerate and review the plan.")
    rows = {row["id"]: row for row in plan["rows"]}
    selected = []
    seen = set()
    with open(args.review, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("Unexpected approval CSV header")
        for reviewed in reader:
            key = reviewed["id"]
            if key in seen or key not in rows:
                raise ValueError("Duplicate or unknown plan ID")
            seen.add(key)
            row = rows[key]
            if any(reviewed[field] != safe_cell(row[field]) for field in FIELDS[:-1]):
                raise ValueError("Only the approved column can be edited")
            approved = reviewed["approved"].strip().upper()
            if approved not in {"", "YES", "NO"}:
                raise ValueError("approved must be YES, NO, or blank")
            if approved == "YES" and row["action"] in PHASES[args.phase]:
                selected.append(row)
    if not selected:
        raise ValueError("No approved actions for this phase")
    if Path(args.result).resolve() in {
        Path(args.review).resolve(), (directory / "review.csv").resolve()
    }:
        raise ValueError("Result path must differ from approval input")
    if Path(args.result).exists():
        raise ValueError("Use a new result filename for each run")
    api = Api(config, plan["domain"])
    if api.base != plan["url"]:
        raise ValueError("Plan server mismatch")
    selected.sort(key=lambda row: row["action"] != "CREATE")
    results = []
    for row in selected:
        try:
            status, error = perform(api, row, snapshot, args.execute), ""
        except (ValueError, RuntimeError, LookupError, OSError) as exc:
            status, error = "FAILED", str(exc)
        results.append({**row, "approved": "YES", "status": status, "error": error})
        write_csv(args.result, results, FIELDS + ("status", "error"))
    if any(row["status"] == "FAILED" for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

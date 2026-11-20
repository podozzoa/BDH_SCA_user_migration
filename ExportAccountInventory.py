import argparse
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from bdh import Api, KINDS, read_json, write_csv, write_json

ACCOUNT_FIELDS = ("domain", "user_id", "username", "account_type", "active")
PERMISSION_FIELDS = ACCOUNT_FIELDS + (
    "kind", "resource", "resource_name", "role", "role_name", "assignment", "via_group",
)
INHERITED = ("global_role", "project", "project_group")


def collect(config, domains, include_inherited, accounts_only=False):
    accounts = []
    permissions = []
    for domain in domains:
        api = Api(config, domain)
        users = api.operation("list_users", {})
        seen_ids = set()
        seen_names = set()

        @lru_cache(maxsize=None)
        def group_permissions(group_id, kind):
            return api.permission_rows(kind, {"group_id": group_id}, f"group_{kind}")

        for user in sorted(users, key=lambda row: row.get("username", "")):
            user = api.validate_user(user)
            user_id = str(user["id"])
            username = user["username"]
            if user_id in seen_ids or username in seen_names:
                raise ValueError(f"{domain}: duplicate user in list_users response")
            seen_ids.add(user_id)
            seen_names.add(username)
            account = dict(zip(ACCOUNT_FIELDS, (domain, user_id, username,
                                                user["type"], user["active"])))
            accounts.append(account)
            if accounts_only:
                continue
            context = {"user_id": user_id, "username": username}
            assigned = set()

            def record(kind, row, assignment, via_group=""):
                key = (kind, row["resource"], row["role"], assignment, via_group)
                if key in assigned:
                    return
                assigned.add(key)
                permissions.append({**account, "kind": kind, "resource": row["resource"],
                                    "resource_name": row.get("resource_name") or "",
                                    "role": row["role"], "role_name": row.get("role_name") or "",
                                    "assignment": assignment, "via_group": via_group})

            for kind in KINDS:
                for row in api.permission_rows(kind, context):
                    record(kind, row, "direct")
                    if kind == "user_group" and include_inherited:
                        group_id = row["resource"]
                        for inherited_kind in INHERITED:
                            for grant in group_permissions(group_id, inherited_kind):
                                record(inherited_kind, grant, "user_group", group_id)
    return accounts, permissions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--domain", action="append")
    selection.add_argument("--all-domains", action="store_true")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--include-inherited", action="store_true")
    scope.add_argument("--accounts-only", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config)
    domains = sorted(config["domains"]) if args.all_domains else list(dict.fromkeys(args.domain))
    if not domains:
        parser.error("No domains configured")
    for domain in domains:
        if domain not in config["domains"]:
            parser.error(f"Unknown domain: {domain}")
    required = ["list_users"] if args.accounts_only else ["list_users", *KINDS]
    if args.include_inherited:
        required.extend(f"group_{kind}" for kind in INHERITED)
    missing = [name for name in required if not config["operations"].get(name)]
    if missing:
        parser.error("Configure API operations: " + ", ".join(missing))
    output = Path(args.out)
    if output.exists():
        parser.error("Use a new output directory")
    started = datetime.now(timezone.utc).isoformat()
    accounts, permissions = collect(config, domains, args.include_inherited, args.accounts_only)
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "users.csv", accounts, ACCOUNT_FIELDS)
    if not args.accounts_only:
        write_csv(output / "permissions.csv", permissions, PERMISSION_FIELDS)
    summary = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "includes_permissions": not args.accounts_only,
        "includes_user_group_grants": args.include_inherited,
        "domains": {
            domain: {
                "users": sum(row["domain"] == domain for row in accounts),
                "active_users": sum(row["domain"] == domain and row["active"] for row in accounts),
                "inactive_users": sum(row["domain"] == domain and not row["active"] for row in accounts),
                "permissions": (None if args.accounts_only else
                                sum(row["domain"] == domain for row in permissions)),
            }
            for domain in domains
        },
    }
    write_json(output / "summary.json", summary)
    print(f"{len(accounts)} accounts and {len(permissions)} permission rows exported")


if __name__ == "__main__":
    main()

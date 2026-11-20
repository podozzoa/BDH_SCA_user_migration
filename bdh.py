import csv
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import jmespath
import requests

KINDS = ("user_group", "project", "project_group", "global_role", "project_owner")
FIELDS = ("id", "action", "domain", "source", "target", "id_type",
          "kind", "resource", "role", "application_lines", "approved")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def write_csv(path, rows, fields=FIELDS):
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: safe_cell(row.get(field, "")) for field in fields})


def render(value, context, path=False):
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if isinstance(value, str):
        values = {key: quote(str(item), safe="") if path else item
                  for key, item in context.items()}
        return value.format_map(values)
    return value


class Api:
    def __init__(self, config, domain):
        self.config = config
        self.settings = config["domains"][domain]
        self.base = self.settings["url"].rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.path:
            raise ValueError("Configure an HTTPS origin with no path or credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Unexpected query or fragment in the origin")
        self.session = requests.Session()
        self.session.verify = self.settings.get("ca_bundle") or True
        self.authenticate()

    def authenticate(self):
        token = os.environ[self.settings["token_env"]]
        data = self.request("POST", "/api/tokens/authenticate",
                            {"Authorization": "token " + token,
                             "Accept": self.settings.get("auth_accept",
                                 "application/vnd.blackducksoftware.user-4+json")})
        self.session.headers["Authorization"] = "Bearer " + data["bearerToken"]

    def request(self, method, path, headers=None, refresh=True, **kwargs):
        if not path.startswith("/api/") or "%" in path[:5] or "?" in path or "#" in path:
            raise ValueError("Expected an /api/ path")
        if path.startswith(("/api/internal/", "/api/v1/")):
            raise ValueError("Use a documented public API")
        response = self.session.request(method, self.base + path, headers=headers,
                                        timeout=45, allow_redirects=False, **kwargs)
        if response.status_code == 401 and method == "GET" and refresh:
            self.authenticate()
            return self.request(method, path, headers, refresh=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"{method} returned HTTP {response.status_code}")
        if response.content and "json" in response.headers.get("Content-Type", ""):
            return response.json()
        return None

    def operation(self, name, context, read=True):
        spec = self.config["operations"].get(name)
        if not spec:
            raise ValueError(f"Configure API operation: {name}")
        method = spec.get("method", "GET").upper()
        if method not in ({"GET"} if read else {"POST", "PUT", "PATCH"}):
            raise ValueError(f"Unexpected HTTP method for {name}")
        path = render(spec["path"], context, path=True)
        params = render(spec.get("query", {}), context)
        headers = {key: spec[key] for key in ("Accept", "Content-Type") if key in spec}
        if not read:
            return self.request(method, path, headers, params=params,
                                json=render(spec.get("body"), context))
        rows = []
        page = spec.get("pagination")
        if page:
            params[page["offset"]] = 0
            params[page["limit"]] = page.get("size", 100)
        while True:
            data = self.request(method, path, headers, params=params)
            items = jmespath.search(spec["items"], data)
            if not isinstance(items, list):
                raise ValueError(f"{name}: expected a list from the configured response expression")
            rows.extend(items)
            if not page:
                return rows
            total = jmespath.search(page["total"], data)
            if not isinstance(total, int) or len(rows) > total:
                raise ValueError(f"{name}: inconsistent pagination total")
            if len(rows) == total:
                return rows
            if not items:
                raise ValueError(f"{name}: incomplete paginated response")
            params[page["offset"]] = len(rows)

    def user(self, username):
        matches = []
        for row in self.operation("users", {"username": username}):
            if not isinstance(row.get("username"), str):
                raise ValueError("User response must include username")
            if row["username"] == username:
                matches.append(row)
        if len(matches) > 1:
            raise ValueError(f"Multiple exact user matches: {username}")
        if not matches:
            return None
        return self.validate_user(matches[0])

    @staticmethod
    def validate_user(user):
        if not isinstance(user.get("username"), str) or not user["username"]:
            raise ValueError("User response must include username")
        if not user.get("id") and isinstance(user.get("href"), str):
            path = urlsplit(user["href"]).path.rstrip("/")
            prefix = "/api/users/"
            if path.startswith(prefix) and path[len(prefix):] and "/" not in path[len(prefix):]:
                user = {**user, "id": path[len(prefix):]}
        if not user.get("id") or type(user.get("active")) is not bool:
            raise ValueError("User response must include id and boolean active")
        if user.get("type") not in {"INTERNAL", "EXTERNAL"}:
            raise ValueError("Normalize account type to INTERNAL or EXTERNAL")
        return user

    def rights(self, user, kind):
        context = {"user_id": user["id"], "username": user["username"]}
        return {(row["resource"], row["role"]) for row in self.permission_rows(kind, context)}

    def permission_rows(self, kind, context, operation=None):
        rows = self.operation(operation or kind, context)
        for row in rows:
            resource, role = row.get("resource"), row.get("role")
            if not isinstance(resource, str) or not isinstance(role, str):
                raise ValueError(f"{kind}: resource and role must be strings")
            if not (role if kind == "global_role" else resource):
                raise ValueError(f"{kind}: missing resource or role identifier")
        return rows

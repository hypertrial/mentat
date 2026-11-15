"""Fixed, independent Polaris REST client used inside the trusted image."""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = os.environ["POLARIS_HOST"].rstrip("/")
LOCATION = "file:///tmp/mentat-catalog/"
if HOST != "http://polaris:8181":
    raise ValueError("unexpected Polaris host")


def request(path, *, method="GET", body=None, token=None, form=False):
    headers = {"Accept": "application/json", "Polaris-Realm": "POLARIS"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
    req = urllib.request.Request(HOST + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("response exceeds limit")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        exc.oracle_method = method
        raise


def access_token():
    deadline = time.monotonic() + 60
    while True:
        try:
            return request(
                "/api/catalog/v1/oauth/tokens",
                method="POST",
                form=True,
                body={
                    "grant_type": "client_credentials",
                    "client_id": os.environ["POLARIS_CLIENT_ID"],
                    "client_secret": os.environ["POLARIS_CLIENT_SECRET"],
                    "scope": "PRINCIPAL_ROLE:ALL",
                },
            )["access_token"]
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or time.monotonic() >= deadline:
                raise
        except urllib.error.URLError:
            if time.monotonic() >= deadline:
                raise
        time.sleep(1)


def seed(token):
    request(
        "/api/management/v1/catalogs",
        method="POST",
        token=token,
        body={
            "catalog": {
                "name": "mentat",
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {"default-base-location": LOCATION},
                "storageConfigInfo": {"storageType": "FILE", "allowedLocations": [LOCATION]},
            }
        },
    )
    with open("/opt/fixture.json", encoding="utf-8") as fixture:
        namespaces = json.load(fixture)["namespaces"]
    for entry in sorted(namespaces, key=lambda item: (len(item["path"]), item["path"])):
        request(
            "/api/catalog/v1/mentat/namespaces",
            method="POST",
            token=token,
            body={"namespace": entry["path"], "properties": entry["properties"]},
        )


def read(token):
    try:
        request("/api/management/v1/catalogs/mentat", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"catalog_exists": False, "namespaces": []}
        raise
    result = []
    pending = [[]]
    seen = set()
    while pending:
        parent = pending.pop()
        page = None
        pages = set()
        while True:
            params = {}
            if parent:
                params["parent"] = "\x1f".join(parent)
            if page:
                params["pageToken"] = page
            query = "?" + urllib.parse.urlencode(params) if params else ""
            response = request("/api/catalog/v1/mentat/namespaces" + query, token=token)
            if not isinstance(response, dict) or not isinstance(response.get("namespaces"), list):
                raise TypeError("invalid namespace response")
            for path in response["namespaces"]:
                if not isinstance(path, list) or any(not isinstance(part, str) for part in path):
                    raise ValueError("invalid namespace path")
                key = tuple(path)
                if key in seen or len(key) != len(parent) + 1 or list(key[:-1]) != parent:
                    raise ValueError("invalid namespace listing")
                seen.add(key)
                if len(seen) > 1024 or len(key) > 16:
                    raise ValueError("namespace listing exceeds limit")
                encoded = urllib.parse.quote("\x1f".join(path), safe="")
                details = request("/api/catalog/v1/mentat/namespaces/" + encoded, token=token)
                if not isinstance(details, dict) or details.get("namespace") != path:
                    raise ValueError("namespace response mismatch")
                properties = details.get("properties") or {}
                if not isinstance(properties, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in properties.items()
                ):
                    raise ValueError("invalid namespace properties")
                result.append({"path": path, "properties": properties})
                pending.append(path)
            next_page = response.get("next-page-token")
            if not next_page:
                break
            if not isinstance(next_page, str) or next_page in pages or len(pages) >= 1024:
                raise ValueError("invalid page token")
            pages.add(next_page)
            page = next_page
    return {"catalog_exists": True, "namespaces": result}


if __name__ == "__main__":
    try:
        token = access_token()
        if sys.argv[1:] == ["seed"]:
            seed(token)
        elif sys.argv[1:] == ["read"]:
            print(json.dumps(read(token)))
        else:
            raise ValueError("expected seed or read")
    except urllib.error.HTTPError as exc:
        print(f"oracle failed: HTTP {exc.code} {exc.oracle_method}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("oracle failed: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)

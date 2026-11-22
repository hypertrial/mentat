"""Independent projections and bounded validation for setup exports."""

import json
import re
from collections import Counter

import yaml

_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_LOCATION = "file:///tmp/mentat-catalog/"


def case_payload(case) -> bytes:
    return json.dumps(
        {
            "namespaces": [
                {"path": list(ns.path), "properties": ns.properties} for ns in case.namespaces
            ]
        },
        ensure_ascii=True,
    ).encode()


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("invalid or duplicate YAML key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def validate_export(raw: bytes, selected_properties: tuple[str, ...]) -> dict:
    if not isinstance(raw, bytes) or len(raw) > 1_000_000:
        raise ValueError("invalid or oversized export")
    try:
        text = raw.decode("utf-8")
        if any(
            isinstance(event, (yaml.AliasEvent,)) or getattr(event, "anchor", None)
            for event in yaml.parse(text, Loader=_UniqueLoader)
        ):
            raise ValueError("YAML anchors and aliases are forbidden")
        data = yaml.load(text, Loader=_UniqueLoader)
    # Tagged scalars make PyYAML raise types beyond YAMLError (AttributeError, KeyError, ...).
    except Exception as exc:
        raise ValueError("invalid YAML export") from exc
    if not isinstance(data, dict) or set(data) - {"principals", "principal_roles", "catalogs"}:
        raise ValueError("unsupported export scope")
    principals = data.get("principals", {})
    if principals not in (
        {},
        {"root": {"type": "service", "roles": ["service_admin"]}},
        {"root": {"type": "service"}},
    ):
        raise ValueError("unsupported principals")
    if data.get("principal_roles", []) not in ([], ["service_admin"]):
        raise ValueError("unsupported principal roles")
    catalogs = data.get("catalogs")
    if not isinstance(catalogs, list) or len(catalogs) != 1 or not isinstance(catalogs[0], dict):
        raise ValueError("expected one catalog")
    catalog = catalogs[0]
    if set(catalog) - {
        "name",
        "type",
        "storage_type",
        "default_base_location",
        "allowed_locations",
        "namespaces",
        "roles",
        "properties",
    }:
        raise ValueError("unsupported catalog scope")
    if (
        catalog.get("name") != "mentat"
        or catalog.get("type", "internal") != "internal"
        or catalog.get("storage_type") != "file"
        or catalog.get("default_base_location") != _LOCATION
        or catalog.get("allowed_locations") != [_LOCATION]
        or catalog.get("properties", {}) != {}
    ):
        raise ValueError("unsafe catalog configuration")
    roles = catalog.get("roles", {})
    if roles:
        expected = {
            "catalog_admin": {
                "assign_to": ["service_admin"],
                "privileges": {"catalog": ["CATALOG_MANAGE_ACCESS", "CATALOG_MANAGE_METADATA"]},
            }
        }
        if roles != expected:
            raise ValueError("unsupported catalog roles")
    entries = catalog.get("namespaces", [])
    if not isinstance(entries, list) or len(entries) > 1024:
        raise ValueError("invalid namespaces")
    projection = []
    for entry in entries:
        if isinstance(entry, (str, list)):
            name, properties = entry, {}
        elif isinstance(entry, dict) and not set(entry) - {"name", "properties"}:
            name, properties = entry.get("name"), entry.get("properties", {})
        else:
            raise ValueError("invalid namespace entry")
        path = name.split(".") if isinstance(name, str) else name
        if (
            not isinstance(path, list)
            or not 1 <= len(path) <= 16
            or any(not isinstance(part, str) or not _NAME.fullmatch(part) for part in path)
        ):
            raise ValueError("invalid namespace path")
        if (
            not isinstance(properties, dict)
            or len(properties) - ("location" in properties) > 32
            or any(
                not isinstance(k, str)
                or not _NAME.fullmatch(k)
                or not isinstance(v, str)
                or len(v) > 4096
                or any(ord(ch) < 32 or 0xD800 <= ord(ch) <= 0xDFFF for ch in v)
                for k, v in properties.items()
            )
        ):
            raise ValueError("invalid namespace properties")
        if "location" in properties:
            location = properties["location"]
            suffix = location.removeprefix(_LOCATION)
            segments = suffix.removesuffix("/").split("/")
            if (
                not location.startswith(_LOCATION)
                or not suffix.endswith("/")
                or any(not _NAME.fullmatch(part) for part in segments)
                or (".".join(segments) != name if isinstance(name, str) else segments != path)
            ):
                raise ValueError("unsafe namespace location")
        if set(properties) - set(selected_properties) - {"location"}:
            raise ValueError("unselected namespace property")
        projection.append(
            {"path": path, "properties": {k: v for k, v in properties.items() if k != "location"}}
        )
    return {"namespaces": projection}


def compare(case, projection: dict, selected_properties: tuple[str, ...], stage: str) -> list[dict]:
    expected = {ns.path: ns.properties for ns in case.namespaces}
    actual_entries = projection["namespaces"]
    counts = Counter(tuple(ns["path"]) for ns in actual_entries)
    actual = {tuple(ns["path"]): ns["properties"] for ns in actual_entries}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    duplicates = sorted(path for path, count in counts.items() if count > 1)
    identity = {
        "missing": [list(p) for p in missing],
        "extra": [list(p) for p in extra],
        "duplicate": [list(p) for p in duplicates],
    }
    differences = []
    for path in sorted(set(expected) & set(actual)):
        for key in selected_properties:
            if key not in expected[path]:
                if key in actual[path]:
                    differences.append(
                        {
                            "path": list(path),
                            "property": key,
                            "expected": None,
                            "actual": actual[path][key],
                            "reason": "unexpected",
                        }
                    )
                continue
            value = actual[path].get(key)
            if value != expected[path][key]:
                differences.append(
                    {
                        "path": list(path),
                        "property": key,
                        "expected": expected[path][key],
                        "actual": value,
                        "reason": "missing"
                        if key not in actual[path]
                        else "empty"
                        if value == ""
                        else "wrong",
                    }
                )
    return [
        {
            "name": stage + ":namespace.identity",
            "claim": "namespace.identity",
            "detail": f"{stage}: namespace paths",
            "stage": stage,
            "status": "violation" if any(identity.values()) else "pass",
            "evidence": identity,
        },
        {
            "name": stage + ":namespace.properties",
            "claim": "namespace.properties",
            "detail": f"{stage}: selected namespace properties",
            "stage": stage,
            "status": "violation" if differences or missing else "pass",
            "evidence": {"differences": differences, "missing_paths": [list(p) for p in missing]},
        },
    ]

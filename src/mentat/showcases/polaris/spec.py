"""Strict, bounded input for the namespace preservation contract."""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mentat.evidence import read_json

PACK = Path(__file__).with_name("pack")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int
    selected_properties: tuple[str, ...] = Field(min_length=1, max_length=32)
    claims: list[str]
    assumptions: list[str]
    checks: list[dict[str, str]]

    @model_validator(mode="after")
    def fixed_contract(self):
        if self.claims != ["namespace.identity", "namespace.properties"]:
            raise ValueError("unsupported claims")
        if self.assumptions != ["distinct paths", "complete snapshot", "successful restore"]:
            raise ValueError("unsupported assumptions")
        if self.checks != [
            {
                "id": "legacy-collision",
                "claim": "namespace.identity",
                "kind": "z3",
                "required": "sat",
                "source": "formal.py:run_z3",
            },
            {
                "id": "list-identity",
                "claim": "namespace.identity",
                "kind": "z3",
                "required": "unsat and nonvacuous",
                "source": "formal.py:run_z3",
            },
            {
                "id": "restore-lookup",
                "claim": "namespace.properties",
                "kind": "lean",
                "required": "kernel checked and axioms audited",
                "source": "lean/Main.lean:restore_lookup",
            },
        ]:
            raise ValueError("unsupported checks")
        return self

    @field_validator("version")
    @classmethod
    def version_one(cls, value):
        if value != 1:
            raise ValueError("unsupported contract version")
        return value

    @field_validator("selected_properties", mode="before")
    @classmethod
    def property_array(cls, value):
        if not isinstance(value, list):
            raise ValueError("selected_properties must be a JSON array")
        return tuple(value)

    @field_validator("selected_properties")
    @classmethod
    def property_names(cls, value):
        if (
            len(set(value)) != len(value)
            or "location" in value
            or any(not _NAME.fullmatch(name) for name in value)
        ):
            raise ValueError("property names must be distinct safe names")
        return value

    def as_dict(self) -> dict:
        return self.model_dump(mode="json")


class Namespace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: tuple[str, ...] = Field(min_length=1, max_length=16)
    properties: dict[str, str] = Field(max_length=32)

    @field_validator("path", mode="before")
    @classmethod
    def path_array(cls, value):
        if not isinstance(value, list):
            raise ValueError("path must be a JSON array")
        return tuple(value)

    @field_validator("path")
    @classmethod
    def path_components(cls, value):
        if any(not _NAME.fullmatch(part) for part in value):
            raise ValueError("path components must be safe names")
        return value

    @field_validator("properties")
    @classmethod
    def property_values(cls, value):
        if any(
            not _NAME.fullmatch(k)
            or len(v) > 4096
            or any(ord(ch) < 32 or 0xD800 <= ord(ch) <= 0xDFFF for ch in v)
            for k, v in value.items()
        ):
            raise ValueError("invalid property name or oversized value")
        return value


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    namespaces: list[Namespace] = Field(min_length=1, max_length=128)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value):
        if not _NAME.fullmatch(value):
            raise ValueError("case id must be a safe name")
        return value

    @model_validator(mode="after")
    def paths_and_parents(self):
        paths = [namespace.path for namespace in self.namespaces]
        if len(set(paths)) != len(paths):
            raise ValueError("namespace paths must be distinct")
        if any(path[:i] not in paths for path in paths for i in range(1, len(path))):
            raise ValueError("every parent namespace must be explicit")
        return self


def load_spec(path: Path) -> Spec:
    return Spec.model_validate(read_json(path))


def load_cases(path: Path, spec: Spec) -> list[Case]:
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "cases"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or not isinstance(data["cases"], list)
        or not 1 <= len(data["cases"]) <= 32
    ):
        raise ValueError("invalid fixture envelope")
    cases = [Case.model_validate(case) for case in data["cases"]]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case IDs must be distinct")
    if any(
        set(namespace.properties) - set(spec.selected_properties)
        for case in cases
        for namespace in case.namespaces
    ):
        raise ValueError("fixture uses an unselected property")
    return cases

"""Canonical JSON bytes, SHA-256 digests and verdict status. Stdlib only, so any module can import it."""

import hashlib
import json
import math

STATUSES = frozenset({"pass", "violation", "incomplete"})


def _finite(text: str) -> float:
    # Literals such as 1e999 parse to infinity; refuse them like NaN and Infinity.
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"invalid JSON constant: {text}")
    return number


def decode_json(data: bytes, *, limit: int | None = 1_000_000):
    if limit is not None and len(data) > limit:
        raise ValueError("JSON exceeds 1 MB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
            parse_float=_finite,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds parser limit") from exc


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def status(checks: list[dict], *, incomplete_first: bool = False) -> str:
    outcomes = {check.get("status") for check in checks}
    if outcomes - STATUSES or not outcomes:
        return "incomplete"
    if incomplete_first and "incomplete" in outcomes:
        return "incomplete"
    if "violation" in outcomes:
        return "violation"
    return "incomplete" if "incomplete" in outcomes else "pass"

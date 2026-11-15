"""Shared CLI output and interactive operator decisions."""

import json
import sys

import typer

from . import evidence


def emit(result: dict, output_json: bool) -> None:
    if output_json:
        typer.echo(evidence.json_bytes({"schema_version": 1, **result}).decode().rstrip())
        return
    title = result.get("status", "pass" if result.get("ok") else "incomplete")
    typer.echo(f"Mentat {title}")
    for key in ("detail", "context", "contract_digest", "run_id", "bundle", "next_action"):
        if result.get(key):
            typer.echo(f"{key.replace('_', ' ')}  {result[key]}")
    for check in result.get("model_checks", []):
        typer.echo(f"{check['kind']}  {check['status']}  {check['detail']}")
        if check.get("witness"):
            typer.echo(json.dumps(check["witness"], ensure_ascii=True, sort_keys=True))
    for case in result.get("cases", []):
        typer.echo(f"{case['case_id']}  {case['status']}")
        for check in case.get("checks", []):
            if check.get("status") != "pass":
                typer.echo(f"  {check.get('detail', check.get('id', 'check'))}")
    for method, counts in result.get("counts", {}).items():
        typer.echo(f"{method}  {json.dumps(counts, sort_keys=True)}")


def finish(result: dict, output_json: bool) -> None:
    emit(result, output_json)
    code = {"pass": 0, "violation": 1, "incomplete": 2}[result["status"]]
    if code:
        raise typer.Exit(code)


def error(exc: Exception, output_json: bool) -> None:
    finish({"status": "incomplete", "detail": str(exc)}, output_json)


def operator_confirm(message: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Operator approval or acceptance requires an interactive terminal")
    if not typer.confirm(message, default=False):
        raise ValueError("The operator declined")

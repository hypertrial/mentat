"""Render a native Polaris regression for a setup namespace round trip."""

from .spec import Case


def render_regression(case: Case, selected_properties: tuple[str, ...]) -> str:
    namespaces = [(list(ns.path), dict(ns.properties)) for ns in case.namespaces]
    return """# Run from client/python: uv run pytest -q tests/test_mentat_regression.py
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from apache_polaris.cli.command.setup import SetupCommand
from apache_polaris.cli.constants import Subcommands, UNIT_SEPARATOR


def test_setup_namespace_round_trip():
    # Synthetic source catalog; no real service or credentials are used.
    source = %r
    selected = %r
    by_wire_name = {UNIT_SEPARATOR.join(path): properties for path, properties in source}
    catalog_api = MagicMock()
    catalog_api.load_namespace_metadata.side_effect = lambda **kwargs: SimpleNamespace(
        properties=dict(by_wire_name[kwargs["namespace"]])
    )
    catalog_api.list_namespaces.return_value = SimpleNamespace(namespaces=[])
    client = MagicMock()

    with (
        patch("apache_polaris.cli.command.setup.IcebergCatalogAPI", return_value=catalog_api),
        patch("apache_polaris.cli.command.namespaces.IcebergCatalogAPI", return_value=catalog_api),
        patch("apache_polaris.cli.command.namespaces.get_catalog_api_client"),
    ):
        export = SetupCommand(setup_subcommand=Subcommands.EXPORT, _catalog_api=MagicMock())
        exported = export._export_namespaces_for_catalog(
            client, "mentat", [list(path) for path, _ in source]
        )
        assert export._failure_count == 0
        restored = yaml.safe_load(yaml.safe_dump(exported))
        apply = SetupCommand(setup_subcommand=Subcommands.APPLY, _catalog_api=MagicMock())
        apply._create_namespaces(client, "mentat", restored)
        assert apply._failure_count == 0

    requests = [call.kwargs["create_namespace_request"]
                for call in catalog_api.create_namespace.call_args_list]
    actual = {tuple(request.namespace): request.properties or {} for request in requests}
    expected_paths = {tuple(path[:i]) for path, _ in source for i in range(1, len(path) + 1)}
    assert len(requests) == len(actual)
    assert set(actual) == expected_paths
    for path, properties in source:
        assert {key: actual[tuple(path)][key] for key in selected if key in actual[tuple(path)]} == {
            key: properties[key] for key in selected if key in properties
        }
""" % (namespaces, selected_properties)

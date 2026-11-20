from types import SimpleNamespace

import pytest

from mentat import project_formal


@pytest.fixture(scope="session")
def lean_tools():
    project = SimpleNamespace(contract=SimpleNamespace(formal=[SimpleNamespace(engine="lean")]))
    try:
        return project_formal.tool_info(project)
    except ValueError as exc:
        pytest.skip(str(exc))

"""Guards of the contract suite: every port method has a contract test, every write is guarded."""

import ast
import inspect
import typing
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from fakes.paperless import FakePaperless, is_write, write

from paperless_mcp.domain import ports
from paperless_mcp.domain.ports import InstanceReader

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType

CONTRACT_TESTS = Path(__file__).parent

pytestmark = pytest.mark.decision("decision-8")


def protocols(module: ModuleType) -> list[type]:
    """The Protocol classes defined in `module`."""
    return [
        value
        for value in cast("dict[str, object]", vars(module)).values()
        if inspect.isclass(value)
        and typing.is_protocol(value)
        and value.__module__ == module.__name__
    ]


def port_methods(classes: Iterable[type]) -> set[str]:
    """The names ("Port.method") of the members of the given protocols."""
    return {
        f"{cls.__name__}.{member}" for cls in classes for member in typing.get_protocol_members(cls)
    }


def contracted(directory: Path) -> set[str]:
    """Names used as `pytest.mark.contract("Port.method")` in the test modules of `directory`."""
    names: set[str] = set()
    for path in directory.glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "pytest.mark.contract"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                names.add(str(node.args[0].value))
    return names


def unguarded_writes(classes: Iterable[type], fake: type) -> set[str]:
    """Methods of the `*Writer` protocols that `fake` implements without the write guard."""
    writers = [cls for cls in classes if cls.__name__.endswith("Writer")]
    return {
        name
        for name in port_methods(writers)
        if not is_write(getattr(fake, name.partition(".")[2], None))
    }


def test_every_port_method_has_a_contract_test() -> None:
    assert port_methods(protocols(ports)) - contracted(CONTRACT_TESTS) == set()


def test_every_contract_marker_names_a_port_method() -> None:
    assert contracted(CONTRACT_TESTS) - port_methods(protocols(ports)) == set()


def test_every_writer_method_of_the_fake_is_guarded() -> None:
    assert unguarded_writes(protocols(ports), FakePaperless) == set()


def test_ports_are_found_by_introspection() -> None:
    assert InstanceReader in protocols(ports)
    assert "InstanceReader.get_overview" in port_methods(protocols(ports))


def test_a_port_method_without_a_contract_test_is_reported(tmp_path: Path) -> None:
    class Probe(Protocol):
        def covered(self) -> None: ...
        def uncovered(self) -> None: ...

    _ = (tmp_path / "test_probe.py").write_text(
        """import pytest
@pytest.mark.contract("Probe.covered")
def test_a(): ...
pytest.mark.decision("Probe.uncovered")
print("Probe.uncovered")
""",
        encoding="utf-8",
    )
    _ = (tmp_path / "helper.py").write_text(
        'import pytest\n@pytest.mark.contract("Probe.uncovered")\ndef f(): ...\n', encoding="utf-8"
    )

    assert port_methods([Probe]) - contracted(tmp_path) == {"Probe.uncovered"}


def test_a_writer_method_without_the_guard_is_reported() -> None:
    class ProbeWriter(Protocol):
        async def guarded(self) -> None: ...
        async def unguarded(self) -> None: ...

    class ProbeReader(Protocol):
        async def read(self) -> None: ...

    class Fake(FakePaperless):
        @write
        async def guarded(self) -> None: ...

        async def unguarded(self) -> None: ...

        async def read(self) -> None: ...

    assert unguarded_writes([ProbeWriter, ProbeReader], Fake) == {"ProbeWriter.unguarded"}

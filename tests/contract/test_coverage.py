"""Guards of the contract suite (decision-8).

- Every port method has a contract test that runs against both backends: checked on the items
  pytest collects from this directory, so only a real test counts (the `contract` marker on a
  test that takes the `backend` fixture, with both its `fake` and `live` parameters).
- Every port is wired for both backends and implemented by the fake and the adapter.
- Every method of a `*Writer` port carries the fake's write guard.
"""

import inspect
import typing
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import httpx2
import pytest
from fakes.paperless import FakePaperless, is_write, write

from contract.conftest import Ports, live_ports
from paperless_mcp.domain import ports
from paperless_mcp.domain.ports import DocumentReader, InstanceReader, TaxonomyReader
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType

ROOT = Path(__file__).parents[2]
CONTRACT_TESTS = Path(__file__).parent
BACKENDS = frozenset({"fake", "live"})

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


def collect(pytester: pytest.Pytester, path: Path, *args: str) -> list[pytest.Item]:
    """The test items pytest collects from `path`, e2e ones included."""
    # pytest leaves the `*args` of inline_genitems unannotated.
    items, _ = pytester.inline_genitems(  # pyright: ignore[reportUnknownMemberType] -- see above
        str(path), "-m", "", "-p", "no:cacheprovider", *args
    )
    return items


def collect_contract_tests(pytester: pytest.Pytester) -> list[pytest.Item]:
    return collect(
        pytester, CONTRACT_TESTS, f"--rootdir={ROOT}", "-c", str(ROOT / "pyproject.toml")
    )


def name(mark: pytest.Mark) -> str:
    """The port method a `contract` marker names."""
    return str(cast("object", mark.args[0]))


def contract_names(items: Iterable[pytest.Item]) -> set[str]:
    """Every name used in a `contract` marker of a collected test."""
    return {name(mark) for item in items for mark in item.iter_markers("contract")}


def contracted(items: Iterable[pytest.Item]) -> set[str]:
    """Port methods whose contract tests take `backend` and run against every backend."""
    runs: dict[str, set[str]] = defaultdict(set)
    for item in items:
        if not isinstance(item, pytest.Function) or "backend" not in item.fixturenames:
            continue
        backend = str(item.callspec.params.get("backend"))
        for mark in item.iter_markers("contract"):
            runs[name(mark)].add(backend)
    return {name for name, backends in runs.items() if backends >= BACKENDS}


def missing_members(implementation: object, protocol: type) -> set[str]:
    """Members of `protocol` that `implementation` does not have as callables."""
    return {
        member
        for member in typing.get_protocol_members(protocol)
        if not callable(cast("object", getattr(implementation, member, None)))
    }


def unguarded_writes(classes: Iterable[type], fake: type) -> set[str]:
    """Methods of the `*Writer` protocols that `fake` implements without the write guard."""
    writers = [cls for cls in classes if cls.__name__.endswith("Writer")]
    return {
        name
        for name in port_methods(writers)
        if not is_write(getattr(fake, name.partition(".")[2], None))
    }


def wired() -> dict[str, type]:
    """The fields of `Ports` and the port each one implements."""
    return typing.get_type_hints(Ports)


def test_every_port_method_has_a_contract_test_on_every_backend(
    pytester: pytest.Pytester,
) -> None:
    assert port_methods(protocols(ports)) - contracted(collect_contract_tests(pytester)) == set()


def test_every_contract_marker_names_a_port_method(pytester: pytest.Pytester) -> None:
    names = contract_names(collect_contract_tests(pytester))
    assert names - port_methods(protocols(ports)) == set()


def test_every_port_is_wired_for_both_backends() -> None:
    assert sorted(map(str, wired().values())) == sorted(map(str, protocols(ports)))


@pytest.mark.anyio
async def test_the_fake_and_the_adapter_implement_every_wired_port() -> None:
    async with httpx2.AsyncClient(base_url="http://paperless.test") as http:
        client = PaperlessClient(
            http, StaticTokenProvider("t"), retry=RetryPolicy(), upload_timeout=1.0
        )
        live = live_ports(client)
        missing = {
            (field, backend): members
            for field, port in wired().items()
            for backend, implementation in (
                ("fake", FakePaperless),
                ("live", cast("object", getattr(live, field))),
            )
            if (members := missing_members(implementation, port))
        }
    assert missing == {}


def test_every_writer_method_of_the_fake_is_guarded() -> None:
    assert unguarded_writes(protocols(ports), FakePaperless) == set()


def test_ports_are_found_by_introspection() -> None:
    assert InstanceReader in protocols(ports)
    assert "InstanceReader.get_overview" in port_methods(protocols(ports))
    assert wired() == {
        "instance": InstanceReader,
        "taxonomy": TaxonomyReader,
        "documents": DocumentReader,
    }


def test_only_real_contract_tests_on_both_backends_count(pytester: pytest.Pytester) -> None:
    _ = pytester.makeconftest(
        """import pytest
@pytest.fixture(params=["fake", "live"])
def backend(request): return request.param
"""
    )
    _ = pytester.makepyfile(
        test_probe="""import pytest
pytest.mark.contract("Probe.bare_expression")

@pytest.mark.contract("Probe.covered")
def test_covered(backend): ...

@pytest.mark.contract("Probe.no_backend")
def test_no_backend(): ...

@pytest.mark.contract("Probe.fake_only")
@pytest.mark.parametrize("backend", ["fake"], indirect=True)
def test_fake_only(backend): ...

def helper(): ...
"""
    )

    items = collect(pytester, pytester.path, "-p", "no:anyio")

    assert contracted(items) == {"Probe.covered"}
    assert contract_names(items) == {"Probe.covered", "Probe.no_backend", "Probe.fake_only"}


def test_a_missing_port_member_is_reported() -> None:
    class Probe(Protocol):
        def present(self) -> None: ...
        def absent(self) -> None: ...

    class Implementation:
        absent: int = 1

        def present(self) -> None: ...

    assert missing_members(Implementation(), Probe) == {"absent"}


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

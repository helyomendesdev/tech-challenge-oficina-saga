"""Fixtures do BDD: o motor da Saga (SAGA_E2E_DRIVER) e o contexto do cenario."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from e2e.driver import criar_driver  # noqa: E402  (depende do sys.path acima)


def pytest_configure(config):
    config.addinivalue_line("markers", "stack-real: cenario que tambem roda contra a stack local")


@pytest.fixture
def driver():
    return criar_driver()


@pytest.fixture
def contexto():
    """Dados que os steps `Dado` juntam para o step que inicia a Saga."""
    return {}

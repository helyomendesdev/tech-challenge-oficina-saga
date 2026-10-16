"""Fixtures do BDD: o motor da Saga (SAGA_E2E_DRIVER) e o contexto do cenario."""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from e2e.driver import cenario_do_titulo, criar_driver  # noqa: E402  (depende do sys.path acima)

PASTA_E2E = pathlib.Path(__file__).resolve().parent

# Nome do cenario no .feature -> cenario que o motor da stack real entende.
CENARIOS = {
    "Fluxo feliz até a OS finalizada": "feliz",
    "Compensação quando a execução falha": "falha-execucao",
    "Compensação quando o pagamento falha": "falha-pagamento",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "stack-real: cenario que tambem roda contra a stack local")


def pytest_collection_modifyitems(config, items):
    """Contra a stack real so rodam os cenarios `@stack-real`; os demais pulam por desenho."""
    if os.environ.get("SAGA_E2E_DRIVER", "").strip() != "stack-real":
        return
    pular = pytest.mark.skip(
        reason="sem a tag @stack-real por desenho: depende de reentrega de mensagem, que a stack real nao provoca"
    )
    for item in items:
        if PASTA_E2E in item.path.parents and item.get_closest_marker("stack-real") is None:
            item.add_marker(pular)


def _nome_do_cenario(request):
    """pytest-bdd 9 grava `<Funcionalidade>: <Cenario>` no __doc__ da funcao de teste."""
    doc = request.node.function.__doc__ or ""
    return doc.split(": ", 1)[-1]


@pytest.fixture
def driver(request):
    motor = criar_driver()
    motor.usar_cenario(cenario_do_titulo(_nome_do_cenario(request), CENARIOS))
    return motor


@pytest.fixture
def contexto():
    """Dados que os steps `Dado` juntam para o step que inicia a Saga."""
    return {}

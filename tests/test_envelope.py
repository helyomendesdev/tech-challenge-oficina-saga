import builtins
import importlib
import json
import pathlib
import sys

import pytest
from jsonschema import ValidationError

from oficina_saga import envelope

EXEMPLOS = pathlib.Path(__file__).resolve().parent.parent / "contrato" / "exemplos"
VALIDOS = sorted(EXEMPLOS.glob("*.json"))
INVALIDOS = sorted((EXEMPLOS / "invalidos").glob("*.json"))


def _carregar(caminho):
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_ha_exemplos():
    assert len(VALIDOS) == 18
    assert INVALIDOS


@pytest.mark.parametrize("caminho", VALIDOS, ids=lambda c: c.name)
def test_exemplos_validos_sao_aceitos(caminho):
    envelope.validar(_carregar(caminho))


@pytest.mark.parametrize("caminho", INVALIDOS, ids=lambda c: c.name)
def test_exemplos_invalidos_sao_recusados(caminho):
    exemplo = _carregar(caminho)
    exemplo.pop("_espera", None)
    exemplo["type"] = exemplo.pop("_schema", exemplo.get("type"))
    with pytest.raises(ValidationError):
        envelope.validar(exemplo)


def test_montar_gerar_orcamento_passa_no_schema():
    real = _carregar(EXEMPLOS / "GerarOrcamento.json")
    msg = envelope.montar(
        "GerarOrcamento",
        saga_id=real["sagaId"],
        os_id=real["osId"],
        request_id=real["requestId"],
        payload=real["payload"],
    )
    envelope.validar(msg)
    assert msg["type"] == "GerarOrcamento"
    assert msg["causationId"] is None
    assert msg["occurredAt"].endswith("Z")
    assert msg["messageId"] != real["messageId"]


def test_montar_funciona_sem_jsonschema_nem_referencing(monkeypatch):
    """`montar` so usa a stdlib: o extra `validacao` nao e exigido para importar nem para montar."""
    import oficina_saga

    antigo = sys.modules["oficina_saga.envelope"]
    monkeypatch.delattr(oficina_saga, "contrato", raising=False)  # `from pkg import sub` olha o atributo antes de importar
    for nome in list(sys.modules):
        if nome.split(".")[0] in ("jsonschema", "referencing") or nome in ("oficina_saga.contrato", "oficina_saga.envelope"):
            monkeypatch.delitem(sys.modules, nome)
    original = builtins.__import__

    def bloqueado(nome, globais=None, locais=None, lista=(), nivel=0):
        if nome.split(".")[0] in ("jsonschema", "referencing"):
            raise ImportError(f"{nome} bloqueado no teste")
        return original(nome, globais, locais, lista, nivel)

    monkeypatch.setattr(builtins, "__import__", bloqueado)
    try:
        novo = importlib.import_module("oficina_saga.envelope")
        assert "oficina_saga.contrato" not in sys.modules
        msg = novo.montar(
            "GerarOrcamento", saga_id="s", os_id=1, request_id="r", payload={"osId": 1}
        )
        assert msg["type"] == "GerarOrcamento"
        with pytest.raises(ImportError):
            novo.validar(msg)  # validar, sim, exige o extra
    finally:
        monkeypatch.undo()
        sys.modules["oficina_saga.envelope"] = antigo
        oficina_saga.envelope = antigo

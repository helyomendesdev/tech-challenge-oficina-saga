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


@pytest.mark.parametrize(
    "nome, tirar",
    [
        ("IniciarExecucao", lambda p: [i.pop("itemPecaOSId") for i in p["pecas"]]),
        ("ExecucaoFinalizada", lambda p: p.pop("pecasConsumidas")),
    ],
)
def test_v1_1_aceita_mensagem_no_formato_v1_0(nome, tirar):
    """ADR-004: os campos da v1.1 sao opcionais; produtor que ainda manda o formato v1.0 segue valido."""
    exemplo = _carregar(EXEMPLOS / f"{nome}.json")
    tirar(exemplo["payload"])
    envelope.validar(exemplo)


def test_v1_1_recusa_campo_desconhecido_no_item_consumido():
    """payload segue com additionalProperties false: consumidor v1.0 recusa os campos novos (ordem de adocao no ADR-004)."""
    exemplo = _carregar(EXEMPLOS / "ExecucaoFinalizada.json")
    exemplo["payload"]["pecasConsumidas"][0]["lote"] = "x"
    with pytest.raises(ValidationError):
        envelope.validar(exemplo)

import json
import pathlib

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

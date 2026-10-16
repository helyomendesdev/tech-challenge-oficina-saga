import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

CONTRATO = Path(__file__).resolve().parent.parent / "contrato"
openapi = yaml.safe_load((CONTRATO / "billing-aprovacao.openapi.yaml").read_text(encoding="utf-8"))
comum = json.loads((CONTRATO / "schemas" / "comum.schema.json").read_text(encoding="utf-8"))["definitions"]

ROTA = openapi["paths"]["/api/v1/orcamentos/{id}/aprovar"]["post"]
SCHEMAS = openapi["components"]["schemas"]


def _validador(no):
    return Draft7Validator({**no, "components": openapi["components"]})


def test_rota_codigos_e_seguranca():
    assert list(openapi["paths"]) == ["/api/v1/orcamentos/{id}/aprovar"]
    assert set(ROTA["responses"]) == {"202", "401", "404", "409"}
    assert ROTA["security"] == [{"bearerAuth": []}]
    assert openapi["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert [s["url"] for s in openapi["servers"]] == ["http://localhost:8001", "http://billing:8000"]


def test_parametro_id_e_uuid():
    (parametro,) = ROTA["parameters"]
    assert parametro["in"] == "path" and parametro["required"] is True
    assert parametro["schema"] == {"$ref": "#/components/schemas/Uuid"}


def test_formatos_batem_com_o_comum():
    assert SCHEMAS["Uuid"] == comum["uuid"]
    assert SCHEMAS["DataHora"] == comum["dataHora"]
    props = SCHEMAS["AprovacaoAceita"]["properties"]
    assert props["osId"]["type"] == comum["idNumerico"]["type"]
    assert props["osId"]["minimum"] == comum["idNumerico"]["minimum"]
    assert props["orcamentoId"] == {"$ref": "#/components/schemas/Uuid"}
    assert props["sagaId"] == {"$ref": "#/components/schemas/Uuid"}
    assert props["aprovadoEm"] == {"$ref": "#/components/schemas/DataHora"}
    erro = SCHEMAS["Erro"]["properties"]
    assert erro["codigo"]["pattern"] == comum["erro"]["properties"]["codigo"]["pattern"]
    assert erro["mensagem"]["maxLength"] == comum["erro"]["properties"]["mensagem"]["maxLength"]


def test_corpo_do_202():
    schema = ROTA["responses"]["202"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/AprovacaoAceita"}
    assert set(SCHEMAS["AprovacaoAceita"]["required"]) == {"orcamentoId", "osId", "sagaId", "aprovadoEm"}


def test_example_do_202_valida():
    exemplo = SCHEMAS["AprovacaoAceita"]["example"]
    erros = list(_validador(SCHEMAS["AprovacaoAceita"]).iter_errors(exemplo))
    assert not erros, [e.message for e in erros]


def test_validador_recusa_orcamento_id_que_nao_e_uuid():
    exemplo = {**SCHEMAS["AprovacaoAceita"]["example"], "orcamentoId": "123"}
    assert list(_validador(SCHEMAS["AprovacaoAceita"]).iter_errors(exemplo))


def test_erros_usam_o_schema_de_erro():
    for codigo in ("401", "404", "409"):
        assert ROTA["responses"][codigo]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Erro"}

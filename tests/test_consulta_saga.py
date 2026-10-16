import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

CONTRATO = Path(__file__).resolve().parent.parent / "contrato"
openapi = yaml.safe_load((CONTRATO / "os-consulta-saga.openapi.yaml").read_text(encoding="utf-8"))
maquina = yaml.safe_load((CONTRATO / "saga-estados.yaml").read_text(encoding="utf-8"))
envelope = json.loads((CONTRATO / "schemas" / "envelope.schema.json").read_text(encoding="utf-8"))
comum = json.loads((CONTRATO / "schemas" / "comum.schema.json").read_text(encoding="utf-8"))["definitions"]

ROTA = openapi["paths"]["/api/v1/ordens-servico/{id}/saga"]["get"]
SCHEMAS = openapi["components"]["schemas"]
SCHEMA_200 = ROTA["responses"]["200"]["content"]["application/json"]["schema"]


def test_enum_de_estado_bate_com_a_maquina():
    enum_estado = set(SCHEMAS["Saga"]["properties"]["estado"]["enum"])
    assert enum_estado == set(maquina["estados"])


def test_enum_de_status_os_bate_com_a_maquina():
    enum_status = set(SCHEMAS["Saga"]["properties"]["statusOs"]["enum"])
    assert enum_status == set(maquina["status_os"].values())


def test_enum_de_type_do_historico_bate_com_o_envelope():
    enum_type = set(SCHEMAS["MensagemDaSaga"]["properties"]["type"]["enum"])
    assert enum_type == set(envelope["properties"]["type"]["enum"])


def test_rota_e_seguranca():
    assert set(ROTA["responses"]) == {"200", "401", "403", "404"}
    assert ROTA["security"] == [{"bearerAuth": []}]


def _sem_nullable(no):
    """OpenAPI 3.0 para JSON Schema: nullable vira tipo [t, "null"]."""
    if isinstance(no, dict):
        no = {k: _sem_nullable(v) for k, v in no.items()}
        if no.pop("nullable", False) and "type" in no:
            no["type"] = [no["type"], "null"]
    elif isinstance(no, list):
        no = [_sem_nullable(v) for v in no]
    return no


def _validador():
    raiz = {**SCHEMA_200, "components": _sem_nullable(openapi["components"])}
    return Draft7Validator(raiz)


def test_example_valida_contra_o_schema_do_200():
    exemplo = SCHEMAS["ConsultaSaga"]["example"]
    erros = list(_validador().iter_errors(exemplo))
    assert not erros, [e.message for e in erros]
    assert exemplo["sagaAtual"] in {s["sagaId"] for s in exemplo["sagas"]}


def test_validador_recusa_estado_fora_do_enum():
    exemplo = json.loads(json.dumps(SCHEMAS["ConsultaSaga"]["example"]))
    exemplo["sagas"][0]["estado"] = "INVENTADO"
    assert list(_validador().iter_errors(exemplo))


def test_historico_traz_request_id_e_trace_id():
    mensagem = SCHEMAS["MensagemDaSaga"]
    assert {"requestId", "traceId"} <= set(mensagem["required"])
    request_id = mensagem["properties"]["requestId"]
    for campo in ("type", "minLength", "maxLength"):
        assert request_id[campo] == envelope["properties"]["requestId"][campo]
    trace_id = Draft7Validator(mensagem["properties"]["traceId"])
    assert not list(trace_id.iter_errors("4bf92f3577b34da6a3ce929d0e0e4736"))
    for invalido in ("4BF92F3577B34DA6A3CE929D0E0E4736", "4bf92f35", "g" * 32, "0" * 31):
        assert list(trace_id.iter_errors(invalido)), invalido


def test_historico_sem_trace_id_e_recusado():
    exemplo = json.loads(json.dumps(SCHEMAS["ConsultaSaga"]["example"]))
    del exemplo["sagas"][0]["historico"][0]["traceId"]
    assert list(_validador().iter_errors(exemplo))


def test_orcamento_id_e_opcional_na_saga():
    assert "orcamentoId" in SCHEMAS["Saga"]["properties"]
    assert "orcamentoId" not in SCHEMAS["Saga"]["required"]


def test_erro_repete_o_formato_do_comum():
    erro = SCHEMAS["Erro"]["properties"]
    assert erro["codigo"]["pattern"] == comum["erro"]["properties"]["codigo"]["pattern"]
    assert erro["mensagem"]["minLength"] == comum["erro"]["properties"]["mensagem"]["minLength"]
    assert erro["mensagem"]["maxLength"] == comum["erro"]["properties"]["mensagem"]["maxLength"]

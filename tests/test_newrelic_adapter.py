import builtins
import json
import sys
import types
from pathlib import Path

import pytest

from oficina_saga import newrelic_adapter as a
from oficina_saga import rastreio

W3C = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
EXEMPLO = Path(__file__).resolve().parent.parent / "contrato" / "exemplos" / "ExecucaoIniciada.json"
TRACEPARENT_NR = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture
def nr_falso(monkeypatch):
    """Agente falso com as assinaturas documentadas em tracing/newrelic-agente.md."""
    chamadas = {"accept": [], "custom": []}

    def insert(headers):
        headers.extend(
            [
                ("traceparent", TRACEPARENT_NR),
                ("tracestate", "nr=abc"),
                ("newrelic", "eyJ2IjpbMCwxXX0="),
            ]
        )

    def accept(headers, transport_type="HTTP"):
        chamadas["accept"].append((dict(headers), transport_type))

    def add_custom_attributes(items):
        chamadas["custom"].extend(items)
        return True

    agent = types.ModuleType("newrelic.agent")
    agent.insert_distributed_trace_headers = insert
    agent.accept_distributed_trace_headers = accept
    agent.add_custom_attributes = add_custom_attributes
    pacote = types.ModuleType("newrelic")
    pacote.agent = agent
    monkeypatch.setitem(sys.modules, "newrelic", pacote)
    monkeypatch.setitem(sys.modules, "newrelic.agent", agent)
    return chamadas


@pytest.fixture
def sem_nr(monkeypatch):
    monkeypatch.delitem(sys.modules, "newrelic", raising=False)
    monkeypatch.delitem(sys.modules, "newrelic.agent", raising=False)
    original = builtins.__import__

    def bloqueado(nome, *args, **kwargs):
        if nome == "newrelic" or nome.startswith("newrelic."):
            raise ImportError("newrelic bloqueado no teste")
        return original(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", bloqueado)


def _envelope():
    return json.loads(EXEMPLO.read_text(encoding="utf-8"))


def test_cabecalho_proprietario_newrelic_nao_vai_para_a_fila(nr_falso):
    attrs = a.atributos_para_envio()
    assert set(attrs) == {"traceparent", "tracestate"}
    assert attrs["traceparent"] == {"DataType": "String", "StringValue": TRACEPARENT_NR}
    assert attrs["tracestate"]["StringValue"] == "nr=abc"


def test_accept_recebe_transport_type_queue_e_o_traceparent(nr_falso):
    efetivo = a.aceitar_recebido(rastreio.injetar({}, W3C, "k=v"))
    assert efetivo == W3C
    assert nr_falso["accept"] == [({"traceparent": W3C, "tracestate": "k=v"}, "Queue")]


def test_sem_newrelic_cai_no_rastreio_filho_do_atual(sem_nr):
    attrs = a.atributos_para_envio(traceparent_atual=W3C)
    filho = rastreio.parse_traceparent(attrs["traceparent"]["StringValue"])
    pai = rastreio.parse_traceparent(W3C)
    assert filho is not None
    assert filho.trace_id == pai.trace_id
    assert filho.parent_id != pai.parent_id


def test_sem_newrelic_e_sem_atual_abre_trace_novo(sem_nr):
    attrs = a.atributos_para_envio({"outro": {"DataType": "String", "StringValue": "x"}})
    assert rastreio.traceparent_valido(attrs["traceparent"]["StringValue"])
    assert "outro" in attrs


def test_sem_newrelic_aceitar_devolve_o_recebido_e_registrar_nao_falha(sem_nr):
    assert a.aceitar_recebido(rastreio.injetar({}, W3C)) == W3C
    assert rastreio.traceparent_valido(a.aceitar_recebido({}))
    a.registrar_mensagem(_envelope())


def test_transacao_inexistente_lista_vazia_cai_no_rastreio(nr_falso, monkeypatch):
    monkeypatch.setattr(sys.modules["newrelic.agent"], "insert_distributed_trace_headers", lambda h: None)
    attrs = a.atributos_para_envio(traceparent_atual=W3C)
    filho = rastreio.parse_traceparent(attrs["traceparent"]["StringValue"])
    assert filho.trace_id == rastreio.parse_traceparent(W3C).trace_id


def test_registrar_mensagem_grava_exatamente_atributos_emitidos(nr_falso):
    envelope = _envelope()
    a.registrar_mensagem(envelope)
    gravados = dict(nr_falso["custom"])
    assert set(gravados) == a.ATRIBUTOS_EMITIDOS
    assert gravados["messageType"] == envelope["type"]
    assert gravados["sagaId"] == envelope["sagaId"]
    assert gravados["osId"] == envelope["osId"]

"""DriverStackReal contra servidores HTTP de teste que falam os formatos dos dois OpenAPI.

O servidor simula o -os (consulta da Saga) e o Billing (aprovacao). Cada resposta
que ele envia e validada contra o schema do contrato; qualquer desvio derruba o
teste no fim (fixture `stack`).
"""

import json
import re
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator

from e2e.driver_stack_real import DriverStackReal

CONTRATO = Path(__file__).resolve().parent.parent / "contrato"
CONSULTA = yaml.safe_load((CONTRATO / "os-consulta-saga.openapi.yaml").read_text(encoding="utf-8"))
APROVACAO = yaml.safe_load((CONTRATO / "billing-aprovacao.openapi.yaml").read_text(encoding="utf-8"))

OS_ID = 42
SAGA_ID = "5c1f0a7e-3b2d-4e8a-9c4f-1a2b3c4d5e6f"
ORCAMENTO_ID = "93f0be09-1d79-5c92-a371-0cd69b36a8de"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
OUTRO_TRACE_ID = "00f067aa0ba902b7a3ce929d0e0e4736"


def _sem_nullable(no):
    """OpenAPI 3.0 para JSON Schema: nullable vira tipo [t, "null"]."""
    if isinstance(no, dict):
        no = {k: _sem_nullable(v) for k, v in no.items()}
        if no.pop("nullable", False) and "type" in no:
            no["type"] = [no["type"], "null"]
    elif isinstance(no, list):
        no = [_sem_nullable(v) for v in no]
    return no


def _validador(openapi, rota, metodo, codigo):
    schema = openapi["paths"][rota][metodo]["responses"][codigo]["content"]["application/json"]["schema"]
    return Draft7Validator({**schema, "components": _sem_nullable(openapi["components"])})


def _mensagem(n, tipo, direcao, request_id="req-bdd-a1b2c3", trace_id=TRACE_ID):
    return {
        "type": tipo,
        "messageId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"mensagem-{n}")),
        "causationId": None,
        "occurredAt": "2026-10-01T12:00:00Z",
        "direcao": direcao,
        "requestId": request_id,
        "traceId": trace_id,
    }


def _consulta(estado, *historico, orcamento_id=None, status_os="AGUARDANDO"):
    """Resposta 200 da consulta; `historico` e uma lista de (type, direcao) ou de mensagens prontas."""
    mensagens = [
        h if isinstance(h, dict) else _mensagem(i, h[0], h[1]) for i, h in enumerate(historico, start=1)
    ]
    saga = {
        "sagaId": SAGA_ID,
        "estado": estado,
        "statusOs": status_os,
        "iniciadaEm": "2026-10-01T12:00:00Z",
        "atualizadaEm": "2026-10-01T12:00:00Z",
        "historico": mensagens,
    }
    if orcamento_id:
        saga["orcamentoId"] = orcamento_id
    return {"osId": OS_ID, "sagaAtual": SAGA_ID, "sagas": [saga]}


class _Estado:
    """O que o servidor de teste serve e o que ele viu."""

    def __init__(self):
        self.consultas = []  # respostas da consulta, em ordem de poll; None = 404; a ultima se repete
        self.polls = 0
        self.aprovacao = 202  # codigo que o Billing devolve
        self.aberturas = []  # (corpo, Authorization)
        self.aprovacoes = []  # (id da rota, Authorization)
        self.erros_de_schema = []


def _handler(estado):
    erro_os = _validador(CONSULTA, "/api/v1/ordens-servico/{id}/saga", "get", "404")
    consulta_200 = _validador(CONSULTA, "/api/v1/ordens-servico/{id}/saga", "get", "200")
    aceita = _validador(APROVACAO, "/api/v1/orcamentos/{id}/aprovar", "post", "202")
    erros_billing = {
        c: _validador(APROVACAO, "/api/v1/orcamentos/{id}/aprovar", "post", c) for c in ("401", "404", "409")
    }
    erro_billing = {
        "401": {"codigo": "NAO_AUTENTICADO", "mensagem": "Token ausente."},
        "404": {"codigo": "ORCAMENTO_NAO_ENCONTRADO", "mensagem": "Orcamento inexistente."},
        "409": {"codigo": "ORCAMENTO_NAO_AGUARDA_APROVACAO", "mensagem": "O orcamento nao esta aguardando aprovacao."},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _responder(self, codigo, corpo, validador=None):
            if validador is not None:
                estado.erros_de_schema += [e.message for e in validador.iter_errors(corpo)]
            bruto = json.dumps(corpo).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(bruto)))
            self.end_headers()
            self.wfile.write(bruto)

        def do_POST(self):
            tamanho = int(self.headers.get("Content-Length") or 0)
            corpo = json.loads(self.rfile.read(tamanho)) if tamanho else None
            autorizacao = self.headers.get("Authorization")
            if self.path == "/api/v1/ordens-servico/abrir/":
                estado.aberturas.append((corpo, autorizacao))
                return self._responder(201, {"id": OS_ID})
            achou = re.fullmatch(r"/api/v1/orcamentos/([^/]+)/aprovar", self.path)
            if achou:
                estado.aprovacoes.append((achou.group(1), autorizacao))
                if estado.aprovacao == 202:
                    corpo = {
                        "orcamentoId": achou.group(1),
                        "osId": OS_ID,
                        "sagaId": SAGA_ID,
                        "aprovadoEm": "2026-10-01T12:06:00Z",
                    }
                    return self._responder(202, corpo, aceita)
                codigo = str(estado.aprovacao)
                return self._responder(estado.aprovacao, erro_billing[codigo], erros_billing[codigo])
            self._responder(404, {"codigo": "ROTA_INEXISTENTE", "mensagem": self.path})

        def do_GET(self):
            if self.path != f"/api/v1/ordens-servico/{OS_ID}/saga":
                return self._responder(404, {"codigo": "ROTA_INEXISTENTE", "mensagem": self.path})
            estado.polls += 1
            if not estado.consultas:
                return self._responder(404, {"codigo": "SAGA_NAO_ENCONTRADA", "mensagem": "Sem Saga."}, erro_os)
            resposta = estado.consultas[min(estado.polls, len(estado.consultas)) - 1]
            if resposta is None:
                return self._responder(404, {"codigo": "SAGA_NAO_ENCONTRADA", "mensagem": "Sem Saga."}, erro_os)
            self._responder(200, resposta, consulta_200)

    return Handler


@pytest.fixture
def stack():
    estado = _Estado()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _handler(estado))
    thread = threading.Thread(target=servidor.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    estado.url = f"http://127.0.0.1:{servidor.server_port}"
    yield estado
    servidor.shutdown()
    servidor.server_close()
    assert not estado.erros_de_schema, f"o servidor de teste saiu do contrato: {estado.erros_de_schema}"


@pytest.fixture
def driver(stack, monkeypatch):
    """Driver apontado para o servidor de teste, sem token e com polls rapidos."""
    monkeypatch.setenv("SAGA_E2E_OS_URL", stack.url)
    monkeypatch.setenv("SAGA_E2E_BILLING_URL", stack.url)
    monkeypatch.setenv("SAGA_E2E_TIMEOUT_S", "0.4")
    monkeypatch.setenv("SAGA_E2E_INTERVALO_S", "0.02")
    monkeypatch.delenv("SAGA_E2E_TOKEN", raising=False)
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, servicos=[{"servicoId": 12, "quantidade": 1}], pecas=[])
    return motor


def test_espera_estado_chega_apos_n_polls(stack, driver):
    stack.consultas = [
        _consulta("ORCAMENTO_SOLICITADO", ("GerarOrcamento", "ENVIADA")),
        _consulta("ORCAMENTO_SOLICITADO", ("GerarOrcamento", "ENVIADA")),
        _consulta("AGUARDANDO_APROVACAO", ("GerarOrcamento", "ENVIADA"), ("OrcamentoGerado", "RECEBIDA")),
    ]
    driver.esperar_estado("AGUARDANDO_APROVACAO")
    assert stack.polls == 3


def test_espera_estado_falha_quando_o_estado_nao_chega(stack, driver):
    stack.consultas = [_consulta("ORCAMENTO_SOLICITADO", ("GerarOrcamento", "ENVIADA"))]
    with pytest.raises(AssertionError) as erro:
        driver.esperar_estado("AGUARDANDO_APROVACAO")
    mensagem = str(erro.value)
    assert "AGUARDANDO_APROVACAO" in mensagem
    assert "ultimo estado ORCAMENTO_SOLICITADO" in mensagem
    assert "GerarOrcamento" in mensagem
    assert stack.polls > 1


def test_espera_continua_enquanto_a_consulta_devolve_404(stack, driver):
    stack.consultas = [None, None, _consulta("INICIADA")]
    driver.esperar_estado("INICIADA")
    assert stack.polls == 3


def test_resposta_de_participante_so_vale_recebida(stack, driver):
    stack.consultas = [_consulta("EM_EXECUCAO", ("PagamentoConfirmado", "ENVIADA"))]
    with pytest.raises(AssertionError, match="PagamentoConfirmado"):
        driver.acontece_resposta("PagamentoConfirmado")
    stack.consultas = [_consulta("EM_EXECUCAO", ("PagamentoConfirmado", "RECEBIDA"))]
    driver.acontece_resposta("PagamentoConfirmado")


def test_comando_do_orquestrador_so_vale_enviado(stack, driver):
    stack.consultas = [_consulta("PAGAMENTO_SOLICITADO", ("SolicitarPagamento", "RECEBIDA"))]
    with pytest.raises(AssertionError, match="SolicitarPagamento"):
        driver.esperar_comando("Billing", "SolicitarPagamento")
    stack.consultas = [_consulta("PAGAMENTO_SOLICITADO", ("SolicitarPagamento", "ENVIADA"))]
    driver.esperar_comando("Billing", "SolicitarPagamento")


def test_status_os_vem_da_saga_atual(stack, driver):
    stack.consultas = [_consulta("CANCELADA", status_os="CANCELADA")]
    assert driver.status_os() == "CANCELADA"


def test_aprovacao_chama_a_rota_do_billing_com_bearer(stack, driver, monkeypatch):
    monkeypatch.setenv("SAGA_E2E_TOKEN", "jwt-de-teste")
    stack.consultas = [
        _consulta(
            "AGUARDANDO_APROVACAO",
            ("GerarOrcamento", "ENVIADA"),
            ("OrcamentoGerado", "RECEBIDA"),
            orcamento_id=ORCAMENTO_ID,
        )
    ]
    driver.aprovar_orcamento()
    assert stack.aprovacoes == [(ORCAMENTO_ID, "Bearer jwt-de-teste")]


def test_aprovacao_espera_o_orcamento_gerado(stack, driver):
    stack.consultas = [
        _consulta("ORCAMENTO_SOLICITADO", ("GerarOrcamento", "ENVIADA")),
        _consulta(
            "AGUARDANDO_APROVACAO",
            ("GerarOrcamento", "ENVIADA"),
            ("OrcamentoGerado", "RECEBIDA"),
            orcamento_id=ORCAMENTO_ID,
        ),
    ]
    driver.aprovar_orcamento()
    assert [a[0] for a in stack.aprovacoes] == [ORCAMENTO_ID]
    assert stack.polls == 2


def test_aprovacao_sem_orcamento_id_na_consulta_falha_com_mensagem_clara(stack, driver):
    stack.consultas = [
        _consulta("AGUARDANDO_APROVACAO", ("GerarOrcamento", "ENVIADA"), ("OrcamentoGerado", "RECEBIDA"))
    ]
    with pytest.raises(AssertionError, match="orcamentoId"):
        driver.aprovar_orcamento()
    assert stack.aprovacoes == []


def test_aprovacao_recusada_pelo_billing_falha(stack, driver):
    stack.aprovacao = 409
    stack.consultas = [
        _consulta(
            "AGUARDANDO_APROVACAO",
            ("GerarOrcamento", "ENVIADA"),
            ("OrcamentoGerado", "RECEBIDA"),
            orcamento_id=ORCAMENTO_ID,
        )
    ]
    with pytest.raises(AssertionError, match="409"):
        driver.aprovar_orcamento()


def test_correlacao_igual_em_todo_o_historico_passa(stack, driver):
    stack.consultas = [_consulta("EM_EXECUCAO", ("GerarOrcamento", "ENVIADA"), ("OrcamentoGerado", "RECEBIDA"))]
    driver.conferir_correlacao()


def test_correlacao_com_trace_id_divergente_falha(stack, driver):
    historico = [
        _mensagem(1, "GerarOrcamento", "ENVIADA"),
        _mensagem(2, "OrcamentoGerado", "RECEBIDA", trace_id=OUTRO_TRACE_ID),
    ]
    stack.consultas = [_consulta("EM_EXECUCAO", *historico)]
    with pytest.raises(AssertionError, match="traceId"):
        driver.conferir_correlacao()


def test_correlacao_com_request_id_divergente_falha(stack, driver):
    historico = [
        _mensagem(1, "GerarOrcamento", "ENVIADA"),
        _mensagem(2, "OrcamentoGerado", "RECEBIDA", request_id="req-outro"),
    ]
    stack.consultas = [_consulta("EM_EXECUCAO", *historico)]
    with pytest.raises(AssertionError, match="requestId"):
        driver.conferir_correlacao()


def test_abertura_leva_os_dados_do_cenario_e_nao_leva_bearer_sem_token(stack, driver):
    driver.usar_cenario("falha-pagamento")
    driver.iniciar_saga(cliente_id=7, veiculo_id=15, servicos=[], pecas=[])
    corpo, autorizacao = stack.aberturas[-1]
    assert corpo["clienteId"] == 7 and corpo["veiculoId"] == 15
    assert "pagamento" in corpo
    assert autorizacao is None
    driver.usar_cenario("feliz")
    driver.iniciar_saga(cliente_id=7, veiculo_id=15, servicos=[], pecas=[])
    assert "pagamento" not in stack.aberturas[-1][0]


def test_cenario_desconhecido_e_recusado(driver):
    with pytest.raises(ValueError, match="inventado"):
        driver.usar_cenario("inventado")


def test_conexao_recusada_da_mensagem_clara(monkeypatch):
    with socket.socket() as soquete:
        soquete.bind(("127.0.0.1", 0))
        porta = soquete.getsockname()[1]
    monkeypatch.setenv("SAGA_E2E_OS_URL", f"http://127.0.0.1:{porta}")
    motor = DriverStackReal()
    with pytest.raises(AssertionError, match="nao consegui falar com o OS Service"):
        motor.iniciar_saga(cliente_id=1, veiculo_id=1, servicos=[], pecas=[])

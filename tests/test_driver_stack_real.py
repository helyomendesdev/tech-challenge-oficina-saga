"""DriverStackReal contra servidores HTTP de teste que falam os formatos dos dois OpenAPI.

O servidor simula o -os (login, abertura, leitura da OS e consulta da Saga) e o
Billing (aprovacao e pagamento). Cada resposta que ele envia e validada contra o
schema do contrato; a abertura e conferida contra as regras do serializer do -os
e o JWT de cliente contra uma copia de `client_identity` do Billing. Qualquer
desvio derruba o teste no fim (fixture `stack`).
"""

import base64
import json
import os
import re
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest
import yaml
from jsonschema import Draft7Validator

from e2e.driver import cenario_do_titulo
from e2e.driver_stack_real import DriverStackReal
from stack import gerar_chaves_e2e

CONTRATO = Path(__file__).resolve().parent.parent / "contrato"
CONSULTA = yaml.safe_load((CONTRATO / "os-consulta-saga.openapi.yaml").read_text(encoding="utf-8"))
APROVACAO = yaml.safe_load((CONTRATO / "billing-aprovacao.openapi.yaml").read_text(encoding="utf-8"))

OS_ID = 42
CLIENTE_ID = 77  # id do Cliente que o -os criou na abertura (o dono do orcamento no Billing)
TOKEN_ATENDENTE = "jwt-do-atendente"
ATENDENTE = ("atendente.demo", "senha-de-teste")
PAGAMENTO_ID = "5b6c9c0e-7a41-5d0e-9b52-1f6f3b8e2a10"
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


# --- copia de billing_service/security.py (client_identity), do clone em 2026-11-06 -------------------

_OBRIGATORIAS = ["iss", "aud", "sub", "cliente_id", "principal_type", "token_type", "iat", "exp", "jti"]


def _client_identity(authorization, public_key_b64):
    """Mesmas regras do Billing; levanta ValueError no lugar de BillingError(401)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ValueError("JWT obrigatorio")
    try:
        chave = base64.b64decode(public_key_b64, validate=True)
        claims = jwt.decode(
            authorization[7:],
            chave,
            algorithms=["RS256"],
            issuer="oficina-auth",
            audience="oficina-api",
            options={"require": _OBRIGATORIAS},
        )
        cid = claims["cliente_id"]
        if type(cid) is not int or cid <= 0 or claims["sub"] != f"cliente:{cid}":
            raise ValueError("Invalid client")
        if claims["principal_type"] != "cliente" or claims["token_type"] != "access":
            raise ValueError("Invalid principal")
        if type(claims["iat"]) is not int or type(claims["exp"]) is not int:
            raise ValueError("Invalid date")
        if claims["exp"] <= claims["iat"] or uuid.UUID(claims["jti"]).version != 4:
            raise ValueError("Invalid claims")
        if {"cpf", "documento", "nome", "email", "created_by_id"}.intersection(claims):
            raise ValueError("Unexpected personal data")
        return cid
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as erro:
        raise ValueError(f"JWT invalido: {erro}") from None


# --- regras do serializer de abertura do -os (interfaces/api/serializers.py) ------------------------


def _cpf_valido(documento):
    if not re.fullmatch(r"\d{11}", documento) or len(set(documento)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(d) * peso for d, peso in zip(documento[:tamanho], range(tamanho + 1, 1, -1)))
        if soma * 10 % 11 % 10 != int(documento[tamanho]):
            return False
    return True


def _erros_da_abertura(corpo, vistos):
    """Desvios do corpo em relacao a AbrirOrdemServicoInputSerializer; `vistos` guarda documentos/placas ja usados."""
    if not isinstance(corpo, dict):
        return ["corpo nao e objeto"]
    erros = []
    cliente, veiculo = corpo.get("cliente"), corpo.get("veiculo")
    if not isinstance(cliente, dict) or set(cliente) != {"nome", "documento", "email", "telefone"}:
        erros.append(f"cliente fora do serializer: {cliente}")
    else:
        if not _cpf_valido(cliente["documento"]) or not isinstance(cliente["telefone"], str):
            erros.append(f"documento/telefone invalidos: {cliente}")
        if "@" not in cliente["email"] or not cliente["nome"]:
            erros.append(f"email/nome invalidos: {cliente}")
        if ("documento", cliente["documento"]) in vistos:
            erros.append(f"documento repetido: {cliente['documento']}")
        vistos.add(("documento", cliente["documento"]))
    if not isinstance(veiculo, dict) or set(veiculo) != {"placa", "marca", "modelo", "ano"}:
        erros.append(f"veiculo fora do serializer: {veiculo}")
    else:
        if not re.fullmatch(r"[A-Z]{3}\d[A-Z\d]\d{2}", veiculo["placa"]) or type(veiculo["ano"]) is not int:
            erros.append(f"placa/ano invalidos: {veiculo}")
        if ("placa", veiculo["placa"]) in vistos:
            erros.append(f"placa repetida: {veiculo['placa']}")
        vistos.add(("placa", veiculo["placa"]))
    servicos = corpo.get("servicos", [])
    if not isinstance(servicos, list) or any(type(x) is not int or x < 1 for x in servicos):
        erros.append(f"servicos deve ser lista de ids: {servicos}")
    for peca in corpo.get("pecas", []):
        if not isinstance(peca, dict) or set(peca) != {"peca_id", "quantidade"}:
            erros.append(f"peca fora do serializer: {peca}")
    return erros


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
        self.prontidao_recusas = 0  # quantos GET /health/ready/ ainda respondem 503
        self.prontidoes = 0
        self.aberturas = []  # (corpo, Authorization)
        self.aprovacoes = []  # (id da rota, Authorization)
        self.erros_de_schema = []
        self.erros_de_corpo = []  # abertura fora das regras do -os
        self.documentos_e_placas = set()
        self.formato_antigo = False  # a abertura responde {"id": ...} em vez de ordem_servico_id
        self.logins = []  # corpos dos POST em /auth/token/
        self.token_atendente = None  # se definido, o -os exige este Bearer (401 sem ele)
        self.os_autorizacoes = []  # Authorization de cada chamada autenticada ao -os
        self.leituras_da_os = 0
        self.chave_publica_b64 = None  # se definida, o Billing valida o JWT como client_identity
        self.clientes_aceitos = []  # cliente_id que client_identity extraiu de cada chamada ao Billing
        self.recusas_de_token = []
        self.pagamento_404s = 0  # quantas consultas do pagamento ainda respondem 404
        self.consultas_pagamento = 0


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

        def _atendente_ok(self):
            """True se o -os aceita a chamada; senao ja respondeu 401."""
            if estado.token_atendente is None:
                return True
            autorizacao = self.headers.get("Authorization")
            estado.os_autorizacoes.append(autorizacao)
            if autorizacao == f"Bearer {estado.token_atendente}":
                return True
            self._responder(401, {"detail": "Credenciais nao fornecidas."})
            return False

        def _cliente_ok(self):
            """True se o Billing aceita o JWT (quando ha chave publica); senao ja respondeu 401."""
            if estado.chave_publica_b64 is None:
                return True
            try:
                cliente = _client_identity(self.headers.get("Authorization"), estado.chave_publica_b64)
            except ValueError as erro:
                estado.recusas_de_token.append(str(erro))
                self._responder(401, erro_billing["401"], erros_billing["401"])
                return False
            estado.clientes_aceitos.append(cliente)
            return True

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
            if self.path == "/api/v1/auth/token/":
                estado.logins.append(corpo)
                if (corpo or {}).get("username") == ATENDENTE[0] and (corpo or {}).get("password") == ATENDENTE[1]:
                    return self._responder(200, {"access": TOKEN_ATENDENTE, "refresh": "refresh-de-teste"})
                return self._responder(401, {"detail": "Credenciais invalidas."})
            if self.path == "/api/v1/ordens-servico/abrir/":
                if not self._atendente_ok():
                    return
                estado.aberturas.append((corpo, autorizacao))
                estado.erros_de_corpo += _erros_da_abertura(corpo, estado.documentos_e_placas)
                if estado.formato_antigo:
                    return self._responder(201, {"id": OS_ID})
                resposta = {
                    "ordem_servico_id": OS_ID,
                    "status": "RECEBIDA",
                    "valor_total": "200.00",
                    "mensagem": "Ordem de servico criada.",
                }
                return self._responder(201, resposta)
            achou = re.fullmatch(r"/api/v1/orcamentos/([^/]+)/aprovar", self.path)
            if achou:
                estado.aprovacoes.append((achou.group(1), autorizacao))
                if not self._cliente_ok():
                    return
                if estado.chave_publica_b64 is not None and estado.clientes_aceitos[-1] != CLIENTE_ID:
                    return self._responder(403, {"codigo": "ACESSO_NEGADO", "mensagem": "Orcamento de outro cliente."})
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
            if self.path == "/health/ready/":
                estado.prontidoes += 1
                if estado.prontidoes <= estado.prontidao_recusas:
                    return self._responder(503, {"status": "migrando"})
                return self._responder(200, {"status": "ok"})
            achou = re.fullmatch(r"/api/v1/orcamentos/([^/]+)/pagamento", self.path)
            if achou:
                estado.consultas_pagamento += 1
                if not self._cliente_ok():
                    return
                if estado.consultas_pagamento <= estado.pagamento_404s:
                    return self._responder(404, {"codigo": "PAGAMENTO_NAO_ENCONTRADO", "mensagem": "Sem pagamento."})
                return self._responder(
                    200,
                    {
                        "pagamentoId": PAGAMENTO_ID,
                        "orcamentoId": achou.group(1),
                        "status": "PENDENTE",
                        "valorTotalCentavos": 20000,
                        "checkoutUrl": None,
                        "provedor": "fake",
                    },
                )
            if self.path == f"/api/v1/ordens-servico/{OS_ID}/":
                if not self._atendente_ok():
                    return
                estado.leituras_da_os += 1
                return self._responder(200, {"id": OS_ID, "cliente": CLIENTE_ID, "veiculo": 3, "status": "RECEBIDA"})
            if self.path != f"/api/v1/ordens-servico/{OS_ID}/saga/":
                return self._responder(404, {"codigo": "ROTA_INEXISTENTE", "mensagem": self.path})
            if not self._atendente_ok():
                return
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
    assert not estado.erros_de_corpo, f"a abertura saiu das regras do -os: {estado.erros_de_corpo}"


def _apontar(stack, monkeypatch):
    """Aponta o driver para o servidor de teste, sem credenciais e com polls rapidos."""
    monkeypatch.setenv("SAGA_E2E_OS_URL", stack.url)
    monkeypatch.setenv("SAGA_E2E_BILLING_URL", stack.url)
    monkeypatch.setenv("SAGA_E2E_TIMEOUT_S", "0.4")
    monkeypatch.setenv("SAGA_E2E_INTERVALO_S", "0.02")
    for nome in (
        "SAGA_E2E_TOKEN",
        "SAGA_E2E_ATENDENTE_USERNAME",
        "SAGA_E2E_ATENDENTE_PASSWORD",
        "SAGA_E2E_CLIENTE_CHAVE_PRIVADA",
        "SAGA_E2E_SIMULAR_PAGAMENTO_CMD",
        "SAGA_E2E_EXECUTION_CLI",
    ):
        monkeypatch.delenv(nome, raising=False)


ITENS = dict(servicos=[{"servicoId": 12, "quantidade": 1}], pecas=[{"pecaId": 40, "quantidade": 2}])


@pytest.fixture
def driver(stack, monkeypatch):
    """Driver apontado para o servidor de teste, sem credenciais e com polls rapidos."""
    _apontar(stack, monkeypatch)
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    return motor


@pytest.fixture
def com_atendente(stack, monkeypatch):
    """O -os exige o Bearer do atendente; o driver recebe usuario e senha."""
    _apontar(stack, monkeypatch)
    stack.token_atendente = TOKEN_ATENDENTE
    monkeypatch.setenv("SAGA_E2E_ATENDENTE_USERNAME", ATENDENTE[0])
    monkeypatch.setenv("SAGA_E2E_ATENDENTE_PASSWORD", ATENDENTE[1])


@pytest.fixture
def com_chaves(stack, tmp_path, monkeypatch):
    """Par RSA gerado por stack/gerar_chaves_e2e.py; o Billing de teste valida com a publica."""
    _apontar(stack, monkeypatch)
    stack.chave_publica_b64 = gerar_chaves_e2e.gerar(tmp_path)
    monkeypatch.setenv("SAGA_E2E_CLIENTE_CHAVE_PRIVADA", str(tmp_path / gerar_chaves_e2e.NOME_PRIVADA))
    return tmp_path


def _orcamento_gerado(*extra):
    return _consulta(
        "AGUARDANDO_APROVACAO",
        ("GerarOrcamento", "ENVIADA"),
        ("OrcamentoGerado", "RECEBIDA"),
        *extra,
        orcamento_id=ORCAMENTO_ID,
    )


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


def test_espera_estado_aceita_estado_posterior_ao_esperado(stack, driver):
    # COMPENSANDO e transitorio na stack real: o poll ja pode ver a Saga cancelada.
    stack.consultas = [_consulta("CANCELADA", ("CancelarOrcamento", "ENVIADA"), status_os="CANCELADA")]
    driver.esperar_estado("COMPENSANDO")
    assert stack.polls == 1


def test_espera_estado_nao_aceita_estado_anterior_ao_esperado(stack, driver):
    stack.consultas = [_consulta("ORCAMENTO_SOLICITADO", ("GerarOrcamento", "ENVIADA"))]
    with pytest.raises(AssertionError, match="timeout"):
        driver.esperar_estado("EM_EXECUCAO")
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


def test_abertura_leva_os_dados_do_cenario_e_nao_leva_bearer_sem_credencial(stack, driver):
    driver.usar_cenario("falha-pagamento")
    driver.iniciar_saga(cliente_id=7, veiculo_id=15, servicos=[], pecas=[])
    corpo, autorizacao = stack.aberturas[-1]
    assert "pagamento" in corpo
    assert autorizacao is None
    driver.usar_cenario("feliz")
    driver.iniciar_saga(cliente_id=7, veiculo_id=15, servicos=[], pecas=[])
    assert "pagamento" not in stack.aberturas[-1][0]


def test_abertura_usa_o_corpo_real_do_os_e_le_ordem_servico_id(stack, driver):
    corpo, _ = stack.aberturas[0]
    assert set(corpo) == {"cliente", "veiculo", "servicos", "pecas"}
    assert "clienteId" not in corpo and "veiculoId" not in corpo
    assert corpo["servicos"] == [12]
    assert corpo["pecas"] == [{"peca_id": 40, "quantidade": 2}]
    # O id vem de `ordem_servico_id`: o servidor de teste so conhece as rotas da OS 42.
    stack.consultas = [_consulta("INICIADA")]
    driver.esperar_estado("INICIADA")
    assert stack.polls == 1


def test_abertura_com_a_resposta_antiga_sem_ordem_servico_id_falha_com_mensagem_clara(stack, monkeypatch):
    _apontar(stack, monkeypatch)
    stack.formato_antigo = True
    motor = DriverStackReal()
    with pytest.raises(AssertionError, match="ordem_servico_id"):
        motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)


def test_cada_abertura_gera_documento_e_placa_novos(stack, driver):
    driver.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    (primeiro, _), (segundo, _) = stack.aberturas
    assert primeiro["cliente"]["documento"] != segundo["cliente"]["documento"]
    assert primeiro["veiculo"]["placa"] != segundo["veiculo"]["placa"]
    # a fixture `stack` tambem confere CPF e placa validos e sem repeticao (erros_de_corpo)


def test_cenario_de_falha_na_execucao_manda_a_peca_com_peca_id(stack, driver):
    driver.usar_cenario("falha-execucao")
    driver.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    assert stack.aberturas[-1][0]["pecas"] == [{"peca_id": 999999, "quantidade": 1}]


def test_cliente_id_vem_da_leitura_da_os_e_nao_do_dado_do_feature(stack, com_atendente):
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    assert stack.leituras_da_os == 1
    assert motor._cliente_id == CLIENTE_ID


def test_login_do_atendente_e_bearer_em_toda_chamada_ao_os(stack, com_atendente):
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    stack.consultas = [_consulta("INICIADA")]
    motor.esperar_estado("INICIADA")
    assert stack.logins == [dict(zip(("username", "password"), ATENDENTE))]  # uma vez so
    # abertura, leitura da OS e consulta da Saga: tres chamadas, todas com o token do login
    assert stack.os_autorizacoes == [f"Bearer {TOKEN_ATENDENTE}"] * 3
    assert stack.aberturas[0][1] == f"Bearer {TOKEN_ATENDENTE}"


def test_login_recusado_falha_com_mensagem_clara_e_nao_abre_a_os(stack, com_atendente, monkeypatch):
    monkeypatch.setenv("SAGA_E2E_ATENDENTE_PASSWORD", "errada")
    motor = DriverStackReal()
    with pytest.raises(AssertionError, match=r"login do atendente 'atendente.demo' devolveu 401"):
        motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    assert stack.aberturas == []


def test_token_do_cliente_e_aceito_por_client_identity_e_usado_na_aprovacao(stack, com_chaves):
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    stack.consultas = [_orcamento_gerado()]
    motor.aprovar_orcamento()
    assert stack.recusas_de_token == []
    assert stack.clientes_aceitos == [CLIENTE_ID]  # client_identity extraiu o cliente do -os, nao o 7 do .feature
    assert [a[0] for a in stack.aprovacoes] == [ORCAMENTO_ID]
    claims = jwt.decode(stack.aprovacoes[0][1][7:], options={"verify_signature": False})
    assert claims["sub"] == f"cliente:{CLIENTE_ID}" and claims["cliente_id"] == CLIENTE_ID
    assert not {"cpf", "documento", "nome", "email", "created_by_id"} & set(claims)


def test_client_identity_recusa_token_assinado_por_outra_chave(stack, com_chaves, tmp_path_factory, monkeypatch):
    outra = tmp_path_factory.mktemp("outra")
    gerar_chaves_e2e.gerar(outra)
    monkeypatch.setenv("SAGA_E2E_CLIENTE_CHAVE_PRIVADA", str(outra / gerar_chaves_e2e.NOME_PRIVADA))
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    stack.consultas = [_orcamento_gerado()]
    with pytest.raises(AssertionError, match="401"):
        motor.aprovar_orcamento()
    assert len(stack.recusas_de_token) == 1


@pytest.mark.skipif(
    not os.environ.get("SAGA_E2E_BILLING_SRC"),
    reason="SAGA_E2E_BILLING_SRC (clone do Billing) nao definido: a copia de client_identity acima cobre o CI",
)
def test_token_do_cliente_e_aceito_pelo_client_identity_real_do_billing(stack, com_chaves, monkeypatch):
    monkeypatch.syspath_prepend(os.environ["SAGA_E2E_BILLING_SRC"])
    from billing_service.security import client_identity  # so existe no clone do Billing

    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    portador = motor._token_cliente()
    assert client_identity(f"Bearer {portador}",stack.chave_publica_b64) == CLIENTE_ID


def _script_de_simulacao(tmp_path, codigo_de_saida=0):
    """Comando de simulacao de teste: grava os argumentos recebidos em um arquivo."""
    script = tmp_path / "simular.py"
    saida = tmp_path / "chamadas.txt"
    script.write_text(
        "import sys\n"
        f"open({saida.as_posix()!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.exit({codigo_de_saida})\n",
        encoding="utf-8",
    )
    return f"{Path(sys.executable).as_posix()} {script.as_posix()}", saida


def _driver_com_orcamento_aprovado(stack, *historico_extra):
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    stack.consultas = [_orcamento_gerado(*historico_extra)]
    motor.aprovar_orcamento()
    return motor


def test_simulacao_do_pagamento_roda_o_comando_com_o_payment_id_quando_configurada(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, saida = _script_de_simulacao(tmp_path)
    monkeypatch.setenv("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", f"{comando} --payment-id {{payment_id}} --outcome approved")
    stack.pagamento_404s = 2  # o Billing so cria o pagamento depois do SolicitarPagamento
    motor = _driver_com_orcamento_aprovado(stack, ("PagamentoConfirmado", "RECEBIDA"))
    motor.acontece_resposta("PagamentoConfirmado")
    assert saida.read_text(encoding="utf-8").splitlines() == [f"--payment-id {PAGAMENTO_ID} --outcome approved"]
    assert stack.consultas_pagamento == 3


def test_sem_o_comando_configurado_so_espera_e_nao_consulta_o_pagamento(stack, monkeypatch):
    _apontar(stack, monkeypatch)
    motor = _driver_com_orcamento_aprovado(stack, ("PagamentoConfirmado", "RECEBIDA"))
    motor.acontece_resposta("PagamentoConfirmado")
    assert stack.consultas_pagamento == 0


def test_simulacao_so_dispara_na_resposta_de_pagamento(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, saida = _script_de_simulacao(tmp_path)
    monkeypatch.setenv("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", f"{comando} {{payment_id}} {{outcome}}")
    motor = _driver_com_orcamento_aprovado(stack, ("ExecucaoIniciada", "RECEBIDA"))
    motor.acontece_resposta("ExecucaoIniciada")
    assert not saida.exists() and stack.consultas_pagamento == 0


def test_pagamento_recusado_so_simula_quando_o_comando_aceita_o_desfecho(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, saida = _script_de_simulacao(tmp_path)
    motor = _driver_com_orcamento_aprovado(stack, ("PagamentoFalhou", "RECEBIDA"))
    monkeypatch.setenv("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", f"{comando} {{payment_id}} approved")  # fixo em approved
    motor.acontece_resposta("PagamentoFalhou")
    assert not saida.exists()
    monkeypatch.setenv("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", f"{comando} {{payment_id}} {{outcome}}")
    motor.acontece_resposta("PagamentoFalhou")
    assert saida.read_text(encoding="utf-8").splitlines() == [f"{PAGAMENTO_ID} rejected"]


def test_comando_de_simulacao_que_falha_derruba_o_passo_com_a_saida(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, _ = _script_de_simulacao(tmp_path, codigo_de_saida=3)
    monkeypatch.setenv("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", f"{comando} {{payment_id}}")
    motor = _driver_com_orcamento_aprovado(stack, ("PagamentoConfirmado", "RECEBIDA"))
    with pytest.raises(AssertionError, match=r"simulacao do pagamento .* saiu com 3"):
        motor.acontece_resposta("PagamentoConfirmado")


def test_simulacao_sem_aprovar_o_orcamento_antes_e_recusada(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, _ = _script_de_simulacao(tmp_path)
    monkeypatch.setenv("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", f"{comando} {{payment_id}}")
    motor = DriverStackReal()
    motor.iniciar_saga(cliente_id=7, veiculo_id=15, **ITENS)
    with pytest.raises(AssertionError, match="aprove o orcamento antes"):
        motor.acontece_resposta("PagamentoConfirmado")


_EXECUCAO_EM_ANDAMENTO = (
    ("IniciarExecucao", "ENVIADA"),
    ("ExecucaoIniciada", "RECEBIDA"),
)


def test_execucao_inicia_e_finaliza_cada_servico_pela_cli_com_o_request_id_da_saga(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, saida = _script_de_simulacao(tmp_path)
    monkeypatch.setenv("SAGA_E2E_EXECUTION_CLI", comando)
    motor = _driver_com_orcamento_aprovado(stack, *_EXECUCAO_EM_ANDAMENTO, ("ExecucaoFinalizada", "RECEBIDA"))
    motor.acontece_resposta("ExecucaoFinalizada")
    args = f"--saga-id {SAGA_ID} --service-id 12 --request-id req-bdd-a1b2c3"
    assert saida.read_text(encoding="utf-8").splitlines() == [f"start-service {args}", f"finish-service {args}"]


def test_sem_a_cli_configurada_a_execucao_so_espera(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    motor = _driver_com_orcamento_aprovado(stack, *_EXECUCAO_EM_ANDAMENTO, ("ExecucaoFinalizada", "RECEBIDA"))
    motor.acontece_resposta("ExecucaoFinalizada")  # nao estoura: so espera, como antes


def test_cli_so_roda_na_finalizacao_e_nao_no_execucao_iniciada(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, saida = _script_de_simulacao(tmp_path)
    monkeypatch.setenv("SAGA_E2E_EXECUTION_CLI", comando)
    motor = _driver_com_orcamento_aprovado(stack, *_EXECUCAO_EM_ANDAMENTO)
    motor.acontece_resposta("ExecucaoIniciada")
    assert not saida.exists()


def test_cli_espera_o_execucao_iniciada_antes_de_trabalhar(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, saida = _script_de_simulacao(tmp_path)
    monkeypatch.setenv("SAGA_E2E_EXECUTION_CLI", comando)
    motor = _driver_com_orcamento_aprovado(stack, ("IniciarExecucao", "ENVIADA"))
    with pytest.raises(AssertionError, match="ExecucaoIniciada chegar antes"):
        motor.acontece_resposta("ExecucaoFinalizada")
    assert not saida.exists()


def test_cli_do_execution_que_falha_derruba_o_passo_com_a_saida(stack, monkeypatch, tmp_path):
    _apontar(stack, monkeypatch)
    comando, _ = _script_de_simulacao(tmp_path, codigo_de_saida=1)
    monkeypatch.setenv("SAGA_E2E_EXECUTION_CLI", comando)
    motor = _driver_com_orcamento_aprovado(stack, *_EXECUCAO_EM_ANDAMENTO)
    with pytest.raises(AssertionError, match=r"CLI do Execution saiu com 1 .*start-service"):
        motor.acontece_resposta("ExecucaoFinalizada")


def test_cenario_desconhecido_e_recusado(driver):
    with pytest.raises(ValueError, match="inventado"):
        driver.usar_cenario("inventado")


def test_prontidao_repete_ate_o_os_responder_200(stack, driver, monkeypatch):
    stack.prontidao_recusas = 3
    stack.prontidoes = 0
    novo = DriverStackReal()
    novo.iniciar_saga(cliente_id=1, veiculo_id=1, servicos=[], pecas=[])
    assert stack.prontidoes == 4
    assert len(stack.aberturas) == 2  # a do fixture e esta; nenhum POST antes de o -os ficar pronto


def test_prontidao_e_conferida_uma_vez_por_driver(stack, driver):
    antes = stack.prontidoes
    driver.iniciar_saga(cliente_id=1, veiculo_id=1, servicos=[], pecas=[])
    assert stack.prontidoes == antes


def test_os_que_nunca_fica_pronto_falha_com_mensagem_de_prontidao(stack, monkeypatch):
    stack.prontidao_recusas = 10**6
    monkeypatch.setenv("SAGA_E2E_OS_URL", stack.url)
    monkeypatch.setenv("SAGA_E2E_TIMEOUT_S", "0.2")
    monkeypatch.setenv("SAGA_E2E_INTERVALO_S", "0.02")
    motor = DriverStackReal()
    with pytest.raises(AssertionError, match=r"nao ficou pronto.*/health/ready/.*status 503"):
        motor.iniciar_saga(cliente_id=1, veiculo_id=1, servicos=[], pecas=[])
    assert stack.aberturas == []


def test_titulo_fora_do_mapa_na_stack_real_e_erro_citando_o_titulo():
    mapa = {"Fluxo feliz": "feliz"}
    with pytest.raises(LookupError, match="Cenario inventado"):
        cenario_do_titulo("Cenario inventado", mapa, "stack-real")


def test_titulo_do_mapa_na_stack_real_devolve_o_cenario():
    assert cenario_do_titulo("Fluxo feliz", {"Fluxo feliz": "feliz"}, "stack-real") == "feliz"


def test_titulo_fora_do_mapa_na_referencia_nao_e_erro():
    assert cenario_do_titulo("Qualquer", {}, "referencia") is None
    assert cenario_do_titulo("Qualquer", {}, "") is None


def test_stack_real_recusa_cenario_nulo(driver):
    with pytest.raises(ValueError, match="explicito"):
        driver.usar_cenario(None)


def test_conexao_recusada_da_mensagem_clara(monkeypatch):
    with socket.socket() as soquete:
        soquete.bind(("127.0.0.1", 0))
        porta = soquete.getsockname()[1]
    monkeypatch.setenv("SAGA_E2E_OS_URL", f"http://127.0.0.1:{porta}")
    monkeypatch.setenv("SAGA_E2E_TIMEOUT_S", "0.2")
    monkeypatch.setenv("SAGA_E2E_INTERVALO_S", "0.02")
    motor = DriverStackReal()
    with pytest.raises(AssertionError, match="nao ficou pronto.*sem conexao"):
        motor.iniciar_saga(cliente_id=1, veiculo_id=1, servicos=[], pecas=[])


def test_gerar_env_tem_os_segredos_e_reaproveita_as_senhas(tmp_path):
    b64 = gerar_chaves_e2e.gerar(tmp_path)
    arquivo = gerar_chaves_e2e.gerar_env(tmp_path, b64)
    linhas = dict(l.split("=", 1) for l in arquivo.read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
    assert linhas["AUTH_JWT_PUBLIC_KEY_B64"] == b64
    assert linhas["ATENDENTE_PASSWORD"] and linhas["ATENDENTE_PASSWORD"] == linhas["SAGA_E2E_ATENDENTE_PASSWORD"]
    assert linhas["POSTGRES_BILLING_PASSWORD"] and linhas["POSTGRES_BILLING_PASSWORD"] != linhas["ATENDENTE_PASSWORD"]
    assert linhas["SAGA_E2E_CLIENTE_CHAVE_PRIVADA"].endswith(gerar_chaves_e2e.NOME_PRIVADA)
    de_novo = gerar_chaves_e2e.gerar_env(tmp_path, b64).read_text(encoding="utf-8")
    assert f"ATENDENTE_PASSWORD={linhas['ATENDENTE_PASSWORD']}" in de_novo
    assert f"POSTGRES_BILLING_PASSWORD={linhas['POSTGRES_BILLING_PASSWORD']}" in de_novo

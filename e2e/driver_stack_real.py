"""Motor da stack real: observa a Saga pela rota de consulta do OS Service.

Nada aqui provoca resposta de participante: Billing e Execucao de verdade
respondem sozinhos pelas filas, e o driver ESPERA o `type` aparecer no
historico de `GET /api/v1/ordens-servico/{id}/saga`
(contrato/os-consulta-saga.openapi.yaml), com timeout. As unicas acoes do
driver sao abrir a OS e aprovar o orcamento
(contrato/billing-aprovacao.openapi.yaml).

Variaveis de ambiente: SAGA_E2E_OS_URL (http://localhost:8000),
SAGA_E2E_BILLING_URL (http://localhost:8001), SAGA_E2E_TIMEOUT_S (60),
SAGA_E2E_INTERVALO_S (1) e as de autenticacao (stack/variaveis.md):

- atendente (chamadas ao -os): login em /api/v1/auth/token/ com
  SAGA_E2E_ATENDENTE_USERNAME (atendente.demo) e SAGA_E2E_ATENDENTE_PASSWORD;
- cliente (chamadas ao Billing): JWT RS256 que o driver assina com a chave
  privada de teste em SAGA_E2E_CLIENTE_CHAVE_PRIVADA (caminho do PEM); o
  cliente_id e o do Cliente que o -os criou na abertura;
- SAGA_E2E_TOKEN: Bearer fixo, usado so onde o login/a chave nao estao
  configurados (sem ele nao vai cabecalho);
- SAGA_E2E_SIMULAR_PAGAMENTO_CMD: comando que confirma o pagamento no Billing
  (fake nao tem webhook), com {payment_id} e {outcome}; sem ele so espera.
- SAGA_E2E_EXECUTION_CLI: prefixo que roda a CLI do Execution
  (`python -m execution_service.cli`) dentro do container ou do pod, por
  `docker compose exec -T execucao ...` ou `kubectl exec ... --`. O Execution nao
  tem HTTP: o mecanico inicia e finaliza cada servico pela CLI, e o ultimo
  `finish-service` gera o `ExecucaoFinalizada`. Sem ele o driver so espera.

Antes da primeira chamada o driver espera `GET /health/ready/` do OS Service
responder 200 (o -os ainda pode estar migrando), dentro do mesmo timeout.

O historico da consulta traz type, direcao, ids e correlacao, mas nao o
destino do comando, o motivo nem o codigo de erro: `destino`, `motivo` e
`codigo` nao sao conferidos aqui (o motor emite um aviso quando recebe um deles).
"""

import json
import os
import secrets
import shlex
import string
import subprocess
import time
import uuid
import urllib.error
import urllib.request
import warnings

from oficina_saga import maquina

# Dados que provocam as falhas reais em cima do corpo base da abertura.
# A COMBINAR COM BILLING E EXECUCAO: os valores abaixo sao marcadores, nao o
# que a stack aceita hoje (cartao recusado do sandbox Mercado Pago; peca sem
# estoque na Execucao).
DADOS_DO_CENARIO = {
    "feliz": {},
    "falha-pagamento": {"pagamento": {"cartao": "CARTAO_RECUSADO_SANDBOX_MERCADO_PAGO"}},
    "falha-execucao": {"pecas": [{"peca_id": 999999, "quantidade": 1}]},  # PECA_SEM_ESTOQUE
}

_ROTA_ABERTURA = "/api/v1/ordens-servico/abrir/"
_ROTA_PRONTIDAO = "/health/ready/"
_ROTA_LOGIN = "/api/v1/auth/token/"

# Resposta do participante -> desfecho que o Billing fake precisa simular.
_DESFECHO_SIMULADO = {"PagamentoConfirmado": "approved", "PagamentoFalhou": "rejected"}


# Gerador do sistema operacional: os dados nao sao segredo, mas assim nao ha
# PRNG previsivel no codigo (Sonar python:S2245).
_ALEATORIO = secrets.SystemRandom()


def _digito_cpf(digitos):
    soma = sum(int(d) * peso for d, peso in zip(digitos, range(len(digitos) + 1, 1, -1)))
    resto = soma * 10 % 11
    return str(0 if resto == 10 else resto)


def _dados_unicos():
    """Cliente e veiculo validos para o -os (CPF e placa unicos por execucao: o banco os exige unicos)."""
    base = "".join(_ALEATORIO.choices(string.digits, k=9))
    if len(set(base)) == 1:  # digitos todos iguais sao CPFs reprovados pelo validador
        base = string.digits[1:]
    cpf = base + _digito_cpf(base)
    cpf += _digito_cpf(cpf)
    letras = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    placa = (
        "".join(_ALEATORIO.choices(letras, k=3))
        + _ALEATORIO.choice(string.digits)
        + _ALEATORIO.choice(letras + string.digits)
        + "".join(_ALEATORIO.choices(string.digits, k=2))
    )
    sufixo = uuid.uuid4().hex[:8]
    cliente = {
        "nome": f"Cliente BDD {sufixo}",
        "documento": cpf,
        "email": f"bdd-{sufixo}@example.com",
        "telefone": "119" + "".join(_ALEATORIO.choices(string.digits, k=8)),
    }
    veiculo = {"placa": placa, "marca": "Fiat", "modelo": "Uno", "ano": 2015}
    return cliente, veiculo


class DriverStackReal:
    def __init__(self):
        self._os_url = os.environ.get("SAGA_E2E_OS_URL", "http://localhost:8000").rstrip("/")
        self._billing_url = os.environ.get("SAGA_E2E_BILLING_URL", "http://localhost:8001").rstrip("/")
        self._timeout_s = float(os.environ.get("SAGA_E2E_TIMEOUT_S", "60"))
        self._intervalo_s = float(os.environ.get("SAGA_E2E_INTERVALO_S", "1"))
        self._maquina = maquina.carregar()
        self._cenario = "feliz"
        self._os_pronto = False
        self._os_id = None
        self._cliente_id = None
        self._orcamento_id = None
        self._ultima_saga = None
        self._token_atendente_cache = None
        self._servicos_ids = []

    # --- operacoes do DriverSaga -------------------------------------------------

    def usar_cenario(self, nome):
        if nome is None:
            raise ValueError("a stack real exige o cenario explicito; o titulo do cenario nao esta no mapa")
        if nome not in DADOS_DO_CENARIO:
            raise ValueError(f"cenario {nome!r} desconhecido; aceitos: {', '.join(DADOS_DO_CENARIO)}")
        self._cenario = nome

    def iniciar_saga(self, *, cliente_id, veiculo_id, servicos, pecas):
        self._aguardar_prontidao()
        corpo = self._corpo_abertura(servicos, pecas)
        self._servicos_ids = list(dict.fromkeys(corpo["servicos"]))
        status, resposta = self._http(
            "POST", self._os_url, _ROTA_ABERTURA, corpo, "OS Service", portador=self._token_atendente()
        )
        assert status in (200, 201, 202), f"abrir a OS devolveu {status}: {resposta}"
        assert isinstance(resposta, dict) and "ordem_servico_id" in resposta, (
            f"a abertura da OS nao devolveu ordem_servico_id: {resposta}"
        )
        self._os_id = resposta["ordem_servico_id"]
        self._cliente_id = self._descobrir_cliente_id()

    def aprovar_orcamento(self):
        saga = self._esperar(
            "o OrcamentoGerado chegar ao orquestrador antes da aprovacao",
            lambda s: self._chegou(s, "RECEBIDA", "OrcamentoGerado"),
        )
        orcamento_id = saga.get("orcamentoId")
        assert orcamento_id, (
            "a consulta da Saga nao expos orcamentoId, necessario para aprovar o orcamento no Billing: "
            f"{json.dumps(saga, ensure_ascii=False)}"
        )
        self._orcamento_id = orcamento_id
        status, resposta = self._http(
            "POST",
            self._billing_url,
            f"/api/v1/orcamentos/{orcamento_id}/aprovar",
            None,
            "Billing",
            portador=self._token_cliente(),
        )
        assert status == 202, f"aprovar o orcamento {orcamento_id} devolveu {status}: {resposta}"

    def acontece_resposta(self, tipo, *, motivo=None, codigo=None):
        self._avisar_nao_conferido(motivo=motivo, codigo=codigo)
        self._simular_pagamento(tipo)
        if tipo == "ExecucaoFinalizada":
            self._executar_servicos()
        self._esperar(f"a resposta {tipo} chegar ao orquestrador", lambda s: self._chegou(s, "RECEBIDA", tipo))

    def esperar_estado(self, estado_esperado):
        """Espera o estado esperado ou um posterior: na stack real a Saga anda sozinha entre dois polls."""
        alcancaveis = maquina.alcancaveis(self._maquina, estado_esperado)

        def _satisfaz(resposta):
            if resposta["estado"] == estado_esperado:
                return True
            return resposta["estado"] in alcancaveis

        self._esperar(f"a Saga chegar a {estado_esperado}", _satisfaz)

    def esperar_comando(self, destino, tipo, motivo=None):
        self._avisar_nao_conferido(motivo=motivo)
        self._esperar(f"o orquestrador enviar {tipo}", lambda s: self._chegou(s, "ENVIADA", tipo))

    def status_os(self):
        saga = self._saga_atual()
        assert saga is not None, "a consulta ainda nao devolveu Saga para a OS"
        return saga["statusOs"]

    def conferir_correlacao(self):
        saga = self._saga_atual()
        assert saga is not None, "a consulta ainda nao devolveu Saga para a OS"
        historico = saga["historico"]
        assert historico, "historico vazio: nenhuma mensagem trocada"
        for campo in ("requestId", "traceId"):
            valores = {m[campo] for m in historico}
            assert len(valores) == 1, f"mais de um {campo} na Saga: {sorted(valores)}"

    def entregar_resposta_duplicada(self, tipo):
        raise NotImplementedError("a stack real nao provoca reentrega de mensagem; o cenario nao e @stack-real")

    def processamento(self):
        raise NotImplementedError("a stack real nao expoe o resultado do processamento de cada resposta")

    # --- abertura da OS ----------------------------------------------------------

    def _corpo_abertura(self, servicos, pecas):
        """Corpo do POST de abertura da OS (os_service/interfaces/api/serializers.py).

        O -os cria o Cliente e o Veiculo a partir dos dados da abertura, entao
        `cliente_id` e `veiculo_id` do Dado do .feature nao viajam: quem manda e
        o id que o -os devolve (ver `_descobrir_cliente_id`). `servicos` e uma
        lista de ids (a quantidade nao existe na rota); `pecas` leva
        peca_id/quantidade. O cenario sobrepoe campos conforme DADOS_DO_CENARIO.
        """
        cliente, veiculo = _dados_unicos()
        corpo = {
            "cliente": cliente,
            "veiculo": veiculo,
            "servicos": [s["servicoId"] for s in servicos],
            "pecas": [{"peca_id": p["pecaId"], "quantidade": p["quantidade"]} for p in pecas],
        }
        corpo.update(DADOS_DO_CENARIO[self._cenario])
        return corpo

    def _descobrir_cliente_id(self):
        """Id do Cliente que o -os criou: GET /ordens-servico/{id}/ (rota legada, atendente) devolve `cliente` (pk)."""
        status, resposta = self._http(
            "GET", self._os_url, f"/api/v1/ordens-servico/{self._os_id}/", None, "OS Service",
            portador=self._token_atendente(),
        )
        assert status == 200, f"ler a OS {self._os_id} para descobrir o cliente devolveu {status}: {resposta}"
        cliente = resposta.get("cliente") if isinstance(resposta, dict) else None
        assert type(cliente) is int, f"a OS {self._os_id} nao expos o id do cliente: {resposta}"
        return cliente

    # --- autenticacao ------------------------------------------------------------

    def _token_atendente(self):
        """Bearer do atendente: login com usuario/senha do ambiente; sem senha, SAGA_E2E_TOKEN (ou nenhum)."""
        if self._token_atendente_cache is not None:
            return self._token_atendente_cache
        segredo = os.environ.get("SAGA_E2E_ATENDENTE_PASSWORD")
        if not segredo:
            return os.environ.get("SAGA_E2E_TOKEN") or None
        usuario = os.environ.get("SAGA_E2E_ATENDENTE_USERNAME", "atendente.demo")
        status, resposta = self._http(
            "POST", self._os_url, _ROTA_LOGIN, dict(zip(("username", "password"), (usuario, segredo))), "OS Service"
        )
        assert status == 200 and isinstance(resposta, dict) and resposta.get("access"), (
            f"o login do atendente {usuario!r} devolveu {status}; o usuario existe "
            f"(seed_atendente_demo) e a senha confere? {resposta}"
        )
        self._token_atendente_cache = resposta["access"]
        return self._token_atendente_cache

    def _token_cliente(self):
        """JWT RS256 do cliente que abriu a OS, no formato de billing_service/security.py (client_identity).

        So no teste local: a chave privada de teste fica fora do repositorio e o
        caminho vem de SAGA_E2E_CLIENTE_CHAVE_PRIVADA. Sem ela, SAGA_E2E_TOKEN.
        """
        caminho = os.environ.get("SAGA_E2E_CLIENTE_CHAVE_PRIVADA")
        if not caminho:
            return os.environ.get("SAGA_E2E_TOKEN") or None
        assert self._cliente_id is not None, "o cliente so e conhecido depois de abrir a OS (iniciar_saga)"
        import jwt  # PyJWT[crypto]; so quando ha chave configurada

        with open(caminho, "rb") as arquivo:
            chave = arquivo.read()
        agora = int(time.time()) - 5  # folga contra relogio do container ligeiramente atrasado
        claims = {
            "iss": "oficina-auth",
            "aud": "oficina-api",
            "sub": f"cliente:{self._cliente_id}",
            "cliente_id": self._cliente_id,
            "principal_type": "cliente",
            "token_type": "access",
            "iat": agora,
            "exp": agora + 900,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(claims, chave, algorithm="RS256")

    # --- pagamento ---------------------------------------------------------------

    def _simular_pagamento(self, tipo):
        """Confirma/recusa o pagamento no Billing fake (sem webhook) quando SAGA_E2E_SIMULAR_PAGAMENTO_CMD existe."""
        modelo = os.environ.get("SAGA_E2E_SIMULAR_PAGAMENTO_CMD", "").strip()
        desfecho = _DESFECHO_SIMULADO.get(tipo)
        if not modelo or desfecho is None:
            return
        if desfecho != "approved" and "{outcome}" not in modelo:
            return  # comando fixo em "approved": nao serve para recusar
        pagamento_id = self._esperar_pagamento_id()
        argv = shlex.split(modelo.replace("{payment_id}", pagamento_id).replace("{outcome}", desfecho))
        try:
            feito = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as erro:
            raise AssertionError(f"nao consegui rodar a simulacao do pagamento {argv}: {erro}") from erro
        assert feito.returncode == 0, (
            f"a simulacao do pagamento {pagamento_id} ({desfecho}) saiu com {feito.returncode}: "
            f"{(feito.stderr or feito.stdout).strip()}"
        )

    # --- execucao ---------------------------------------------------------------

    def _executar_servicos(self):
        """Inicia e finaliza cada servico da OS pela CLI do Execution, quando SAGA_E2E_EXECUTION_CLI existe.

        Sem `record-consumption`: o driver nao conhece o itemPecaOSId, e
        `ExecucaoFinalizada` sem `pecasConsumidas` trata o reservado como
        consumido (ADR-004). O `--request-id` e o da Saga: a CLI o grava no
        `ExecucaoFinalizada`, e a correlacao exige um requestId so.
        """
        prefixo = os.environ.get("SAGA_E2E_EXECUTION_CLI", "").strip()
        if not prefixo:
            return
        assert self._servicos_ids, "a OS foi aberta sem servicos; nada a executar"
        saga = self._esperar(
            "o ExecucaoIniciada chegar antes de o mecanico trabalhar",
            lambda s: self._chegou(s, "RECEBIDA", "ExecucaoIniciada"),
        )
        request_ids = {m["requestId"] for m in saga["historico"]}
        assert len(request_ids) == 1, f"mais de um requestId na Saga antes da execucao: {sorted(request_ids)}"
        base = shlex.split(prefixo)
        for servico_id in self._servicos_ids:
            for acao in ("start-service", "finish-service"):
                argv = base + [
                    acao, "--saga-id", saga["sagaId"], "--service-id", str(servico_id),
                    "--request-id", next(iter(request_ids)),
                ]
                self._rodar_cli_execucao(argv)

    @staticmethod
    def _rodar_cli_execucao(argv):
        try:
            feito = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as erro:
            raise AssertionError(f"nao consegui rodar a CLI do Execution {argv}: {erro}") from erro
        assert feito.returncode == 0, (
            f"a CLI do Execution saiu com {feito.returncode} em {argv}: {(feito.stderr or feito.stdout).strip()}"
        )

    def _esperar_pagamento_id(self):
        """pagamentoId do orcamento aprovado: o Billing so o cria depois do SolicitarPagamento (404 ate la)."""
        assert self._orcamento_id, "aprove o orcamento antes de simular o pagamento"
        prazo = time.monotonic() + self._timeout_s
        while True:
            status, resposta = self._http(
                "GET", self._billing_url, f"/api/v1/orcamentos/{self._orcamento_id}/pagamento", None,
                "Billing", portador=self._token_cliente(),
            )
            if status == 200:
                assert isinstance(resposta, dict) and resposta.get("pagamentoId"), (
                    f"a consulta do pagamento nao devolveu pagamentoId: {resposta}"
                )
                return resposta["pagamentoId"]
            assert status == 404, f"consultar o pagamento do orcamento {self._orcamento_id} devolveu {status}: {resposta}"
            if time.monotonic() >= prazo:
                raise AssertionError(
                    f"timeout de {self._timeout_s}s esperando o Billing criar o pagamento do orcamento "
                    f"{self._orcamento_id}: {resposta}"
                )
            time.sleep(self._intervalo_s)

    def _aguardar_prontidao(self):
        """Espera o OS Service responder 200 em /health/ready/; repete em conexao recusada ou status != 200."""
        if self._os_pronto:
            return
        prazo = time.monotonic() + self._timeout_s
        while True:
            try:
                with urllib.request.urlopen(self._os_url + _ROTA_PRONTIDAO, timeout=10) as resposta:
                    ultimo = f"status {resposta.status}"
                    if resposta.status == 200:
                        self._os_pronto = True
                        return
            except urllib.error.HTTPError as erro:
                ultimo = f"status {erro.code}"
            except (urllib.error.URLError, OSError) as erro:
                ultimo = f"sem conexao ({erro})"
            if time.monotonic() >= prazo:
                raise AssertionError(
                    f"o OS Service em {self._os_url} nao ficou pronto em {self._timeout_s}s "
                    f"(GET {_ROTA_PRONTIDAO}: {ultimo}); suba a stack (stack/docker-compose.yml), "
                    "espere as migracoes ou ajuste SAGA_E2E_OS_URL/SAGA_E2E_TIMEOUT_S"
                )
            time.sleep(self._intervalo_s)

    # --- consulta e espera -------------------------------------------------------

    def _saga_atual(self):
        """Saga ativa da OS na consulta, ou None enquanto a OS ainda nao tem Saga (404)."""
        assert self._os_id is not None, "a Saga ainda nao foi iniciada neste cenario"
        status, resposta = self._http("GET", self._os_url, f"/api/v1/ordens-servico/{self._os_id}/saga/", None, "OS Service", portador=self._token_atendente())
        if status == 404:
            return None
        assert status == 200, f"a consulta da Saga da OS {self._os_id} devolveu {status}: {resposta}"
        atual = [s for s in resposta["sagas"] if s["sagaId"] == resposta["sagaAtual"]]
        assert atual, f"sagaAtual {resposta['sagaAtual']} nao esta em sagas: {resposta}"
        self._ultima_saga = atual[0]
        return atual[0]

    @staticmethod
    def _chegou(saga, direcao, tipo):
        return any(m["type"] == tipo and m["direcao"] == direcao for m in saga["historico"])

    def _esperar(self, descricao, chegou):
        """Consulta a Saga a cada intervalo ate `chegou(saga)`; no timeout, AssertionError com o ultimo estado e o historico."""
        prazo = time.monotonic() + self._timeout_s
        while True:
            saga = self._saga_atual()
            if saga is not None and chegou(saga):
                return saga
            if time.monotonic() >= prazo:
                if saga is None:
                    detalhe = "a consulta ainda nao devolveu Saga para a OS"
                else:
                    tipos = [(m["direcao"], m["type"]) for m in saga["historico"]]
                    detalhe = f"ultimo estado {saga['estado']}; historico {tipos}"
                raise AssertionError(f"timeout de {self._timeout_s}s esperando {descricao}: {detalhe}")
            time.sleep(self._intervalo_s)

    # --- HTTP --------------------------------------------------------------------

    def _http(self, metodo, base, rota, corpo, servico, portador=None):
        """(status, json) de uma chamada; erro HTTP volta como status, falha de rede vira AssertionError."""
        dados = None
        cabecalhos = {"Accept": "application/json"}
        if portador:
            cabecalhos["Authorization"] = f"Bearer {portador}"
        if corpo is not None:
            dados = json.dumps(corpo).encode("utf-8")
            cabecalhos["Content-Type"] = "application/json"
        pedido = urllib.request.Request(base + rota, data=dados, headers=cabecalhos, method=metodo)
        try:
            with urllib.request.urlopen(pedido, timeout=10) as resposta:
                status, bruto = resposta.status, resposta.read()
        except urllib.error.HTTPError as erro:
            status, bruto = erro.code, erro.read()
        except (urllib.error.URLError, OSError) as erro:
            raise AssertionError(
                f"nao consegui falar com o {servico} em {base} ({metodo} {rota}): {erro}; "
                "suba a stack (stack/docker-compose.yml) ou ajuste SAGA_E2E_OS_URL/SAGA_E2E_BILLING_URL"
            ) from erro
        try:
            return status, json.loads(bruto) if bruto else None
        except ValueError:
            return status, bruto.decode("utf-8", "replace")

    @staticmethod
    def _avisar_nao_conferido(**campos):
        for nome, valor in campos.items():
            if valor is not None:
                warnings.warn(
                    f"a consulta da Saga nao expoe {nome}: {valor!r} nao foi conferido na stack real",
                    stacklevel=3,
                )

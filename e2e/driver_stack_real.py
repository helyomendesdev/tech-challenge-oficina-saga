"""Motor da stack real: observa a Saga pela rota de consulta do OS Service.

Nada aqui provoca resposta de participante: Billing e Execucao de verdade
respondem sozinhos pelas filas, e o driver ESPERA o `type` aparecer no
historico de `GET /api/v1/ordens-servico/{id}/saga`
(contrato/os-consulta-saga.openapi.yaml), com timeout. As unicas acoes do
driver sao abrir a OS e aprovar o orcamento
(contrato/billing-aprovacao.openapi.yaml).

Variaveis de ambiente: SAGA_E2E_OS_URL (http://localhost:8000),
SAGA_E2E_BILLING_URL (http://localhost:8001), SAGA_E2E_TOKEN (Bearer JWT, sem
ele nao vai cabecalho), SAGA_E2E_TIMEOUT_S (60), SAGA_E2E_INTERVALO_S (1).

O historico da consulta traz type, direcao, ids e correlacao, mas nao o
destino do comando, o motivo nem o codigo de erro: `destino`, `motivo` e
`codigo` nao sao conferidos aqui (o motor emite um aviso quando recebe um deles).
"""

import json
import os
import time
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
    "falha-execucao": {"pecas": [{"pecaId": 999999, "quantidade": 1}]},  # PECA_SEM_ESTOQUE
}

_ROTA_ABERTURA = "/api/v1/ordens-servico/abrir/"


class DriverStackReal:
    def __init__(self):
        self._os_url = os.environ.get("SAGA_E2E_OS_URL", "http://localhost:8000").rstrip("/")
        self._billing_url = os.environ.get("SAGA_E2E_BILLING_URL", "http://localhost:8001").rstrip("/")
        self._timeout_s = float(os.environ.get("SAGA_E2E_TIMEOUT_S", "60"))
        self._intervalo_s = float(os.environ.get("SAGA_E2E_INTERVALO_S", "1"))
        self._maquina = maquina.carregar()
        self._cenario = "feliz"
        self._os_id = None
        self._ultima_saga = None

    # --- operacoes do DriverSaga -------------------------------------------------

    def usar_cenario(self, nome):
        if nome is None:
            nome = "feliz"
        if nome not in DADOS_DO_CENARIO:
            raise ValueError(f"cenario {nome!r} desconhecido; aceitos: {', '.join(DADOS_DO_CENARIO)}")
        self._cenario = nome

    def iniciar_saga(self, *, cliente_id, veiculo_id, servicos, pecas):
        corpo = self._corpo_abertura(cliente_id, veiculo_id, servicos, pecas)
        status, resposta = self._http("POST", self._os_url, _ROTA_ABERTURA, corpo, "OS Service")
        assert status in (200, 201, 202), f"abrir a OS devolveu {status}: {resposta}"
        assert isinstance(resposta, dict) and "id" in resposta, f"a abertura da OS nao devolveu o id: {resposta}"
        self._os_id = resposta["id"]

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
        status, resposta = self._http(
            "POST", self._billing_url, f"/api/v1/orcamentos/{orcamento_id}/aprovar", None, "Billing"
        )
        assert status == 202, f"aprovar o orcamento {orcamento_id} devolveu {status}: {resposta}"

    def acontece_resposta(self, tipo, *, motivo=None, codigo=None):
        self._avisar_nao_conferido(motivo=motivo, codigo=codigo)
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

    def _corpo_abertura(self, cliente_id, veiculo_id, servicos, pecas):
        """Corpo do POST de abertura da OS.

        A CONFERIR NA PRIMEIRA INTEGRACAO: o contrato nao especifica esta rota
        do -os; os nomes dos campos abaixo e o `id` da resposta sao suposicao.
        O cenario sobrepoe campos conforme DADOS_DO_CENARIO.
        """
        corpo = {"clienteId": cliente_id, "veiculoId": veiculo_id, "servicos": servicos, "pecas": pecas}
        corpo.update(DADOS_DO_CENARIO[self._cenario])
        return corpo

    # --- consulta e espera -------------------------------------------------------

    def _saga_atual(self):
        """Saga ativa da OS na consulta, ou None enquanto a OS ainda nao tem Saga (404)."""
        assert self._os_id is not None, "a Saga ainda nao foi iniciada neste cenario"
        status, resposta = self._http("GET", self._os_url, f"/api/v1/ordens-servico/{self._os_id}/saga", None, "OS Service")
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

    def _cabecalhos(self):
        valor = os.environ.get("SAGA_E2E_TOKEN")
        return {"Authorization": f"Bearer {valor}"} if valor else {}

    def _http(self, metodo, base, rota, corpo, servico):
        """(status, json) de uma chamada; erro HTTP volta como status, falha de rede vira AssertionError."""
        dados = None
        cabecalhos = {"Accept": "application/json", **self._cabecalhos()}
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

"""Motor de referencia: orquestrador da Saga em memoria, guiado pelo yaml do contrato.

Nenhuma transicao, estado, comando ou motivo mora aqui: tudo vem de
contrato/saga-estados.yaml (maquina) e contrato/asyncapi.yaml (quem recebe cada
comando). Billing e Execucao falsos respondem com payloads de
contrato/exemplos/<tipo>.json, ajustados aos ids da Saga em curso.
"""

import copy
import unicodedata
import uuid

import yaml

from e2e.driver import Mensagem
from oficina_saga import contrato, envelope, maquina, rastreio


def _destinos_dos_comandos(raiz):
    """{comando: participante} a partir dos canais `<Participante>Comandos` do asyncapi."""
    asyncapi = yaml.safe_load((raiz / "asyncapi.yaml").read_text(encoding="utf-8"))
    destinos = {}
    for canal, dados in asyncapi["channels"].items():
        if canal.endswith("Comandos"):
            for comando in dados["messages"]:
                destinos[comando] = canal[: -len("Comandos")].capitalize()
    return destinos


def _chave(nome):
    """Participante sem acento e em minusculas: "Execução" casa com "Execucao"."""
    sem_acento = unicodedata.normalize("NFD", nome)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


class DriverReferencia:
    def __init__(self, raiz=None):
        self._raiz = raiz or contrato.RAIZ_CONTRATO
        self._maquina = maquina.carregar(self._raiz)
        self._tabela = maquina.tabela(self._maquina)
        self._destinos = _destinos_dos_comandos(self._raiz)
        self._estado = self._maquina["inicial"]
        self._saga_id = str(uuid.uuid4())
        self._os_id = 101
        self._request_id = f"req-bdd-{uuid.uuid4().hex[:6]}"
        self._dados = {}  # campos de payload ja vistos na Saga (ids, valores), repassados aos comandos
        self._motivo = None  # motivo de quem abriu a compensacao, repetido nas transicoes `herdado`
        self._traceparent = None
        self._processados = set()
        self._mensagens = []
        self._processamento = []

    # --- operacoes do DriverSaga -------------------------------------------------

    def iniciar_saga(self, *, cliente_id, veiculo_id, servicos, pecas):
        self._dados.update(clienteId=cliente_id, veiculoId=veiculo_id, servicos=servicos, pecas=pecas)
        self._traceparent = rastreio.novo_traceparent()
        gatilhos = set(self._maquina["gatilhos_externos"])
        abertura = [
            t for t in self._maquina["transicoes"]
            if t["de"] == self._maquina["inicial"] and t["recebe"] in gatilhos
        ]
        assert len(abertura) == 1, "a maquina deve ter um unico gatilho de abertura"
        self._aplicar(abertura[0], causa=None)

    def usar_cenario(self, nome):
        """A referencia nao distingue cenarios: quem decide o caminho e a resposta que o step entrega."""

    def aprovar_orcamento(self):
        self.acontece_resposta("OrcamentoGerado")
        self.entregar_resposta("OrcamentoAprovado")

    def acontece_resposta(self, tipo, *, motivo=None, codigo=None):
        """Provoca a resposta `tipo` se ela ainda nao chegou a esta Saga."""
        if not any(m.direcao == "resposta" and m.envelope["type"] == tipo for m in self._mensagens):
            self.entregar_resposta(tipo, motivo=motivo, codigo=codigo)

    def esperar_estado(self, estado):
        """Imediato: a maquina em memoria ja esta no estado que esta."""
        assert self._estado == estado, f"Saga em {self._estado}, esperado {estado}"

    def esperar_comando(self, destino, tipo, motivo=None):
        enviados = [m for m in self._mensagens if m.direcao == "comando"]
        comandos = [
            m.envelope for m in enviados
            if _chave(m.destino) == _chave(destino) and m.envelope["type"] == tipo
        ]
        assert comandos, (
            f"{destino} nao recebeu {tipo}; comandos enviados: "
            f"{[(m.destino, m.envelope['type']) for m in enviados]}"
        )
        if motivo is not None:
            assert comandos[-1]["payload"].get("motivo") == motivo

    def conferir_correlacao(self):
        mensagens = self._mensagens
        assert mensagens, "nenhuma mensagem trocada"
        sem_traceparent = [m.envelope["type"] for m in mensagens if "traceparent" not in m.atributos]
        assert not sem_traceparent, f"mensagens sem traceparent nos MessageAttributes: {sem_traceparent}"
        trace_ids = {rastreio.parse_traceparent(m.atributos["traceparent"]["StringValue"]).trace_id for m in mensagens}
        assert len(trace_ids) == 1, f"mais de um trace-id na Saga: {sorted(trace_ids)}"
        request_ids = {m.envelope["requestId"] for m in mensagens}
        assert len(request_ids) == 1, f"mais de um requestId na Saga: {sorted(request_ids)}"

    def entregar_resposta(self, tipo, *, motivo=None, codigo=None, com_traceparent=True):
        mensagem, atributos = self._participante_responde(tipo, motivo, codigo, com_traceparent)
        self._receber(mensagem, atributos)
        return mensagem["messageId"]

    def entregar_resposta_duplicada(self, tipo):
        mensagem, atributos = self._participante_responde(tipo, None, None, True)
        self._receber(mensagem, atributos)
        self._receber(mensagem, atributos)
        return mensagem["messageId"]

    def estado_saga(self):
        return self._estado

    def status_os(self):
        return self._maquina["status_os"][self._estado]

    def mensagens(self):
        return list(self._mensagens)

    def processamento(self):
        return list(self._processamento)

    # --- orquestrador ------------------------------------------------------------

    def _receber(self, mensagem, atributos):
        self._mensagens.append(Mensagem("resposta", None, mensagem, atributos))
        if mensagem["messageId"] in self._processados:
            self._processamento.append((mensagem["messageId"], "duplicada"))
            return
        self._processados.add(mensagem["messageId"])
        transicao = self._tabela.get((self._estado, mensagem["type"]))
        if transicao is None:
            self._processamento.append((mensagem["messageId"], "sem_transicao"))
            return
        self._processamento.append((mensagem["messageId"], "aplicada"))
        self._traceparent, _ = rastreio.extrair(atributos)
        for campo, valor in mensagem["payload"].items():
            if valor is not None and campo != "motivo":
                self._dados[campo] = valor
        self._aplicar(transicao, causa=mensagem)

    def _aplicar(self, transicao, causa):
        self._estado = transicao["para"]
        if "envia" in transicao:
            self._enviar(transicao, causa)

    def _enviar(self, transicao, causa):
        motivo = transicao.get("motivo")
        if motivo == "herdado":
            motivo = self._motivo
        elif motivo is not None:
            self._motivo = motivo
        tipo = transicao["envia"]
        comando = self._montar(tipo, causa, self._payload(tipo, motivo=motivo))
        self._traceparent = rastreio.novo_span_filho(self._traceparent)
        atributos = rastreio.injetar({}, self._traceparent)
        self._mensagens.append(Mensagem("comando", self._destinos[tipo], comando, atributos))

    # --- participantes falsos ----------------------------------------------------

    def _participante_responde(self, tipo, motivo, codigo, com_traceparent):
        if tipo in self._destinos:
            raise ValueError(f"{tipo} e um comando, nao uma resposta")
        payload = self._payload(tipo, motivo=motivo)
        if codigo is not None:
            payload["erro"]["codigo"] = codigo
        resposta = self._montar(tipo, self._mensagens[-1].envelope, payload)
        atributos = {}
        if com_traceparent:
            atributos = rastreio.injetar(atributos, rastreio.novo_span_filho(self._traceparent))
        return resposta, atributos

    # --- montagem ----------------------------------------------------------------

    def _payload(self, tipo, *, motivo=None):
        """Payload do exemplo de `tipo`, com os ids/valores da Saga e o motivo dados."""
        exemplo = contrato.carregar_json(self._raiz / "exemplos" / f"{tipo}.json")["payload"]
        payload = {}
        for campo, valor in exemplo.items():
            if campo == "motivo" and motivo is not None:
                payload[campo] = motivo
            elif campo != "motivo" and campo in self._dados:
                payload[campo] = copy.deepcopy(self._dados[campo])
            else:
                payload[campo] = copy.deepcopy(valor)
        return payload

    def _montar(self, tipo, causa, payload):
        mensagem = envelope.montar(
            tipo,
            saga_id=self._saga_id,
            os_id=self._os_id,
            request_id=self._request_id,
            payload=payload,
            causation_id=causa["messageId"] if causa else None,
        )
        envelope.validar(mensagem)
        return mensagem

"""Contrato entre os steps do BDD e o motor que executa a Saga.

Os steps falam so com `DriverSaga`. O motor e escolhido por SAGA_E2E_DRIVER:
`referencia` (padrao) roda a maquina do contrato em memoria; `stack-real` fala
com a stack local (filas e servicos de verdade).
"""

import os
from dataclasses import dataclass
from typing import Any, Protocol

MOTORES = ("referencia", "stack-real")


@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem trocada na Saga, como saiu/chegou na fila."""

    direcao: str  # "comando" (orquestrador -> participante) ou "resposta" (participante -> orquestrador)
    destino: str | None  # participante que recebe o comando; None nas respostas
    envelope: dict[str, Any]
    atributos: dict[str, Any]  # MessageAttributes do SQS (traceparent, tracestate)


class DriverSaga(Protocol):
    """Cada operacao diz "isto acontece"; o motor decide como.

    O motor de referencia PROVOCA o que o step descreve (entrega a resposta do
    participante, avanca a maquina em memoria) e confere na hora. O motor da
    stack real ESPERA aparecer no historico da rota de consulta da Saga do OS
    Service (contrato/os-consulta-saga.openapi.yaml), com timeout.
    """

    def usar_cenario(self, nome: str | None) -> None:
        """Informa o cenario (`feliz`, `falha-pagamento`, `falha-execucao`); so a stack real precisa dele."""

    def iniciar_saga(self, *, cliente_id: int, veiculo_id: int, servicos: list, pecas: list) -> None:
        """Abre a Saga para uma OS com o diagnostico concluido."""

    def aprovar_orcamento(self) -> None:
        """O cliente aprova o orcamento (que ja foi gerado)."""

    def acontece_resposta(self, tipo: str, *, motivo: str | None = None, codigo: str | None = None) -> None:
        """A resposta `tipo` de um participante chega ao orquestrador (uma vez por Saga)."""

    def esperar_estado(self, estado: str) -> None:
        """A Saga chega ao estado `estado`; AssertionError se nao chegar."""

    def esperar_comando(self, destino: str, tipo: str, motivo: str | None = None) -> None:
        """O orquestrador envia o comando `tipo` (a `destino`, com `motivo` quando dado)."""

    def status_os(self) -> str:
        """Status da OS que o estado atual da Saga espelha."""

    def conferir_correlacao(self) -> None:
        """Todas as mensagens da Saga compartilham o mesmo requestId e trace-id; AssertionError se nao."""

    def entregar_resposta_duplicada(self, tipo: str) -> str:
        """So referencia: entrega `tipo` duas vezes com o mesmo messageId (SQS "pelo menos uma vez")."""

    def processamento(self) -> list[tuple[str, str]]:
        """So referencia: (messageId, resultado) de cada resposta: aplicada, duplicada ou sem_transicao."""


def criar_driver(nome: str | None = None) -> DriverSaga:
    """Cria o motor pedido (padrao: SAGA_E2E_DRIVER; vazio = referencia)."""
    if nome is None:
        nome = os.environ.get("SAGA_E2E_DRIVER", "")
    nome = nome.strip()
    if nome in ("", "referencia"):
        from e2e.driver_referencia import DriverReferencia

        return DriverReferencia()
    if nome == "stack-real":
        from e2e.driver_stack_real import DriverStackReal

        return DriverStackReal()
    raise ValueError(
        f"SAGA_E2E_DRIVER={nome!r} desconhecido; motores aceitos: {', '.join(MOTORES)}"
    )

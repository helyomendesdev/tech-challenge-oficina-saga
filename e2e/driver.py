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
    def iniciar_saga(self, *, cliente_id: int, veiculo_id: int, servicos: list, pecas: list) -> None:
        """Abre a Saga para uma OS com o diagnostico concluido."""

    def entregar_resposta(
        self, tipo: str, *, motivo: str | None = None, codigo: str | None = None,
        com_traceparent: bool = True,
    ) -> str:
        """Um participante responde `tipo` ao orquestrador; devolve o messageId da resposta."""

    def entregar_resposta_duplicada(self, tipo: str) -> str:
        """Entrega `tipo` duas vezes com o mesmo messageId (entrega SQS "pelo menos uma vez")."""

    def estado_saga(self) -> str:
        """Estado atual da Saga."""

    def status_os(self) -> str:
        """Status da OS que o estado atual da Saga espelha."""

    def mensagens(self) -> list[Mensagem]:
        """Todas as mensagens trocadas, em ordem, com envelope e MessageAttributes."""

    def processamento(self) -> list[tuple[str, str]]:
        """(messageId, resultado) de cada resposta recebida: aplicada, duplicada ou sem_transicao."""


def criar_driver(nome: str | None = None) -> DriverSaga:
    """Cria o motor pedido (padrao: SAGA_E2E_DRIVER; vazio = referencia)."""
    if nome is None:
        nome = os.environ.get("SAGA_E2E_DRIVER", "")
    nome = nome.strip()
    if nome in ("", "referencia"):
        from e2e.driver_referencia import DriverReferencia

        return DriverReferencia()
    if nome == "stack-real":
        try:
            from e2e.driver_stack_real import DriverStackReal
        except ModuleNotFoundError as erro:
            if erro.name != "e2e.driver_stack_real":
                raise
            raise RuntimeError(
                "SAGA_E2E_DRIVER=stack-real, mas e2e/driver_stack_real.py ainda nao existe"
            ) from erro
        return DriverStackReal()
    raise ValueError(
        f"SAGA_E2E_DRIVER={nome!r} desconhecido; motores aceitos: {', '.join(MOTORES)}"
    )

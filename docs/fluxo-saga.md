# Fluxo da Saga da Ordem de Serviço (v0.1)

Orquestrador: OS Service ([ADR-001](adr-001-saga-orquestrada.md)). Mensagens: [`contrato/asyncapi.yaml`](../contrato/asyncapi.yaml).

## Fluxo feliz

```mermaid
sequenceDiagram
    autonumber
    actor Cliente
    participant OS as OS Service (orquestrador)
    participant BIL as Billing
    participant EXE as Execução

    Cliente->>OS: conclui diagnóstico (HTTP, X-Request-Id)
    Note over OS: Saga INICIADA
    OS->>BIL: GerarOrcamento (oficina-billing-comandos.fifo)
    Note over OS: ORCAMENTO_SOLICITADO
    BIL-->>OS: OrcamentoGerado (oficina-os-saga-respostas.fifo)
    Note over OS: AGUARDANDO_APROVACAO
    Cliente->>BIL: aprova o orçamento
    BIL-->>OS: OrcamentoAprovado
    OS->>EXE: IniciarExecucao (oficina-execucao-comandos.fifo)
    Note over OS: EXECUCAO_SOLICITADA
    EXE-->>OS: ExecucaoIniciada
    Note over OS: EM_EXECUCAO
    EXE-->>OS: ExecucaoFinalizada
    Note over OS: CONCLUIDA
```

## Compensação

Falha **depois** de o orçamento existir desfaz o que já foi feito, na ordem inversa.

```mermaid
sequenceDiagram
    autonumber
    participant OS as OS Service (orquestrador)
    participant BIL as Billing
    participant EXE as Execução

    Note over OS: EXECUCAO_SOLICITADA (orçamento aprovado)
    EXE-->>OS: ExecucaoFalhou (ex.: PECA_SEM_ESTOQUE)
    Note over OS: COMPENSANDO
    OS->>BIL: CancelarOrcamento (motivo EXECUCAO_FALHOU)
    BIL-->>OS: OrcamentoCancelado
    Note over OS: CANCELADA
```

Variação — cancelamento com a execução em andamento:

```mermaid
sequenceDiagram
    autonumber
    participant OS as OS Service (orquestrador)
    participant BIL as Billing
    participant EXE as Execução

    Note over OS: EM_EXECUCAO
    OS->>EXE: CancelarExecucao (motivo CANCELADO_PELO_CLIENTE)
    Note over OS: COMPENSANDO
    EXE-->>OS: ExecucaoCancelada
    OS->>BIL: CancelarOrcamento
    BIL-->>OS: OrcamentoCancelado
    Note over OS: CANCELADA
```

## Máquina de estados

```mermaid
stateDiagram-v2
    [*] --> INICIADA
    INICIADA --> ORCAMENTO_SOLICITADO: envia GerarOrcamento
    ORCAMENTO_SOLICITADO --> AGUARDANDO_APROVACAO: OrcamentoGerado
    ORCAMENTO_SOLICITADO --> CANCELADA: OrcamentoFalhou
    AGUARDANDO_APROVACAO --> EXECUCAO_SOLICITADA: OrcamentoAprovado, envia IniciarExecucao
    AGUARDANDO_APROVACAO --> CANCELADA: OrcamentoRecusado (em aberto)
    EXECUCAO_SOLICITADA --> EM_EXECUCAO: ExecucaoIniciada
    EXECUCAO_SOLICITADA --> COMPENSANDO: ExecucaoFalhou, envia CancelarOrcamento
    EM_EXECUCAO --> CONCLUIDA: ExecucaoFinalizada
    EM_EXECUCAO --> COMPENSANDO: ExecucaoFalhou ou cancelamento, envia CancelarExecucao
    COMPENSANDO --> COMPENSANDO: ExecucaoCancelada, envia CancelarOrcamento
    COMPENSANDO --> CANCELADA: OrcamentoCancelado
    CONCLUIDA --> [*]
    CANCELADA --> [*]
```

Regras do orquestrador:

- Resposta que não corresponde a uma transição válida do estado atual **não muda o estado**: é registrada e descartada (entrega repetida, por exemplo reprocessamento da DLQ). Com FIFO por `sagaId`, fora de ordem não acontece dentro de uma Saga.
- Toda transição grava o estado novo e o próximo comando na mesma transação (outbox).
- `OrcamentoFalhou` não precisa de compensação: nenhum orçamento foi criado.

## Correspondência com o status da OS no monólito

Status de hoje em `atendimento/domain/enums.py` e transições em `atendimento/domain/policies.py` do repositório da Fase 1.

| Estado da Saga | Status da OS (monólito) | Observação |
|---|---|---|
| — (antes da Saga) | `RECEBIDA`, `DIAGNOSTICO` | A Saga começa quando o diagnóstico termina — **em aberto** |
| `INICIADA`, `ORCAMENTO_SOLICITADO`, `AGUARDANDO_APROVACAO` | `AGUARDANDO` | |
| `EXECUCAO_SOLICITADA`, `EM_EXECUCAO` | `EXECUCAO` | |
| `CONCLUIDA` | `FINALIZADA` | |
| — (depois da Saga) | `ENTREGUE` | Entrega do veículo fica fora da Saga |
| `COMPENSANDO`, `CANCELADA` | `CANCELADA` | |

Divergência a decidir: no monólito, `AGUARDANDO → DIAGNOSTICO` é permitido (recusar o orçamento volta ao diagnóstico, não cancela). Na v0.1 da Saga, `OrcamentoRecusado` leva a `CANCELADA`. Ver [`questoes-abertas.md`](questoes-abertas.md).

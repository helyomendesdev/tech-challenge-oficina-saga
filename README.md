# Oficina — Saga da Ordem de Serviço (Tech Challenge FIAP, Fase 4)

Contrato de mensagens, documentação e testes ponta a ponta da **Saga orquestrada** que liga OS Service, Billing e Execução.

> **Status: v0.1 — rascunho.** Primeira versão do contrato combinada para 24/10/2026. Pontos em aberto em [`docs/questoes-abertas.md`](docs/questoes-abertas.md).

## O que tem aqui

| Caminho | Conteúdo |
|---|---|
| [`contrato/asyncapi.yaml`](contrato/asyncapi.yaml) | Contrato AsyncAPI 3.0: filas, comandos, respostas, DLQs |
| [`contrato/schemas/`](contrato/schemas) | JSON Schema (draft-07): `envelope`, `comum`, um schema por mensagem em `comandos/` e `respostas/` |
| [`contrato/exemplos/`](contrato/exemplos) | Um exemplo válido por mensagem; `invalidos/` tem exemplos que **devem** ser rejeitados |
| [`scripts/validar_contrato.py`](scripts/validar_contrato.py) | Valida exemplos contra schemas e a coerência de nomes com o AsyncAPI |
| [`docs/adr-001-saga-orquestrada.md`](docs/adr-001-saga-orquestrada.md) | Por que orquestrada, e por que o orquestrador mora no OS Service |
| [`docs/fluxo-saga.md`](docs/fluxo-saga.md) | Diagramas: fluxo feliz, compensação, máquina de estados, mapa para o status da OS |
| [`tracing/propagacao-sqs.md`](tracing/propagacao-sqs.md) | `traceparent`/`tracestate` em MessageAttributes do SQS; `requestId` no envelope |
| [`e2e/`](e2e) | BDD ponta a ponta (pytest-bdd); hoje os steps fazem `pytest.skip` |

## Mensagens

Envelope comum a todas: `messageId` (chave de idempotência), `type`, `version`, `sagaId`, `osId`, `causationId`, `requestId`, `occurredAt`, `payload`.

| Fila FIFO (+ DLQ `…-dlq.fifo`) | Quem consome | Mensagens |
|---|---|---|
| `oficina-billing-comandos.fifo` | Billing | `GerarOrcamento`, `CancelarOrcamento` |
| `oficina-execucao-comandos.fifo` | Execução | `IniciarExecucao`, `CancelarExecucao` |
| `oficina-os-saga-respostas.fifo` | OS Service (orquestrador) | `OrcamentoGerado`, `OrcamentoFalhou`, `OrcamentoAprovado`, `OrcamentoRecusado`, `OrcamentoCancelado`, `ExecucaoIniciada`, `ExecucaoFalhou`, `ExecucaoFinalizada`, `ExecucaoCancelada` |

Todo envio usa `MessageGroupId = sagaId` e `MessageDeduplicationId = messageId`.

## Rodar localmente

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python scripts/validar_contrato.py        # exemplos x schemas x AsyncAPI
pytest --collect-only -q                  # cenários BDD coletados
```

A validação do AsyncAPI usa o CLI oficial via `npx` (versão fixada em [`.github/workflows/ci.yml`](.github/workflows/ci.yml)). A CI roda os mesmos passos e ainda falha se o `.feature` tiver step sem definição.

## Mudando o contrato

1. Altere o schema em `contrato/schemas/` e o exemplo em `contrato/exemplos/`.
2. Mensagem nova: entra no `enum` de `type` do envelope, ganha schema, exemplo e entrada em `components.messages` do AsyncAPI — o validador recusa se faltar qualquer um dos quatro.
3. Mudança incompatível sobe `version` da mensagem.

## Divisão do grupo

| Pessoa | Parte |
|---|---|
| Hélio | OS Service (com o orquestrador) e template de CI |
| Lucas | Billing, Mercado Pago, JWT |
| Sophia | Execução (NoSQL), infra, SQS, Terraform |
| Luís | Contrato da Saga, tracing distribuído, BDD ponta a ponta |

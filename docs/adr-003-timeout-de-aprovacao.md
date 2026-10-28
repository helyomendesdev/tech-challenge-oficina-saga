# ADR-003 — Timeout de aprovação do orçamento

| Informação | Valor |
|---|---|
| **ADR** | 003 |
| **Status** | Proposto (v1 — para revisão do grupo) |
| **Autor** | Luís Fernando Montes |
| **Data** | 2026-10-28 |

## 1. Contexto

Orçamento sem resposta do cliente ficava em `AGUARDANDO_APROVACAO` para sempre. O `CancelarOrcamento` já aceitava o motivo `TIMEOUT`, mas a máquina não tinha transição que o enviasse, e faltava decidir o prazo e quem dispara. O OS Service já implementou o timeout (PR #7 do -os): prazo de 30 minutos, configurável, verificado por um comando agendado.

## 2. Decisão

1. **Quem dispara:** o OS Service, que é o orquestrador (ADR-001). Um agendamento periódico procura Sagas em `AGUARDANDO_APROVACAO` além do prazo e dispara o gatilho externo `timeout`.
2. **Prazo:** configurável no OS Service (`ORCAMENTO_TIMEOUT_MINUTOS`), padrão de 30 minutos. O prazo é do orquestrador, não do contrato: as mensagens não mudam se ele mudar.
3. **Transição:** `AGUARDANDO_APROVACAO` + `timeout` vai para `COMPENSANDO` e envia `CancelarOrcamento` com motivo `TIMEOUT`. Nada foi pago ainda, então só o orçamento é desfeito; `OrcamentoCancelado` leva a `CANCELADA`, como nas outras compensações.

## 3. Por que

- **O orquestrador é quem sabe há quanto tempo a Saga espera.** O Billing não conhece o estado da Saga; pôr o timeout lá exigiria uma mensagem nova só para isso.
- **Compensar em vez de cancelar direto.** O Billing gerou um orçamento e precisa saber que ele não vale mais; `CancelarOrcamento` já existe para isso.
- **Prazo fora do contrato.** Trinta minutos serve para a demonstração; em produção o número seria outro, e trocá-lo não deve exigir versão nova do contrato.

## 4. Consequências

- Gatilho externo novo (`timeout`), uma transição e a trilha `timeout` em `contrato/saga-estados.yaml`.
- O `validoAte` do `OrcamentoGerado` continua informativo para o cliente; o prazo que vale para a Saga é o do OS Service.
- Com o timeout decidido, a questão do `causationId` nulo (`questoes-abertas.md`) pode seguir a recomendação B: `CancelarOrcamento` entra entre os comandos com gatilho externo.

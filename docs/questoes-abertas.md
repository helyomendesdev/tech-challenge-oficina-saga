# Questões abertas — contrato v0.1

Cada questão traz a recomendação atual. Fechada a questão, a decisão vai para um ADR e sai daqui.

## 1. Onde entra o pagamento do Mercado Pago na Saga

O enunciado põe o Mercado Pago no Billing. Hoje a v0.1 não tem mensagem de pagamento: `OrcamentoAprovado` significa "o cliente aprovou".

- **A. Aprovar = pagar.** `OrcamentoAprovado` só é emitido quando o pagamento é confirmado (webhook do Mercado Pago). Nenhuma mensagem nova; `CancelarOrcamento` passa a incluir **estorno**.
- **B. Pagamento como passo próprio.** Novos `SolicitarPagamento` / `PagamentoConfirmado` / `PagamentoFalhou` e um estado `PAGAMENTO_SOLICITADO` entre a aprovação e a execução.
- **C. Pagamento depois da execução**, na entrega do veículo — fora da Saga.

**Recomendação: A.** Mantém a Saga com três passos e a compensação continua sendo uma mensagem só (`CancelarOrcamento` estorna se houver pagamento). O Billing esconde o Mercado Pago atrás do contrato. Depende do Lucas (Billing).

## 2. Onde fica o diagnóstico

No monólito, `DIAGNOSTICO` é status da OS antes de `AGUARDANDO`, e `AGUARDANDO → DIAGNOSTICO` é permitido — recusar o orçamento volta ao diagnóstico.

- **A. No OS Service, antes da Saga.** A Saga começa quando o diagnóstico termina; `GerarOrcamento` leva os serviços e peças diagnosticados.
- **B. No Execution Service**, com a Saga começando na abertura da OS.

**Recomendação: A** (é o que os exemplos da v0.1 assumem). Consequência a decidir junto: o que `OrcamentoRecusado` faz. Na v0.1 ele **cancela** a Saga; para manter o comportamento do monólito, ele encerraria a Saga e devolveria a OS a `DIAGNOSTICO`, e um novo diagnóstico abriria **outra** Saga para a mesma OS (por isso `sagaId` e `osId` são campos separados).

## 3. Linguagem de cada serviço

| Serviço | Responsável | Linguagem | Banco |
|---|---|---|---|
| OS Service (+ orquestrador) | Hélio | ? | SQL |
| Billing (+ Mercado Pago, JWT) | Lucas | ? | ? |
| Execução | Sophia | ? | NoSQL |

O contrato é independente de linguagem (JSON Schema). Com Python em algum serviço, o `scripts/validar_contrato.py` serve de referência de validação; em outras linguagens, cada serviço valida contra os mesmos schemas com a biblioteca da sua stack.

## 4. Filas padrão ou FIFO

SQS padrão pode entregar **fora de ordem** e **repetido**. FIFO garante ordem por `MessageGroupId` e deduplica por 5 minutos com `MessageDeduplicationId`, mas exige o sufixo `.fifo` no nome da fila e tem limite menor de vazão.

**Recomendação: FIFO**, com `MessageGroupId = sagaId` e `MessageDeduplicationId = messageId`. A Saga é sensível à ordem (`ExecucaoFinalizada` antes de `ExecucaoIniciada` seria descartada como transição inválida), e a vazão de uma oficina é irrelevante para o limite. A idempotência por `messageId` continua obrigatória (a deduplicação do FIFO vale só por 5 minutos). A v0.1 usa filas **padrão** com os nomes combinados; trocar para FIFO muda os nomes (`oficina-billing-comandos.fifo` etc.) e o binding no `asyncapi.yaml`.

## 5. Tipo do `osId`

A v0.1 usa **inteiro** porque a OS do monólito usa `BigAutoField` (`app/settings.py`, `DEFAULT_AUTO_FIELD`). Se o OS Service novo adotar UUID, muda o schema em `contrato/schemas/comum.schema.json` (`idNumerico`) — decisão do Hélio.

## 6. `X-Correlation-Id` no envelope

O ADR-007 do monólito criou o `X-Correlation-Id` para ligar o fluxo de autenticação ao de aplicação. A v0.1 leva só o `requestId`. Recomendação: **não incluir** por ora — o `requestId` já liga a Saga à requisição de origem, e o correlation id continua consultável no log dessa requisição.

## 7. Timeout de aprovação

Orçamento sem resposta do cliente fica em `AGUARDANDO_APROVACAO` para sempre. `CancelarOrcamento` já aceita o motivo `TIMEOUT`; falta decidir o prazo (sugestão: `validoAte` do `OrcamentoGerado`) e quem dispara (agendamento no OS Service).

# Questões abertas — contrato v0.1

Cada questão traz a recomendação atual. Fechada a questão, a decisão vai para um ADR e sai daqui.

Já fechadas: filas FIFO (ADR-001, 2026-10-07).

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

## 4. Tipo do `osId`

A v0.1 usa **inteiro** porque a OS do monólito usa `BigAutoField` (`app/settings.py`, `DEFAULT_AUTO_FIELD`). Se o OS Service novo adotar UUID, muda o schema em `contrato/schemas/comum.schema.json` (`idNumerico`) — decisão do Hélio.

## 5. `X-Correlation-Id` no envelope

O ADR-007 do monólito criou o `X-Correlation-Id` para ligar o fluxo de autenticação ao de aplicação. A v0.1 leva só o `requestId`. Recomendação: **não incluir** por ora — o `requestId` já liga a Saga à requisição de origem, e o correlation id continua consultável no log dessa requisição.

## 6. Timeout de aprovação

Orçamento sem resposta do cliente fica em `AGUARDANDO_APROVACAO` para sempre. `CancelarOrcamento` já aceita o motivo `TIMEOUT`; falta decidir o prazo (sugestão: `validoAte` do `OrcamentoGerado`) e quem dispara (agendamento no OS Service).

## 7. Escopo do `causationId` nulo

Hoje todo comando aceita `causationId` nulo, para cobrir o gatilho externo: abrir a Saga (`GerarOrcamento`), o cliente cancelar (`CancelarExecucao`) e, se entrar, o timeout (`CancelarOrcamento`). Só que `IniciarExecucao` com causa nula também passa no schema, embora sempre seja reação a `OrcamentoAprovado`. Quem pega isso hoje é só o validador de exemplos.

- **A. Manter em todo comando.** Um schema só; o consumidor trata causa nula em qualquer comando.
- **B. Só nos comandos com gatilho externo** (`GerarOrcamento`, `CancelarExecucao`, e `CancelarOrcamento` se o timeout entrar). Os demais exigem causa não nula no schema.

**Recomendação: B**, quando o timeout for decidido (item 6). Sem isso, `IniciarExecucao` com causa nula continua válido e só o validador de exemplos percebe. Consequência: consumidores passam a poder confiar em causa não nula onde ela sempre existe.

## 8. Qual `requestId` leva o comando de cancelamento

O envelope diz que o `requestId` é o da requisição de origem, repassado inalterado por toda a Saga. O cancelamento pelo cliente é outra requisição HTTP, com identificador próprio. Pergunta: o `CancelarExecucao` leva o `requestId` da Saga ou o da requisição de cancelamento?

- **A. Manter o `requestId` original da Saga.** Tudo que a Saga emite responde a um só identificador. O `requestId` do cancelamento vai para o log da requisição HTTP, à parte.
- **B. Usar o `requestId` da requisição de cancelamento a partir dali.** Cada requisição HTTP tem a sua linha no rastro, mas a Saga passa a carregar mais de um `requestId`.

**Recomendação: A.** A pergunta "o que aconteceu com esta OS" fica respondida por um só identificador. O `requestId` do cancelamento aparece no log da requisição HTTP, e o `causationId` nulo do comando marca o ponto de entrada.

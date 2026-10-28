# Questões abertas — contrato v1

Cada questão traz a recomendação atual. Fechada a questão, a decisão vai para um ADR e sai daqui.

Já fechadas: filas FIFO (ADR-001, 2026-10-07); onde entra o pagamento do Mercado Pago ([ADR-002](adr-002-pagamento-antes-da-execucao.md): passo próprio antes da execução, com estorno separado); timeout de aprovação ([ADR-003](adr-003-timeout-de-aprovacao.md): OS Service dispara, prazo configurável, padrão 30 min).

## 1. Como o link de pagamento do Mercado Pago chega ao cliente

Com o pagamento como passo da Saga, o Billing cria a cobrança no Mercado Pago ao receber `SolicitarPagamento`, e a Saga fica em `PAGAMENTO_SOLICITADO` até `PagamentoConfirmado` ou `PagamentoFalhou`. Falta decidir como o cliente recebe o link (ou QR code) para pagar.

- **A. API do Billing, fora da Saga.** O front consulta o Billing (por exemplo, `GET` do pagamento do orçamento) e mostra o link. O contrato da Saga não muda.
- **B. Mensagem nova na Saga** (`PagamentoCriado`, com o link) do Billing para o OS Service, que repassa ao cliente.

**Recomendação: A.** O link é dado do Mercado Pago, que só o Billing conhece; passá-lo pela Saga acrescentaria uma mensagem e um estado sem ganho para a orquestração, que só precisa do resultado (confirmado ou falhou). Depende do Lucas (Billing).

## 2. Onde fica o diagnóstico

No monólito, `DIAGNOSTICO` é status da OS antes de `AGUARDANDO`, e `AGUARDANDO → DIAGNOSTICO` é permitido — recusar o orçamento volta ao diagnóstico.

- **A. No OS Service, antes da Saga.** A Saga começa quando o diagnóstico termina; `GerarOrcamento` leva os serviços e peças diagnosticados.
- **B. No Execution Service**, com a Saga começando na abertura da OS.

**Recomendação: A** (é o que os exemplos assumem). Consequência a decidir junto: o que `OrcamentoRecusado` faz. Na v1 ele **cancela** a Saga; para manter o comportamento do monólito, ele encerraria a Saga e devolveria a OS a `DIAGNOSTICO`, e um novo diagnóstico abriria **outra** Saga para a mesma OS (por isso `sagaId` e `osId` são campos separados).

## 3. Linguagem de cada serviço

| Serviço | Responsável | Linguagem | Banco |
|---|---|---|---|
| OS Service (+ orquestrador) | Hélio | Python 3.11 / Django 5.1 | PostgreSQL |
| Billing (+ Mercado Pago, JWT) | Lucas | ? | ? |
| Execução | Sophia | ? | NoSQL (DynamoDB, pela descrição do repo) |

O contrato é independente de linguagem (JSON Schema). Com Python em algum serviço, o `scripts/validar_contrato.py` serve de referência de validação; em outras linguagens, cada serviço valida contra os mesmos schemas com a biblioteca da sua stack.

## 4. Tipo do `osId`

A v1 usa **inteiro** porque a OS do monólito usa `BigAutoField` (`app/settings.py`, `DEFAULT_AUTO_FIELD`). Se o OS Service novo adotar UUID, muda o schema em `contrato/schemas/comum.schema.json` (`idNumerico`) — decisão do Hélio. O `-os` atual mantém inteiro (`SagaInstance.os_id` é `PositiveIntegerField`); falta só ele confirmar que fica assim.

## 5. `X-Correlation-Id` no envelope

O ADR-007 do monólito criou o `X-Correlation-Id` para ligar o fluxo de autenticação ao de aplicação. A v1 leva só o `requestId`. Recomendação: **não incluir** por ora — o `requestId` já liga a Saga à requisição de origem, e o correlation id continua consultável no log dessa requisição.

## 6. Escopo do `causationId` nulo

Hoje todo comando aceita `causationId` nulo, para cobrir o gatilho externo: abrir a Saga (`GerarOrcamento`), o cliente cancelar (`CancelarExecucao`) e o timeout (`CancelarOrcamento`, ADR-003). Só que `IniciarExecucao` com causa nula também passa no schema, embora sempre seja reação a `PagamentoConfirmado`; `SolicitarPagamento` (reação a `OrcamentoAprovado`) e `EstornarPagamento` (reação a `ExecucaoFalhou`, `ExecucaoCancelada` ou ao cancelamento do cliente) seguem a mesma regra. Quem pega isso hoje é só o validador de exemplos.

- **A. Manter em todo comando.** Um schema só; o consumidor trata causa nula em qualquer comando.
- **B. Só nos comandos com gatilho externo** (`GerarOrcamento`, `CancelarExecucao` e `CancelarOrcamento`). Os demais exigem causa não nula no schema.

**Recomendação: B.** O timeout já está decidido (ADR-003). Sem isso, `IniciarExecucao` com causa nula continua válido e só o validador de exemplos percebe. Consequência: consumidores passam a poder confiar em causa não nula onde ela sempre existe.

## 7. Qual `requestId` leva o comando de cancelamento

O envelope diz que o `requestId` é o da requisição de origem, repassado inalterado por toda a Saga. O cancelamento pelo cliente é outra requisição HTTP, com identificador próprio. Pergunta: o `CancelarExecucao` leva o `requestId` da Saga ou o da requisição de cancelamento?

- **A. Manter o `requestId` original da Saga.** Tudo que a Saga emite responde a um só identificador. O `requestId` do cancelamento vai para o log da requisição HTTP, à parte.
- **B. Usar o `requestId` da requisição de cancelamento a partir dali.** Cada requisição HTTP tem a sua linha no rastro, mas a Saga passa a carregar mais de um `requestId`.

**Recomendação: A.** A pergunta "o que aconteceu com esta OS" fica respondida por um só identificador. O `requestId` do cancelamento aparece no log da requisição HTTP, e o `causationId` nulo do comando marca o ponto de entrada.

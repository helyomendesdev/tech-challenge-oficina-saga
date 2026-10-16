# Painel e consultas NRQL da Saga

O painel de `observabilidade/painel-saga.json` usa os atributos customizados que o
adaptador (`oficina_saga/newrelic_adapter.py`) grava na transacao: `sagaId`,
`osId`, `messageId`, `messageType`, `causationId`, `requestId`. Eles ficam no
evento `Transaction`. Spans e traces usam so atributos padrao do agente.

## Importar

1. New Relic One: Dashboards, Import dashboard.
2. Cole o conteudo de `painel-saga.json`.
3. Antes, troque `"accountIds": [0]` pelo ID da sua conta (o `0` e marcador; vale
   para os cinco widgets).
4. Escolha a conta de destino e importe.
5. A integracao AWS (CloudWatch Metric Streams ou integracao SQS) precisa estar
   ligada na conta para o ultimo widget ter dados. O nome da dimensao da fila
   (`aws.sqs.QueueName`) deve ser conferido no Data explorer.

## Consultas

### 1. Mensagens da Saga por tipo no tempo
Responde: que comandos e respostas estao circulando e em que volume.

```nrql
SELECT count(*) FROM Transaction WHERE messageType IS NOT NULL FACET messageType TIMESERIES SINCE 1 hour ago
```

### 2. Uma Saga por sagaId
Responde: a trilha completa de uma Saga, em ordem. Troque `<sagaId>` pelo valor
real (o formato exportado nao leva variavel de dashboard) antes de rodar, ou
edite o widget depois de importar.

```nrql
SELECT timestamp, appName, messageType, messageId, causationId, osId, requestId FROM Transaction WHERE sagaId = '<sagaId>' ORDER BY timestamp LIMIT 100 SINCE 1 day ago
```

### 3. Compensacoes
Responde: quantas vezes a Saga desfez passos, por tipo de comando de compensacao.

```nrql
SELECT count(*) FROM Transaction WHERE messageType IN ('EstornarPagamento', 'CancelarOrcamento', 'CancelarExecucao') FACET messageType SINCE 1 day ago
```

### 4. Spans dos servicos
Responde: quais traces atravessam os servicos, em que app, e quanto tempo cada
span levou. Use o `trace.id` para abrir o trace distribuido.

```nrql
SELECT trace.id, appName, name, duration FROM Span WHERE trace.id IS NOT NULL ORDER BY timestamp DESC LIMIT 50 SINCE 1 hour ago
```

### 5. Mensagens nas DLQs
Responde: se alguma mensagem esgotou as tentativas e foi para a fila morta (o
ideal e zero).

```nrql
SELECT latest(aws.sqs.ApproximateNumberOfMessagesVisible) FROM Metric WHERE aws.sqs.QueueName IN ('oficina-billing-comandos-dlq.fifo', 'oficina-execucao-comandos-dlq.fifo', 'oficina-os-saga-respostas-dlq.fifo') FACET aws.sqs.QueueName TIMESERIES SINCE 6 hours ago
```

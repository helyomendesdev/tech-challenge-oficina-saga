# Propagação de trace pelo SQS (v0.1)

Segue o ADR-007 do monólito (repositório da Fase 1, `docs/adrs/adr-007-correlacao-w3c-trace-context.md`): **W3C Trace Context** para o trace técnico, **`requestId`** como identificador de negócio. O que muda aqui é o transporte — HTTP vira SQS.

## Onde vai cada coisa

| Dado | Onde | Por quê |
|---|---|---|
| `traceparent` | **MessageAttribute** do SQS, `DataType: String` | É metadado de transporte, como o header HTTP. Fora do corpo, o contrato de negócio não muda quando a ferramenta de tracing muda, e o envelope rejeita o campo (`additionalProperties: false`) |
| `tracestate` | **MessageAttribute**, `DataType: String`, só se vier preenchido | Repassado sem alteração |
| `requestId` | **Corpo** (envelope) | É de negócio: precisa sobreviver a reprocessamento, DLQ e redrive, e aparecer em qualquer log que leia a mensagem — inclusive de quem não usa tracing |
| `sagaId`, `causationId` | **Corpo** (envelope) | Ligam as mensagens da Saga entre si, independentemente do trace |

O schema dos atributos está no trait `rastreio` do [`asyncapi.yaml`](../contrato/asyncapi.yaml).

## Produtor (quem envia)

```python
attrs = {"traceparent": {"DataType": "String", "StringValue": traceparent_do_span_atual()}}
if tracestate:
    attrs["tracestate"] = {"DataType": "String", "StringValue": tracestate}
sqs.send_message(QueueUrl=fila, MessageBody=json.dumps(envelope), MessageAttributes=attrs)
```

- O `span-id` dentro do `traceparent` enviado é o do **span de envio** do produtor (span `PRODUCER`), não o da requisição HTTP que chegou.
- Com outbox, o span de envio nasce no relay que publica; o `traceparent` a usar é o gravado junto com a linha do outbox, para o trace não partir entre "salvou" e "publicou".

## Consumidor (quem recebe)

```python
msg = sqs.receive_message(QueueUrl=fila, MessageAttributeNames=["traceparent", "tracestate"], ...)
```

1. **Pedir os atributos no `receive_message`.** Sem `MessageAttributeNames`, o SQS não os devolve — e isso não dá erro, só um trace partido.
2. Abrir um span **`CONSUMER` novo**: o `trace-id` é herdado; o `span-id` recebido vira o **pai** (`parent_span_id`). **Nunca** adotar o `span-id` recebido como próprio — é a armadilha do §5.1 do ADR-007, e aqui ela é mais fácil de cair porque não há middleware fazendo isso por você.
3. Pôr `requestId`, `sagaId` e `messageId` nos `contextvars` do log, como o `CorrelationIdMiddleware` faz no HTTP.
4. Mensagem **sem** `traceparent`: iniciar trace novo e logar o `requestId` — a correlação de negócio continua de pé.
5. Os comandos que o orquestrador envia a partir de uma resposta propagam o trace dessa resposta: a Saga inteira fica num trace só.

## Limites e armadilhas

- **SQS aceita no máximo 10 MessageAttributes por mensagem.** Trace usa 2; reservar os outros para necessidade real.
- **DLQ e redrive preservam os atributos.** Mensagem reprocessada da DLQ continua ligada ao trace original — útil para investigar, mas o span de reprocessamento deve ser filho novo, não reaproveitar span.
- **X-Ray usa o atributo de sistema `AWSTraceHeader`**, não o `traceparent`. Esta v0.1 não depende dele; se o grupo usar X-Ray, vale propagar os dois.
- **Agente New Relic:** a instrumentação automática do `boto3` pode já injetar cabeçalho próprio. Conferir na primeira integração se ela grava `traceparent` (W3C) ou só o cabeçalho proprietário; o contrato exige o W3C.

## Pendente

- Biblioteca de propagação por serviço depende da linguagem de cada um ([questões abertas](../docs/questoes-abertas.md), item 3). Em Python, `opentelemetry-instrumentation-boto3sqs` cobre os passos 1 e 2.
- Teste ponta a ponta que prova um trace só da HTTP de origem até `ExecucaoFinalizada` — cenário a entrar no `e2e/` quando os serviços existirem.

# Propagação de trace pelo SQS (v1)

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

Com o pacote `oficina_saga` (instalação no [README](../README.md)), o adaptador monta os MessageAttributes. Com o agente New Relic e uma transação ativa, ele usa o `traceparent` que o agente gera; sem eles, cai em `oficina_saga.rastreio` (W3C puro). O header proprietário `newrelic`, que o agente também emite, **não vai para a fila**: o adaptador só repassa `traceparent` e `tracestate`.

```python
import json

from oficina_saga import newrelic_adapter, rastreio

# Dentro de uma transação com agente: o adaptador pede o contexto ao agente.
attrs = newrelic_adapter.atributos_para_envio()

# Sem agente (ou para encadear a partir de uma mensagem recebida), o rastreio
# direto: span filho do traceparent que chegou, mais o tracestate se houver.
# attrs = rastreio.injetar(None, rastreio.novo_span_filho(traceparent_recebido), tracestate)

sqs.send_message(
    QueueUrl=fila,  # FIFO: ...-comandos.fifo / ...-respostas.fifo
    MessageBody=json.dumps(envelope),
    MessageAttributes=attrs,
    MessageGroupId=envelope["sagaId"],
    MessageDeduplicationId=envelope["messageId"],
)
```

Assinaturas: `atributos_para_envio(message_attributes=None, *, traceparent_atual=None)`, `rastreio.injetar(message_attributes, traceparent, tracestate=None)`, `rastreio.novo_span_filho(traceparent)`, `rastreio.novo_traceparent()`. Passando `traceparent_atual=` ao adaptador, o fallback sem agente gera um filho dele em vez de trace novo.

- O `span-id` dentro do `traceparent` enviado é o do **span de envio** do produtor (span `PRODUCER`), não o da requisição HTTP que chegou.
- Com outbox, o span de envio nasce no relay que publica; o `traceparent` a usar é o gravado junto com a linha do outbox (passe-o em `traceparent_atual`), para o trace não partir entre "salvou" e "publicou".

## Consumidor (quem recebe)

```python
import json

from oficina_saga import newrelic_adapter, rastreio

resposta = sqs.receive_message(
    QueueUrl=fila, MessageAttributeNames=["traceparent", "tracestate"]
)
for m in resposta.get("Messages", []):
    # Aceita o contexto (transport_type="Queue") e devolve o traceparent efetivo;
    # sem traceparent válido na mensagem, abre trace novo.
    traceparent = newrelic_adapter.aceitar_recebido(m.get("MessageAttributes"))
    envelope = json.loads(m["Body"])
    newrelic_adapter.registrar_mensagem(envelope)  # sagaId, osId, messageId, ...
    campos = rastreio.parse_traceparent(traceparent)  # trace_id, parent_id, flags
    ...  # processa; ao responder, atributos_para_envio(traceparent_atual=traceparent)
```

Assinaturas: `aceitar_recebido(message_attributes)` devolve o `traceparent` (str), `registrar_mensagem(envelope)` grava os atributos customizados na transação atual (não faz nada sem agente), `rastreio.extrair(message_attributes)` devolve `(traceparent, tracestate)` e `rastreio.parse_traceparent(valor)` devolve um `Traceparent` ou `None`. O `transport_type` é `"Queue"`: o agente 9.13 não tem valor para SQS, e valor fora da lista vira `"Unknown"` (ver [`newrelic-agente.md`](newrelic-agente.md)).

1. **Pedir os atributos no `receive_message`.** Sem `MessageAttributeNames`, o SQS não os devolve — e isso não dá erro, só um trace partido.
2. Abrir um span **`CONSUMER` novo**: o `trace-id` é herdado; o `span-id` recebido vira o **pai** (`parent_span_id`). **Nunca** adotar o `span-id` recebido como próprio — é a armadilha do §5.1 do ADR-007, e aqui ela é mais fácil de cair porque não há middleware fazendo isso por você. Com o agente, `aceitar_recebido` já faz isso pela API do agente.
3. Pôr `requestId`, `sagaId` e `messageId` nos `contextvars` do log, como o `CorrelationIdMiddleware` faz no HTTP.
4. Mensagem **sem** `traceparent`: iniciar trace novo e logar o `requestId` — a correlação de negócio continua de pé.
5. Os comandos que o orquestrador envia a partir de uma resposta propagam o trace dessa resposta: a Saga inteira fica num trace só.

## Limites e armadilhas

- **SQS aceita no máximo 10 MessageAttributes por mensagem.** Trace usa 2; reservar os outros para necessidade real.
- **DLQ e redrive preservam os atributos.** Mensagem reprocessada da DLQ continua ligada ao trace original — útil para investigar, mas o span de reprocessamento deve ser filho novo, não reaproveitar span.
- **X-Ray usa o atributo de sistema `AWSTraceHeader`**, não o `traceparent`. Esta v1 não depende dele; se o grupo usar X-Ray, vale propagar os dois.
- **Agente New Relic:** a instrumentação automática do `boto3` pode já injetar cabeçalho próprio. Conferir na primeira integração se ela grava `traceparent` (W3C) ou só o cabeçalho proprietário; o contrato exige o W3C. O header proprietário `newrelic` não vai para a fila (o adaptador o descarta).

## Pendente

- A decisão de instrumentação é o agente New Relic da Fase 3: [`newrelic-agente.md`](newrelic-agente.md) registra a API usada. Cada serviço que não seja Python precisa do equivalente na sua linguagem ([questões abertas](../docs/questoes-abertas.md), item 3).
- Teste ponta a ponta que prova um trace só da HTTP de origem até `ExecucaoFinalizada` — o cenário do `.feature` confere `requestId` e `trace-id` iguais nas mensagens da Saga; falta conferi-lo numa stack com o agente ligado.

# API de distributed tracing do agente Python do New Relic

Fonte: `newrelic/newrelic-python-agent`, tag **v9.13.0** (a mais recente da linha 9.x; a 10.0.0 já existe, mas a tarefa pede 9.x). Linhas citadas valem para essa tag.

## Métodos e funções de módulo

`newrelic/api/transaction.py`:

| Onde | Linha | Assinatura (literal) |
|---|---|---|
| método de `Transaction` | 1169 | `def insert_distributed_trace_headers(self, headers):` |
| método de `Transaction` | 1303 | `def accept_distributed_trace_headers(self, headers, transport_type="HTTP"):` |
| função de módulo | 2170 | `def accept_distributed_trace_headers(headers, transport_type="HTTP"):` |
| função de módulo | 2182 | `def insert_distributed_trace_headers(headers):` |

As funções de módulo pegam `current_transaction()` e delegam; sem transação ativa, não fazem nada (`insert` devolve `None`, `accept` também).

Exportação em `newrelic.agent` (`newrelic/agent.py`):

- import: linha 27 (`accept_distributed_trace_headers as __accept_distributed_trace_headers`) e linha 56 (`insert_distributed_trace_headers as __insert_distributed_trace_headers`);
- atribuição pública: linha 260 (`accept_distributed_trace_headers = __wrap_api_call(`) e linha 263 (`insert_distributed_trace_headers = __wrap_api_call(`).

Uso: `newrelic.agent.insert_distributed_trace_headers(headers)` e `newrelic.agent.accept_distributed_trace_headers(headers, transport_type=...)`.

## Formato do argumento `headers`

- `insert`: o corpo é `headers.extend(self._generate_distributed_trace_headers())` (linha 1170). O gerador devolve **tuplas `(nome, valor)`**, então `headers` precisa ser uma **lista** (`headers = []`), não um dict. Depois, converter para MessageAttributes do SQS.
- `accept`: aceita objeto com `.get(...)` (dict) ou lista de tuplas; no segundo caso itera `for k, v in headers:` (linhas 1308-1322) e reconhece as chaves `traceparent`, `tracestate` e `newrelic`.
- `accept` retorna sem efeito se o tracing distribuído está desligado ou se não há `trusted_account_key` (`_can_accept_distributed_trace_headers`, linhas 1172-1190). Também ignora se a transação já criou ou aceitou contexto antes.

## `transport_type`

Lista de valores aceitos: `newrelic/api/transaction.py` linha 85:

`DISTRIBUTED_TRACE_TRANSPORT_TYPES = set(("HTTP", "HTTPS", "Kafka", "JMS", "IronMQ", "AMQP", "Queue", "Other"))`

Validação: linhas 1272-1273 (`if transport_type not in DISTRIBUTED_TRACE_TRANSPORT_TYPES: transport_type = "Unknown"`). Valor fora da lista não dá erro, vira `"Unknown"`, e é gravado como atributo `parent.transportType` (linhas 925-926).

Para SQS: **`"Queue"`**. Não há valor `SQS` na lista, e o fonte não define outro para fila gerenciada. Fora de `AMQP`/`JMS`/`Kafka`/`IronMQ`, `Queue` é o genérico. Isso é uma escolha por exclusão, não algo que o fonte diga explicitamente sobre SQS: `SQS` não encontrado no fonte em `newrelic/api/transaction.py`.

## Headers emitidos

Gerados em `_generate_distributed_trace_headers` (`newrelic/api/transaction.py`, linhas 1140-1167):

| Header | Linha | Condição |
|---|---|---|
| `traceparent` | 1145 | sempre que há dados de trace |
| `tracestate` | 1150 | sempre que há dados de trace (acrescenta o `tracestate` recebido, linhas 1148-1149) |
| `newrelic` | 1160 | só se `not self._settings.distributed_tracing.exclude_newrelic_header` (linha 1154) |

Não existe flag "W3C ligado" na 9.13.0: `traceparent` e `tracestate` são emitidos incondicionalmente; só o `newrelic` é opcional.

Config do header `newrelic`:

- `newrelic/core/config.py` linha 770: `_settings.distributed_tracing.exclude_newrelic_header = False` (padrão: o header `newrelic` **é** emitido);
- `newrelic/config.py` linha 449: lido do `newrelic.ini` como `distributed_tracing.exclude_newrelic_header` (`getboolean`);
- variável de ambiente para essa chave: não encontrado no fonte em `newrelic/core/config.py` (só há `NEW_RELIC_DISTRIBUTED_TRACING_ENABLED`, linha 769, que liga/desliga o tracing distribuído inteiro).

Para o contrato desta Saga (só `traceparent` e `tracestate` como MessageAttributes): ligar `distributed_tracing.exclude_newrelic_header = true` no ini, ou simplesmente descartar a tupla `newrelic` ao montar os atributos.

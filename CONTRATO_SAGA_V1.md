# CONTRATO_SAGA_V1 — Saga Orquestrada (Grupo 47)

> Status: RASCUNHO v1 (autor: Hélio Mendes) — revisão e assinatura pendentes do Luís (owner do `-saga`).
> Aceito como base de implementação após revisão. Mudança de quebra = nova versão do contrato (v2), nunca mutar v1.

## 1. Escopo

Contrato de eventos da Saga orquestrada (orquestrador = OS Service, repo `-os`).
Fluxo de felicidade: abrir OS → orçamento → aprovação → execução → finalização → pagamento → notificação.

## 2. Topologia

```
       +---------------------+
os-billing <--- SQS.odos --->   -os (orquestrador)
       |                        |
SQS.billing.pos-pagamento      SQS.execucao.eventos
       |                        |
       v                        v
   -billing                 -execution
```

- Fila: SQS (decisão pendente da Sophia; contrato agnóstico no transporte — Kafka/RabbitMQ muda só a impl)
- Formato do envelope: JSON UTF-8
- Participantes: `-os` (orquestrador), `-billing`, `-execution`

## 3. Envelope padrão de mensagem

```json
{
  "event_id": "uuid-v4 (idempotência)",
  "event_type": "OrdemServicoCriada",
  "event_version": 1,
  "occurred_at": "2026-10-13T17:55:00Z",
  "correlation_id": "id da OS para tracing (New Relic)",
  "payload": { },
  "metadata": { "producer": "os-service", "schema": "v1" }
}
```

Regras:
- `event_id` único por mensagem; consumidor dedupe por `event_id` + `event_type`
- `event_version` obrigatório: mudança de quebra = v2, nunca mutar v1
- `correlation_id` propagado em todo log/tracing

## 4. Eventos do fluxo principal

### 4.1 OrdemServicoCriada (os → saga)
```
payload: { os_id: int, cliente_id: int, veiculo_id: int,
           servico_ids: [int], peca_ids: [int],
           valor_estimado: decimal, status: "RECEBIDA" }
```

### 4.2 OrcamentoGerado (billing → saga)
```
payload: { os_id: int, orcamento_id: str, valor_total: decimal,
           detalhamento: [{ tipo: "SERVICO"|"PECA", ref_id: int,
                            quantidade: int, valor: decimal }] }
```

### 4.3 OrcamentoAprovado / OrcamentoRecusado (cliente → billing → saga)
```
payload: { os_id: int, orcamento_id: str, decisao: "APROVADO"|"RECUSADO" }
```

### 4.4 ExecucaoIniciada (saga → execution)
```
payload: { os_id: int, itens_servico: [{ servico_id, status }],
           prazo_estimado_horas: int }
```

### 4.5 ExecucaoConcluida (execution → saga)
```
payload: { os_id: int, itens_servico: [{ servico_id, status: "CONCLUIDO" }],
           data_conclusao: iso-8601 }
```

### 4.6 PagamentoSolicitado (saga → billing)
```
payload: { os_id: int, valor_total: decimal,
           mercado_pago: { preference_id: str|null,
                           payment_link: str|null } }
```

### 4.7 PagamentoAprovado / PagamentoRecusado (billing → saga)
```
payload: { os_id: int, pagamento_id: str, valor_total: decimal,
           status: "APROVADO"|"RECUSADO", mercado_pago_id: str|null }
```

### 4.8 OrdemServicoEntregue (saga → todos os participantes)
```
payload: { os_id: int, data_entrega: iso-8601 }
```

## 5. Compensações (rollback da orquestrada)

Cada passo tem ação de compensação; disparada em ordem inversa a quem iniciou:

| Passo           | Compensação                    | Publicada como           |
|------------------|--------------------------------|--------------------------|
| Reserva estoque  | Devolve estoque                | `EstoqueLiberado`        |
| Orçamento        | Marca orçamento como EXPIRADO  | `OrcamentoCancelado`     |
| Execução         | Marca execução como ABORTADA   | `ExecucaoAbortada`       |
| Pagamento        | Estorno no Mercado Pago        | `PagamentoEstornado`     |
| OS               | Status → CANCELADA             | `OrdemServicoCancelada`  |

- Nenhuma compensação acessa o banco de outro serviço (comunicação via SQS)
- Compensação que falha = retry com backoff; após 3 tentativas, evento em DLQ + alerta New Relic

## 6. Idempotência

- Chave: `event_id`
- Consumidor mantém tabela `processed_events(event_id PK, event_type, processed_at)` (ou equivalente NoSQL)
- Mensagem duplicada = skip silencioso + log `evento_duplicado_id`

## 7. DLQ e observabilidade

- DLQ por fila: `SQS.odos.dlq`, `SQS.execucao.eventos.dlq`, `SQS.billing.pos-pagamento.dlq`
- Métricas New Relic: `saga.step.duration`, `saga.compensation.invoked`, `saga.dlq.messages`
- Todo evento loga `event_id`, `event_type`, `correlation_id`, `os_id`

## 8. Limites (v1)

- Contrato não exige HTTPS/callback: comunicação com billing/execution é por fila
- Timeout de orçamento pendente: 30 min sem resposta → compensação automática por timeout
- Timeout de pagamento: expira conforme regra do Mercado Pago (varia por preference)

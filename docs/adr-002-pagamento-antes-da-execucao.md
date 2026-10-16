# ADR-002 — Pagamento antes da execução, como passo próprio da Saga

| Informação | Valor |
|---|---|
| **ADR** | 002 |
| **Status** | Proposto (v1 — para revisão do grupo, prazo 24/10/2026) |
| **Autor** | Luís Fernando Montes |
| **Data** | 2026-10-16 |

## 1. Contexto

O enunciado põe o Mercado Pago no Billing, e a v0.1 do contrato não tinha mensagem de pagamento. O rascunho do Hélio punha o pagamento **depois** da execução e previa uma reserva de estoque.

## 2. Decisão

1. O pagamento é um **passo próprio da Saga, antes da execução**: `OrcamentoAprovado` leva a `PAGAMENTO_SOLICITADO` (comando `SolicitarPagamento`); `PagamentoConfirmado` leva a `EXECUCAO_SOLICITADA` (comando `IniciarExecucao`); `PagamentoFalhou` leva a `COMPENSANDO`.
2. O **estorno é uma mensagem separada** (`EstornarPagamento`, resposta `PagamentoEstornado`), não está embutido no `CancelarOrcamento`.
3. A compensação segue a ordem inversa da ida: execução, pagamento, orçamento. Quando é o pagamento que falha, não há o que estornar e só o orçamento é desfeito (`CancelarOrcamento`, motivo `PAGAMENTO_FALHOU`).
4. A **reserva de estoque fica fora do v1**: não há serviço de estoque, e as peças são reservadas pela Execução (que devolve as reservadas em `CancelarExecucao`).

## 3. Por que

- **Nunca fica serviço executado sem pagamento.** Com o pagamento depois da execução, uma falha ao cobrar deixaria o trabalho feito e sem receita.
- **A compensação é simétrica.** Cada passo que muda o mundo tem seu comando de desfazer, e o orquestrador só avança na compensação com a confirmação do passo anterior. Com o estorno embutido no `CancelarOrcamento`, o orquestrador não saberia se o dinheiro voltou antes de dar o orçamento por cancelado.
- **O Billing continua dono do Mercado Pago.** O OS Service só vê confirmado, falhou ou estornado.

## 4. Consequências

- Um estado novo (`PAGAMENTO_SOLICITADO`), cinco mensagens (`SolicitarPagamento`, `EstornarPagamento`, `PagamentoConfirmado`, `PagamentoFalhou`, `PagamentoEstornado`) e o motivo `PAGAMENTO_FALHOU` em `CancelarOrcamento`.
- `ExecucaoFalhou` em `EXECUCAO_SOLICITADA` passa a estornar o pagamento antes de cancelar o orçamento.
- O cliente precisa receber o link de pagamento: em aberto em [`questoes-abertas.md`](questoes-abertas.md), item 1.

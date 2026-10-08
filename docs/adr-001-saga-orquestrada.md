# ADR-001 — Saga orquestrada, com o orquestrador dentro do OS Service

| Informação | Valor |
|---|---|
| **ADR** | 001 |
| **Status** | Aceito (v0.1 — rascunho do contrato) |
| **Autor** | Luís Fernando Montes |
| **Data** | 2026-10-07 |

## 1. Contexto

A Fase 4 quebra o monólito da oficina em pelo menos três microsserviços, cada um com repositório, infraestrutura e banco próprios: **OS Service** (ordem de serviço), **Billing** (orçamento e pagamento, Mercado Pago) e **Execução** (execução dos serviços e uso de peças, NoSQL). O ciclo de uma OS atravessa os três, e não existe mais uma transação de banco que cubra o caminho inteiro. O enunciado pede o padrão **Saga** com **compensação**.

Há duas formas de Saga:

- **Coreografada** — cada serviço reage a eventos dos outros; não há dono do fluxo.
- **Orquestrada** — um orquestrador envia comandos, recebe respostas e guarda o estado da Saga.

## 2. Decisão

1. **Saga orquestrada.** O orquestrador é um componente **dentro do OS Service**, não um serviço à parte.
2. **Comando e resposta** sobre **Amazon SQS**: uma fila de comandos por serviço participante e uma fila de respostas para o orquestrador, cada uma com **DLQ**:
   - `oficina-billing-comandos` (+ `-dlq`)
   - `oficina-execucao-comandos` (+ `-dlq`)
   - `oficina-os-saga-respostas` (+ `-dlq`)
3. **Contrato em AsyncAPI 3.0**, com um **JSON Schema (draft-07) por mensagem** sobre um **envelope comum** (`contrato/`). O contrato é a fonte única: os serviços validam contra ele e a CI valida os exemplos contra ele. Draft-07 porque é a versão de JSON Schema que o parser do AsyncAPI aceita sem conversão.
4. **Idempotência por `messageId`.** SQS entrega "pelo menos uma vez"; todo consumidor registra os `messageId` já processados e descarta a repetição.
5. **Estado da Saga persistido no banco do OS Service**, na mesma transação que muda o status da OS e grava o próximo comando (outbox), para não haver comando enviado sem estado salvo nem estado salvo sem comando.

## 3. Justificativa

| Critério | Orquestrada | Coreografada |
|---|---|---|
| Onde está o fluxo | Num lugar só — a máquina de estados do orquestrador | Espalhado pelos assinantes de cada evento |
| Compensação | O orquestrador sabe o que já foi feito e manda desfazer na ordem inversa | Cada serviço precisa inferir sozinho o que desfazer |
| Visibilidade para depurar e para a banca | Estado atual da Saga consultável por `sagaId` | Exige reconstruir o fluxo pelos logs |
| Acoplamento | Participantes não se conhecem; só conhecem o contrato de comando/resposta | Participantes conhecem os eventos uns dos outros |
| Custo | Um componente a mais para manter | Nenhum componente central |

O fluxo de uma OS é **linear e curto** (orçamento → aprovação → execução), e a compensação é a parte que a avaliação olha. Orquestrar deixa essa parte explícita num lugar só.

**Por que dentro do OS Service:** a OS já é a dona do ciclo de vida e do status; um orquestrador separado seria um quarto serviço, com repositório, pipeline e banco próprios, para guardar um estado que é da OS. Fica dentro, como módulo isolado, e pode sair depois se crescer.

## 4. Consequências

**Positivas**
- Compensação explícita e testável ponta a ponta (`e2e/features/fluxo_os.feature`).
- Billing e Execução ficam simples: recebem comando, respondem, não conhecem a Saga.
- Contrato versionado (`version` no envelope) e validado em CI.

**Negativas**
- O OS Service concentra mais responsabilidade e vira ponto de atenção de disponibilidade: se ele cair, as Sagas param (mas não se perdem — as respostas esperam na fila).
- Exige outbox ou equivalente para não perder comando entre "salvei o estado" e "publiquei na fila".
- Entrega "pelo menos uma vez" obriga idempotência em todos os consumidores.

## 5. Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Saga coreografada | Fluxo e compensação espalhados; mais difícil de explicar, testar e depurar com três pessoas implementando serviços diferentes |
| Orquestrador como serviço próprio | Um repo/pipeline/banco a mais sem ganho para o tamanho do fluxo |
| AWS Step Functions como orquestrador | Prende o fluxo à AWS e tira a máquina de estados do código que o grupo testa; disponibilidade no Learner Lab também é incerta |
| SNS + SQS (pub/sub) | Comando tem destinatário único; fan-out não agrega nesta versão |

## 6. Pendências

Ver [`questoes-abertas.md`](questoes-abertas.md) — em especial onde entra o pagamento do Mercado Pago e se as filas são padrão ou FIFO.

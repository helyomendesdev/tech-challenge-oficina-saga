# ADR-004 — Peças consumidas e identificação da reserva

| Informação | Valor |
|---|---|
| **ADR** | 004 |
| **Status** | Proposto (v1.1 — para revisão do grupo) |
| **Autor** | Luís Fernando Montes |
| **Data** | 2026-11-01 |

## 1. Contexto

Na v1.0, `IniciarExecucao` identifica a peça só por `pecaId` + `quantidade`, e `ExecucaoFinalizada` leva só `execucaoId` + `finalizadaEm`. Faltam duas coisas para o Execution Service fechar o ciclo:

- **Casar o consumo com a reserva.** O OS Service reserva por item de peça da OS (`item_peca_os_id`, já usado em `iniciar_servico`). A mesma peça pode aparecer em mais de um item, e o `pecaId` sozinho não diz qual reserva baixar.
- **Devolver o que não foi usado.** Sem a lista do que foi consumido, o OS Service só pode tratar a quantidade reservada como consumida, e a peça que sobrou nunca volta ao estoque.

## 2. Decisão

1. **`itemPecaOSId` em `IniciarExecucao.pecas[]`**, opcional. O OS Service informa o item reservado de cada peça. Ele fica na definição comum `itensPeca`, então também é aceito, sem uso, em `GerarOrcamento` (onde ainda não existe reserva).
2. **`pecasConsumidas[]` em `ExecucaoFinalizada`**, opcional. Cada item leva `itemPecaOSId`, `pecaId` e `quantidade` (mínimo 1), e os três são obrigatórios dentro do item. Item reservado que não aparece na lista não foi consumido, e o OS Service devolve a reserva ao estoque.
3. **Versão.** O contrato passa a 1.1.0 (`asyncapi.yaml` e `pyproject.toml`). O campo `version` das mensagens continua `1`: a mudança só acrescenta campos opcionais.
4. **Iniciar e finalizar cada serviço** ficam internos ao Execution Service. Para a Saga só existem `IniciarExecucao`, `ExecucaoFinalizada` e `CancelarExecucao`. Os endpoints por serviço do OS Service (`legacy_views.py`) são da Fase 3 e não entram na Saga.

## 3. Por que

- **Opcional em vez de obrigatório:** quem produz no formato v1.0 continua válido, e nenhum serviço precisa parar para migrar.
- **Por item reservado, e não por peça:** é a chave que o OS Service já usa para reservar e baixar, e a que o Execution Service já modela (`item_peca_os_id`).
- **Lista do consumido, e não do sobrado:** o evento descreve o que aconteceu na execução. A devolução é consequência, decidida pelo dono do estoque.

## 4. Ordem de adoção (compatibilidade)

Os payloads seguem com `additionalProperties: false`. Um **consumidor** que valide com os schemas v1.0 **recusa** mensagem que traga os campos novos. Por isso a ordem é:

1. O consumidor passa aos schemas v1.1: o Execution Service para `IniciarExecucao`, e o OS Service para `ExecucaoFinalizada` (re-vendor da tag `v1.1.0`).
2. Só depois o produtor começa a mandar os campos.

Hoje o OS Service não valida as mensagens recebidas contra o JSON Schema em tempo de execução, então o passo 1 não é bloqueante para ele. Ainda assim, o re-vendor mantém os testes de contrato dele alinhados.

## 5. Consequências

- O OS Service precisa mandar `itemPecaOSId` ao montar `IniciarExecucao` e, ao receber `ExecucaoFinalizada`, baixar o consumido e devolver o restante.
- O Execution Service registra o consumo por `itemPecaOSId` e preenche `pecasConsumidas`.
- `ExecucaoFinalizada` sem `pecasConsumidas` (produtor ainda no formato v1.0) mantém o comportamento da v1.0: a quantidade reservada é tratada como consumida.

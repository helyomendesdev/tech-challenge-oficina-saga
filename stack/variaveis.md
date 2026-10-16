# Variaveis da stack

Cada servico le estas variaveis do ambiente. O `docker-compose.yml` as injeta com
`${VAR:-padrao}`: o padrao e o valor do LocalStack; para sobrescrever, exporte a
variavel ou use um `.env` ao lado do compose. **No EKS/AWS so os valores mudam**
(URLs reais do SQS, credenciais do Learner Lab ou IRSA, sem `AWS_ENDPOINT_URL`);
os nomes sao os mesmos.

| Variavel | Padrao local | Para que serve |
|---|---|---|
| `QUEUE_BILLING_COMANDOS_URL` | `http://localstack:4566/000000000000/oficina-billing-comandos.fifo` | Fila de comandos do orquestrador para o Billing (Billing consome, OS envia). |
| `QUEUE_BILLING_COMANDOS_DLQ_URL` | `.../oficina-billing-comandos-dlq.fifo` | DLQ da fila acima (mensagem que estoura `maxReceiveCount`). |
| `QUEUE_EXECUCAO_COMANDOS_URL` | `.../oficina-execucao-comandos.fifo` | Fila de comandos para a Execucao (Execucao consome, OS envia). |
| `QUEUE_EXECUCAO_COMANDOS_DLQ_URL` | `.../oficina-execucao-comandos-dlq.fifo` | DLQ da fila acima. |
| `QUEUE_OS_SAGA_RESPOSTAS_URL` | `.../oficina-os-saga-respostas.fifo` | Fila de respostas de Billing e Execucao (OS consome, os dois enviam). |
| `QUEUE_OS_SAGA_RESPOSTAS_DLQ_URL` | `.../oficina-os-saga-respostas-dlq.fifo` | DLQ da fila de respostas. |
| `SAGA_QUEUE_URL` | igual a `QUEUE_BILLING_COMANDOS_URL` (provisorio) | Legado: hoje o `-os` publica **todas** as mensagens da Saga nesta fila unica (`app/settings.py:150`, `SQSSagaPublisher` em `os_service/infrastructure/saga_adapters.py`). No v1 cada comando vai para a fila do destinatario (Billing ou Execucao) e o `-os` consome `QUEUE_OS_SAGA_RESPOSTAS_URL`; a variavel sai no realinhamento do orquestrador. |
| `AWS_ENDPOINT_URL` | `http://localstack:4566` | Endpoint do SQS/DynamoDB. So existe no local; em AWS fica **vazia/ausente** para o SDK usar o endpoint real. |
| `AWS_REGION` | `us-east-1` | Regiao dos clientes SQS/DynamoDB (a confirmar com a infra, ver `servers` do AsyncAPI). Tambem exportada como `AWS_DEFAULT_REGION`. |
| `AWS_ACCESS_KEY_ID` | `test` | Credencial **fake** do LocalStack; qualquer valor serve la. Em AWS vem do Learner Lab/IRSA. |
| `AWS_SECRET_ACCESS_KEY` | `test` | Idem. Nunca versionar credencial real. |
| `NEW_RELIC_LICENSE_KEY` | vazia | Chave do agente New Relic. Vazia = o servico sobe sem agente (o entrypoint do `-os` so liga o agente se ela existir). |
| `NEW_RELIC_APP_NAME` | `oficina` | Nome da aplicacao no New Relic; o compose sobrescreve por servico (`oficina-os`, `oficina-billing`, `oficina-execucao`) via `NEW_RELIC_APP_NAME_OS`, `_BILLING`, `_EXECUCAO`. |

## Especificas de servico (INFERIDAS, confirmar com o dono de cada repo)

| Variavel | Servico | Para que serve |
|---|---|---|
| `DJANGO_SECRET_KEY` | os | Segredo do Django; o padrao local e inseguro de proposito. |
| `RUN_MIGRATIONS` | os | `true` faz o entrypoint rodar `migrate` na subida (confirmado no `docker-entrypoint.sh`). |
| `POSTGRES_OS_DB`, `POSTGRES_OS_USER`, `POSTGRES_OS_PASSWORD` | os, postgres-os | Banco local do OS; o servico recebe como `POSTGRES_HOST/DB/USER/PASSWORD`. |
| `POSTGRES_BILLING_DB`, `POSTGRES_BILLING_USER`, `POSTGRES_BILLING_PASSWORD` | billing, postgres-billing | Banco local do Billing; o servico recebe `DATABASE_URL`. Repo ainda vazio: o formato e proposta. |
| `DYNAMODB_ENDPOINT_URL` | execucao | Endpoint do DynamoDB (o LocalStack). Repo ainda vazio: o nome e proposta. |

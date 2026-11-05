# Variaveis da stack

Cada servico le estas variaveis do ambiente. O `docker-compose.yml` as injeta com
`${VAR:-padrao}`: o padrao e o valor do LocalStack; para sobrescrever, exporte a
variavel ou use um `.env` ao lado do compose. **No EKS/AWS so os valores mudam**
(URLs reais do SQS, credenciais do Learner Lab ou IRSA, sem `AWS_ENDPOINT_URL`);
os nomes sao os mesmos.

As `QUEUE_*` abaixo sao os nomes **da stack**. Cada servico le a URL com o nome
que o seu codigo define, e o compose faz a ponte (tabela "Por servico", mais abaixo).

| Variavel | Padrao local | Para que serve |
|---|---|---|
| `QUEUE_BILLING_COMANDOS_URL` | `http://localstack:4566/000000000000/oficina-billing-comandos.fifo` | Fila de comandos do orquestrador para o Billing (Billing consome, OS envia). |
| `QUEUE_BILLING_COMANDOS_DLQ_URL` | `.../oficina-billing-comandos-dlq.fifo` | DLQ da fila acima (mensagem que estoura `maxReceiveCount`). |
| `QUEUE_EXECUCAO_COMANDOS_URL` | `.../oficina-execucao-comandos.fifo` | Fila de comandos para a Execucao (Execucao consome, OS envia). |
| `QUEUE_EXECUCAO_COMANDOS_DLQ_URL` | `.../oficina-execucao-comandos-dlq.fifo` | DLQ da fila acima. |
| `QUEUE_OS_SAGA_RESPOSTAS_URL` | `.../oficina-os-saga-respostas.fifo` | Fila de respostas de Billing e Execucao (OS consome, os dois enviam). |
| `QUEUE_OS_SAGA_RESPOSTAS_DLQ_URL` | `.../oficina-os-saga-respostas-dlq.fifo` | DLQ da fila de respostas. |
| `AWS_ENDPOINT_URL` | `http://localstack:4566` | Endpoint do SQS/DynamoDB. So existe no local; em AWS fica **vazia/ausente** para o SDK usar o endpoint real. |
| `AWS_REGION` | `us-east-1` | Regiao dos clientes SQS/DynamoDB (a confirmar com a infra, ver `servers` do AsyncAPI). Tambem exportada como `AWS_DEFAULT_REGION`. |
| `AWS_ACCESS_KEY_ID` | `test` | Credencial **fake** do LocalStack; qualquer valor serve la. Em AWS vem do Learner Lab/IRSA. |
| `AWS_SECRET_ACCESS_KEY` | `test` | Idem. Nunca versionar credencial real. |
| `NEW_RELIC_LICENSE_KEY` | vazia | Chave do agente New Relic. Vazia = o servico sobe sem agente (o entrypoint do `-os` so liga o agente se ela existir). |
| `NEW_RELIC_APP_NAME` | `oficina` | Nome da aplicacao no New Relic; o compose sobrescreve por servico (`oficina-os`, `oficina-billing`, `oficina-execucao`) via `NEW_RELIC_APP_NAME_OS`, `_BILLING`, `_EXECUCAO`. |

## Por servico (lido no codigo de cada repo)

| Servico | Variavel que o codigo le | Recebe no compose | Onde o nome esta |
|---|---|---|---|
| os | `SAGA_BILLING_QUEUE_URL` | `QUEUE_BILLING_COMANDOS_URL` | `app/settings.py:153` |
| os | `SAGA_EXECUTION_QUEUE_URL` | `QUEUE_EXECUCAO_COMANDOS_URL` | `app/settings.py:154` |
| os, os-consumidor | `SAGA_RESPONSE_QUEUE_URL` | `QUEUE_OS_SAGA_RESPOSTAS_URL` | `app/settings.py:158`, `saga_consumidor.py:116` |

O `-os` ainda aceita `SAGA_QUEUE_URL` (fila unica, legado) como fallback, mas
replica o comando para os dois destinos e loga aviso; o compose nao a passa mais.
| execucao | `EXECUTION_COMMANDS_QUEUE_URL` | `QUEUE_EXECUCAO_COMANDOS_URL` | `execution_service/config.py` |
| execucao | `SAGA_RESPONSES_QUEUE_URL` (plural, diferente do -os) | `QUEUE_OS_SAGA_RESPOSTAS_URL` | `execution_service/config.py` |
| execucao | `EXECUTION_DYNAMODB_TABLE` | `oficina-execucao` | `execution_service/config.py` |

O `-os` e o Execution nao passam `endpoint_url` ao boto3: no local os dois
dependem de o SDK ler `AWS_ENDPOINT_URL` do ambiente. A tabela do Execution e
criada pelo servico `criar-filas` (`stack/criar_tabela_execucao.py`), com o
mesmo esquema do `infra/main.tf` dele.

O servico `os-consumidor` roda `python manage.py consumir_respostas_saga` na
mesma imagem do `os`: o entrypoint do `-os` so sobe o gunicorn, e sem esse
processo a Saga nao anda depois da primeira resposta.

O Execution e construido de `feat/execution-service` (`EXECUTION_GIT_REF`),
porque a `main` dele ainda nao tem codigo nem Dockerfile.

## Outras especificas de servico

| Variavel | Servico | Para que serve |
|---|---|---|
| `DJANGO_SECRET_KEY` | os | Segredo do Django; o padrao local e inseguro de proposito. |
| `RUN_MIGRATIONS` | os, os-consumidor | `true` faz o entrypoint rodar `migrate` na subida (confirmado no `docker-entrypoint.sh`); so o `os` migra. |
| `POSTGRES_OS_DB`, `POSTGRES_OS_USER`, `POSTGRES_OS_PASSWORD` | os, postgres-os | Banco local do OS; o servico recebe como `POSTGRES_HOST/DB/USER/PASSWORD`. |
| `POSTGRES_BILLING_DB`, `POSTGRES_BILLING_USER`, `POSTGRES_BILLING_PASSWORD` | billing, postgres-billing | Banco local do Billing; o servico recebe `DATABASE_URL`. Repo ainda vazio: o formato e proposta, e o `up --build` falha ate o Billing ter Dockerfile. |

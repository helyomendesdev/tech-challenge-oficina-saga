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

O -os e o Billing tambem aceitam ref: `OS_GIT_URL` (padrao o repo do -os) e
`OS_GIT_REF` (padrao `main`) servem para testar um PR antes do merge, por exemplo
`OS_GIT_URL=https://github.com/luisfmontes/tech-challenge-oficina-os.git`
e `OS_GIT_REF=feat/consulta-saga`; `BILLING_GIT_REF` (padrao `main`) faz o mesmo no
Billing (e `BILLING_GIT_URL`/`EXECUTION_GIT_URL` trocam o repositorio, ex.: um fork com a branch do PR). A imagem do Billing sobe so a API; `billing-migrate`, `billing-consumidor`,
`billing-relay` e `billing-operations` rodam o `billing_service.cli` na mesma imagem.

## Autenticacao e pagamento no teste integrado (BDD stack-real)

Estas variaveis sao do **driver** (`e2e/driver_stack_real.py`), exceto as marcadas
com um servico. Sao so para o teste local: a chave privada nunca entra no
repositorio (`stack/.local/` esta no `.gitignore`).

| Variavel | Quem le | Padrao | Para que serve |
|---|---|---|---|
| `ATENDENTE_USERNAME` | compose (`seed-os`) → `seed_atendente_demo` do -os | `atendente.demo` | Usuario staff criado no -os para o BDD. |
| `ATENDENTE_PASSWORD` | compose (`seed-os`) → `seed_atendente_demo` do -os | vazia | Senha do atendente. **Vazia = o seed nao cria o usuario** (aviso, exit 0); defina antes do `up`. Sem padrao versionado de proposito. |
| `SAGA_E2E_ATENDENTE_USERNAME` | driver | `atendente.demo` | Usuario do login em `POST /api/v1/auth/token/`; use o mesmo valor de `ATENDENTE_USERNAME`. |
| `SAGA_E2E_ATENDENTE_PASSWORD` | driver | vazia | Senha do login (mesmo valor de `ATENDENTE_PASSWORD`). Com ela o driver faz login uma vez e manda `Bearer <access>` na abertura, na leitura da OS e na consulta da Saga. Vazia = cai em `SAGA_E2E_TOKEN`, se houver. |
| `AUTH_JWT_PUBLIC_KEY_B64` | billing (`config.py`) | vazia | Chave **publica** (PEM em base64) que o Billing usa para validar o JWT RS256 do cliente. Vazia = toda rota de cliente responde 503. Imprimir com `python stack/gerar_chaves_e2e.py`. |
| `SAGA_E2E_CLIENTE_CHAVE_PRIVADA` | driver | vazia | Caminho do PEM da chave **privada** de teste (`stack/.local/cliente-e2e.pem`, gerada pelo mesmo script). Com ela o driver assina o JWT do cliente (`iss oficina-auth`, `aud oficina-api`, `sub cliente:<id>`) com o `cliente_id` do Cliente que o -os criou na abertura. Vazia = cai em `SAGA_E2E_TOKEN`. |
| `SAGA_E2E_TOKEN` | driver | vazia | Bearer fixo, usado so onde o login do atendente / a chave do cliente nao estao configurados. |
| `SAGA_E2E_SIMULAR_PAGAMENTO_CMD` | driver | vazia | Comando que confirma o pagamento no Billing fake (que nao tem webhook). `{payment_id}` e `{outcome}` sao trocados; o driver espera o pagamento existir (`GET /api/v1/orcamentos/{id}/pagamento`) e roda o comando quando o passo `PagamentoConfirmado` (`approved`) ou `PagamentoFalhou` (`rejected`, so se o comando tiver `{outcome}`) acontece. Vazia = so espera, como antes. |
| `APP_ENV`, `PAYMENT_PROVIDER` | billing | `development`, `fake` | Fixados no compose: `simulate-payment` so roda com os dois. |

Passo a passo local:

```bash
python stack/gerar_chaves_e2e.py   # cria stack/.local/cliente-e2e.pem e stack/.local/e2e.env
docker compose --env-file stack/.local/e2e.env -f stack/docker-compose.yml up -d --build --wait
set -a; . stack/.local/e2e.env; set +a
export SAGA_E2E_DRIVER=stack-real
export SAGA_E2E_SIMULAR_PAGAMENTO_CMD='docker compose --env-file stack/.local/e2e.env -f stack/docker-compose.yml exec -T billing python -m billing_service.cli simulate-payment --payment-id {payment_id} --outcome {outcome}'
pytest -q e2e
```

O `e2e.env` traz `AUTH_JWT_PUBLIC_KEY_B64`, `ATENDENTE_PASSWORD` (e o mesmo valor em
`SAGA_E2E_ATENDENTE_PASSWORD`), `POSTGRES_BILLING_PASSWORD` e
`SAGA_E2E_CLIENTE_CHAVE_PRIVADA`, com senhas aleatorias geradas na maquina. O compose
exige `POSTGRES_BILLING_PASSWORD` (sem padrao versionado). Se outro processo da
maquina escutar em `[::1]:8000`, aponte o driver para `SAGA_E2E_OS_URL=http://127.0.0.1:8000`
(e `SAGA_E2E_BILLING_URL=http://127.0.0.1:8001`). No Docker Desktop, o build a partir
do git pode precisar de `COMPOSE_BAKE=false`.

O servico `seed-os` (one-shot) roda `stack/seed_os.py` pelo `manage.py shell` e
`seed_atendente_demo`: cria o servico 12 e a peca 40 (estoque 100) do catalogo do
Billing (`config/catalog.demo.json`, 15000 e 2500 centavos) e o atendente. E
idempotente; a cada execucao repoe o estoque.

## Outras especificas de servico

| Variavel | Servico | Para que serve |
|---|---|---|
| `DJANGO_SECRET_KEY` | os | Segredo do Django; o padrao local e inseguro de proposito. |
| `RUN_MIGRATIONS` | os, os-consumidor | `true` faz o entrypoint rodar `migrate` na subida (confirmado no `docker-entrypoint.sh`); so o `os` migra. |
| `POSTGRES_OS_DB`, `POSTGRES_OS_USER`, `POSTGRES_OS_PASSWORD` | os, postgres-os | Banco local do OS; o servico recebe como `DB_HOST` e `POSTGRES_DB/USER/PASSWORD`. |
| `POSTGRES_BILLING_DB`, `POSTGRES_BILLING_USER`, `POSTGRES_BILLING_PASSWORD` | billing, postgres-billing | Banco local do Billing; os servicos billing* recebem `DATABASE_URL` (dialeto `postgresql+psycopg`). `POSTGRES_BILLING_PASSWORD` e obrigatoria (gerada no `stack/.local/e2e.env`). |

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "stack" / "criar_tabela_execucao.py"
COMPOSE = RAIZ / "stack" / "docker-compose.yml"

sys.path.insert(0, str(RAIZ / "stack"))
from criar_tabela_execucao import INDICE_OUTBOX, planejar  # noqa: E402


def test_esquema_igual_ao_do_execution():
    plano = planejar("t")
    assert plano["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    (indice,) = plano["GlobalSecondaryIndexes"]
    # O repositorio do Execution consulta o indice por este nome
    assert indice["IndexName"] == INDICE_OUTBOX == "pending-outbox-index"
    assert [k["AttributeName"] for k in indice["KeySchema"]] == ["outbox_status", "outbox_created_at"]
    definidos = {a["AttributeName"] for a in plano["AttributeDefinitions"]}
    usados = {k["AttributeName"] for k in plano["KeySchema"] + indice["KeySchema"]}
    assert definidos == usados


def test_dry_run_sem_boto3_usa_o_nome_da_variavel():
    env = {**os.environ, "EXECUTION_DYNAMODB_TABLE": "tabela-do-teste"}
    r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run"],
                       capture_output=True, text=True, cwd=RAIZ, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["TableName"] == "tabela-do-teste"


def _servicos():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_compose_entrega_ao_execution_as_variaveis_que_ele_le():
    # Nomes lidos em execution_service/config.py (main do -execution)
    env = _servicos()["execucao"]["environment"]
    for nome in ("EXECUTION_COMMANDS_QUEUE_URL", "SAGA_RESPONSES_QUEUE_URL",
                 "EXECUTION_DYNAMODB_TABLE", "AWS_REGION", "AWS_ENDPOINT_URL"):
        assert nome in env, nome
    assert "oficina-execucao-comandos.fifo" in env["EXECUTION_COMMANDS_QUEUE_URL"]
    assert "oficina-os-saga-respostas.fifo" in env["SAGA_RESPONSES_QUEUE_URL"]


def test_compose_cria_a_tabela_com_o_mesmo_nome_que_o_execution_le():
    servicos = _servicos()
    assert "criar_tabela_execucao.py" in servicos["criar-filas"]["command"]
    assert (servicos["criar-filas"]["environment"]["EXECUTION_DYNAMODB_TABLE"]
            == servicos["execucao"]["environment"]["EXECUTION_DYNAMODB_TABLE"])


def test_compose_sobe_o_consumidor_de_respostas_do_os():
    servicos = _servicos()
    for nome in ("os", "os-consumidor"):
        assert "oficina-os-saga-respostas.fifo" in servicos[nome]["environment"]["SAGA_RESPONSE_QUEUE_URL"]
    assert "consumir_respostas_saga" in servicos["os-consumidor"]["command"]
    assert servicos["os-consumidor"]["environment"]["RUN_MIGRATIONS"] == "false"


def test_compose_constroi_o_execution_da_main():
    # feat/execution-service foi apagada no merge do -execution#3; ref que nao
    # existe derruba o build da stack inteira.
    build = _servicos()["execucao"]["build"]
    assert build.endswith("#${EXECUTION_GIT_REF:-main}"), build

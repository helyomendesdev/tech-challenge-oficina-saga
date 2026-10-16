import subprocess
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
ASYNCAPI = RAIZ / "contrato" / "asyncapi.yaml"
SCRIPT = RAIZ / "stack" / "criar_filas.py"

sys.path.insert(0, str(RAIZ / "stack"))
from criar_filas import planejar  # noqa: E402


def _nomes_do_contrato():
    canais = yaml.safe_load(ASYNCAPI.read_text(encoding="utf-8"))["channels"]
    filas = {c["address"] for c in canais.values()}
    dlqs = {a.replace(".fifo", "-dlq.fifo") for a in filas}
    return filas, dlqs


def test_toda_fila_e_fifo_com_dlq():
    filas, dlqs = _nomes_do_contrato()
    plano = planejar(ASYNCAPI)
    assert len(filas) == 3 and len(dlqs) == 3
    assert {i["nome"] for i in plano if i["tipo"] == "fila"} == filas
    assert {i["nome"] for i in plano if i["tipo"] == "dlq"} == dlqs
    assert len(plano) == 6
    for item in plano:
        assert item["atributos"]["FifoQueue"] == "true"
        assert item["atributos"]["ContentBasedDeduplication"] == "false"
        if item["tipo"] == "fila":
            assert item["dlq"]["nome"] == item["nome"].replace(".fifo", "-dlq.fifo")
            assert item["dlq"]["maxReceiveCount"] >= 1
        else:
            assert item["dlq"] is None


def test_dlq_vem_antes_da_fila_que_aponta_para_ela():
    plano = planejar(ASYNCAPI)
    posicao = {i["nome"]: n for n, i in enumerate(plano)}
    for item in plano:
        if item["dlq"]:
            assert posicao[item["dlq"]["nome"]] < posicao[item["nome"]]


def test_dry_run_sem_boto3_sai_zero():
    r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run"],
                       capture_output=True, text=True, cwd=RAIZ)
    assert r.returncode == 0, r.stderr
    filas, dlqs = _nomes_do_contrato()
    for nome in filas | dlqs:
        assert nome in r.stdout

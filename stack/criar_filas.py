"""Cria no SQS (LocalStack) as filas FIFO e DLQs descritas em contrato/asyncapi.yaml.

Uso:
    python stack/criar_filas.py --dry-run   # imprime o plano, sem boto3
    python stack/criar_filas.py             # cria (AWS_ENDPOINT_URL, AWS_REGION)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

ASYNCAPI_PADRAO = Path(__file__).resolve().parent.parent / "contrato" / "asyncapi.yaml"
MAX_RECEIVE_COUNT_PADRAO = 3
# Atributos comuns a toda fila (e DLQ): sempre FIFO, sem deduplicacao por conteudo
ATRIBUTOS_BASE = {"FifoQueue": "true", "ContentBasedDeduplication": "false"}


def planejar(asyncapi_path):
    """Deriva as filas do AsyncAPI. DLQs vem antes das filas que apontam para elas."""
    with open(asyncapi_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    dlqs, filas = [], []
    for canal in doc["channels"].values():
        sqs = canal["bindings"]["sqs"]
        fila = sqs["queue"]
        dlq = sqs["deadLetterQueue"]
        redrive = fila["redrivePolicy"]
        nome_dlq = redrive["deadLetterQueue"]["name"]
        dlqs.append({
            "nome": dlq["name"],
            "tipo": "dlq",
            "atributos": {
                **ATRIBUTOS_BASE,
                "MessageRetentionPeriod": str(dlq.get("messageRetentionPeriod", 1209600)),
            },
            "dlq": None,
        })
        filas.append({
            "nome": fila["name"],
            "tipo": "fila",
            "atributos": {
                **ATRIBUTOS_BASE,
                "DeduplicationScope": fila.get("deduplicationScope", "messageGroup"),
                "FifoThroughputLimit": fila.get("fifoThroughputLimit", "perMessageGroupId"),
                "VisibilityTimeout": str(fila.get("visibilityTimeout", 30)),
            },
            "dlq": {
                "nome": nome_dlq,
                "maxReceiveCount": int(redrive.get("maxReceiveCount", MAX_RECEIVE_COUNT_PADRAO)),
            },
        })
    return dlqs + filas


def _imprimir(plano):
    for item in plano:
        print(f"{item['tipo']:5} {item['nome']}")
        print(f"      atributos: {json.dumps(item['atributos'], sort_keys=True)}")
        if item["dlq"]:
            print(f"      dlq: {item['dlq']['nome']} (maxReceiveCount {item['dlq']['maxReceiveCount']})")


def _criar(plano):
    import boto3  # so no modo de criacao

    sqs = boto3.client("sqs", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
                       region_name=os.environ.get("AWS_REGION", "us-east-1"))
    arns = {}
    for item in plano:
        atributos = dict(item["atributos"])
        if item["dlq"]:
            atributos["RedrivePolicy"] = json.dumps({
                "deadLetterTargetArn": arns[item["dlq"]["nome"]],
                "maxReceiveCount": item["dlq"]["maxReceiveCount"],
            })
        url = sqs.create_queue(QueueName=item["nome"], Attributes=atributos)["QueueUrl"]
        arns[item["nome"]] = sqs.get_queue_attributes(
            QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        print(f"criada {item['nome']} -> {url}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="imprime o plano sem criar nada")
    ap.add_argument("--asyncapi", default=str(ASYNCAPI_PADRAO))
    args = ap.parse_args(argv)
    plano = planejar(args.asyncapi)
    if args.dry_run:
        _imprimir(plano)
        return 0
    # LocalStack pode ainda estar subindo: tenta por ate ~60 s
    for tentativa in range(12):
        try:
            _criar(plano)
            return 0
        except Exception as e:  # noqa: BLE001 - reporta e tenta de novo; falha final sai !=0
            print(f"tentativa {tentativa + 1} falhou: {e}", file=sys.stderr)
            time.sleep(5)
    return 1


if __name__ == "__main__":
    sys.exit(main())

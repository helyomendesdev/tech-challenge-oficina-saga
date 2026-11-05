"""Cria no DynamoDB (LocalStack) a tabela do Execution Service.

O esquema copia o do repo do Execution: infra/main.tf (aws_dynamodb_table
execution_store) e o indice que execution_service/infrastructure/dynamodb_repository.py
consulta por nome (pending-outbox-index). Se um dos dois mudar la, mude aqui.

Uso:
    python stack/criar_tabela_execucao.py --dry-run   # imprime o plano, sem boto3
    python stack/criar_tabela_execucao.py             # cria (AWS_ENDPOINT_URL, AWS_REGION)

Nome da tabela: EXECUTION_DYNAMODB_TABLE (padrao oficina-execucao), a mesma
variavel que o Execution le.
"""
import argparse
import json
import os
import sys

TABELA_PADRAO = "oficina-execucao"
INDICE_OUTBOX = "pending-outbox-index"


def planejar(nome_tabela):
    """Parametros de create_table, iguais ao infra/main.tf do Execution."""
    return {
        "TableName": nome_tabela,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "outbox_status", "AttributeType": "S"},
            {"AttributeName": "outbox_created_at", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": INDICE_OUTBOX,
                "KeySchema": [
                    {"AttributeName": "outbox_status", "KeyType": "HASH"},
                    {"AttributeName": "outbox_created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    }


def _criar(plano):
    import boto3  # so no modo de criacao

    dynamodb = boto3.client("dynamodb", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
                            region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        dynamodb.create_table(**plano)
        print(f"tabela {plano['TableName']}: criada")
    except dynamodb.exceptions.ResourceInUseException:
        print(f"tabela {plano['TableName']}: ja existia")
    dynamodb.get_waiter("table_exists").wait(TableName=plano["TableName"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="so imprime o plano")
    args = parser.parse_args(argv)
    plano = planejar(os.environ.get("EXECUTION_DYNAMODB_TABLE") or TABELA_PADRAO)
    if args.dry_run:
        print(json.dumps(plano, indent=2, sort_keys=True))
        return 0
    _criar(plano)
    return 0


if __name__ == "__main__":
    sys.exit(main())

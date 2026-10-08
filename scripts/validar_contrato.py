"""Valida o contrato da Saga: exemplos contra schemas e coerencia com o AsyncAPI.

Uso: python scripts/validar_contrato.py   (exit 0 = contrato coerente)

Checagens:
1. Todo schema e um JSON Schema draft-07 valido.
2. Todo exemplo em contrato/exemplos/*.json valida contra o schema do seu `type`.
3. Todo exemplo em contrato/exemplos/invalidos/*.json e REJEITADO (prova de que
   os schemas mordem; cada arquivo diz em "_espera" por que deveria falhar).
4. O enum `type` do envelope, os schemas de mensagem, os exemplos e as mensagens
   do asyncapi.yaml cobrem exatamente o mesmo conjunto de nomes.
"""

import json
import pathlib
import sys

import yaml
from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CONTRATO = RAIZ / "contrato"
SCHEMAS = CONTRATO / "schemas"
EXEMPLOS = CONTRATO / "exemplos"


def carregar_json(caminho):
    return json.loads(caminho.read_text(encoding="utf-8"))


def montar_registro():
    """Registra todos os schemas pelo $id, para os $ref relativos resolverem."""
    schemas = {}
    for caminho in sorted(SCHEMAS.rglob("*.schema.json")):
        schema = carregar_json(caminho)
        Draft7Validator.check_schema(schema)
        schemas[caminho] = schema
    registro = Registry().with_resources(
        (s["$id"], Resource.from_contents(s, default_specification=DRAFT7))
        for s in schemas.values()
    )
    return schemas, registro


def main():
    falhas = []
    schemas, registro = montar_registro()

    por_tipo = {}
    for caminho, schema in schemas.items():
        if caminho.parent.name in ("comandos", "respostas"):
            por_tipo[schema["title"]] = schema

    def validador(tipo):
        return Draft7Validator(por_tipo[tipo], registry=registro, format_checker=FormatChecker())

    # 2. exemplos validos
    tipos_com_exemplo = set()
    for caminho in sorted(EXEMPLOS.glob("*.json")):
        exemplo = carregar_json(caminho)
        tipo = exemplo.get("type")
        if tipo not in por_tipo:
            falhas.append(f"{caminho.name}: type {tipo!r} sem schema")
            continue
        erros = sorted(validador(tipo).iter_errors(exemplo), key=lambda e: list(e.path))
        if erros:
            for erro in erros:
                local = "/".join(str(p) for p in erro.path) or "(raiz)"
                falhas.append(f"{caminho.name}: {local}: {erro.message}")
        else:
            print(f"ok      {caminho.relative_to(RAIZ).as_posix()}")
        tipos_com_exemplo.add(tipo)

    # 3. exemplos invalidos tem de falhar
    for caminho in sorted((EXEMPLOS / "invalidos").glob("*.json")):
        exemplo = carregar_json(caminho)
        espera = exemplo.pop("_espera", "?")
        tipo = exemplo.pop("_schema", exemplo.get("type"))
        if tipo not in por_tipo:
            falhas.append(f"invalidos/{caminho.name}: _schema {tipo!r} desconhecido")
            continue
        if validador(tipo).is_valid(exemplo):
            falhas.append(f"invalidos/{caminho.name}: foi ACEITO, mas deveria falhar ({espera})")
        else:
            print(f"rejeita {caminho.relative_to(RAIZ).as_posix()} ({espera})")

    # 4. coerencia de nomes
    envelope = carregar_json(SCHEMAS / "envelope.schema.json")
    nomes_envelope = set(envelope["properties"]["type"]["enum"])
    asyncapi = yaml.safe_load((CONTRATO / "asyncapi.yaml").read_text(encoding="utf-8"))
    nomes_asyncapi = set(asyncapi["components"]["messages"])
    conjuntos = {
        "enum do envelope": nomes_envelope,
        "schemas de mensagem": set(por_tipo),
        "exemplos validos": tipos_com_exemplo,
        "mensagens do asyncapi": nomes_asyncapi,
    }
    for nome, conjunto in conjuntos.items():
        if conjunto != nomes_envelope:
            falhas.append(
                f"{nome} diverge do enum do envelope: "
                f"falta {sorted(nomes_envelope - conjunto)}, sobra {sorted(conjunto - nomes_envelope)}"
            )
    for nome, mensagem in asyncapi["components"]["messages"].items():
        ref = mensagem["payload"]["schema"]["$ref"]
        alvo = (CONTRATO / ref).resolve()
        if not alvo.is_file() or carregar_json(alvo).get("title") != nome:
            falhas.append(f"asyncapi: mensagem {nome} aponta para {ref}, que nao e o schema dela")

    print()
    if falhas:
        print(f"FALHOU: {len(falhas)} problema(s)")
        for falha in falhas:
            print(f"  - {falha}")
        return 1
    print(f"contrato ok: {len(por_tipo)} mensagens, {len(tipos_com_exemplo)} exemplos validos")
    return 0


if __name__ == "__main__":
    sys.exit(main())

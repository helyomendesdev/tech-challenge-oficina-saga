"""Carga dos JSON Schemas do contrato da Saga e resolucao dos `$ref` entre eles."""

import json
import pathlib

from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

RAIZ_REPO = pathlib.Path(__file__).resolve().parent.parent
RAIZ_CONTRATO = RAIZ_REPO / "contrato"
if not (RAIZ_CONTRATO / "schemas").is_dir():
    # Instalado por pip: o contrato vem empacotado como oficina_saga._contrato
    # (ver package-dir no pyproject.toml).
    import importlib.util

    _spec = importlib.util.find_spec("oficina_saga._contrato")
    if _spec is not None and _spec.submodule_search_locations:
        RAIZ_CONTRATO = pathlib.Path(list(_spec.submodule_search_locations)[0])


def carregar_json(caminho):
    return json.loads(caminho.read_text(encoding="utf-8"))


def montar_registro(raiz=None):
    """Registra todos os schemas pelo $id, para os $ref relativos resolverem.

    `raiz` e a pasta do contrato (padrao: `contrato/` na raiz do repositorio).
    """
    schemas_dir = pathlib.Path(raiz or RAIZ_CONTRATO) / "schemas"
    schemas = {}
    for caminho in sorted(schemas_dir.rglob("*.schema.json")):
        schema = carregar_json(caminho)
        Draft7Validator.check_schema(schema)
        schemas[caminho] = schema
    registro = Registry().with_resources(
        (s["$id"], Resource.from_contents(s, default_specification=DRAFT7))
        for s in schemas.values()
    )
    return schemas, registro


def schemas_por_tipo(schemas):
    """Schemas de comandos/ e respostas/ indexados pelo `title` (= `type` da mensagem)."""
    return {
        schema["title"]: schema
        for caminho, schema in schemas.items()
        if caminho.parent.name in ("comandos", "respostas")
    }


def montar_validador(por_tipo, registro):
    """Devolve `validador(tipo)`: Draft7Validator do schema da mensagem, com $ref e formatos."""

    def validador(tipo):
        return Draft7Validator(por_tipo[tipo], registry=registro, format_checker=FormatChecker())

    return validador

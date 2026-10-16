"""O painel da Saga so pode consultar atributos que o adaptador emite."""

import json
import re
from pathlib import Path

from oficina_saga.newrelic_adapter import ATRIBUTOS_EMITIDOS

RAIZ = Path(__file__).resolve().parent.parent
PAINEL = RAIZ / "observabilidade" / "painel-saga.json"
CONSULTAS_MD = RAIZ / "observabilidade" / "consultas-nrql.md"
ASYNCAPI = RAIZ / "contrato" / "asyncapi.yaml"

# Atributos camelCase padrao do New Relic usados nas consultas.
ATRIBUTOS_PADRAO_NR = frozenset({"appName", "duration", "name", "timestamp"})


def _painel():
    return json.loads(PAINEL.read_text(encoding="utf-8"))


def _queries():
    return [
        q["query"]
        for pagina in _painel()["pages"]
        for w in pagina["widgets"]
        for q in w["rawConfiguration"]["nrqlQueries"]
    ]


def test_painel_tem_o_formato_de_importacao():
    painel = _painel()
    assert {"name", "permissions", "pages"} <= painel.keys()
    assert painel["pages"]
    for pagina in painel["pages"]:
        assert pagina["name"]
        assert len(pagina["widgets"]) >= 5
        for w in pagina["widgets"]:
            assert w["title"]
            assert {"column", "row", "width", "height"} <= w["layout"].keys()
            assert w["visualization"]["id"].startswith("viz.")
            consultas = w["rawConfiguration"]["nrqlQueries"]
            assert consultas
            for q in consultas:
                assert q["accountIds"] == [0]
                assert q["query"].strip()


def test_painel_so_usa_atributos_que_o_adaptador_emite():
    atributos_usados = set()
    for query in _queries():
        sem_literais = re.sub(r"'[^']*'", "", query)
        atributos_usados |= set(re.findall(r"\b[a-z]+[A-Z]\w*\b", sem_literais))
    atributos_usados -= ATRIBUTOS_PADRAO_NR
    assert atributos_usados, "nenhum atributo customizado encontrado"
    assert atributos_usados <= ATRIBUTOS_EMITIDOS


def test_dlqs_citadas_existem_no_asyncapi():
    asyncapi = ASYNCAPI.read_text(encoding="utf-8")
    existentes = set(re.findall(r"oficina-[\w-]+-dlq\.fifo", asyncapi))
    dlq = [q for q in _queries() if "aws.sqs." in q]
    assert dlq
    citadas = set(re.findall(r"oficina-[\w-]+-dlq\.fifo", " ".join(dlq)))
    assert citadas
    assert citadas == existentes


def test_md_traz_as_mesmas_queries_do_json():
    md = CONSULTAS_MD.read_text(encoding="utf-8")
    blocos = set(re.findall(r"```nrql\n(.*?)\n```", md, re.S))
    assert set(_queries()) == blocos

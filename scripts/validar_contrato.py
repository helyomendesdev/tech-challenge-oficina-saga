"""Valida o contrato da Saga: exemplos contra schemas e coerencia com o AsyncAPI.

Uso: python scripts/validar_contrato.py   (exit 0 = contrato coerente)

Checagens:
1. Todo schema e um JSON Schema draft-07 valido.
2. Todo exemplo em contrato/exemplos/*.json valida contra o schema do seu `type`.
3. Todo exemplo em contrato/exemplos/invalidos/*.json e REJEITADO (prova de que
   os schemas mordem; cada arquivo diz em "_espera" por que deveria falhar).
4. O enum `type` do envelope, os schemas de mensagem, os exemplos e as mensagens
   do asyncapi.yaml cobrem exatamente o mesmo conjunto de nomes.
5. A maquina de estados (contrato/saga-estados.yaml) e consistente: estados
   alcancaveis, sem transicao ambigua, toda resposta consumida e todo comando
   enviado, gatilho externo usado por alguma transicao, motivo obrigatorio
   onde o schema do comando exige, e o diagrama de docs/fluxo-saga.md tem as
   mesmas transicoes da tabela, com o mesmo inicio (`[*] -->`) e o mesmo fim
   (`--> [*]`).
6. Os exemplos percorrem a maquina pelas trilhas: cada mensagem e aceita no
   estado corrente, causationId aponta para a mensagem anterior, e
   sagaId/osId/requestId nao mudam ao longo da trilha. Toda transicao da tabela
   e percorrida por alguma trilha; a trilha que termina com comando pendente
   declara `pendente` com o comando e o motivo esperados; dois exemplos com o
   mesmo `type` sao erro.
"""

import json
import pathlib
import re
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

    exemplos = {}
    arquivo_do_tipo = {}
    for c in sorted(EXEMPLOS.glob("*.json")):
        ex = carregar_json(c)
        tipo = ex["type"]
        if tipo in exemplos:
            falhas.append(f"exemplos: {tipo} aparece em {arquivo_do_tipo[tipo]} e {c.name}")
            continue
        exemplos[tipo] = ex
        arquivo_do_tipo[tipo] = c.name
    comandos = {s["title"] for c, s in schemas.items() if c.parent.name == "comandos"}
    respostas = {s["title"] for c, s in schemas.items() if c.parent.name == "respostas"}
    motivos = set(carregar_json(SCHEMAS / "comum.schema.json")["definitions"]["motivoCancelamento"]["enum"])
    exige_motivo = {
        s["title"] for c, s in schemas.items()
        if c.parent.name == "comandos" and "motivo" in s["properties"]["payload"].get("required", [])
    }
    falhas_maquina, contagem = checar_maquina(comandos, respostas, motivos, exemplos, exige_motivo)
    falhas += falhas_maquina

    print()
    if falhas:
        print(f"FALHOU: {len(falhas)} problema(s)")
        for falha in falhas:
            print(f"  - {falha}")
        return 1
    estados, transicoes, trilhas = contagem
    print(f"maquina ok: {estados} estados, {transicoes} transicoes, {trilhas} trilhas, diagrama coerente")
    print(f"contrato ok: {len(por_tipo)} mensagens, {len(tipos_com_exemplo)} exemplos validos")
    return 0


def transicoes_do_diagrama():
    """Pares (de, para) do bloco stateDiagram-v2 de docs/fluxo-saga.md, incluindo `[*]`."""
    texto = (RAIZ / "docs" / "fluxo-saga.md").read_text(encoding="utf-8")
    bloco = re.search(r"```mermaid\s*\nstateDiagram-v2\n(.*?)```", texto, re.S)
    if not bloco:
        return None
    pares = set()
    for linha in bloco.group(1).splitlines():
        m = re.match(r"\s*(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)", linha)
        if m:
            pares.add((m.group(1), m.group(2)))
    return pares


def checar_maquina(comandos, respostas, motivos, exemplos, exige_motivo):
    falhas = []
    maquina = yaml.safe_load((CONTRATO / "saga-estados.yaml").read_text(encoding="utf-8"))
    estados = set(maquina["estados"])
    finais = set(maquina["finais"])
    gatilhos = set(maquina["gatilhos_externos"])
    if maquina["inicial"] not in estados or not finais <= estados:
        falhas.append("maquina: inicial/finais fora da lista de estados")

    tabela = {}
    for t in maquina["transicoes"]:
        chave = (t["de"], t["recebe"])
        rotulo = f"maquina: {t['de']} + {t['recebe']}"
        if chave in tabela:
            falhas.append(f"{rotulo}: transicao duplicada (ambigua)")
        tabela[chave] = t
        if t["de"] not in estados or t["para"] not in estados:
            falhas.append(f"{rotulo}: estado desconhecido")
        if t["recebe"] not in respostas | gatilhos:
            falhas.append(f"{rotulo}: recebe {t['recebe']!r}, que nao e resposta nem gatilho externo")
        if "envia" in t and t["envia"] not in comandos:
            falhas.append(f"{rotulo}: envia {t['envia']!r}, que nao e comando")
        if "motivo" in t and t["motivo"] not in motivos | {"herdado"}:
            falhas.append(f"{rotulo}: motivo {t['motivo']!r} fora do enum motivoCancelamento")
        if t.get("envia") in exige_motivo and "motivo" not in t:
            falhas.append(f"{rotulo}: envia {t['envia']} sem motivo (o schema exige)")
        if t["de"] in finais:
            falhas.append(f"{rotulo}: estado final nao pode ter saida")

    for s in sorted(estados):
        entrada = sorted({t["motivo"] for t in maquina["transicoes"]
                          if t["para"] == s and t["de"] != s and t.get("envia") in exige_motivo
                          and t.get("motivo", "herdado") != "herdado"})
        if len(entrada) < 2:
            continue
        for t in maquina["transicoes"]:
            if t["de"] == s and t.get("envia") in exige_motivo and t.get("motivo") != "herdado":
                falhas.append(f"maquina: {s} + {t['recebe']}: {s} e entrado com motivos {entrada}; "
                              f"o {t['envia']} enviado daqui tem de usar motivo herdado")

    recebidos = {t["recebe"] for t in maquina["transicoes"]}
    enviados = {t["envia"] for t in maquina["transicoes"] if "envia" in t}
    for r in sorted(respostas - recebidos):
        falhas.append(f"maquina: resposta {r} nunca e consumida")
    for c in sorted(comandos - enviados):
        falhas.append(f"maquina: comando {c} nunca e enviado")
    for g in sorted(gatilhos - recebidos):
        falhas.append(f"maquina: gatilho externo {g} sem transicao")

    alcancados, fronteira = {maquina["inicial"]}, [maquina["inicial"]]
    while fronteira:
        atual = fronteira.pop()
        for (de, _), t in tabela.items():
            if de == atual and t["para"] not in alcancados:
                alcancados.add(t["para"])
                fronteira.append(t["para"])
    for e in sorted(estados - alcancados):
        falhas.append(f"maquina: estado {e} inalcancavel a partir de {maquina['inicial']}")
    for e in sorted(estados - finais):
        if not any(de == e for de, _ in tabela):
            falhas.append(f"maquina: estado {e} nao e final e nao tem saida")

    pares_tabela = {(t["de"], t["para"]) for t in maquina["transicoes"]}
    pares_diagrama = transicoes_do_diagrama()
    if pares_diagrama is None:
        falhas.append("fluxo-saga.md: bloco stateDiagram-v2 nao encontrado")
    else:
        inicios = {para for de, para in pares_diagrama if de == "[*]"}
        fins = {de for de, para in pares_diagrama if para == "[*]"}
        for x in sorted(inicios - {maquina["inicial"]}):
            falhas.append(f"fluxo-saga.md: [*] --> {x} no diagrama, mas o inicial e {maquina['inicial']}")
        if maquina["inicial"] not in inicios:
            falhas.append(f"fluxo-saga.md: falta [*] --> {maquina['inicial']} no diagrama")
        for x in sorted(fins - finais):
            falhas.append(f"fluxo-saga.md: {x} --> [*] no diagrama, mas {x} nao e final")
        for x in sorted(finais - fins):
            falhas.append(f"fluxo-saga.md: falta {x} --> [*] no diagrama")
        internos = {(de, para) for de, para in pares_diagrama if de != "[*]" and para != "[*]"}
        for de, para in sorted(pares_tabela - internos):
            falhas.append(f"fluxo-saga.md: falta {de} --> {para} no diagrama")
        for de, para in sorted(internos - pares_tabela):
            falhas.append(f"fluxo-saga.md: diagrama tem {de} --> {para}, que a maquina nao tem")

    na_trilha = set()
    percorridas = set()
    for nome, trilha in maquina["trilhas"].items():
        f, p = percorrer_trilha(nome, trilha, tabela, comandos, exemplos)
        falhas += f
        percorridas |= p
        na_trilha.update(trilha["mensagens"])
    for de, recebe in sorted(set(tabela) - percorridas):
        falhas.append(f"maquina: transicao {de} + {recebe} nao e percorrida por nenhuma trilha")
    for tipo in sorted(set(exemplos) - na_trilha):
        falhas.append(f"trilhas: exemplo {tipo} nao aparece em nenhuma trilha")
    return falhas, (len(estados), len(tabela), len(maquina["trilhas"]))


def pendente_da_transicao(nome, t, ultimo_motivo):
    """(comando a enviar, motivo esperado) da transicao; `herdado` usa o ultimo motivo enviado.

    Devolve (pendente, erro): erro e None quando o motivo resolveu.
    """
    motivo = t.get("motivo")
    if motivo == "herdado":
        if ultimo_motivo is None:
            return None, f"trilha {nome}: motivo herdado sem motivo anterior"
        motivo = ultimo_motivo
    return (t.get("envia"), motivo), None


def conferir_pendente(nome, trilha, pendente):
    """O comando que a trilha termina devendo tem de estar declarado em `pendente`."""
    declarado = trilha.get("pendente")
    if pendente and pendente[0]:
        if declarado is None:
            return [f"trilha {nome}: termina com {pendente[0]} pendente sem declarar"]
        dec_cmd, dec_motivo = declarado.get("comando"), declarado.get("motivo")
        if (dec_cmd, dec_motivo) != pendente:
            return [f"trilha {nome}: pendente declarado {dec_cmd} com motivo {dec_motivo!r}, "
                    f"esperado {pendente[0]} com motivo {pendente[1]!r}"]
        return []
    if declarado is not None:
        return [f"trilha {nome}: declara pendente {declarado.get('comando')}, mas termina sem pendente"]
    return []


def percorrer_trilha(nome, trilha, tabela, comandos, exemplos):
    """Devolve (falhas, chaves (de, recebe) de tabela percorridas por esta trilha)."""
    falhas = []
    percorridas = set()
    pendente_ok = "pendente" not in trilha or (
        isinstance(trilha["pendente"], dict) and "comando" in trilha["pendente"])
    if not pendente_ok:
        falhas.append(f"trilha {nome}: pendente deve ser {{comando, motivo}}")
    estado = trilha["comeca_em"]
    pendente = None  # (comando que a transicao mandou enviar, motivo esperado ja resolvido)
    ultimo_motivo = None  # motivo do ultimo comando enviado nesta trilha
    if "gatilho" in trilha:
        t = tabela.get((estado, trilha["gatilho"]))
        if not t:
            return falhas + [f"trilha {nome}: {estado} nao aceita o gatilho {trilha['gatilho']}"], percorridas
        percorridas.add((estado, trilha["gatilho"]))
        estado = t["para"]
        pendente, erro = pendente_da_transicao(nome, t, ultimo_motivo)
        if erro:
            return falhas + [erro], percorridas
    anterior = None
    for tipo in trilha["mensagens"]:
        ex = exemplos.get(tipo)
        if ex is None:
            falhas.append(f"trilha {nome}: sem exemplo para {tipo}")
            break
        if tipo in comandos:
            if not pendente or pendente[0] != tipo:
                falhas.append(f"trilha {nome}: {tipo} nao e o comando que {estado} manda enviar "
                              f"(esperado {pendente[0] if pendente else 'nenhum'})")
                break
            motivo_enviado = ex["payload"].get("motivo")
            if pendente[1] and motivo_enviado != pendente[1]:
                falhas.append(f"trilha {nome}: {tipo} com motivo {motivo_enviado!r}, "
                              f"a maquina manda {pendente[1]!r}")
            ultimo_motivo = ex["payload"].get("motivo", ultimo_motivo)
            pendente = None
        else:
            if pendente and pendente[0]:
                falhas.append(f"trilha {nome}: {tipo} chegou antes do comando {pendente[0]} ser enviado")
                break
            t = tabela.get((estado, tipo))
            if not t:
                falhas.append(f"trilha {nome}: {estado} nao aceita {tipo}")
                break
            percorridas.add((estado, tipo))
            estado = t["para"]
            pendente, erro = pendente_da_transicao(nome, t, ultimo_motivo)
            if erro:
                falhas.append(erro)
                break
        if anterior is not None:
            if ex["causationId"] != anterior["messageId"]:
                falhas.append(f"trilha {nome}: causationId de {tipo} nao aponta para {anterior['type']}")
            for campo in ("sagaId", "osId", "requestId"):
                if ex[campo] != anterior[campo]:
                    falhas.append(f"trilha {nome}: {campo} muda entre {anterior['type']} e {tipo}")
        elif tipo in comandos and "gatilho" in trilha and ex["causationId"] is not None:
            falhas.append(f"trilha {nome}: {tipo} vem de gatilho externo e deveria ter causationId nulo")
        anterior = ex
    else:
        if estado != trilha["termina_em"]:
            falhas.append(f"trilha {nome}: termina em {estado}, esperado {trilha['termina_em']}")
        if pendente_ok:
            falhas += conferir_pendente(nome, trilha, pendente)
    return falhas, percorridas


if __name__ == "__main__":
    sys.exit(main())

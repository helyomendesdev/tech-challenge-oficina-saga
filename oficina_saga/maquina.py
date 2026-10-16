"""Carga da maquina de estados da Saga (contrato/saga-estados.yaml)."""

import pathlib

import yaml

from oficina_saga.contrato import RAIZ_CONTRATO


def carregar(raiz=None):
    """Le o yaml da maquina; `raiz` e a pasta do contrato (padrao: `contrato/` do repositorio)."""
    caminho = pathlib.Path(raiz or RAIZ_CONTRATO) / "saga-estados.yaml"
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


def tabela(maquina):
    """{(estado `de`, `recebe`): transicao} da maquina."""
    return {(t["de"], t["recebe"]): t for t in maquina["transicoes"]}


def alcancaveis(maquina, estado):
    """Estados a que a maquina pode chegar a partir de `estado` (o proprio `estado` incluido)."""
    adjacentes = {}
    for t in maquina["transicoes"]:
        adjacentes.setdefault(t["de"], set()).add(t["para"])
    vistos = {estado}
    pendentes = [estado]
    while pendentes:
        for proximo in adjacentes.get(pendentes.pop(), ()):
            if proximo not in vistos:
                vistos.add(proximo)
                pendentes.append(proximo)
    return vistos

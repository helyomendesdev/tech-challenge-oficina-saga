"""Rastreio W3C Trace Context e propagacao pelos MessageAttributes do SQS."""

import re
import secrets
from typing import NamedTuple

_TRACEPARENT = re.compile(
    r"(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})"
    r"-(?P<parent_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})"
)


class Traceparent(NamedTuple):
    """Campos de um traceparent valido."""

    version: str
    trace_id: str
    parent_id: str
    flags: str


def _novo_id(n_bytes: int) -> str:
    """Hex minusculo aleatorio de n_bytes, nunca todo zero."""
    while True:
        valor = secrets.token_hex(n_bytes)
        if int(valor, 16) != 0:
            return valor


def parse_traceparent(valor) -> Traceparent | None:
    """Parseia um traceparent; devolve None se for invalido."""
    if not isinstance(valor, str):
        return None
    m = _TRACEPARENT.fullmatch(valor)
    if m is None:
        return None
    campos = Traceparent(**m.groupdict())
    if campos.version == "ff":
        return None
    if int(campos.trace_id, 16) == 0 or int(campos.parent_id, 16) == 0:
        return None
    return campos


def traceparent_valido(valor) -> bool:
    """Diz se o valor e um traceparent W3C valido."""
    return parse_traceparent(valor) is not None


def novo_traceparent() -> str:
    """Cria um traceparent novo (trace novo, amostrado)."""
    return f"00-{_novo_id(16)}-{_novo_id(8)}-01"


def novo_span_filho(traceparent: str) -> str:
    """Span filho: mesmo trace-id e flags, parent-id novo."""
    recebido = parse_traceparent(traceparent)
    if recebido is None:
        raise ValueError(f"traceparent invalido: {traceparent!r}")
    span_id = _novo_id(8)
    return f"{recebido.version}-{recebido.trace_id}-{span_id}-{recebido.flags}"


def injetar(message_attributes, traceparent, tracestate=None) -> dict:
    """Devolve copia dos atributos SQS com traceparent (e tracestate, se houver)."""
    if not traceparent_valido(traceparent):
        raise ValueError(f"traceparent invalido: {traceparent!r}")
    saida = dict(message_attributes or {})
    saida["traceparent"] = {"DataType": "String", "StringValue": traceparent}
    if tracestate:
        saida["tracestate"] = {"DataType": "String", "StringValue": tracestate}
    return saida


def _valor(message_attributes, nome):
    attr = (message_attributes or {}).get(nome)
    if isinstance(attr, dict):
        return attr.get("StringValue")
    return None


def extrair_recebido(message_attributes):
    """Le (traceparent, tracestate) como chegaram; sem traceparent valido, (None, None)."""
    traceparent = _valor(message_attributes, "traceparent")
    if not traceparent_valido(traceparent):
        return None, None
    return traceparent, _valor(message_attributes, "tracestate") or None


def extrair(message_attributes):
    """Le (traceparent, tracestate); sem traceparent valido, abre trace novo."""
    traceparent, tracestate = extrair_recebido(message_attributes)
    if traceparent is None:
        return novo_traceparent(), None
    return traceparent, tracestate

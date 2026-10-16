import pytest

from oficina_saga.rastreio import (
    extrair,
    injetar,
    novo_span_filho,
    novo_traceparent,
    parse_traceparent,
    traceparent_valido,
)

W3C = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_parse_exemplo_w3c():
    p = parse_traceparent(W3C)
    assert p.version == "00"
    assert p.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert p.parent_id == "00f067aa0ba902b7"
    assert p.flags == "01"


@pytest.mark.parametrize(
    "ruim",
    [
        None,
        "",
        123,
        "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00F067AA0BA902B7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e47-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba9-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-1",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01\n",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",
    ],
)
def test_parse_recusa_invalidos(ruim):
    assert parse_traceparent(ruim) is None
    assert not traceparent_valido(ruim)


def test_novo_traceparent_e_valido_e_diferente():
    a, b = novo_traceparent(), novo_traceparent()
    assert traceparent_valido(a)
    assert a != b


def test_novo_span_filho_gera_span_id_novo():
    filho = parse_traceparent(novo_span_filho(W3C))
    pai = parse_traceparent(W3C)
    assert filho.trace_id == pai.trace_id
    assert filho.parent_id != pai.parent_id


def test_novo_span_filho_mantem_flags_e_e_valido():
    filho = novo_span_filho("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00")
    assert traceparent_valido(filho)
    assert parse_traceparent(filho).flags == "00"


def test_novo_span_filho_recusa_invalido():
    with pytest.raises(ValueError):
        novo_span_filho("lixo")


def test_injetar_formato_sqs():
    assert injetar({}, W3C) == {
        "traceparent": {"DataType": "String", "StringValue": W3C}
    }


def test_injetar_tracestate_so_se_preenchido():
    assert "tracestate" not in injetar({}, W3C, "")
    assert "tracestate" not in injetar({}, W3C, None)
    com = injetar({}, W3C, "vendor=a")
    assert com["tracestate"] == {"DataType": "String", "StringValue": "vendor=a"}


def test_injetar_nao_muta_entrada_e_preserva_outros():
    entrada = {"outro": {"DataType": "String", "StringValue": "x"}}
    saida = injetar(entrada, W3C)
    assert entrada == {"outro": {"DataType": "String", "StringValue": "x"}}
    assert saida["outro"] == entrada["outro"]
    assert saida is not entrada


def test_injetar_recusa_traceparent_invalido():
    with pytest.raises(ValueError):
        injetar({}, "lixo")


def test_extrair_ida_e_volta():
    attrs = injetar({}, W3C, "vendor=a")
    assert extrair(attrs) == (W3C, "vendor=a")


def test_extrair_sem_tracestate():
    assert extrair(injetar({}, W3C)) == (W3C, None)


@pytest.mark.parametrize(
    "attrs",
    [
        None,
        {},
        {"traceparent": {"DataType": "String", "StringValue": "lixo"}},
        {"traceparent": {"DataType": "String"}},
        {"traceparent": "texto"},
    ],
)
def test_extrair_sem_ou_invalido_abre_trace_novo(attrs):
    tp, ts = extrair(attrs)
    assert traceparent_valido(tp)
    assert tp != W3C
    assert ts is None

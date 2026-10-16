"""Adaptador fino do agente Python do New Relic (9.13) para o rastreio da Saga.

O import de `newrelic.agent` e preguicoso: sem o agente instalado, tudo cai no
`rastreio` (W3C puro). Detalhes da API em `tracing/newrelic-agente.md`.

A conferir na primeira integracao real: o comportamento com transacao
inexistente. Segundo o fonte v9.13.0, as funcoes de modulo nao fazem nada sem
transacao: `insert_distributed_trace_headers` deixa a lista vazia
(transaction.py:2182) e `add_custom_attributes` devolve False
(transaction.py:1924). Aqui, lista sem `traceparent` cai no `rastreio`.
"""

from oficina_saga import rastreio

CABECALHOS_W3C = frozenset({"traceparent", "tracestate"})

# Atributos customizados gravados por `registrar_mensagem` (nome exato).
ATRIBUTOS_EMITIDOS = frozenset(
    {"sagaId", "osId", "messageId", "messageType", "causationId", "requestId"}
)


def _agente():
    """Devolve `newrelic.agent` ou None se nao estiver instalado."""
    try:
        import newrelic.agent as agente
    except ImportError:
        return None
    return agente


def atributos_para_envio(message_attributes=None, *, traceparent_atual=None):
    """MessageAttributes SQS com o contexto W3C para a mensagem a enviar.

    Com agente e transacao ativa, usa o que o agente gera; o header proprietario
    `newrelic` nao vai para a fila. Caso contrario, `rastreio` (filho do
    `traceparent_atual` se dado, trace novo se nao).
    """
    agente = _agente()
    if agente is not None:
        cabecalhos = []
        agente.insert_distributed_trace_headers(cabecalhos)
        gerados = {
            nome: {"DataType": "String", "StringValue": valor}
            for nome, valor in cabecalhos
            if nome in CABECALHOS_W3C
        }
        traceparent = gerados.get("traceparent", {}).get("StringValue")
        if rastreio.traceparent_valido(traceparent):
            saida = dict(message_attributes or {})
            saida.update(gerados)
            return saida
    traceparent = (
        rastreio.novo_span_filho(traceparent_atual)
        if traceparent_atual
        else rastreio.novo_traceparent()
    )
    return rastreio.injetar(message_attributes, traceparent)


def aceitar_recebido(message_attributes):
    """Aceita o contexto da mensagem recebida; devolve o traceparent efetivo.

    Sem traceparent valido na mensagem nao ha pai a aceitar: o agente nao e
    chamado (comeca trace proprio) e o devolvido e um traceparent novo.
    """
    traceparent, tracestate = rastreio.extrair_recebido(message_attributes)
    if traceparent is None:
        return rastreio.novo_traceparent()
    agente = _agente()
    if agente is not None:
        cabecalhos = {"traceparent": traceparent}
        if tracestate:
            cabecalhos["tracestate"] = tracestate
        agente.accept_distributed_trace_headers(cabecalhos, transport_type="Queue")
    return traceparent


def registrar_mensagem(envelope):
    """Grava na transacao atual os atributos de correlacao do envelope.

    `add_custom_attributes` existe na v9.13.0: newrelic/api/transaction.py:1924
    (funcao de modulo) e newrelic/agent.py:241 (exportacao).
    """
    agente = _agente()
    if agente is None:
        return
    itens = [
        ("sagaId", envelope.get("sagaId")),
        ("osId", envelope.get("osId")),
        ("messageId", envelope.get("messageId")),
        ("messageType", envelope.get("type")),
        ("causationId", envelope.get("causationId")),
        ("requestId", envelope.get("requestId")),
    ]
    agente.add_custom_attributes([(n, v) for n, v in itens if v is not None])

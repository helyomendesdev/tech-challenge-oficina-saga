"""Steps do BDD ponta a ponta da Saga.

Os steps falam com o motor da Saga pela fixture `driver` (ver e2e/driver.py e
SAGA_E2E_DRIVER). Billing e Execucao respondem so quando um step manda; onde o
cenario omite uma resposta intermediaria (ex.: o orcamento gerado antes de o
cliente aprovar), o step a entrega antes, uma unica vez.
"""

import unicodedata

from pytest_bdd import given, parsers, scenarios, then, when

from oficina_saga import rastreio

scenarios("features/fluxo_os.feature")


def _chave(nome):
    """Participante sem acento e em minusculas: "Execução" casa com "Execucao"."""
    sem_acento = unicodedata.normalize("NFD", nome)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


def _garantir_resposta(driver, tipo):
    """Entrega `tipo` se ele ainda nao chegou ao orquestrador nesta Saga."""
    if not any(m.direcao == "resposta" and m.envelope["type"] == tipo for m in driver.mensagens()):
        driver.entregar_resposta(tipo)


def _comando_recebido(driver, participante, tipo):
    comandos = [
        m.envelope for m in driver.mensagens()
        if m.direcao == "comando" and _chave(m.destino) == _chave(participante) and m.envelope["type"] == tipo
    ]
    assert comandos, (
        f"{participante} nao recebeu {tipo}; comandos enviados: "
        f"{[(m.destino, m.envelope['type']) for m in driver.mensagens() if m.direcao == 'comando']}"
    )
    return comandos[-1]


@given(parsers.parse("uma OS com diagnóstico concluído para o cliente {cliente_id:d} e o veículo {veiculo_id:d}"))
def os_diagnosticada(contexto, cliente_id, veiculo_id):
    contexto.update(cliente_id=cliente_id, veiculo_id=veiculo_id)


@given(parsers.parse("o diagnóstico indica {qtd_servico:d} serviço {servico_id:d} e {qtd_peca:d} peças {peca_id:d}"))
def itens_diagnosticados(contexto, qtd_servico, servico_id, qtd_peca, peca_id):
    contexto["servicos"] = [{"servicoId": servico_id, "quantidade": qtd_servico}]
    contexto["pecas"] = [{"pecaId": peca_id, "quantidade": qtd_peca}]


@when("o orquestrador inicia a Saga da OS")
def iniciar_saga(driver, contexto):
    driver.iniciar_saga(**contexto)


@when("o cliente aprova o orçamento")
def aprovar_orcamento(driver):
    _garantir_resposta(driver, "OrcamentoGerado")
    driver.entregar_resposta("OrcamentoAprovado")


@when("a Execução finaliza todos os serviços")
def finalizar_execucao(driver):
    _garantir_resposta(driver, "ExecucaoIniciada")
    driver.entregar_resposta("ExecucaoFinalizada")


@when(parsers.parse('a Execução responde "{tipo}" com o código "{codigo}"'))
def execucao_responde_erro(driver, tipo, codigo):
    driver.entregar_resposta(tipo, codigo=codigo)


@when(parsers.parse('o Billing confirma com "{tipo}"'))
def billing_confirma(driver, tipo):
    driver.entregar_resposta(tipo)


@when(parsers.parse('o Billing responde "{tipo}" com o motivo "{motivo}"'))
def billing_responde_com_motivo(driver, tipo, motivo):
    driver.entregar_resposta(tipo, motivo=motivo)


@when(parsers.parse('a resposta "{tipo}" é entregue duas vezes com o mesmo messageId'))
def resposta_duplicada(driver, contexto, tipo):
    contexto["duplicada"] = driver.entregar_resposta_duplicada(tipo)


@then(parsers.parse('o Billing recebe o comando "{tipo}" com o motivo "{motivo}"'))
def billing_recebe_com_motivo(driver, tipo, motivo):
    comando = _comando_recebido(driver, "Billing", tipo)
    assert comando["payload"].get("motivo") == motivo


@then(parsers.re(r'o Billing recebe o comando "(?P<tipo>\w+)"$'))
def billing_recebe(driver, tipo):
    _comando_recebido(driver, "Billing", tipo)


@then(parsers.parse('a Execução recebe o comando "{tipo}"'))
def execucao_recebe(driver, tipo):
    _comando_recebido(driver, "Execução", tipo)


@then(parsers.re(r'a Saga fica em "(?P<estado>\w+)" depois de "(?P<tipo>\w+)"'))
def saga_em_estado_apos(driver, estado, tipo):
    _garantir_resposta(driver, tipo)
    assert driver.estado_saga() == estado


@then(parsers.re(r'a Saga fica em "(?P<estado>\w+)"$'))
def saga_em_estado(driver, estado):
    assert driver.estado_saga() == estado


@then(parsers.parse('a OS fica com status "{status}"'))
def os_com_status(driver, status):
    assert driver.status_os() == status


@then("todas as mensagens da Saga compartilham o mesmo requestId e trace-id")
def mesma_correlacao(driver):
    mensagens = driver.mensagens()
    assert mensagens, "nenhuma mensagem trocada"
    sem_traceparent = [m.envelope["type"] for m in mensagens if "traceparent" not in m.atributos]
    assert not sem_traceparent, f"mensagens sem traceparent nos MessageAttributes: {sem_traceparent}"
    trace_ids = {rastreio.parse_traceparent(m.atributos["traceparent"]["StringValue"]).trace_id for m in mensagens}
    assert len(trace_ids) == 1, f"mais de um trace-id na Saga: {sorted(trace_ids)}"
    request_ids = {m.envelope["requestId"] for m in mensagens}
    assert len(request_ids) == 1, f"mais de um requestId na Saga: {sorted(request_ids)}"


@then("o orquestrador processa a resposta uma única vez")
def processada_uma_vez(driver, contexto):
    resultados = [r for message_id, r in driver.processamento() if message_id == contexto["duplicada"]]
    assert resultados == ["aplicada", "duplicada"], resultados

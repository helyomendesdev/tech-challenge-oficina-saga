"""Steps do BDD ponta a ponta da Saga.

Os steps falam com o motor da Saga pela fixture `driver` (ver e2e/driver.py e
SAGA_E2E_DRIVER) e dizem "isto acontece": o motor de referencia provoca, o da
stack real espera aparecer no historico da consulta da Saga. Onde o cenario
omite uma resposta intermediaria (ex.: o orcamento gerado antes de o cliente
aprovar), o step a exige antes, uma unica vez.
"""

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/fluxo_os.feature")


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
    driver.aprovar_orcamento()


@when("a Execução finaliza todos os serviços")
def finalizar_execucao(driver):
    driver.acontece_resposta("ExecucaoIniciada")
    driver.acontece_resposta("ExecucaoFinalizada")


@when(parsers.parse('a Execução responde "{tipo}" com o código "{codigo}"'))
def execucao_responde_erro(driver, tipo, codigo):
    driver.acontece_resposta(tipo, codigo=codigo)


@when(parsers.parse('o Billing confirma com "{tipo}"'))
def billing_confirma(driver, tipo):
    driver.acontece_resposta(tipo)


@when(parsers.parse('o Billing responde "{tipo}" com o motivo "{motivo}"'))
def billing_responde_com_motivo(driver, tipo, motivo):
    driver.acontece_resposta(tipo, motivo=motivo)


@when(parsers.parse('a resposta "{tipo}" é entregue duas vezes com o mesmo messageId'))
def resposta_duplicada(driver, contexto, tipo):
    contexto["duplicada"] = driver.entregar_resposta_duplicada(tipo)


@then(parsers.parse('o Billing recebe o comando "{tipo}" com o motivo "{motivo}"'))
def billing_recebe_com_motivo(driver, tipo, motivo):
    driver.esperar_comando("Billing", tipo, motivo=motivo)


@then(parsers.re(r'o Billing recebe o comando "(?P<tipo>\w+)"$'))
def billing_recebe(driver, tipo):
    driver.esperar_comando("Billing", tipo)


@then(parsers.parse('a Execução recebe o comando "{tipo}"'))
def execucao_recebe(driver, tipo):
    driver.esperar_comando("Execução", tipo)


@then(parsers.re(r'a Saga fica em "(?P<estado>\w+)" depois de "(?P<tipo>\w+)"'))
def saga_em_estado_apos(driver, estado, tipo):
    driver.acontece_resposta(tipo)
    driver.esperar_estado(estado)


@then(parsers.re(r'a Saga fica em "(?P<estado>\w+)"$'))
def saga_em_estado(driver, estado):
    driver.esperar_estado(estado)


@then(parsers.parse('a OS fica com status "{status}"'))
def os_com_status(driver, status):
    assert driver.status_os() == status


@then("todas as mensagens da Saga compartilham o mesmo requestId e trace-id")
def mesma_correlacao(driver):
    driver.conferir_correlacao()


@then("o orquestrador processa a resposta uma única vez")
def processada_uma_vez(driver, contexto):
    resultados = [r for message_id, r in driver.processamento() if message_id == contexto["duplicada"]]
    assert resultados == ["aplicada", "duplicada"], resultados

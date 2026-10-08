"""Steps do BDD ponta a ponta da Saga.

v0.1: os serviços ainda não existem. Cada step chama pytest.skip, então os
cenários aparecem como SKIPPED (coletados, ligados aos steps, ainda não
executáveis). Ao implementar, troque o skip pela chamada real (HTTP no OS
Service, leitura das filas SQS, consulta de estado da Saga).
"""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/fluxo_os.feature")

PENDENTE = "pendente: serviços da Fase 4 ainda não implementados"


@given(parsers.parse("uma OS com diagnóstico concluído para o cliente {cliente_id:d} e o veículo {veiculo_id:d}"))
def os_diagnosticada(cliente_id, veiculo_id):
    pytest.skip(PENDENTE)


@given(parsers.parse("o diagnóstico indica {qtd_servico:d} serviço {servico_id:d} e {qtd_peca:d} peças {peca_id:d}"))
def itens_diagnosticados(qtd_servico, servico_id, qtd_peca, peca_id):
    pytest.skip(PENDENTE)


@when("o orquestrador inicia a Saga da OS")
def iniciar_saga():
    pytest.skip(PENDENTE)


@when("o cliente aprova o orçamento")
def aprovar_orcamento():
    pytest.skip(PENDENTE)


@when("a Execução finaliza todos os serviços")
def finalizar_execucao():
    pytest.skip(PENDENTE)


@when(parsers.parse('a Execução responde "{tipo}" com o código "{codigo}"'))
def execucao_responde_erro(tipo, codigo):
    pytest.skip(PENDENTE)


@when(parsers.parse('o Billing confirma com "{tipo}"'))
def billing_confirma(tipo):
    pytest.skip(PENDENTE)


@when(parsers.parse('a resposta "{tipo}" é entregue duas vezes com o mesmo messageId'))
def resposta_duplicada(tipo):
    pytest.skip(PENDENTE)


@then(parsers.parse('o Billing recebe o comando "{tipo}" com o motivo "{motivo}"'))
def billing_recebe_com_motivo(tipo, motivo):
    pytest.skip(PENDENTE)


@then(parsers.re(r'o Billing recebe o comando "(?P<tipo>\w+)"$'))
def billing_recebe(tipo):
    pytest.skip(PENDENTE)


@then(parsers.parse('a Execução recebe o comando "{tipo}"'))
def execucao_recebe(tipo):
    pytest.skip(PENDENTE)


@then(parsers.re(r'a Saga fica em "(?P<estado>\w+)" depois de "(?P<tipo>\w+)"'))
def saga_em_estado_apos(estado, tipo):
    pytest.skip(PENDENTE)


@then(parsers.re(r'a Saga fica em "(?P<estado>\w+)"$'))
def saga_em_estado(estado):
    pytest.skip(PENDENTE)


@then(parsers.parse('a OS fica com status "{status}"'))
def os_com_status(status):
    pytest.skip(PENDENTE)


@then("todas as mensagens da Saga compartilham o mesmo requestId e trace-id")
def mesma_correlacao():
    pytest.skip(PENDENTE)


@then("o orquestrador processa a resposta uma única vez")
def processada_uma_vez():
    pytest.skip(PENDENTE)

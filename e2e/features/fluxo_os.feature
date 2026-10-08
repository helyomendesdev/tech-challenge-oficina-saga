# language: pt
Funcionalidade: Saga da ordem de serviço ponta a ponta
  Como oficina
  Quero que a OS atravesse OS Service, Billing e Execução de forma consistente
  Para que uma falha no meio desfaça o que já foi feito

  Contexto:
    Dado uma OS com diagnóstico concluído para o cliente 7 e o veículo 15
    E o diagnóstico indica 1 serviço 12 e 2 peças 40

  Cenário: Fluxo feliz até a OS finalizada
    Quando o orquestrador inicia a Saga da OS
    Então o Billing recebe o comando "GerarOrcamento"
    E a Saga fica em "AGUARDANDO_APROVACAO" depois de "OrcamentoGerado"
    Quando o cliente aprova o orçamento
    Então a Execução recebe o comando "IniciarExecucao"
    Quando a Execução finaliza todos os serviços
    Então a Saga fica em "CONCLUIDA"
    E a OS fica com status "FINALIZADA"
    E todas as mensagens da Saga compartilham o mesmo requestId e trace-id

  Cenário: Compensação quando a execução falha
    Quando o orquestrador inicia a Saga da OS
    E o cliente aprova o orçamento
    E a Execução responde "ExecucaoFalhou" com o código "PECA_SEM_ESTOQUE"
    Então a Saga fica em "COMPENSANDO"
    E o Billing recebe o comando "CancelarOrcamento" com o motivo "EXECUCAO_FALHOU"
    Quando o Billing confirma com "OrcamentoCancelado"
    Então a Saga fica em "CANCELADA"
    E a OS fica com status "CANCELADA"

  Cenário: Mensagem repetida não avança a Saga duas vezes
    Quando o orquestrador inicia a Saga da OS
    E a resposta "OrcamentoGerado" é entregue duas vezes com o mesmo messageId
    Então o orquestrador processa a resposta uma única vez
    E a Saga fica em "AGUARDANDO_APROVACAO"

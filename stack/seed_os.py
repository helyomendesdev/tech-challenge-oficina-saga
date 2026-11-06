"""Seed do catalogo do -os para o BDD da stack real (idempotente).

Rodado pelo servico `seed-os` do compose: `python manage.py shell < /stack/seed_os.py`
(o script usa os models do Django ja carregados pelo shell). Os ids e os precos
batem com o catalogo demo do Billing (`config/catalog.demo.json`):
servico:12 = 15000 centavos e peca:40 = 2500 centavos. O `.feature` pede 1
servico 12 e 2 pecas 40, entao a peca precisa de estoque >= 2; cada execucao do
seed repoe o estoque em 100.

Ids fixos num Postgres nao avancam a sequence; ok para a demo, que nao cria
servico/peca por outra via.
"""

from decimal import Decimal

from os_service.models import Peca, Servico

SERVICO_ID = 12
PECA_ID = 40
ESTOQUE = 100

servico, criado_servico = Servico.objects.update_or_create(
    pk=SERVICO_ID,
    defaults={"descricao": "Servico demo BDD (catalogo do Billing)", "valor_mao_de_obra": Decimal("150.00")},
)
peca, criada_peca = Peca.objects.update_or_create(
    pk=PECA_ID,
    defaults={"nome": "Peca demo BDD (catalogo do Billing)", "valor_unitario": Decimal("25.00"), "estoque_atual": ESTOQUE},
)
print(f"seed-os: servico {servico.pk} {'criado' if criado_servico else 'atualizado'}; "
      f"peca {peca.pk} {'criada' if criada_peca else 'atualizada'} (estoque {peca.estoque_atual})")

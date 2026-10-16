"""Envelope comum de comandos e respostas da Saga: montagem e validacao."""

import functools
import uuid
from datetime import datetime, timezone


@functools.lru_cache(maxsize=None)
def _validador_de(raiz):
    # Import preguicoso: `montar` so usa a stdlib; jsonschema (extra `validacao`) so e exigido ao validar.
    from oficina_saga import contrato

    schemas, registro = contrato.montar_registro(raiz)
    return contrato.montar_validador(contrato.schemas_por_tipo(schemas), registro)


def _validador_para(tipo, raiz=None):
    return _validador_de(raiz)(tipo)


def montar(tipo, *, saga_id, os_id, request_id, payload, causation_id=None, version=1):
    return {
        "messageId": str(uuid.uuid4()),
        "type": tipo,
        "version": version,
        "sagaId": saga_id,
        "osId": os_id,
        "causationId": causation_id,
        "requestId": request_id,
        "occurredAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payload": payload,
    }


def validar(envelope):
    """Levanta jsonschema.ValidationError (ou KeyError se `type` nao existe) se invalido."""
    _validador_para(envelope["type"]).validate(envelope)

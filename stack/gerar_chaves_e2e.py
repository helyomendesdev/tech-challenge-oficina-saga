"""Gera o par RSA de TESTE com que o BDD da stack real assina o JWT de cliente.

O Billing so valida (chave publica, AUTH_JWT_PUBLIC_KEY_B64); quem emite o token
em producao e o -auth. No teste local o driver (e2e/driver_stack_real.py) assina
com a chave privada gerada aqui. A pasta de saida (padrao `stack/.local/`) esta
no .gitignore: a chave privada nunca entra no repositorio.

    python stack/gerar_chaves_e2e.py                 # cria (ou reusa) o par e imprime o b64 da publica
    export AUTH_JWT_PUBLIC_KEY_B64=<b64 impresso>     # lido pelo billing no compose
    export SAGA_E2E_CLIENTE_CHAVE_PRIVADA=stack/.local/cliente-e2e.pem

Se o par ja existe, e reaproveitado (o b64 nao muda entre execucoes); apague os
arquivos para trocar de chave. Requer `cryptography` (vem com PyJWT[crypto]).
"""

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PASTA_PADRAO = Path(__file__).resolve().parent / ".local"
NOME_PRIVADA = "cliente-e2e.pem"
NOME_PUBLICA = "cliente-e2e.pub.pem"


def gerar(pasta: Path) -> str:
    """Garante o par em `pasta` e devolve o base64 do PEM da chave publica."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo_privado = pasta / NOME_PRIVADA
    arquivo_publico = pasta / NOME_PUBLICA
    if arquivo_privado.exists():
        chave = serialization.load_pem_private_key(arquivo_privado.read_bytes(), None)
    else:
        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        arquivo_privado.write_bytes(
            chave.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        try:
            arquivo_privado.chmod(0o600)
        except OSError:
            pass  # sem efeito no Windows
    pem_publico = chave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    arquivo_publico.write_bytes(pem_publico)
    return base64.b64encode(pem_publico).decode("ascii")


def main(argv=None):
    analisador = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analisador.add_argument("--pasta", type=Path, default=PASTA_PADRAO, help="onde gravar o par (padrao: stack/.local)")
    argumentos = analisador.parse_args(argv)
    b64 = gerar(argumentos.pasta)
    print(b64)
    print(f"chave privada de teste: {argumentos.pasta / NOME_PRIVADA}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Gera o par RSA de TESTE com que o BDD da stack real assina o JWT de cliente.

O Billing so valida (chave publica, AUTH_JWT_PUBLIC_KEY_B64); quem emite o token
em producao e o -auth. No teste local o driver (e2e/driver_stack_real.py) assina
com a chave privada gerada aqui. Tambem grava `stack/.local/e2e.env` com a chave
publica, a senha do atendente e a senha do banco do Billing (aleatorias): o compose
nao tem senha fixa. A pasta `stack/.local/` esta no .gitignore.

    python stack/gerar_chaves_e2e.py      # cria (ou reusa) o par e o e2e.env
    docker compose --env-file stack/.local/e2e.env -f stack/docker-compose.yml up -d --build --wait
    set -a; . stack/.local/e2e.env; set +a   # o driver le as mesmas variaveis

Se o par ou as senhas ja existem, sao reaproveitados; apague os arquivos para
trocar. Requer `cryptography` (vem com PyJWT[crypto]).
"""

import argparse
import base64
import sys
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PASTA_PADRAO = Path(__file__).resolve().parent / ".local"
NOME_PRIVADA = "cliente-e2e.pem"
NOME_PUBLICA = "cliente-e2e.pub.pem"
NOME_ENV = "e2e.env"


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


def gerar_env(pasta: Path, chave_publica_b64: str) -> Path:
    """Grava `pasta/e2e.env` com os segredos de teste da stack (reaproveita os que ja existem).

    Senhas aleatorias por maquina: nenhuma senha fica versionada no compose.
    O arquivo serve ao `docker compose --env-file` e ao driver (exportado no shell).
    """
    arquivo = pasta / NOME_ENV
    atuais = {}
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                nome, valor = linha.split("=", 1)
                atuais[nome] = valor
    senha_atendente = atuais.get("ATENDENTE_PASSWORD") or secrets.token_urlsafe(16)
    senha_billing = atuais.get("POSTGRES_BILLING_PASSWORD") or secrets.token_urlsafe(16)
    valores = {
        "AUTH_JWT_PUBLIC_KEY_B64": chave_publica_b64,
        "ATENDENTE_PASSWORD": senha_atendente,
        "SAGA_E2E_ATENDENTE_PASSWORD": senha_atendente,
        "POSTGRES_BILLING_PASSWORD": senha_billing,
        "SAGA_E2E_CLIENTE_CHAVE_PRIVADA": (pasta / NOME_PRIVADA).as_posix(),  # "/" sobrevive ao `. e2e.env` do shell
    }
    arquivo.write_text(
        "# Gerado por stack/gerar_chaves_e2e.py; fica fora do git (stack/.local/).\n"
        + "".join(f"{nome}={valor}\n" for nome, valor in valores.items()),
        encoding="utf-8",
    )
    return arquivo


def main(argv=None):
    # Sem opcao de pasta: tudo vai para stack/.local/ (no .gitignore), e nenhum
    # caminho vindo da linha de comando chega ao sistema de arquivos.
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    b64 = gerar(PASTA_PADRAO)
    arquivo_env = gerar_env(PASTA_PADRAO, b64)
    print(b64)
    print(f"chave privada de teste: {PASTA_PADRAO / NOME_PRIVADA}", file=sys.stderr)
    print(f"variaveis da stack de teste: {arquivo_env}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

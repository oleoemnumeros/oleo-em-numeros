"""Manda por e-mail os textos do resumo do mês (versão para o X e versão para o LinkedIn).

  python scripts/enviar_resumo.py --redes redes.json --marcador .enviado/ultimo.txt

`redes.json` é gravado por `python -m gerador.gerar --redes redes.json` (resumo do mês mais recente).
O marcador guarda o último mês enviado: a execução diária não repete o e-mail, e só um mês novo dispara outro.

Variáveis de ambiente (segredos do repositório):
  GMAIL_USER          conta que envia (a mesma que recebe, por padrão)
  GMAIL_APP_PASSWORD  "senha de app" do Gmail (exige verificação em duas etapas)
  EMAIL_DESTINO       opcional; padrão: oleoemnumeros@gmail.com

Nunca derruba a publicação: sem segredos, sem texto ou com falha de envio, avisa e termina com sucesso.
Só grava o marcador depois de enviar, então um envio que falhou é tentado de novo na próxima execução.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

ASSUNTO = "post para redes sociais - oleo em números"
SERVIDOR, PORTA = "smtp.gmail.com", 465
DESTINO = "oleoemnumeros@gmail.com"


def montar_mensagem(redes: dict, de: str, para: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = ASSUNTO
    msg["From"] = de
    msg["To"] = para
    corpo = [
        redes["manchete"], "",
        f"Página do resumo: {redes['url']}", "",
        "=" * 40, f"VERSÃO PARA O X ({len(redes['x'])} caracteres; o link conta 23 no X)", "=" * 40, "",
        redes["x"], "", "",
        "=" * 40, "VERSÃO PARA O LINKEDIN", "=" * 40, "",
        redes["linkedin"], "",
    ]
    msg.set_content("\n".join(corpo))
    return msg


def enviar(msg: EmailMessage, usuario: str, senha: str, fabrica=smtplib.SMTP_SSL) -> None:
    with fabrica(SERVIDOR, PORTA, context=ssl.create_default_context(), timeout=60) as s:
        s.login(usuario, senha)
        s.send_message(msg)


def _saida(chave: str, valor: str) -> None:
    arq = os.environ.get("GITHUB_OUTPUT")
    if arq:
        with open(arq, "a", encoding="utf-8") as f:
            f.write(f"{chave}={valor}\n")


def principal(redes_arq: Path, marcador: Path, env=os.environ, fabrica=smtplib.SMTP_SSL) -> int:
    _saida("enviado", "nao")
    if not redes_arq.exists():
        print("Sem texto de resumo nesta execução; nada a enviar.")
        return 0
    redes = json.loads(redes_arq.read_text("utf-8"))
    if marcador.exists() and marcador.read_text("utf-8").strip() == redes["per"]:
        print(f"O resumo de {redes['per']} já foi enviado; nada a fazer.")
        return 0
    usuario, senha = env.get("GMAIL_USER", "").strip(), env.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not usuario or not senha:
        print("::warning::Resumo para as redes não enviado: faltam os segredos GMAIL_USER e GMAIL_APP_PASSWORD no repositório.")
        return 0
    para = env.get("EMAIL_DESTINO", "").strip() or DESTINO
    try:
        enviar(montar_mensagem(redes, usuario, para), usuario, senha, fabrica)
    except Exception as e:  # noqa: BLE001 - e-mail nunca derruba a publicação; o marcador fica de fora e a próxima execução tenta de novo
        print(f"::warning::Não consegui enviar o e-mail do resumo ({type(e).__name__}): tento de novo na próxima execução.")
        return 0
    marcador.parent.mkdir(parents=True, exist_ok=True)
    marcador.write_text(redes["per"], "utf-8")
    _saida("enviado", "sim")
    print(f"E-mail do resumo de {redes['per']} enviado para {para}.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--redes", type=Path, required=True)
    ap.add_argument("--marcador", type=Path, required=True)
    a = ap.parse_args()
    sys.exit(principal(a.redes, a.marcador))

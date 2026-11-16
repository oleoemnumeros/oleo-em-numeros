"""IndexNow: avisa o Bing (e os buscadores que usam o mesmo protocolo) quais páginas mudaram nesta publicação.

  python scripts/indexnow.py preparar <pasta do site> <https://dominio> <chave>
      Compara o miolo (<main>) de cada página com o manifesto da publicação anterior, que fica no próprio
      site (/indexnow-manifesto.json). Grava na pasta o manifesto novo, a lista do que mudou
      (/indexnow-novas.json) e o arquivo da chave (/<chave>.txt), que prova que o site é nosso.
  python scripts/indexnow.py enviar <pasta do site ou -> <https://dominio> <chave>
      Depois do deploy: envia a lista. Sem a pasta ("-"), lê a lista já publicada no site.

Nunca derruba a publicação: qualquer falha vira aviso e o script termina com sucesso.
"""
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

LIMITE = 10000          # máximo de endereços por envio, pelo protocolo
MIOLO = re.compile(rb"<main\b.*?</main>", re.S)
AGENTE = {"User-Agent": "familia-em-numeros-indexnow/1.0"}


def baixar(url: str, espera: int = 30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENTE), timeout=espera) as r:
        return r.read()


def manifesto(pasta: Path) -> dict:
    m = {}
    for arq in sorted(pasta.rglob("index.html")):
        bruto = arq.read_bytes()
        if b'name="robots" content="noindex' in bruto[:4000] or b'http-equiv="refresh"' in bruto[:4000]:
            continue
        achado = MIOLO.search(bruto)
        rel = arq.parent.relative_to(pasta).as_posix()
        m["/" if rel == "." else f"/{rel}/"] = hashlib.md5(achado.group(0) if achado else bruto).hexdigest()[:10]
    return m


def preparar(pasta: Path, base: str, chave: str) -> list:
    novo = manifesto(pasta)
    robots = pasta / "robots.txt"
    fechado = robots.exists() and re.search(r"^Disallow: /\s*$", robots.read_text("utf-8"), re.M)
    try:
        velho = json.loads(baixar(f"{base}/indexnow-manifesto.json?t={int(time.time())}"))
    except Exception as e:  # primeira vez, ou site fora do ar: trata tudo como novo
        print(f"IndexNow: sem manifesto anterior ({e}); todas as páginas contam como novas.")
        velho = {}
    mudou = [] if fechado else [c for c, h in novo.items() if velho.get(c) != h]
    mudou.sort(key=lambda c: (c.count("/"), c))          # páginas principais primeiro, se passar do limite
    (pasta / "indexnow-manifesto.json").write_text(json.dumps(novo, separators=(",", ":")), "utf-8")
    (pasta / "indexnow-novas.json").write_text(json.dumps(mudou[:LIMITE], separators=(",", ":")), "utf-8")
    (pasta / f"{chave}.txt").write_text(chave, "utf-8")
    print(f"IndexNow: {len(mudou)} de {len(novo)} páginas mudaram" + (" (site fechado aos buscadores: nada será enviado)" if fechado else ""))
    return mudou


def enviar(pasta, base: str, chave: str) -> int:
    local = Path(pasta) / "indexnow-novas.json" if pasta != "-" else None
    caminhos = json.loads(local.read_text("utf-8") if local and local.exists() else baixar(f"{base}/indexnow-novas.json?t={int(time.time())}"))
    if not caminhos:
        print("IndexNow: nenhuma página mudou; nada a enviar.")
        return 0
    corpo = json.dumps({"host": base.split("//", 1)[1], "key": chave, "keyLocation": f"{base}/{chave}.txt",
                        "urlList": [base + c for c in caminhos[:LIMITE]]}).encode("utf-8")
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=corpo, method="POST",
                                 headers={**AGENTE, "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=60) as r:
        print(f"IndexNow: {len(caminhos[:LIMITE])} endereços enviados, resposta {r.status}.")
    return len(caminhos)


if __name__ == "__main__":
    try:
        modo, pasta, base, chave = sys.argv[1:5]
        base = base.rstrip("/")
        preparar(Path(pasta), base, chave) if modo == "preparar" else enviar(pasta, base, chave)
    except Exception as e:  # noqa: BLE001 — a publicação não pode cair por causa do aviso aos buscadores
        print(f"::warning::IndexNow não concluído: {e}")

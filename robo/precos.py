"""Preços de combustíveis ao consumidor (ANP, levantamento de preços de revenda).

Uso (fase de exploração):
    python -m robo.precos --listar        # imprime os links /shpc/ das páginas de dados abertos
    python -m robo.precos --baixar URL..  # baixa arquivos para dados/precos
"""
import argparse
import re
import sys
from urllib.parse import unquote, urljoin

from . import config
from .baixar import _Links, _get, _sessao

BASE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
PAGINAS = [BASE + p for p in (
    "serie-historica-de-precos-de-combustiveis",
    "levantamento-de-precos-de-combustiveis",
    "precos-de-combustiveis",
    "serie-historica-do-levantamento-de-precos-e-de-margens-de-comercializacao-de-combustiveis",
    "levantamento-de-precos-e-de-margens-de-comercializacao-de-combustiveis",
    "")]
PASTA = config.DADOS / "precos"


def links() -> list[str]:
    s = _sessao()
    achados = []
    for pg in PAGINAS:
        try:
            p = _Links()
            p.feed(_get(s, pg, tentativas=1).text)
        except Exception as e:  # noqa: BLE001
            print(f"(falhou: {pg}: {str(e)[:70]})")
            continue
        n = 0
        for h in p.links:
            u = re.sub(r"/@@download/file$", "", urljoin(pg, h.strip()))
            if re.search(r"/shpc/|preco", u, re.I) and u not in achados:
                achados.append(u)
                n += 1
        print(f"{pg or BASE}: {n} links")
    return achados


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--baixar", nargs="*")
    a = ap.parse_args()
    if a.listar:
        for u in links():
            print(" -", unquote(u))
    if a.baixar:
        PASTA.mkdir(parents=True, exist_ok=True)
        s = _sessao()
        for u in a.baixar:
            nome = unquote(u).rsplit("/", 1)[-1]
            (PASTA / nome).write_bytes(_get(s, u).content)
            print(f"{nome}: {(PASTA / nome).stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    sys.exit(main())

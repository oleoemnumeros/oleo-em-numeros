"""Refino: processamento de petróleo e produção de derivados por refinaria (ANP, dados abertos, mensal desde 1990).

Uso:
    python -m robo.refino --so-baixar    # baixa os CSVs para dados/refino
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin

from . import config
from .baixar import _Links, _get, _sessao

BASE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
PAGINAS = [BASE + "painel-de-produtores-de-derivados-processamento-de-petroleo-e-producao-de-derivados",
           BASE + "processamento-de-petroleo-e-producao-de-derivados",
           BASE + "producao-de-derivados-de-petroleo-e-processamento-de-gas-natural",
           BASE + "painel-dinamico-de-produtores-de-derivados-processamento-de-petroleo-e-producao-de-derivados",
           BASE]
PASTA = config.DADOS / "refino"
ARQUIVOS = {"processamento": r"processamento.*petroleo", "derivados": r"producao-derivados.*refinaria"}


def descobrir(s) -> list[str]:
    achados = []
    for pg in PAGINAS:
        try:
            p = _Links()
            p.feed(_get(s, pg, tentativas=1).text)
        except Exception as e:  # noqa: BLE001
            print(f"(falhou: {pg}: {str(e)[:80]})")
            continue
        for h in p.links:
            u = re.sub(r"/@@download/file$", "", urljoin(pg, h.strip()))
            if "/pppd/" in u and "metadado" not in u.lower():
                achados.append(u)
        if achados:
            print(f"página com os arquivos: {pg}")
            break
    return list(dict.fromkeys(achados))


def baixar_csvs(destino: Path = PASTA) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    s = _sessao()
    links = descobrir(s)
    print("links /pppd/:")
    for u in links:
        print(" -", unquote(u))
    saida = {}
    for tipo, padrao in ARQUIVOS.items():
        url = next((u for u in links if re.search(padrao, unquote(u), re.I)), None)
        if not url:
            raise RuntimeError(f"Arquivo de refino ({tipo}) não encontrado")
        (destino / f"{tipo}.csv").write_bytes(_get(s, url).content)
        saida[tipo] = url
        print(f"refino {tipo}: {(destino / f'{tipo}.csv').stat().st_size / 1e3:.0f} KB de {unquote(url)}")
    return saida


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-baixar", action="store_true")
    ap.add_argument("--sem-baixar", action="store_true")
    a = ap.parse_args()
    if not a.sem_baixar:
        baixar_csvs()
    if a.so_baixar:
        return
    raise SystemExit("processamento ainda não implementado")


if __name__ == "__main__":
    sys.exit(main())

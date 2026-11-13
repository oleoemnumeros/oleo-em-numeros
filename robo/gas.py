"""Gás natural por destino: produção, queima, reinjeção, consumo próprio e gás disponível ao mercado.

Fontes da ANP:
- dados abertos "Produção de petróleo e gás natural por estado e localização": CSVs mensais desde 2000,
  por estado e terra/mar (mil m³), um arquivo por destino;
- Boletim Mensal da Produção (PDF): o ranking dos 20 campos com maior queima, com operador (mil m³/d).
A ANP não publica a reinjeção nem o aproveitamento por campo ou por empresa, então o site não os mostra.

Uso:
    python -m robo.gas                # baixa e processa
    python -m robo.gas --sem-baixar   # só processa o que já está em dados/gas
    python -m robo.gas --so-baixar    # só baixa (ferramenta de manutenção)
"""
import argparse
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd

from . import config
from .baixar import _Links, _get, _sessao

PAGINA = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/producao-de-petroleo-e-gas-natural-por-estado-e-localizacao"
BOLETIM = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/publicacoes/boletins-anp/arquivos-bmppgn/{ano}/{mes}.pdf"
PASTA = config.DADOS / "gas"
# destino -> padrão do nome do arquivo na página da ANP
ARQUIVOS = {"producao": r"producao-gas-natural", "queima": r"queima-e-perda", "reinjecao": r"reinjecao-gn",
            "consumo": r"consumo-proprio", "disponivel": r"gn-disponivel"}
MESES_PT = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
            "novembro", "dezembro"]
N_BOLETINS = 14


def baixar_csvs(destino: Path = PASTA) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    s = _sessao()
    p = _Links()
    p.feed(_get(s, PAGINA).text)
    links = [re.sub(r"/@@download/file$", "", urljoin(PAGINA, h.strip())) for h in p.links]
    links = [u for u in dict.fromkeys(links) if "ppgn-el" in u and "metadado" not in u.lower()]
    saida = {}
    for tipo, padrao in ARQUIVOS.items():
        url = next((u for u in links if re.search(padrao, u, re.I)), None)
        if not url:
            raise RuntimeError(f"Arquivo de gás ({tipo}) não encontrado em {PAGINA}")
        (destino / f"{tipo}.csv").write_bytes(_get(s, url).content)
        saida[tipo] = url
        print(f"gás {tipo}: {(destino / f'{tipo}.csv').stat().st_size / 1e3:.0f} KB de {url}")
    return saida


def baixar_boletins(destino: Path = PASTA / "boletins", quantos: int = N_BOLETINS) -> list[str]:
    """Baixa os boletins mensais mais recentes (o do mês corrente costuma sair com atraso, então
    tenta do mês atual para trás e para quando já tem `quantos`)."""
    destino.mkdir(parents=True, exist_ok=True)
    s = _sessao()
    hoje = date.today()
    a, m = hoje.year, hoje.month
    feitos = []
    for _ in range(quantos + 6):
        arq = destino / f"{a}-{m:02d}.pdf"
        if arq.exists() and arq.stat().st_size > 10000:
            feitos.append(arq.name)
        else:
            try:
                arq.write_bytes(_get(s, BOLETIM.format(ano=a, mes=MESES_PT[m - 1]), tentativas=1).content)
                feitos.append(arq.name)
                print(f"boletim {a}-{m:02d}: {arq.stat().st_size / 1e6:.1f} MB")
            except RuntimeError:
                pass
        if len(feitos) >= quantos:
            break
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    return feitos


def texto_paginas(pdf: bytes | Path) -> list[str]:
    import pdfplumber
    fonte = io.BytesIO(pdf) if isinstance(pdf, bytes) else pdf
    with pdfplumber.open(fonte) as doc:
        return [(p.extract_text() or "") for p in doc.pages]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-baixar", action="store_true")
    ap.add_argument("--sem-baixar", action="store_true")
    a = ap.parse_args()
    if not a.sem_baixar:
        baixar_csvs()
        baixar_boletins()
    if a.so_baixar:
        return
    raise SystemExit("processamento ainda não implementado")


if __name__ == "__main__":
    sys.exit(main())

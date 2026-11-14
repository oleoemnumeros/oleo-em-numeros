"""Refino: processamento de petróleo e produção de derivados por refinaria (ANP, dados abertos, mensal desde 1990).

Uso:
    python -m robo.refino --so-baixar    # baixa os CSVs para dados/refino
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin

import pandas as pd

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


MES = {m: i + 1 for i, m in enumerate(["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"])}
ORIGENS = {"PETRÓLEO NACIONAL": "nacional", "PETRÓLEO IMPORTADO": "importado", "OUTRAS CARGAS": "outras"}
CADASTRO = config.MANUAIS / "refinarias.csv"


def canonica(nome: str) -> str:
    """A ANP renomeou refinarias ao longo da série (RLAM virou REFMAT, REMAN virou REAM, RPCC virou 3R Potiguar)
    e repete o 'ex-' em alguns arquivos; junta tudo sob o nome atual."""
    n = re.sub(r"\s+", " ", str(nome).strip().upper())
    if "3R POTIGUAR" in n or n == "RPCC":
        return "3R POTIGUAR"
    return {"RLAM": "REFMAT", "REMAN": "REAM"}.get(n, n)


def _ler(caminho: Path, valor: str, decimal: str) -> pd.DataFrame:
    df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig", decimal=decimal)
    df.columns = ["ano", "mes", "uf", "refinaria", "item", "valor"]
    df["mes"] = df["mes"].astype(str).str.strip().str.upper().map(MES)
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["ano", "mes"]).astype({"ano": int, "mes": int})
    df["refinaria"] = df["refinaria"].map(canonica)
    df["item"] = df["item"].astype(str).str.strip().str.upper()
    df["periodo"] = df["ano"].map(str) + "-" + df["mes"].map(lambda m: f"{m:02d}")
    return df


def processar(pasta: Path, saida: Path, cadastro: Path = CADASTRO) -> dict:
    """Série mensal por refinaria: petróleo processado por origem e derivados produzidos, em m³ no mês."""
    p = _ler(pasta / "processamento.csv", "processado", ",")
    d = _ler(pasta / "derivados.csv", "producao", ",")
    meses = sorted(set(p["periodo"]) & set(d["periodo"]))
    ultimo = meses[-1]
    idx = {m: i for i, m in enumerate(meses)}
    reg = {}
    if cadastro.exists():
        for r in pd.read_csv(cadastro, encoding="utf-8").fillna("").to_dict("records"):
            reg[canonica(r["anp"])] = r
    refinarias = {}

    def serie(x):
        v = [0] * len(meses)
        for per, tot in x.groupby("periodo")["valor"].sum().items():
            if per in idx:
                v[idx[per]] = int(round(tot))
        return v

    for nome, x in p.groupby("refinaria"):
        r = refinarias.setdefault(nome, {"uf": x["uf"].iloc[-1], "proc": {}, "deriv": {}})
        for item, y in x.groupby("item"):
            if item in ORIGENS:
                r["proc"][ORIGENS[item]] = serie(y)
    for nome, x in d.groupby("refinaria"):
        r = refinarias.setdefault(nome, {"uf": x["uf"].iloc[-1], "proc": {}, "deriv": {}})
        for item, y in x.groupby("item"):
            s = serie(y)
            if any(s):
                r["deriv"][item] = s
    for nome in list(refinarias):
        r = refinarias[nome]
        if not any(any(v) for v in r["proc"].values()) and not r["deriv"]:
            del refinarias[nome]
            continue
        for o in ORIGENS.values():
            r["proc"].setdefault(o, [0] * len(meses))
        r["uf"] = {"RIO GRANDE DO SUL": "RS", "SÃO PAULO": "SP", "RIO DE JANEIRO": "RJ", "BAHIA": "BA", "PARANÁ": "PR",
                   "MINAS GERAIS": "MG", "PERNAMBUCO": "PE", "CEARÁ": "CE", "AMAZONAS": "AM", "RIO GRANDE DO NORTE": "RN"
                   }.get(str(r["uf"]).upper(), str(r["uf"]))
        r["cadastro"] = reg.get(nome, {"nome": nome.title(), "sigla": nome.title()})
    resultado = {"meses": meses, "ultimo": ultimo, "unidade": "m³ no mês", "refinarias": refinarias}
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(resultado, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return resultado


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-baixar", action="store_true")
    ap.add_argument("--sem-baixar", action="store_true")
    a = ap.parse_args()
    if not a.sem_baixar:
        baixar_csvs()
    if a.so_baixar:
        return
    r = processar(PASTA, config.PROCESSADOS / "refino.json")
    print(f"Refino: {r['meses'][0]} a {r['ultimo']}, {len(r['refinarias'])} refinarias")


if __name__ == "__main__":
    sys.exit(main())

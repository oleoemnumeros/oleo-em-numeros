"""Preços de combustíveis ao consumidor (ANP, levantamento de preços nos postos).

A ANP publica os preços coletados posto a posto: arquivos semestrais até 2022 e mensais desde 2023
(gasolina e etanol num arquivo, diesel e GNV noutro). O robô não guarda os arquivos (são centenas de
MB): lê cada um, resume por município, produto e mês e guarda só o resumo em dados/precos/agg. O site
mostra só médias por município, estado e país: nunca o posto, o endereço nem a bandeira.

Uso:
    python -m robo.precos                # baixa o que falta e processa
    python -m robo.precos --sem-baixar   # só processa os resumos já guardados
    python -m robo.precos --listar       # imprime as fontes encontradas na página da ANP
    python -m robo.precos --baixar URL.. # baixa arquivos soltos para dados/precos (diagnóstico)
"""
import argparse
import json
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urljoin

import pandas as pd

from . import config
from .baixar import _Links, _get, _sessao
from .royalties import _chave, carregar_ibge
from .util import sem_acento

PAGINA = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/serie-historica-de-precos-de-combustiveis"
PASTA = config.DADOS / "precos"
AGG = PASTA / "agg"
INICIO = (2019, 1)          # primeiro semestre da série do site
ULTIMO_SEMESTRAL = (2022, 2)  # de 2023 em diante a ANP publica um arquivo por mês
ORCAMENTO_S = 12 * 60       # tempo máximo baixando por execução; o resto fica para a próxima
COLUNAS = ["Estado - Sigla", "Municipio", "CNPJ da Revenda", "Produto", "Data da Coleta", "Valor de Venda"]
PRODUTOS = {"GASOLINA": "Gasolina comum", "GASOLINA ADITIVADA": "Gasolina aditivada", "ETANOL": "Etanol",
            "DIESEL S10": "Diesel S10", "DIESEL": "Diesel S500", "GNV": "GNV"}
MIN_COLETAS = 3             # município com menos coletas no mês não mostra média


def _chaves(links: list[str]) -> dict[str, str]:
    """Chave de cada arquivo (ca-2019-01, dsan-2026-10-gasolina-etanol...) -> URL."""
    out = {}
    for u in links:
        m = re.search(r"/dsas/ca/ca-(\d{4})-(\d{2})\.(?:csv|zip)$", u)
        if m:
            ano, sem = int(m[1]), int(m[2])
            if INICIO <= (ano, sem) <= ULTIMO_SEMESTRAL:
                out.setdefault(f"ca-{ano}-{sem:02d}", u)
            continue
        m = re.search(r"/dsan/(\d{4})/([^/]+)$", u)
        if m:
            ano, nome = int(m[1]), m[2].lower()
            grupo = "gasolina-etanol" if "gasolina" in nome and "etanol" in nome else "diesel-gnv" if "diesel" in nome else None
            mes = re.match(r"(\d{2})-", nome) or re.search(r"-(\d{2})(?:\.csv)?$", nome)
            if grupo and mes and ano >= ULTIMO_SEMESTRAL[0] + 1:
                out.setdefault(f"dsan-{ano}-{int(mes[1]):02d}-{grupo}", u)
    return dict(sorted(out.items(), reverse=True))


def fontes() -> dict[str, str]:
    p = _Links()
    p.feed(_get(_sessao(), PAGINA).text)
    return _chaves([re.sub(r"/@@download/file$", "", urljoin(PAGINA, h.strip())) for h in p.links])


def resumir(caminho: Path) -> pd.DataFrame:
    """Um arquivo de preços -> uma linha por município, produto e mês (n, soma, mínimo, máximo, postos)."""
    try:
        d = pd.read_csv(caminho, sep=";", encoding="utf-8-sig", dtype=str, usecols=COLUNAS)
    except UnicodeDecodeError:
        d = pd.read_csv(caminho, sep=";", encoding="latin-1", dtype=str, usecols=COLUNAS)
    d = d.rename(columns={"Estado - Sigla": "uf", "Municipio": "mun", "CNPJ da Revenda": "posto", "Produto": "prod"})
    d["dt"] = pd.to_datetime(d["Data da Coleta"], format="%d/%m/%Y", errors="coerce")
    d["v"] = pd.to_numeric(d["Valor de Venda"].str.replace(",", ".", regex=False), errors="coerce")
    d = d.dropna(subset=["dt", "v"])
    d = d[(d["v"] >= 1) & (d["v"] <= 30)]          # descarta erro de digitação
    d["uf"] = d["uf"].str.strip().str.upper()
    d["mun"] = d["mun"].str.strip().str.upper()
    d["prod"] = d["prod"].str.strip().str.upper()
    d = d[d["prod"].isin(PRODUTOS)]
    d["mes"] = d["dt"].dt.strftime("%Y-%m")
    g = d.groupby(["uf", "mun", "prod", "mes"]).agg(n=("v", "size"), soma=("v", "sum"), minimo=("v", "min"),
                                                   maximo=("v", "max"), postos=("posto", "nunique"),
                                                   dia=("dt", "max")).reset_index()
    g["dia"] = g["dia"].dt.strftime("%Y-%m-%d")
    g["soma"] = g["soma"].round(4)
    return g


def _recente(chave: str) -> bool:
    """Arquivo mensal do mês corrente ou do anterior: a ANP ainda acrescenta e corrige."""
    m = re.match(r"dsan-(\d{4})-(\d{2})-", chave)
    if not m:
        return False
    hoje = date.today()
    k = hoje.year * 12 + hoje.month - (int(m[1]) * 12 + int(m[2]))
    return k <= 1


def baixar_e_resumir(destino: Path = AGG) -> int:
    destino.mkdir(parents=True, exist_ok=True)
    s = _sessao()
    ini, feitos = time.time(), 0
    for chave, url in fontes().items():
        arq = destino / f"{chave}.csv.gz"
        if arq.exists() and not _recente(chave):
            continue
        if time.time() - ini > ORCAMENTO_S:
            print(f"Preços: tempo esgotado; o restante fica para a próxima execução (faltou {chave} e anteriores).")
            break
        try:
            conteudo = _get(s, url).content
        except RuntimeError as e:
            print(f"::warning::Preços: não baixei {chave}: {str(e)[:100]}")
            continue
        sufixo = ".zip" if url.lower().endswith(".zip") else ".csv"
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t) / f"arq{sufixo}"
            tmp.write_bytes(conteudo)
            res = resumir(tmp)
        res.to_csv(arq, index=False, compression="gzip")
        feitos += 1
        print(f"Preços: {chave}: {len(conteudo) / 1e6:.1f} MB -> {len(res)} linhas")
    return feitos


def _lista(df: pd.DataFrame, col: str) -> list:
    return [None if pd.isna(x) else x for x in df[col]]


def processar(pasta: Path = AGG, saida: Path | None = None, ibge: dict | None = None) -> dict | None:
    arqs = sorted(pasta.glob("*.csv.gz"))
    if not arqs:
        return None
    t = pd.concat([pd.read_csv(a, dtype={"uf": str, "mun": str, "prod": str, "mes": str}) for a in arqs], ignore_index=True)
    # um mês pode vir de dois arquivos (virada de semestre, correções): vale o último arquivo lido
    t = t.drop_duplicates(["uf", "mun", "prod", "mes"], keep="last")
    meses = sorted(t["mes"].unique())
    idx = {m: i for i, m in enumerate(meses)}
    prods = [p for p in PRODUTOS if p in set(t["prod"])]
    ibge = carregar_ibge() if ibge is None else ibge
    # a ANP escreve "Santana do Livramento", o IBGE "Sant'Ana do Livramento": cai na comparação só com letras e números
    ibge_solto = {re.sub(r"[^a-z0-9]", "", k): v for k, v in ibge.items()}

    def serie(x):
        m, n = [None] * len(meses), [0] * len(meses)
        for mes, soma, k in zip(x["mes"], x["soma"], x["n"]):
            m[idx[mes]] = round(soma / k, 3)
            n[idx[mes]] = int(k)
        return {"m": m, "n": n}

    def agregado(x):
        g = x.groupby(["prod", "mes"]).agg(soma=("soma", "sum"), n=("n", "sum")).reset_index()
        return {p: serie(g[g["prod"] == p]) for p in prods if (g["prod"] == p).any()}

    ultimo = meses[-1]
    municipios = []
    for (uf, mun), x in t.groupby(["uf", "mun"]):
        info = ibge.get(_chave(mun, uf)) or ibge_solto.get(re.sub(r"[^a-z0-9]", "", _chave(mun, uf)))
        u = x[x["mes"] == ultimo].set_index("prod")
        municipios.append({
            "uf": uf, "nome": info["nome"] if info else mun.title(), "ibge": info["ibge"] if info else None,
            "serie": {p: serie(y) for p, y in x.groupby("prod")},
            "ultimo": {p: {"min": float(r["minimo"]), "max": float(r["maximo"]), "postos": int(r["postos"])}
                       for p, r in u.iterrows()}})
    municipios.sort(key=lambda m: (m["uf"], sem_acento(m["nome"])))
    r = {"meses": meses, "ultimo": ultimo, "coleta_ate": t["dia"].max(), "produtos": prods,
         "rotulos": {p: PRODUTOS[p] for p in prods}, "min_coletas": MIN_COLETAS,
         "brasil": agregado(t), "estados": {uf: agregado(x) for uf, x in t.groupby("uf")}, "municipios": municipios}
    if saida:
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-baixar", action="store_true")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--baixar", nargs="*")
    a = ap.parse_args()
    if a.listar:
        for k, u in fontes().items():
            print(k, unquote(u))
        return
    if a.baixar:
        PASTA.mkdir(parents=True, exist_ok=True)
        s = _sessao()
        for u in a.baixar:
            nome = unquote(u).rsplit("/", 1)[-1]
            (PASTA / nome).write_bytes(_get(s, u).content)
            print(f"{nome}: {(PASTA / nome).stat().st_size / 1e3:.0f} KB")
        return
    if not a.sem_baixar:
        baixar_e_resumir()
    r = processar(AGG, config.PROCESSADOS / "precos.json")
    if r is None:
        raise SystemExit("Sem arquivos de preços resumidos")
    print(f"Preços: {r['meses'][0]} a {r['ultimo']} (coletas até {r['coleta_ate']}), {len(r['municipios'])} municípios, "
          f"produtos: {', '.join(r['produtos'])}")


if __name__ == "__main__":
    sys.exit(main())

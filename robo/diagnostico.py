"""Diagnóstico dos arquivos brutos da ANP: cabeçalhos, amostras, períodos,
duplicidades e totais por fonte. Serve para validar a leitura quando a ANP
muda algum formato. Uso: python -m robo.diagnostico
"""
import re
import sys
from pathlib import Path

import pandas as pd

from . import config
from .ler import _ler_formato_mensal, _ler_formato_por_poco, _ler_tabela, _membros, _texto
from .util import normalizar_cabecalho


def _bruto(conteudo):
    texto = _texto(conteudo)
    df = _ler_tabela(texto)
    df.columns = [normalizar_cabecalho(c) for c in df.columns]
    return texto, df


def _col(df, *palavras):
    for c in df.columns:
        if all(p in c for p in palavras):
            return c
    return None


def amostra(arquivo: Path):
    for nome, conteudo in _membros(arquivo):
        texto, df = _bruto(conteudo)
        print(f"\n=== {arquivo.name} / {nome}: {len(df)} linhas")
        print("colunas:", list(df.columns))
        for linha in texto.splitlines()[1:4]:
            print("  |", linha[:400])
        per = _col(df, "mes/ano") or _col(df, "periodo")
        if per:
            print("períodos brutos:", df[per].value_counts().sort_index().to_dict())
        poco = _col(df, "nome poco anp") or _col(df, "poco")
        if poco and per:
            dup = df.duplicated([poco, per]).sum()
            print(f"linhas com poço+período repetidos: {dup}; linhas idênticas: {df.duplicated().sum()}")
            if dup:
                exemplo = df[df.duplicated([poco, per], keep=False)].head(4)
                print(exemplo.to_string(max_colwidth=30)[:1500])
        for c in ("ambiente", "instalacao", "tipo"):
            col = _col(df, c)
            if col and df[col].nunique() < 15:
                print(f"valores de '{col}':", df[col].value_counts().to_dict())


def comparar_2023(pasta: Path):
    """Compara, mês a mês em 2023, o formato por poço (Terra, Mar, PreSal) com o mensal."""
    linhas = []
    for z in sorted(pasta.glob("2023_producao-*.zip")):
        for nome, conteudo in _membros(z):
            _, df = _bruto(conteudo)
            r = _ler_formato_por_poco(df, nome)
            chave = "A_presal" if "sal" in nome.lower() else ("A_terra" if "terra" in nome.lower() else "A_mar")
            for per, v in r.groupby("periodo")["petroleo_bbl_d"].sum().items():
                linhas.append((per, chave, v))
    for f in sorted(pasta.glob("*2023*.csv")):
        _, df = _bruto(f.read_bytes())
        if not _col(df, "producao de oleo"):
            continue
        r = _ler_formato_mensal(df, f.name)
        for (per, amb), v in r.groupby(["periodo", "ambiente"])["petroleo_bbl_d"].sum().items():
            linhas.append((per, f"B_{amb}", v))
    if linhas:
        t = pd.DataFrame(linhas, columns=["periodo", "fonte", "bbl_d"]).pivot_table(
            index="periodo", columns="fonte", values="bbl_d", aggfunc="sum").round(0)
        print("\n=== 2023: petróleo (bbl/d) por fonte")
        print(t.to_string())


def recentes(pasta: Path):
    """Totais por mês e ambiente nos arquivos do formato mensal mais recentes."""
    partes = []
    for f in sorted(pasta.glob("*.csv")):
        if not re.search(r"202[4-6]", f.name):
            continue
        _, df = _bruto(f.read_bytes())
        if _col(df, "producao de oleo"):
            r = _ler_formato_mensal(df, f.name)
            r["arquivo"] = f.name
            partes.append(r)
    if not partes:
        return
    b = pd.concat(partes)
    t = b.groupby(["periodo", "ambiente"]).agg(petroleo=("petroleo_bbl_d", "sum"), gas=("gas_mil_m3_d", "sum"),
                                               linhas=("poco", "size"), pocos=("poco", "nunique")).round(0)
    print("\n=== 2024–2026, formato mensal: totais por mês e ambiente")
    print(t.tail(30).to_string())


def main(pasta: Path = config.BRUTOS):
    pd.set_option("display.width", 250)
    alvos = ["2023_producao-12.zip", "pm_producao-mar-2026.csv", "pm_producao_por_poco_2024.csv",
             "2026_producao_por_poco_terra_2026_2_trim.csv", "2026_producao_por_poco_terra_2026_3_trim.csv"]
    for nome in alvos:
        if (pasta / nome).exists():
            amostra(pasta / nome)
    comparar_2023(pasta)
    recentes(pasta)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else config.BRUTOS)

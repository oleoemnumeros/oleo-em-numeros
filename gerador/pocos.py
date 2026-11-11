"""Dados da busca de poços: um índice com todos os poços e um arquivo por campo
com a série mensal de cada poço. A página /pocos/ carrega só o que precisa.

Mês sem produção do poço = 0. Mês que a ANP não publicou para aquele ambiente
(terra ou mar) = null, para virar lacuna e não queda a zero.
"""
import json
from pathlib import Path

import pandas as pd

from robo import grupos
from robo.ler import _chave_poco
from robo.util import nome_bonito, nome_campo, slug


def _meses(ini: str, fim: str) -> list[str]:
    a, m = map(int, ini.split("-"))
    saida = []
    while f"{a:04d}-{m:02d}" <= fim:
        saida.append(f"{a:04d}-{m:02d}")
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return saida


def _r(v, casas):
    v = round(float(v), casas)
    return int(v) if v == int(v) else v


def gerar_pocos(base_csv: Path, saida: Path, ag: dict) -> dict:
    b = pd.read_csv(base_csv, dtype={"periodo": str, "campo": str, "poco": str, "operador": str, "ambiente": str,
                                     "estado": str})
    b = b[(b["petroleo_bbl_d"].fillna(0) > 0) | (b["gas_mil_m3_d"].fillna(0) > 0) | (b["agua_bbl_d"].fillna(0) > 0)]
    regras = grupos.carregar()
    da_tabela = {g for _, g in regras}
    ng = lambda op: "" if not isinstance(op, str) else (lambda g: g if g in da_tabela else nome_bonito(g))(grupos.grupo_de(op, regras))  # noqa: E731
    b = b.assign(chave=b["poco"].map(_chave_poco), cslug=b["campo"].map(slug))
    meses = _meses(b["periodo"].min(), b["periodo"].max())
    idx = {p: i for i, p in enumerate(meses)}
    ultimo = meses[-1]
    presentes = set(zip(b["periodo"], b["ambiente"]))
    paginas = {c["slug"]: c for c in ag["campos"]}

    t = (b.groupby(["cslug", "chave", "periodo"])
         .agg(poco=("poco", "last"), campo=("campo", "last"), operador=("operador", "last"), ambiente=("ambiente", "last"),
              estado=("estado", "last"), p=("petroleo_bbl_d", "sum"), g=("gas_mil_m3_d", "sum"), a=("agua_bbl_d", "sum"))
         .reset_index().sort_values(["cslug", "chave", "periodo"]))

    pasta = saida / "dados" / "pocos"
    pasta.mkdir(parents=True, exist_ok=True)
    indice, campos = [], {}
    for cslug, tc in t.groupby("cslug", sort=False):
        lista = []
        for _, tp in tc.groupby("chave", sort=False):
            u = tp.iloc[-1]
            amb = u["ambiente"]
            i0, i1 = idx[tp["periodo"].iloc[0]], idx[tp["periodo"].iloc[-1]]
            if tp["periodo"].iloc[-1] < ultimo and tp["periodo"].iloc[-1] >= meses[-3]:
                i1 = len(meses) - 1     # parou há pouco: mostra os zeros até o último mês
            serie = {k: [None if (meses[i], amb) not in presentes else 0 for i in range(i0, i1 + 1)] for k in "pga"}
            js = [idx[x] - i0 for x in tp["periodo"]]
            for k, casas in (("p", 1), ("g", 3), ("a", 1)):
                for j, v in zip(js, tp[k].fillna(0).tolist()):
                    serie[k][j] = _r(v, casas)
            grupo = ng(u["operador"])
            # produzindo = petróleo ou gás no último mês (mesma regra dos poços ativos do resto do site)
            ativo = tp["periodo"].iloc[-1] == ultimo and (u["p"] > 0 or u["g"] > 0)
            boe = (u["p"] + u["g"] * 6.289811) if ativo else 0.0
            nome = str(u["poco"]).strip()
            lista.append({"n": nome, "i": i0, "o": grupo, **serie})
            indice.append([nome, cslug, 1 if ativo else 0, round(boe)])
        u = tc.iloc[-1]
        pag = paginas.get(cslug)
        campos[cslug] = {"n": pag["nome"] if pag else nome_campo(u["campo"]),
                         "uf": pag["estado"]["uf"] if pag else str(u["estado"]),
                         "g": pag["grupo"] if pag else ng(u["operador"]), "pg": 1 if pag else 0}
        (pasta / f"{cslug}.json").write_text(
            json.dumps({"inicio": meses[0], "pocos": lista}, ensure_ascii=False, separators=(",", ":")), "utf-8")

    indice.sort(key=lambda x: (-x[2], -x[3], x[0]))
    (pasta / "indice.json").write_text(
        json.dumps({"meses": [meses[0], ultimo], "campos": campos, "pocos": indice}, ensure_ascii=False,
                   separators=(",", ":")), "utf-8")
    arquivos = list(pasta.iterdir())
    maior = max(arquivos, key=lambda f: f.stat().st_size)
    return {"pocos": len(indice), "ativos": sum(x[2] for x in indice), "campos": len(campos),
            "mb": round(sum(f.stat().st_size for f in arquivos) / 1e6, 1),
            "maior": f"{maior.name} {maior.stat().st_size / 1e6:.1f} MB",
            "indice_mb": round((pasta / "indice.json").stat().st_size / 1e6, 2)}

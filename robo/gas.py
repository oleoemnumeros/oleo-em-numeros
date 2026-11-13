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
import calendar
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd

from . import config, grupos
from .agregar import UFS
from .baixar import _Links, _get, _sessao
from .util import nome_bonito, nome_campo, sem_acento, slug

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


def baixar_boletins(destino: Path = PASTA, quantos: int = N_BOLETINS) -> list[str]:
    """Lê os boletins mensais mais recentes e guarda só o texto das páginas em boletins.json (os PDFs
    têm 3 a 7 MB cada). Só baixa o que ainda não está no arquivo; o boletim do mês corrente costuma sair
    com atraso, então tenta do mês atual para trás até ter `quantos`."""
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / "boletins.json"
    textos = json.loads(arq.read_text("utf-8")) if arq.exists() else {}
    s = _sessao()
    hoje = date.today()
    a, m = hoje.year, hoje.month
    achados = 0
    for _ in range(quantos + 6):
        chave = f"{a}-{m:02d}"
        if chave in textos:
            achados += 1
        else:
            try:
                pdf = _get(s, BOLETIM.format(ano=a, mes=MESES_PT[m - 1]), tentativas=1).content
                textos[chave] = texto_paginas(pdf)
                achados += 1
                print(f"boletim {chave}: {len(pdf) / 1e6:.1f} MB, {len(textos[chave])} páginas")
            except RuntimeError:
                pass
        if achados >= quantos:
            break
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    textos = dict(sorted(textos.items())[-quantos:])
    arq.write_text(json.dumps(textos, ensure_ascii=False), "utf-8")
    return list(textos)


def texto_paginas(pdf: bytes | Path) -> list[str]:
    import pdfplumber
    fonte = io.BytesIO(pdf) if isinstance(pdf, bytes) else pdf
    with pdfplumber.open(fonte) as doc:
        return [(p.extract_text() or "") for p in doc.pages]


MES_ABREV = {m: i + 1 for i, m in enumerate(["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"])}
DESTINOS = ["producao", "queima", "reinjecao", "consumo"]
BACIAS_2 = {"espirito santo", "tucano sul", "rio do peixe", "sergipe alagoas", "sergipe-alagoas"}
_UF_POR_NOME = {sem_acento(nome).upper(): uf for uf, (_, nome) in UFS.items()}


def _ler_csv(caminho: Path, destino: str) -> pd.DataFrame:
    df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig", decimal=",")
    df.columns = ["ano", "mes", "regiao", "estado", "produto", "loc", destino]
    df["mes"] = df["mes"].astype(str).str.strip().str.upper().map(MES_ABREV)
    df["loc"] = df["loc"].astype(str).str.strip().str.upper()
    df["estado"] = df["estado"].astype(str).str.strip().str.upper()
    return df.dropna(subset=["ano", "mes"]).astype({"ano": int, "mes": int})[["ano", "mes", "estado", "loc", destino]]


def balanco(pasta: Path = PASTA) -> pd.DataFrame:
    """Uma linha por mês, estado e ambiente, em mil m³ no mês. O gás disponível é o resto do balanço
    (produção − queima − reinjeção − consumo): é o que o boletim da ANP publica e fecha com a produção;
    a coluna 'disponível' do CSV difere cerca de 1%."""
    t = None
    for d in DESTINOS:
        x = _ler_csv(pasta / f"{d}.csv", d).groupby(["ano", "mes", "estado", "loc"], as_index=False)[d].sum()
        t = x if t is None else t.merge(x, how="outer", on=["ano", "mes", "estado", "loc"])
    t = t.fillna(0.0)
    t["disponivel"] = (t["producao"] - t["queima"] - t["reinjecao"] - t["consumo"]).clip(lower=0)
    t["periodo"] = t["ano"].map(str) + "-" + t["mes"].map(lambda m: f"{m:02d}")
    t["dias"] = [calendar.monthrange(a, m)[1] for a, m in zip(t["ano"], t["mes"])]
    return t


def _por_dia(t: pd.DataFrame, meses: list[str]) -> dict:
    """Médias diárias (mil m³/d) de cada destino, na ordem de `meses`; mês sem registro vira 0."""
    g = t.groupby("periodo")[DESTINOS + ["disponivel"]].sum()
    dias = t.groupby("periodo")["dias"].first()
    out = {}
    for c in DESTINOS + ["disponivel"]:
        out[c] = [round(float(g[c].get(m, 0.0) / dias[m]), 2) if m in dias.index else 0.0 for m in meses]
    return out


def _numero(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


_LINHA = re.compile(r"^(?P<antes>.+?)\s+(?P<loc>Mar|Terra)\s+(?P<op>.+?)\s+(?P<v>\d{1,3}(?:\.\d{3})*(?:,\d+)?)$")


def _separar_campo_bacia(antes: str) -> tuple[str, str]:
    partes = antes.split()
    if len(partes) >= 3 and sem_acento(" ".join(partes[-2:])).casefold() in BACIAS_2:
        return " ".join(partes[:-2]), " ".join(partes[-2:])
    return " ".join(partes[:-1]), partes[-1]


def ler_queima_boletim(paginas: list[str], regras: list | None = None) -> list[dict]:
    """Os 20 campos com maior queima de um boletim, a partir do texto das páginas (ou [] se não achar)."""
    regras = grupos.carregar() if regras is None else regras
    nomes_grupos = {g for _, g in regras}
    for texto in paginas:
        # o título muda de um mês para outro ("20 campos com maior queima…", "Queima de gás por campo")
        if not (re.search(r"queima de g[aá]s (natural )?por campo", texto, re.I) and "Operador" in texto and "Localiza" in texto):
            continue
        linhas = []
        for ln in texto.splitlines():
            m = _LINHA.match(ln.strip())
            if not m or ln.strip().startswith("Total"):
                continue
            campo, bacia = _separar_campo_bacia(m["antes"])
            if not campo:
                continue
            g = grupos.grupo_de(m["op"], regras)
            g = g if g in nomes_grupos else nome_bonito(g)
            linhas.append({"campo": nome_campo(campo), "chave": campo, "bacia": nome_bonito(bacia), "ambiente": m["loc"].upper(),
                           "operador": g, "operador_slug": slug(g), "queima": _numero(m["v"])})
        if linhas:
            for i, r in enumerate(sorted(linhas, key=lambda r: -r["queima"]), 1):
                r["posicao"] = i
            return sorted(linhas, key=lambda r: -r["queima"])
    return []


_LINHA_NACIONAL = re.compile(r"^(\d\d)/(\d{4}) ([\d.,]+) ([\d.,]+) ([\d.,]+) ([\d.,]+) ([\d.,]+) [\d,]+%")


def ler_nacional_boletim(paginas: list[str]) -> dict | None:
    """Balanço nacional do mês do boletim (primeira linha da tabela 'Histórico da movimentação de gás natural'),
    em mil m³/d. É a base do ranking de queima, que fecha com o total do próprio boletim."""
    for texto in paginas:
        if "Histórico da movimentação de gás natural" not in texto:
            continue
        for ln in texto.splitlines():
            m = _LINHA_NACIONAL.match(ln.strip())
            if m:
                cons, q, inj, disp, prod = (_numero(x) for x in m.groups()[2:7])
                return {"periodo": f"{m[2]}-{m[1]}", "consumo": cons, "queima": q, "reinjecao": inj,
                        "disponivel": disp, "producao": prod}
    return None


def processar(pasta: Path, saida: Path) -> dict:
    t = balanco(pasta)
    t = t[t["ano"] >= 2000]   # a queima, a reinjeção e o consumo só existem a partir de 2000
    meses = sorted(t["periodo"].unique())
    ultimo = meses[-1]
    brasil = _por_dia(t, meses)
    estados = []
    for est, x in t.groupby("estado"):
        uf = _UF_POR_NOME.get(sem_acento(est).upper())
        if not uf:
            continue
        s = _por_dia(x, meses)
        if s["producao"][-1] <= 0 and max(s["producao"][-12:]) <= 0:
            continue
        estados.append({"uf": uf, "nome": UFS[uf][1], "slug": slug(UFS[uf][1]), **s})
    estados.sort(key=lambda e: -e["producao"][-1])
    ambientes = {a: {k: v[-1] for k, v in _por_dia(x, [ultimo]).items()} for a, x in t[t["periodo"] == ultimo].groupby("loc")}

    queima = {"meses": {}, "nacional": {}, "unidade": "mil m³/d"}
    arq = pasta / "boletins.json"
    textos = json.loads(arq.read_text("utf-8")) if arq.exists() else {}
    for mes, paginas in sorted(textos.items()):
        r = ler_queima_boletim(paginas)
        if r:
            queima["meses"][mes] = r
            nac = ler_nacional_boletim(paginas)
            if nac and nac["periodo"] == mes:
                queima["nacional"][mes] = nac
    saida.parent.mkdir(parents=True, exist_ok=True)
    resultado = {"meses": meses, "ultimo": ultimo, "unidade": "mil m³/d", "brasil": brasil, "ambientes": ambientes,
                 "estados": estados, "queima": queima}
    saida.write_text(json.dumps(resultado, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return resultado


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
    r = processar(PASTA, config.PROCESSADOS / "gas.json")
    print(f"Gás: {r['meses'][0]} a {r['ultimo']}, {len(r['estados'])} estados; queima por campo em {len(r['queima']['meses'])} meses")


if __name__ == "__main__":
    sys.exit(main())

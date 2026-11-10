"""Resultados trimestrais das petroleiras listadas, a partir dos dados abertos da CVM.

Fonte: ITR (trimestres) e DFP (ano) consolidados, em dados.cvm.gov.br.
Só entra o que já foi entregue à CVM, ou seja, depois da divulgação oficial.

Regras de cálculo (as mesmas descritas na metodologia do site):
- Fluxos (receita, EBITDA, lucro, capex) por trimestre. A DRE do ITR traz o
  trimestre; o fluxo de caixa e a DVA vêm acumulados no ano, então o trimestre
  é a diferença entre acumulados. O 4º trimestre é o ano (DFP) menos 9 meses.
- EBITDA calculado = resultado antes do resultado financeiro e dos tributos
  (conta 3.05) + depreciação, amortização e exaustão (DVA, conta 7.04.01).
  Não é o "EBITDA ajustado" que as empresas divulgam.
- Lucro líquido = atribuído aos acionistas da controladora (3.11.01), como as
  empresas divulgam; sem essa conta, o consolidado (3.11).
- Dívida líquida = empréstimos, financiamentos e debêntures (2.01.04 + 2.02.01,
  sem os arrendamentos que algumas empresas registram ali, 2.01.04.03 e 2.02.01.03)
  menos caixa e aplicações financeiras de curto prazo (1.01.01 + 1.01.02).
  Arrendamentos (IFRS 16) aparecem à parte, estejam onde estiverem no balanço,
  porque cada empresa os registra numa conta diferente.
- Capex = investimento pago no período (fluxo de caixa): imobilizado, intangível,
  exploração e desenvolvimento. Compra de empresas ou de participação em campos fica fora.

Uso:
    python -m robo.cvm            # baixa, calcula e grava
    python -m robo.cvm --mostrar  # imprime a tabela de cada empresa
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from . import config
from .util import sem_acento

PASTA = config.BRUTOS / "cvm"
SAIDA = config.PROCESSADOS / "financeiro.json"
URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{doc}/DADOS/{doc_min}_cia_aberta_{ano}.zip"
ANO_INICIAL = 2021

# grupo como aparece no site -> como achar a empresa no cadastro da CVM e o ticker
EMPRESAS = {
    "Petrobras": {"nome": r"PETROLEO BRASILEIRO", "ticker": "PETR4"},
    "PRIO": {"nome": r"^(PRIO S\.?A|PETRO RIO S\.?A|PETRORIO)", "ticker": "PRIO3"},
    # a Enauta incorporou a 3R em agosto de 2024 e virou Brava: antes disso, os números não são comparáveis
    "Brava Energia": {"nome": r"^(BRAVA ENERGIA|ENAUTA PARTICIPACOES)", "ticker": "BRAV3", "desde": "2024T4"},
    "PetroRecôncavo": {"nome": r"^PETRORECONCAVO", "ticker": "RECV3"},
}
DEMONSTRACOES = ["DRE_con", "BPA_con", "BPP_con", "DFC_MI_con", "DVA_con"]

CAPEX_SIM = re.compile(r"imobilizado|intangivel|exploracao|desenvolvimento|ativo de producao")
CAPEX_NAO = re.compile(r"venda|alienac|recebimento|baixa|controlad|coligad|participac|combinac|aquisicao de empresa|"
                       r"aquisicao de negocio|adiantamento|caixa liquido|ativos? (?:de )?oleo|mantido para venda")


def _filtrar_zip(conteudo: bytes, doc: str, ano: int) -> pd.DataFrame:
    """Do ZIP inteiro da CVM (dezenas de MB), fica só o que é das empresas acompanhadas."""
    partes = []
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        for dem in DEMONSTRACOES:
            nomes = [n for n in z.namelist() if n.endswith(f"_{dem}_{ano}.csv")]
            if not nomes:
                continue
            with z.open(nomes[0]) as f:
                d = pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str)
            d = d[d["DENOM_CIA"].map(_empresa).notna()]
            d["DOC"], d["DEM"] = doc, dem
            partes.append(d)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def baixar(anos: list[int], pasta: Path = PASTA) -> list[Path]:
    """Baixa ITR e DFP e guarda só as linhas das empresas. Anos fechados não são baixados de novo."""
    pasta.mkdir(parents=True, exist_ok=True)
    hoje = date.today().year
    for ano in anos:
        for doc in ("ITR", "DFP"):
            destino = pasta / f"{doc.lower()}_{ano}.csv.gz"
            if destino.exists() and ano < hoje - 1:
                continue
            url = URL.format(doc=doc, doc_min=doc.lower(), ano=ano)
            for tentativa in range(3):
                try:
                    r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=300)
                    if r.status_code == 404:
                        break                      # DFP do ano corrente ainda não existe
                    r.raise_for_status()
                    d = _filtrar_zip(r.content, doc, ano)
                    d.to_csv(destino, index=False, compression="gzip")
                    print(f"CVM: {doc} {ano}: {len(r.content) / 1e6:.0f} MB baixados, {len(d)} linhas das empresas")
                    break
                except (requests.RequestException, zipfile.BadZipFile) as e:
                    if tentativa == 2:
                        print(f"CVM: falhou {url}: {e}")
                    time.sleep(5)
    return sorted(pasta.glob("*.csv.gz"))


def _empresa(denom: str):
    n = sem_acento(str(denom)).upper().strip()
    for grupo, e in EMPRESAS.items():
        if re.search(e["nome"], n):
            return grupo
    return None


def carregar(arquivos: list[Path]) -> pd.DataFrame:
    partes = [pd.read_csv(a, dtype=str) for a in arquivos if a.stat().st_size > 0]
    partes = [p for p in partes if not p.empty]
    if not partes:
        return pd.DataFrame()
    d = pd.concat(partes, ignore_index=True)
    d["grupo"] = d["DENOM_CIA"].map(_empresa)
    # o CNPJ de cada grupo é o da entrega mais recente (nome de empresa muda; CNPJ não)
    cnpj = d.sort_values("DT_REFER").groupby("grupo")["CNPJ_CIA"].last()
    d = d[d["CNPJ_CIA"] == d["grupo"].map(cnpj)]
    d = d[sem_acento_serie(d["ORDEM_EXERC"]).str.upper().str.startswith("ULTIMO")]
    # reapresentação: fica a versão mais nova de cada entrega
    d["VERSAO"] = pd.to_numeric(d["VERSAO"], errors="coerce")
    d = d[d["VERSAO"] == d.groupby(["CNPJ_CIA", "DOC", "DT_REFER"])["VERSAO"].transform("max")]
    escala = d["ESCALA_MOEDA"].str.upper().map({"MIL": 1.0, "UNIDADE": 0.001}).fillna(1.0)
    d["valor"] = pd.to_numeric(d["VL_CONTA"], errors="coerce") * escala      # em R$ mil
    d["desc"] = sem_acento_serie(d["DS_CONTA"]).str.lower()
    return d


def sem_acento_serie(s: pd.Series) -> pd.Series:
    return s.fillna("").map(sem_acento)


def _trimestre(dt_fim: str) -> str:
    a, m = int(dt_fim[:4]), int(dt_fim[5:7])
    return f"{a}T{(m - 1) // 3 + 1}"


def _conta(d, codigo):
    return d[d["CD_CONTA"] == codigo]


def _capex(dfc: pd.DataFrame) -> pd.Series:
    inv = dfc[dfc["CD_CONTA"].str.match(r"^6\.02\.\d+") & dfc["desc"].str.contains(CAPEX_SIM)
              & ~dfc["desc"].str.contains(CAPEX_NAO)]
    inv = inv[inv["CD_CONTA"].str.count(r"\.") == 2]          # só o primeiro nível de detalhe (evita somar em dobro)
    return -inv.groupby(["DT_INI_EXERC", "DT_FIM_EXERC"])["valor"].sum()


def _arrendamentos_fora(bp: pd.DataFrame) -> float:
    """Passivo de arrendamento registrado fora de 'Empréstimos e Financiamentos' (em outras obrigações).
    Fica só a conta mais detalhada, para não somar a conta-mãe e a filha."""
    a = bp[bp["desc"].str.contains("arrendament") & ~bp["CD_CONTA"].str.match(r"^2\.0[12]\.0[14]")
           & bp["CD_CONTA"].str.match(r"^2\.0[12]\.")]
    if a.empty:
        return 0.0
    codigos = set(a["CD_CONTA"])
    folha = [not any(o != c and o.startswith(c + ".") for o in codigos) for c in a["CD_CONTA"]]
    return float(a.loc[folha, "valor"].sum())


def calcular(d: pd.DataFrame) -> dict:
    """Uma linha por trimestre para cada grupo, valores em R$ milhões."""
    saida = {}
    for grupo, g in d.groupby("grupo"):
        linhas = {}
        # --- fluxos acumulados no ano (YTD), por data de fim ---
        acum = {}
        dre = g[g["DEM"] == "DRE_con"]
        conta_lucro = "3.11.01" if (dre["CD_CONTA"] == "3.11.01").any() else "3.11"
        for dem, codigo, chave in (("DRE_con", "3.01", "receita"), ("DRE_con", "3.05", "ebit"),
                                   ("DRE_con", conta_lucro, "lucro"), ("DVA_con", "7.04.01", "da")):
            x = _conta(g[g["DEM"] == dem], codigo)
            x = x[x["DT_INI_EXERC"].str[5:] == "01-01"]           # acumulado desde 1º de janeiro
            for fim, v in x.groupby("DT_FIM_EXERC")["valor"].sum().items():
                acum.setdefault(fim, {})[chave] = v
        dfc = g[g["DEM"] == "DFC_MI_con"]
        for (ini, fim), v in _capex(dfc[dfc["DT_INI_EXERC"].str[5:] == "01-01"]).items():
            acum.setdefault(fim, {})["capex"] = v
        # --- trimestre = acumulado do trimestre menos acumulado do trimestre anterior (mesmo ano) ---
        fins = sorted(acum)
        for fim in fins:
            ano, mes = fim[:4], int(fim[5:7])
            ant = [f for f in fins if f[:4] == ano and int(f[5:7]) == mes - 3]
            if mes != 3 and not ant:
                continue                                           # falta o trimestre anterior: não dá para separar
            base = acum[ant[0]] if mes != 3 else {}
            linha = {}
            for k in ("receita", "ebit", "lucro", "da", "capex"):
                if k in acum[fim] and (mes == 3 or k in base):
                    linha[k] = (acum[fim][k] - base.get(k, 0.0)) / 1000
            linhas[_trimestre(fim)] = {"fim": fim, **linha}
        # --- balanço no fim de cada trimestre ---
        for fim, bp in g[g["DEM"].isin(["BPA_con", "BPP_con"])].groupby("DT_FIM_EXERC"):
            v = lambda c: float(_conta(bp, c)["valor"].sum())  # noqa: E731
            t = _trimestre(fim)
            if t not in linhas:
                continue
            caixa = v("1.01.01") + v("1.01.02")
            arrend_dentro = v("2.01.04.03") + v("2.02.01.03")
            divida = v("2.01.04") + v("2.02.01") - arrend_dentro
            linhas[t].update({"caixa": caixa / 1000, "divida_bruta": divida / 1000,
                              "divida_liquida": (divida - caixa) / 1000,
                              "arrendamentos": (arrend_dentro + _arrendamentos_fora(bp)) / 1000})
        # --- EBITDA, margem e alavancagem ---
        ordem = sorted(linhas, key=lambda t: linhas[t]["fim"])
        for i, t in enumerate(ordem):
            l = linhas[t]
            if "ebit" in l and "da" in l:
                l["ebitda"] = l["ebit"] + abs(l["da"])
                l["margem_ebitda"] = l["ebitda"] / l["receita"] * 100 if l.get("receita") else None
            ult4 = ordem[max(0, i - 3): i + 1]
            if len(ult4) == 4 and all("ebitda" in linhas[x] for x in ult4) and "divida_liquida" in l:
                e12 = sum(linhas[x]["ebitda"] for x in ult4)
                l["ebitda_12m"] = e12
                l["alavancagem"] = l["divida_liquida"] / e12 if e12 > 0 else None
                l["alavancagem_com_arrend"] = (l["divida_liquida"] + l["arrendamentos"]) / e12 if e12 > 0 else None
        desde = EMPRESAS[grupo].get("desde", "")
        ordem = [t for t in ordem if t >= desde]
        saida[grupo] = {"ticker": EMPRESAS[grupo]["ticker"],
                        "empresa_cvm": g.sort_values("DT_REFER")["DENOM_CIA"].iloc[-1],
                        "cnpj": g["CNPJ_CIA"].iloc[-1],
                        "trimestres": [{"trimestre": t, **linhas[t]} for t in ordem]}
    return saida


def executar(baixar_dados: bool = True) -> dict:
    hoje = date.today().year
    arquivos = baixar(list(range(ANO_INICIAL, hoje + 1))) if baixar_dados else sorted(PASTA.glob("*.csv.gz"))
    d = carregar(arquivos)
    if d.empty:
        raise RuntimeError("Nenhum dado da CVM encontrado para as empresas listadas.")
    r = calcular(d)
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(r, ensure_ascii=False, indent=1), "utf-8")
    return r


def contas(ultimo_so: bool = True) -> str:
    """Para conferir a regra: contas de dívida, arrendamento e investimento do último trimestre de cada empresa."""
    d = carregar(sorted(PASTA.glob("*.csv.gz")))
    linhas = []
    for grupo, g in d.groupby("grupo"):
        fim = g["DT_FIM_EXERC"].max()
        u = g[g["DT_FIM_EXERC"] == fim]
        linhas.append(f"## {grupo} {fim}")
        bp = u[u["DEM"] == "BPP_con"]
        sel = bp[bp["CD_CONTA"].str.match(r"^2\.0[12]\.0[1-5]") & (bp["CD_CONTA"].str.count(r"\.") <= 4)
                 & (bp["CD_CONTA"].str.match(r"^2\.0[12]\.0[14]") | bp["desc"].str.contains("arrend"))]
        for _, r in sel.iterrows():
            linhas.append(f"  BP {r['CD_CONTA']:<16} {r['DS_CONTA'][:60]:<60} {r['valor'] / 1000:>12,.0f}")
        dfc = u[(u["DEM"] == "DFC_MI_con") & u["CD_CONTA"].str.match(r"^6\.02\.")]
        for _, r in dfc.iterrows():
            marca = "*" if (CAPEX_SIM.search(r["desc"]) and not CAPEX_NAO.search(r["desc"]) and r["CD_CONTA"].count(".") == 2) else " "
            linhas.append(f" {marca}DFC {r['CD_CONTA']:<15} {r['DS_CONTA'][:60]:<60} {r['valor'] / 1000:>12,.0f}")
    return "\n".join(linhas)


def tabela(r: dict, ultimos: int = 6) -> str:
    linhas = []
    for grupo, e in r.items():
        linhas += [f"## {grupo} ({e['ticker']}) · {e['empresa_cvm']}", "",
                   "| Tri | Receita | EBIT | D&A | EBITDA | Margem | Lucro | Capex | Caixa | Dív. bruta | Dív. líq. | Arrend. | Alav. | Alav. c/ arrend. |",
                   "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        f = lambda v, c=0: "—" if v is None else f"{v:,.{c}f}"  # noqa: E731
        for t in e["trimestres"][-ultimos:]:
            linhas.append(f"| {t['trimestre']} | {f(t.get('receita'))} | {f(t.get('ebit'))} | {f(t.get('da'))} | "
                          f"{f(t.get('ebitda'))} | {f(t.get('margem_ebitda'), 1)} | {f(t.get('lucro'))} | {f(t.get('capex'))} | "
                          f"{f(t.get('caixa'))} | {f(t.get('divida_bruta'))} | {f(t.get('divida_liquida'))} | "
                          f"{f(t.get('arrendamentos'))} | {f(t.get('alavancagem'), 2)} | {f(t.get('alavancagem_com_arrend'), 2)} |")
        linhas.append("")
    return "\n".join(linhas)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mostrar", action="store_true")
    p.add_argument("--sem-baixar", action="store_true")
    a = p.parse_args(argv)
    r = executar(baixar_dados=not a.sem_baixar)
    resumo = ", ".join(g + " " + str(len(e["trimestres"])) + " trimestres" for g, e in r.items())
    print("CVM: " + resumo)
    if a.mostrar:
        print(tabela(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Royalties e participação especial recebidos por estados e municípios, a partir da ANP.

Fontes (todas públicas, da ANP):
- Dados abertos de participações governamentais: royalties por município e por
  estado, um CSV por ano, por mês de pagamento; participação especial por
  município e por estado, um CSV com o histórico.
- Ano corrente, ainda sem CSV: a planilha "valor mensal dos royalties dos
  beneficiários" (aba TOTAL = concessão + partilha).
- Participação especial dos trimestres que ainda não estão no CSV: a planilha
  de distribuição de cada trimestre.

O eixo do tempo é o mês em que o dinheiro foi pago ao beneficiário.

Uso:
    python -m robo.royalties            # baixa, lê e grava dados/processados/royalties.json
    python -m robo.royalties --mostrar  # e imprime um resumo para conferência
"""
import argparse
import csv
import difflib
import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from . import config
from .agregar import UFS
from .util import nome_bonito, sem_acento, slug

PASTA = config.BRUTOS / "royalties"
SAIDA = config.PROCESSADOS / "royalties.json"
ANO_INICIAL = 2018
PAGINA_DADOS = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/participacoes-governamentais"
PAGINA_ROYALTIES = "https://www.gov.br/anp/pt-br/assuntos/royalties-e-outras-participacoes/royalties"
PAGINA_PE = "https://www.gov.br/anp/pt-br/assuntos/royalties-e-outras-participacoes/participacao-especial"
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36")
MESES = {"JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8,
         "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12}
UF_POR_NOME = {sem_acento(nome).lower(): uf for uf, (_, nome) in UFS.items()}


# ---------------- download ----------------

def _get(url: str, tentativas: int = 3) -> bytes:
    for t in range(tentativas):
        try:
            r = requests.get(url, headers={"User-Agent": NAVEGADOR}, timeout=180)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if t == tentativas - 1:
                raise
            time.sleep(5)


def _links(url: str) -> list[tuple[str, str]]:
    html = _get(url).decode("utf-8", errors="replace")
    return [(re.sub(r"<[^>]+>|\s+", " ", t).strip(), h)
            for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)]


def baixar(pasta: Path = PASTA) -> dict:
    """Baixa o que falta. Anos fechados ficam no cache; o ano corrente e o anterior são rebaixados."""
    pasta.mkdir(parents=True, exist_ok=True)
    hoje = date.today().year
    alvo = {}
    for texto, h in _links(PAGINA_DADOS):
        m = re.search(r"royalties[-_]municipios?[-_](\d{4})\.csv$", h)
        if m and int(m.group(1)) >= ANO_INICIAL:
            alvo[f"mun_{m.group(1)}.csv"] = h
        m = re.search(r"royalties[-_]estados?[-_](\d{4})\.csv$", h)
        if m and int(m.group(1)) >= ANO_INICIAL:
            alvo[f"est_{m.group(1)}.csv"] = h
        if h.endswith("pe_municipio.csv"):
            alvo["pe_municipio.csv"] = h
        if h.endswith("pe_estado.csv"):
            alvo["pe_estado.csv"] = h
    anos_csv = {int(n[4:8]) for n in alvo if n.startswith("mun_")}
    for texto, h in _links(PAGINA_ROYALTIES):
        m = re.search(r"valor-mensal-dos-royalties-dos-beneficiarios/(\d{4})/[^/]+\.xlsx$", h)
        if m and int(m.group(1)) > max(anos_csv or {0}):
            alvo[f"tabela_{m.group(1)}.xlsx"] = h
    for texto, h in _links(PAGINA_PE):
        m = re.search(r"(\d)º\s*Trimestre de (\d{4})", texto, re.I)
        if m and h.lower().endswith((".xls", ".xlsx")):
            alvo[f"pe_{m.group(2)}T{m.group(1)}{Path(h).suffix.lower()}"] = h
    baixados = 0
    for nome, url in sorted(alvo.items()):
        destino = pasta / nome
        ano = re.search(r"(\d{4})", nome)
        fechado = ano and int(ano.group(1)) < hoje - 1 and not nome.startswith("pe_m") and not nome.startswith("pe_e")
        if destino.exists() and (fechado or nome.startswith("pe_2")):
            continue
        try:
            destino.write_bytes(_get(url))
            baixados += 1
        except requests.RequestException as e:
            print(f"Royalties: falhou {url}: {e}")
    print(f"Royalties: {len(alvo)} arquivos na ANP, {baixados} baixados agora")
    return alvo


# ---------------- leitura ----------------

def _texto_csv(caminho: Path) -> str:
    b = caminho.read_bytes()
    return b.decode("utf-8-sig") if b[:3] == b"\xef\xbb\xbf" else b.decode("latin-1")


def _data(v) -> str | None:
    """Data da ANP (número serial do Excel ou dd/mm/aaaa) como 'AAAA-MM'."""
    v = str(v).strip()
    if not v or v.lower() == "nan":
        return None
    if re.fullmatch(r"\d+(\.0+)?", v):
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(float(v)))).strftime("%Y-%m")
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%Y"):
        try:
            return pd.to_datetime(v, format=fmt).strftime("%Y-%m")
        except ValueError:
            pass
    return None


def _num(v) -> float:
    v = str(v).strip()
    if not v or v.lower() == "nan":
        return 0.0
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0


def _chave(nome: str, uf: str) -> str:
    return f"{slug(nome)}-{uf.lower()}"


def ler_csv_municipios(caminho: Path) -> pd.DataFrame:
    linhas = list(csv.reader(io.StringIO(_texto_csv(caminho)), delimiter=";"))
    cab = [sem_acento(c).lower().strip() for c in linhas[0]]
    i_cx, i_mun, i_uf = cab.index("caixa"), cab.index("municipio"), cab.index("estado")
    i_val = [i for i, c in enumerate(cab) if c.startswith("royalties")]
    saida = []
    for l in linhas[1:]:
        if len(l) <= max(i_val) or not l[i_mun].strip():
            continue
        uf = l[i_uf].strip().upper()
        nome = re.sub(rf"-{uf.lower()}$", "", l[i_mun].strip().lower())
        mes = _data(l[i_cx])
        if mes:
            saida.append((mes, _chave(nome, uf), nome, uf, sum(_num(l[i]) for i in i_val)))
    return pd.DataFrame(saida, columns=["mes", "chave", "nome_anp", "uf", "valor"])


def ler_csv_estados(caminho: Path) -> pd.DataFrame:
    linhas = list(csv.reader(io.StringIO(_texto_csv(caminho)), delimiter=";"))
    cab = [sem_acento(c).lower().strip() for c in linhas[0]]
    i_cx = cab.index("caixa")
    saida = []
    for l in linhas[1:]:
        mes = _data(l[i_cx]) if len(l) > i_cx else None
        if not mes:
            continue
        for i, c in enumerate(cab):
            uf = UF_POR_NOME.get(c)
            if uf and i < len(l) and _num(l[i]):
                saida.append((mes, uf, _num(l[i])))
    return pd.DataFrame(saida, columns=["mes", "uf", "valor"])


def ler_tabela_ano(caminho: Path, ano: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Planilha do ano corrente, aba TOTAL: blocos de estados e de municípios, 3 colunas por mês."""
    d = pd.read_excel(caminho, sheet_name="TOTAL", header=None, dtype=object)
    col_mes = {}
    for _, linha in d.iterrows():
        for j, v in linha.items():
            m = re.match(r"total\s*-\s*(\w+)", sem_acento(str(v)).strip(), re.I)
            if m and m.group(1).upper() in MESES:
                col_mes[j] = MESES[m.group(1).upper()]
        if col_mes:
            break
    mun, est, secao = [], [], None
    for _, linha in d.iterrows():
        rot = sem_acento(str(linha[1])).strip().upper()
        if rot in ("ESTADOS", "MUNICIPIOS"):
            secao = rot
            continue
        if rot.startswith("TOTAL") or rot in ("", "NAN"):
            continue
        for j, m in col_mes.items():
            v = _num(linha[j])
            if not v:
                continue
            mes = f"{ano}-{m:02d}"
            if secao == "ESTADOS" and rot.lower() in UF_POR_NOME:
                est.append((mes, UF_POR_NOME[rot.lower()], v))
            elif secao == "MUNICIPIOS":
                mm = re.match(r"^(.+)-([A-Z]{2})$", rot)
                if mm and mm.group(2) in UFS:
                    nome = mm.group(1).lower()
                    mun.append((mes, _chave(nome, mm.group(2)), nome, mm.group(2), v))
    return (pd.DataFrame(mun, columns=["mes", "chave", "nome_anp", "uf", "valor"]),
            pd.DataFrame(est, columns=["mes", "uf", "valor"]))


def _tri(mes: str) -> str:
    return f"{mes[:4]}T{(int(mes[5:7]) - 1) // 3 + 1}"


def ler_pe_csv(caminho: Path, municipio: bool) -> pd.DataFrame:
    linhas = list(csv.reader(io.StringIO(_texto_csv(caminho)), delimiter=";"))
    cab = [sem_acento(c).lower().strip() for c in linhas[0]]
    i_comp, i_cx, i_uf, i_val = cab.index("competencia"), cab.index("caixa"), cab.index("estado"), cab.index("valor")
    i_mun = cab.index("municipio") if municipio else None
    saida = []
    for l in linhas[1:]:
        if len(l) <= i_val:
            continue
        uf = UF_POR_NOME.get(sem_acento(l[i_uf]).lower().strip()) or l[i_uf].strip().upper()
        comp = _data(l[i_comp])
        if not comp or uf not in UFS:
            continue
        chave = _chave(l[i_mun].strip().lower(), uf) if municipio else uf
        saida.append((_tri(comp), _data(l[i_cx]), chave, l[i_mun].strip().lower() if municipio else "", uf, _num(l[i_val])))
    return pd.DataFrame(saida, columns=["tri", "mes", "chave", "nome_anp", "uf", "valor"])


def ler_pe_planilha(caminho: Path, ano: int, tri: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Distribuição de um trimestre: blocos 'Estados' (siglas) e 'Municípios' (NOME-UF)."""
    d = pd.read_excel(caminho, sheet_name=0, header=None, dtype=object)
    pago = None
    for _, l in d.iterrows():
        if "pagamento" in sem_acento(str(l[0])).lower():
            pago = _data(str(l[1])[:10]) if not isinstance(l[1], (pd.Timestamp,)) else l[1].strftime("%Y-%m")
    mun, est, secao = [], [], None
    for _, l in d.iterrows():
        rot = sem_acento(str(l[0])).strip()
        if rot.lower() in ("estados", "municipios"):
            secao = rot.lower()
            continue
        v = _num(l[1])
        if not v:
            continue
        if secao == "estados" and rot.upper() in UFS:
            est.append((f"{ano}T{tri}", pago, rot.upper(), "", rot.upper(), v))
        elif secao == "municipios":
            mm = re.match(r"^(.+)-([A-Z]{2})$", rot.upper())
            if mm and mm.group(2) in UFS:
                mun.append((f"{ano}T{tri}", pago, _chave(mm.group(1).lower(), mm.group(2)), mm.group(1).lower(), mm.group(2), v))
    cols = ["tri", "mes", "chave", "nome_anp", "uf", "valor"]
    return pd.DataFrame(mun, columns=cols), pd.DataFrame(est, columns=cols)


# ---------------- nomes oficiais (IBGE) ----------------

def carregar_ibge(arq: Path = config.MANUAIS / "municipios.csv") -> dict:
    t = pd.read_csv(arq, dtype=str, comment="#")
    return {_chave(n, uf): {"nome": n, "uf": uf, "ibge": c} for c, n, uf in zip(t["ibge"], t["nome"], t["uf"])}


def casar_nomes(chaves: dict, ibge: dict) -> tuple[dict, list]:
    """chave ANP -> nome oficial. Sem par exato, procura o nome mais parecido no mesmo estado."""
    por_uf = {}
    for k, v in ibge.items():
        por_uf.setdefault(v["uf"], {})[k] = v
    saida, sem_par = {}, []
    for k, (nome_anp, uf) in chaves.items():
        if k in ibge:
            saida[k] = ibge[k]
            continue
        candidatos = por_uf.get(uf, {})
        parecido = difflib.get_close_matches(k, list(candidatos), n=1, cutoff=0.85)
        if parecido:
            saida[k] = candidatos[parecido[0]]
        else:
            saida[k] = {"nome": nome_bonito(nome_anp.upper()), "uf": uf, "ibge": None}
            sem_par.append(f"{nome_anp}-{uf}")
    return saida, sem_par


# ---------------- montagem ----------------

def montar(pasta: Path = PASTA) -> dict:
    mun, est, pe_m, pe_e = [], [], [], []
    anos_csv = sorted(int(p.stem[4:8]) for p in pasta.glob("mun_*.csv"))
    for p in sorted(pasta.glob("mun_*.csv")):
        mun.append(ler_csv_municipios(p))
    for p in sorted(pasta.glob("est_*.csv")):
        est.append(ler_csv_estados(p))
    for p in sorted(pasta.glob("tabela_*.xlsx")):
        ano = int(p.stem[-4:])
        if ano > max(anos_csv or [0]):
            m, e = ler_tabela_ano(p, ano)
            mun.append(m)
            est.append(e)
    if (pasta / "pe_municipio.csv").exists():
        pe_m.append(ler_pe_csv(pasta / "pe_municipio.csv", True))
    if (pasta / "pe_estado.csv").exists():
        pe_e.append(ler_pe_csv(pasta / "pe_estado.csv", False))
    tri_csv = set(pd.concat(pe_m)["tri"]) if pe_m else set()
    for p in sorted(pasta.glob("pe_*T*.xls*")):
        m = re.match(r"pe_(\d{4})T(\d)", p.stem)
        if m and f"{m.group(1)}T{m.group(2)}" not in tri_csv:
            a, b = ler_pe_planilha(p, int(m.group(1)), int(m.group(2)))
            pe_m.append(a)
            pe_e.append(b)
    mun = pd.concat(mun, ignore_index=True)
    mun = mun[mun["mes"] >= f"{ANO_INICIAL}-01"]
    est = pd.concat(est, ignore_index=True) if est else pd.DataFrame(columns=["mes", "uf", "valor"])
    est = est[est["mes"] >= f"{ANO_INICIAL}-01"]
    pe_m = pd.concat(pe_m, ignore_index=True) if pe_m else pd.DataFrame(columns=["tri", "mes", "chave", "nome_anp", "uf", "valor"])
    pe_e = pd.concat(pe_e, ignore_index=True) if pe_e else pd.DataFrame(columns=["tri", "mes", "chave", "nome_anp", "uf", "valor"])
    pe_m = pe_m[pe_m["tri"] >= f"{ANO_INICIAL}T1"]
    pe_e = pe_e[pe_e["tri"] >= f"{ANO_INICIAL}T1"]

    ultimo = mun["mes"].max()
    meses = sorted({f"{a}-{m:02d}" for a in range(ANO_INICIAL, int(ultimo[:4]) + 1) for m in range(1, 13)
                    if f"{a}-{m:02d}" <= ultimo})
    idx = {m: i for i, m in enumerate(meses)}
    chaves = {}
    for k, n, uf in zip(pd.concat([mun, pe_m])["chave"], pd.concat([mun, pe_m])["nome_anp"], pd.concat([mun, pe_m])["uf"]):
        chaves.setdefault(k, (n, uf))
    nomes, sem_par = casar_nomes(chaves, carregar_ibge())

    # a mesma cidade pode vir com duas grafias da ANP (Parati/Paraty): junta pelo código do IBGE
    unificada = {k: (f"ibge{v['ibge']}" if v["ibge"] else k) for k, v in nomes.items()}
    mun["id"] = mun["chave"].map(unificada)
    pe_m["id"] = pe_m["chave"].map(unificada)
    por_id = {}
    for k, v in nomes.items():
        por_id.setdefault(unificada[k], v)

    municipios = []
    serie_m = mun.groupby(["id", "mes"])["valor"].sum()
    pe_mt = pe_m.groupby(["id", "tri"])["valor"].sum()
    for i in sorted(set(mun["id"]) | set(pe_m["id"])):
        r = [0.0] * len(meses)
        if i in serie_m.index.get_level_values(0):
            for mes, v in serie_m.loc[i].items():
                if mes in idx:
                    r[idx[mes]] = round(float(v), 2)
        pe = {t: round(float(v), 2) for t, v in pe_mt.loc[i].items()} if i in pe_mt.index.get_level_values(0) else {}
        info = por_id[i]
        municipios.append({"nome": info["nome"], "uf": info["uf"], "ibge": info["ibge"],
                           "slug": slug(info["nome"]), "r": r, "pe": pe})
    estados = []
    serie_e = est.groupby(["uf", "mes"])["valor"].sum()
    pe_et = pe_e.groupby(["chave", "tri"])["valor"].sum()
    for uf in sorted(set(est["uf"]) | set(pe_e["chave"])):
        r = [0.0] * len(meses)
        if uf in serie_e.index.get_level_values(0):
            for mes, v in serie_e.loc[uf].items():
                if mes in idx:
                    r[idx[mes]] = round(float(v), 2)
        pe = {t: round(float(v), 2) for t, v in pe_et.loc[uf].items()} if uf in pe_et.index.get_level_values(0) else {}
        estados.append({"uf": uf, "nome": UFS[uf][1], "slug": slug(UFS[uf][1]), "r": r, "pe": pe})
    tris = sorted(set(pe_m["tri"]) | set(pe_e["tri"]))
    return {"meses": meses, "ultimo": ultimo, "trimestres": tris, "ultimo_tri": tris[-1] if tris else None,
            "municipios": municipios, "estados": estados, "sem_par_ibge": sem_par}


def executar(baixar_dados: bool = True) -> dict:
    if baixar_dados:
        baixar()
    r = montar()
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return r


def resumo(r: dict) -> str:
    n = len(r["meses"])
    ult12 = slice(max(0, n - 12), n)
    linhas = [f"Royalties: {len(r['municipios'])} municípios, {len(r['estados'])} estados, "
              f"meses {r['meses'][0]} a {r['ultimo']}, participação especial até {r['ultimo_tri']}"]
    tot_m = sum(sum(m["r"][ult12]) for m in r["municipios"])
    tot_e = sum(sum(e["r"][ult12]) for e in r["estados"])
    linhas.append(f"Últimos 12 meses: municípios R$ {tot_m / 1e9:.2f} bi · estados R$ {tot_e / 1e9:.2f} bi")
    for ano in sorted({m[:4] for m in r["meses"]})[-3:]:
        sel = [i for i, m in enumerate(r["meses"]) if m.startswith(ano)]
        tm = sum(sum(m["r"][i] for i in sel) for m in r["municipios"])
        te = sum(sum(e["r"][i] for i in sel) for e in r["estados"])
        pm = sum(v for m in r["municipios"] for t, v in m["pe"].items() if t.startswith(ano))
        linhas.append(f"  {ano}: royalties municípios R$ {tm / 1e9:.2f} bi, estados R$ {te / 1e9:.2f} bi; "
                      f"PE municípios R$ {pm / 1e9:.2f} bi ({len(sel)} meses)")
    top = sorted(r["municipios"], key=lambda m: -sum(m["r"][ult12]))[:12]
    linhas.append("Maiores (12 meses, royalties): " + "; ".join(
        f"{m['nome']}-{m['uf']} R$ {sum(m['r'][ult12]) / 1e6:,.0f} mi" for m in top))
    for alvo in ("Maricá", "Niterói", "Pojuca", "Mossoró", "São Francisco do Conde"):
        for m in r["municipios"]:
            if m["nome"] == alvo:
                anos = {}
                for mes, v in zip(r["meses"], m["r"]):
                    anos[mes[:4]] = anos.get(mes[:4], 0) + v
                pe = {}
                for t, v in m["pe"].items():
                    pe[t[:4]] = pe.get(t[:4], 0) + v
                linhas.append(f"  {alvo}-{m['uf']}: royalties por ano " +
                              ", ".join(f"{a}: {v / 1e6:,.1f} mi" for a, v in list(anos.items())[-4:]) +
                              " | PE por ano " + ", ".join(f"{a}: {v / 1e6:,.1f} mi" for a, v in list(pe.items())[-4:]))
    if r["sem_par_ibge"]:
        linhas.append(f"Sem nome oficial do IBGE ({len(r['sem_par_ibge'])}): " + ", ".join(r["sem_par_ibge"][:30]))
    return "\n".join(linhas)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mostrar", action="store_true")
    p.add_argument("--sem-baixar", action="store_true")
    a = p.parse_args(argv)
    r = executar(baixar_dados=not a.sem_baixar)
    print(resumo(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())

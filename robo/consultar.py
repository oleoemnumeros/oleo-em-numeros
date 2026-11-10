"""Consulta para conferir números contra relatórios das empresas.

Lista, campo a campo, a produção dos últimos meses de um grupo econômico,
como a ANP publica (100% do campo, gás produzido total).

Uso:
    python -m robo.consultar PRIO
    python -m robo.consultar "Brava Energia" --meses 6
"""
import argparse

from . import config, grupos
from .ler import consolidar
from .util import sem_acento


def _k(t):
    return sem_acento(str(t)).casefold()


def consultar(grupo: str, meses: int = 3) -> str:
    base, _ = consolidar(config.BRUTOS, config.MANUAIS / "operador_por_campo.csv", config.ANO_INICIAL)
    regras = grupos.carregar()
    base = base.assign(grupo=base["operador"].map(lambda o: grupos.grupo_de(o, regras)))
    sel = base[base["grupo"].map(_k).str.contains(_k(grupo), regex=False)]
    if sel.empty:
        return f"Nenhuma empresa com '{grupo}' no nome."
    ultimos = sorted(sel["periodo"].unique())[-meses:]
    sel = sel[sel["periodo"].isin(ultimos)]
    t = (sel.groupby(["campo", "operador", "periodo"])
         .agg(petroleo=("petroleo_bbl_d", "sum"), gas=("gas_mil_m3_d", "sum"), pocos=("poco", "nunique"))
         .reset_index())
    t["boe"] = t["petroleo"] + t["gas"] * config.M3_PARA_BBL
    linhas = [f"## {grupo}: produção por campo (100% do campo, como a ANP publica)", "",
              "| Campo | Empresa na ANP | Mês | Petróleo (bbl/d) | Gás (mil m³/d) | Gás (boe/d) | Total (boe/d) | Poços |",
              "|---|---|---|---:|---:|---:|---:|---:|"]
    for _, r in t.sort_values(["campo", "periodo"]).iterrows():
        linhas.append(f"| {r['campo']} | {r['operador']} | {r['periodo']} | {r['petroleo']:,.0f} | {r['gas']:,.1f} | "
                      f"{r['gas'] * config.M3_PARA_BBL:,.0f} | {r['boe']:,.0f} | {r['pocos']} |")
    tot = t.groupby("periodo")[["petroleo", "gas", "boe"]].sum()
    linhas += ["", "| Mês | Petróleo (bbl/d) | Gás (mil m³/d) | Total (boe/d) |", "|---|---:|---:|---:|"]
    for per, r in tot.iterrows():
        linhas.append(f"| {per} | {r['petroleo']:,.0f} | {r['gas']:,.1f} | {r['boe']:,.0f} |")
    return "\n".join(linhas) + "\n"


def compartilhados() -> str:
    """Campos que a ANP registra com mais de uma empresa no último mês com operadora informada."""
    base, _ = consolidar(config.BRUTOS, config.MANUAIS / "nao_usar.csv", config.ANO_INICIAL)
    # só os arquivos por poço (até 2023) trazem a empresa de cada poço
    com_op = base[(base["periodo"] <= "2023-12") & base["operador"].ne("Não identificado")]
    com_op = com_op[com_op["periodo"] == com_op.groupby("campo")["periodo"].transform("max")]
    com_op = com_op.assign(boe=com_op["petroleo_bbl_d"] + com_op["gas_mil_m3_d"] * config.M3_PARA_BBL)
    t = com_op.groupby(["campo", "periodo", "operador"]).agg(boe=("boe", "sum"), pocos=("poco", "nunique")).reset_index()
    varios = t[t.groupby("campo")["operador"].transform("nunique") > 1]
    linhas = ["## Campos com mais de uma empresa no mesmo mês", "",
              "| Campo | Mês | Empresa na ANP | boe/d | Poços |", "|---|---|---|---:|---:|"]
    for _, r in varios.sort_values(["campo", "boe"], ascending=[True, False]).iterrows():
        linhas.append(f"| {r['campo']} | {r['periodo']} | {r['operador']} | {r['boe']:,.0f} | {r['pocos']} |")
    return "\n".join(linhas) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("grupo")
    p.add_argument("--meses", type=int, default=3)
    a = p.parse_args(argv)
    if a.grupo.startswith("links:"):
        # lista os links de arquivos de uma página (para achar fontes novas)
        import re
        import requests
        from .noticias import NAVEGADOR
        url = a.grupo[len("links:"):]
        r = requests.get(url, headers={"User-Agent": NAVEGADOR}, timeout=60)
        print(f"HTTP {r.status_code} {url}")
        for href, texto in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
            t = re.sub(r"<[^>]+>|\s+", " ", texto).strip()
            if re.search(r"\.(xlsx?|csv|zip|ods|json)(\?|$)|royalt|particip|benefici|distribu", href + " " + t, re.I):
                print(f"LINK | {t[:90]} | {href}")
    elif a.grupo.startswith("planilha:"):
        # mostra abas e primeiras linhas de uma planilha (xlsx/xls/zip) para entender o formato
        import io
        import zipfile
        import pandas as pd
        import requests
        from .noticias import NAVEGADOR
        url = a.grupo[len("planilha:"):]
        r = requests.get(url, headers={"User-Agent": NAVEGADOR}, timeout=180)
        print(f"HTTP {r.status_code} {len(r.content) / 1e6:.1f} MB {url}")
        arquivos = []
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                print("ZIP:", z.namelist()[:30])
                arquivos = [(n, z.read(n)) for n in z.namelist() if n.lower().endswith((".xlsx", ".xls", ".csv"))][:2]
        else:
            arquivos = [(url, r.content)]
        for nome, conteudo in arquivos:
            if nome.lower().endswith(".csv"):
                print(conteudo[:1500].decode("latin-1"))
                continue
            abas = pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, nrows=14)
            print(f"### {nome}: {len(abas)} abas: {list(abas)[:40]}")
            for aba, d in list(abas.items())[:4]:
                print(f"--- aba {aba} ({d.shape})")
                print(d.head(14).to_string(max_colwidth=28)[:3000])
    elif a.grupo == "royalties-amostra":
        import io
        import pandas as pd
        import requests
        from .noticias import NAVEGADOR
        base = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/pg/"
        for arq in ("royalties-municipios/royalties-municipio-2025.csv", "royalties-estados/royalties-estado-2025.csv",
                    "participacao-especial/pe_municipio.csv", "participacao-especial/pe_estado.csv"):
            r = requests.get(base + arq, headers={"User-Agent": NAVEGADOR}, timeout=120)
            t = r.content.decode("utf-8", errors="replace") if r.content[:3] == b"\xef\xbb\xbf" else r.content.decode("latin-1")
            linhas = t.splitlines()
            print(f"### {arq}: HTTP {r.status_code}, {len(r.content) / 1e6:.1f} MB, {len(linhas)} linhas")
            print("\n".join(linhas[:6]))
            print("...")
            print("\n".join(l for l in linhas if "MARIC" in l.upper())[:900])
            print("\n".join(linhas[-3:]))
        url = ("https://www.gov.br/anp/pt-br/assuntos/royalties-e-outras-participacoes/arq-royalties/"
               "tabelas-contendo-o-valor-mensal-dos-royalties-dos-beneficiarios/2026/royalties-tabelas-jan-dez-2026_03.xlsx")
        r = requests.get(url, headers={"User-Agent": NAVEGADOR}, timeout=180)
        for aba in ("TOTAL", "CONCESSÃO"):
            d = pd.read_excel(io.BytesIO(r.content), sheet_name=aba, header=None)
            print(f"### xlsx 2026 aba {aba}: {d.shape}")
            with pd.option_context("display.width", 250, "display.max_columns", 12):
                print(d.iloc[:22, :11].to_string(max_colwidth=26))
                m = d[d.astype(str).apply(lambda c: c.str.upper().str.contains("MARIC")).any(axis=1)]
                print(m.iloc[:3, :11].to_string(max_colwidth=26))
                print(d.iloc[-6:, :11].to_string(max_colwidth=26))
    elif a.grupo == "royalties-amostra2":
        import io
        import pandas as pd
        import requests
        from .noticias import NAVEGADOR
        H = {"User-Agent": NAVEGADOR}
        url = "https://www.gov.br/anp/pt-br/assuntos/royalties-e-outras-participacoes/arq-royalties/pe/tab/2026/19-pe-2t-2026.xls"
        d = pd.read_excel(io.BytesIO(requests.get(url, headers=H, timeout=120).content), sheet_name=None, header=None)
        for aba, t in d.items():
            print(f"### PE xls aba {aba} {t.shape}")
            with pd.option_context("display.width", 250, "display.max_columns", 8, "display.max_rows", 400):
                print(t.dropna(how="all").iloc[:, :6].to_string(max_colwidth=40))
        base = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/pg/participacao-especial/"
        for arq in ("pe_municipio.csv", "pe_estado.csv"):
            t = pd.read_csv(io.BytesIO(requests.get(base + arq, headers=H, timeout=120).content), sep=";", encoding="latin-1")
            t["comp"] = pd.to_datetime(pd.to_numeric(t["competencia"], errors="coerce"), unit="D", origin="1899-12-30")
            t["cx"] = pd.to_datetime(pd.to_numeric(t["caixa"], errors="coerce"), unit="D", origin="1899-12-30")
            print(f"### {arq}: {len(t)} linhas; competência {t['comp'].min()} a {t['comp'].max()}; caixa {t['cx'].min()} a {t['cx'].max()}")
            print(t.groupby(t["comp"].dt.to_period("M")).size().tail(8).to_string())
        r = requests.get("https://servicodados.ibge.gov.br/api/v1/localidades/municipios", headers=H, timeout=120)
        m = r.json()
        print(f"### IBGE municípios: HTTP {r.status_code}, {len(m)} itens; exemplo: {m[0]['id']} {m[0]['nome']}")
        r = requests.get("https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1/variaveis/9324?localidades=N6[all]",
                         headers=H, timeout=180)
        print(f"### IBGE população: HTTP {r.status_code}, {len(r.content) / 1e6:.1f} MB")
        try:
            j = r.json()
            res = j[0]["resultados"][0]["series"]
            print(len(res), res[0]["localidade"], res[0]["serie"])
        except Exception as e:
            print("erro população:", e, r.text[:300])
    elif a.grupo == "fontes":
        from .noticias import testar
        print(testar())
    elif a.grupo == "cvm-contas":
        from .cvm import baixar, contas, ANO_INICIAL
        from datetime import date
        baixar(list(range(date.today().year - 1, date.today().year + 1)))
        print(contas())
    elif a.grupo == "cvm":
        from .cvm import main as cvm_main
        cvm_main(["--mostrar"])
    elif a.grupo == "govbr":
        from .noticias import depurar_govbr
        print(depurar_govbr())
    else:
        print(compartilhados() if a.grupo == "compartilhados" else consultar(a.grupo, a.meses))


if __name__ == "__main__":
    main()

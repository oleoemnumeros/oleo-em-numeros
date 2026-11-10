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

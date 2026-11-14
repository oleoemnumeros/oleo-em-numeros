"""Prepara os preços de combustíveis (robo/precos.py) para as páginas do Brasil, dos estados e dos municípios."""
import json
from pathlib import Path

from robo.agregar import UFS
from robo.util import slug

PRINCIPAIS = ["GASOLINA", "ETANOL", "DIESEL S10"]
UNIDADE = {"GNV": "R$ por m³"}
RANKING_MIN = 10        # coletas no mês para um município entrar nos rankings
MESES_RECENTES = 3      # município sem coleta de gasolina nos últimos meses não ganha página


def _var(a, b):
    return a / b - 1 if a is not None and b else None


def preparar(dados: Path) -> dict | None:
    arq = dados / "precos.json"
    if not arq.exists():
        return None
    P = json.loads(arq.read_text("utf-8"))
    meses, minc, rot = P["meses"], P["min_coletas"], P["rotulos"]
    n = len(meses)

    def valida(s):   # média de cada mês, ou None quando houve poucas coletas
        return [m if k >= minc else None for m, k in zip(s["m"], s["n"])]

    def kpi(s):
        v = valida(s)
        i = max((j for j, x in enumerate(v) if x is not None), default=None)
        if i is None:
            return None
        return {"valor": v[i], "i": i, "mes": meses[i], "var_mes": _var(v[i], v[i - 1]) if i >= 1 else None,
                "var12": _var(v[i], v[i - 12]) if i >= 12 else None, "recente": i >= n - MESES_RECENTES}

    def bloco(series):
        out = {}
        for p, s in series.items():
            k = kpi(s)
            if k:
                out[p] = {"k": k, "serie": valida(s), "n": s["n"][k["i"]]}
        return out

    def razao(b):    # etanol ÷ gasolina, em %, mês a mês
        e, g = b["ETANOL"]["serie"], b["GASOLINA"]["serie"]
        return [100 * x / y if x is not None and y else None for x, y in zip(e, g)]

    brasil = bloco(P["brasil"])
    if "ETANOL" in brasil:
        brasil["razao"] = razao(brasil)
    ult = n - 1

    estados = []
    for uf, series in sorted(P["estados"].items(), key=lambda kv: UFS.get(kv[0], ("", kv[0]))[1]):
        if uf not in UFS:
            continue
        b = bloco(series)
        if "GASOLINA" not in b:
            continue
        nome = UFS[uf][1]
        e = {"uf": uf, "nome": nome, "de": f"{UFS[uf][0]} {nome}", "slug": slug(nome), "p": b, "razao_ult": None}
        if "ETANOL" in b:
            e["razao"] = razao(b)
            e["razao_ult"] = next((x for x in reversed(e["razao"]) if x is not None), None)
        estados.append(e)
    por_uf = {e["uf"]: e for e in estados}

    municipios, usados = [], set()
    for m in P["municipios"]:
        b = bloco(m["serie"])
        if "GASOLINA" not in b or not b["GASOLINA"]["k"]["recente"] or m["uf"] not in por_uf:
            continue
        sl = slug(m["nome"])
        if (m["uf"], sl) in usados:
            sl = f"{sl}-{m['ibge']}" if m["ibge"] else f"{sl}-2"
        usados.add((m["uf"], sl))
        for p, x in b.items():
            u = m["ultimo"].get(p)
            if u and x["k"]["i"] == ult:
                x.update({"min": u["min"], "max": u["max"], "postos": u["postos"]})
        e = por_uf[m["uf"]]
        g = b["GASOLINA"]
        i = g["k"]["i"]
        ref_uf = e["p"]["GASOLINA"]["serie"][i]
        ref_br = brasil["GASOLINA"]["serie"][i]
        d = {"uf": m["uf"], "nome": m["nome"], "slug": sl, "estado_slug": e["slug"], "estado_nome": e["nome"],
             "estado_de": e["de"], "ibge": m["ibge"], "p": b, "razao": None, "razao_ult": None,
             "vs_uf": _var(g["k"]["valor"], ref_uf), "vs_br": _var(g["k"]["valor"], ref_br), "ref_uf": ref_uf, "ref_br": ref_br,
             "ranking": g["k"]["i"] == ult and g["n"] >= RANKING_MIN}
        if "ETANOL" in b:
            d["razao"] = razao(b)
            d["razao_ult"] = next((x for x in reversed(d["razao"]) if x is not None), None)
        municipios.append(d)
    # posição entre os municípios com coletas suficientes no último mês
    rk = sorted([m for m in municipios if m["ranking"]], key=lambda m: m["p"]["GASOLINA"]["k"]["valor"])
    for pos, m in enumerate(rk, 1):
        m["pos_br"] = pos
    for uf in {m["uf"] for m in rk}:
        for pos, m in enumerate([x for x in rk if x["uf"] == uf], 1):
            m["pos_uf"] = pos
    for m in municipios:
        m.setdefault("pos_br", None)
        m.setdefault("pos_uf", None)
    for e in estados:
        e["municipios"] = [m for m in municipios if m["uf"] == e["uf"]]
    d = P["coleta_ate"]
    razao_ult = next((x for x in reversed(brasil["razao"]) if x is not None), None) if "ETANOL" in brasil else None
    return {"meses": meses, "ultimo": P["ultimo"], "coleta_ate": d, "coleta_txt": f"{d[8:10]}/{d[5:7]}/{d[:4]}",
            "rotulos": rot, "brasil": brasil, "razao_ult": razao_ult, "min_ranking": RANKING_MIN,
            "n_municipios": sum(1 for m in municipios if m["p"]["GASOLINA"]["k"]["i"] == ult),
            "estados": estados, "municipios": municipios, "n_ranking": len(rk), "min_coletas": minc,
            "baratos": rk[:10], "caros": list(reversed(rk))[:10], "unidade": UNIDADE}

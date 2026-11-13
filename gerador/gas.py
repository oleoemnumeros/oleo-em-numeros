"""Prepara o gás por destino (robo/gas.py) para as páginas /gas/ e /gas/queima/ e para o cartão de queima nos campos."""
import json
from pathlib import Path

from robo.mapa import chave_campo


def _aprov(prod, queima):
    return (1 - queima / prod) * 100 if prod else None


def _mes_anterior(periodo: str, n: int) -> str:
    a, m = int(periodo[:4]), int(periodo[5:7])
    k = a * 12 + (m - 1) - n
    return f"{k // 12}-{k % 12 + 1:02d}"


def _var(atual, antes):
    return atual / antes - 1 if antes else None


def preparar(dados: Path, ag: dict) -> dict | None:
    arq = dados / "gas.json"
    if not arq.exists():
        return None
    g = json.loads(arq.read_text("utf-8"))
    meses, b = g["meses"], g["brasil"]
    ultimo = g["ultimo"]
    i = len(meses) - 1
    i12 = meses.index(_mes_anterior(ultimo, 12)) if _mes_anterior(ultimo, 12) in meses else None
    aprov = [_aprov(p, q) for p, q in zip(b["producao"], b["queima"])]

    def kpi(campo):
        return {"valor": b[campo][i], "var12": _var(b[campo][i], b[campo][i12]) if i12 is not None else None}

    estados_ag = {e["uf"]: e for e in ag.get("estados", []) if e.get("slug")}
    estados = []
    for e in g["estados"]:
        estados.append({"nome": e["nome"], "uf": e["uf"], "slug": estados_ag.get(e["uf"], {}).get("slug"),
                        "producao": e["producao"][-1], "queima": e["queima"][-1], "reinjecao": e["reinjecao"][-1],
                        "consumo": e["consumo"][-1], "disponivel": e["disponivel"][-1],
                        "aprov": _aprov(e["producao"][-1], e["queima"][-1]),
                        "pct_queima_pais": e["queima"][-1] / b["queima"][-1] * 100 if b["queima"][-1] else None})
    ambientes = [{"nome": "Mar" if a == "MAR" else "Terra", **v, "aprov": _aprov(v["producao"], v["queima"])}
                 for a, v in sorted(g["ambientes"].items())]
    por_chave = {chave_campo(c["chave"]): c for c in ag["campos"]}
    ops = {o["slug"]: o for o in ag["operadores"]}

    # ---------- ranking de queima por campo (boletim mensal) ----------
    qm = g["queima"]["meses"]
    mq = sorted(qm)
    queima = None
    historico: dict[str, list] = {}   # slug do campo -> [{mes, queima, posicao}]
    if mq:
        for mes in mq:
            for r in qm[mes]:
                c = por_chave.get(chave_campo(r["chave"]))
                if c:
                    historico.setdefault(c["slug"], []).append({"mes": mes, "queima": r["queima"], "posicao": r["posicao"]})
        ult = mq[-1]
        nac = g["queima"]["nacional"]

        def nacional(mes):
            if mes in nac:
                return nac[mes]["queima"]
            return b["queima"][meses.index(mes)] if mes in meses else None

        ant, ano = _mes_anterior(ult, 1), _mes_anterior(ult, 12)
        antes = {chave_campo(r["chave"]): r["queima"] for r in qm.get(ant, [])}
        antes12 = {chave_campo(r["chave"]): r["queima"] for r in qm.get(ano, [])}
        total_pais = nacional(ult)
        linhas, por_op = [], {}
        for r in qm[ult]:
            k = chave_campo(r["chave"])
            c = por_chave.get(k)
            op = ops.get(r["operador_slug"])
            linhas.append({"posicao": r["posicao"], "campo": c["nome"] if c else r["campo"], "slug": c["slug"] if c else None,
                           "bacia": r["bacia"], "ambiente": "Mar" if r["ambiente"] == "MAR" else "Terra",
                           "operador": op["nome"] if op else r["operador"], "operador_slug": op["slug"] if op else None,
                           "queima": r["queima"], "pct_pais": r["queima"] / total_pais * 100 if total_pais else None,
                           "var_mes": _var(r["queima"], antes.get(k)) if ant in qm else None,
                           "entrou": ant in qm and k not in antes,
                           "var_ano": _var(r["queima"], antes12.get(k)) if ano in qm else None})
            d = por_op.setdefault(linhas[-1]["operador"], {"nome": linhas[-1]["operador"], "slug": linhas[-1]["operador_slug"],
                                                           "queima": 0.0, "campos": []})
            d["queima"] += r["queima"]
            d["campos"].append(linhas[-1]["campo"])
        soma = sum(r["queima"] for r in qm[ult])
        queima = {"mes": ult, "anterior": ant if ant in qm else None, "linhas": linhas, "total_pais": total_pais,
                  "soma": soma, "pct_soma": soma / total_pais * 100 if total_pais else None, "n": len(linhas),
                  "empresas": sorted(por_op.values(), key=lambda d: -d["queima"]),
                  "meses": [{"mes": m, "soma": sum(r["queima"] for r in qm[m]), "total": nacional(m)} for m in mq]}

    def escala(v):
        return [None if x is None else round(x / 1000, 3) for x in v]

    return {"meses": meses, "ultimo": ultimo, "serie_aprov": [None if x is None else round(x, 2) for x in aprov],
            "serie_queima": escala(b["queima"]), "serie_reinj": escala(b["reinjecao"]),
            "serie_producao": escala(b["producao"]),
            "k": {c: kpi(c) for c in ("producao", "queima", "reinjecao", "consumo", "disponivel")},
            "aprov": aprov[i], "aprov12": aprov[i12] if i12 is not None else None,
            "estados": estados, "ambientes": ambientes, "queima": queima, "historico_campo": historico}

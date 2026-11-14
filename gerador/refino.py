"""Prepara o refino (robo/refino.py) para as páginas /refino/ e uma página por refinaria."""
import calendar
import json
from pathlib import Path

from robo.util import slug

BARRIL_POR_M3 = 6.289811
NOMES = {"GASOLINA A": "Gasolina A", "ÓLEO DIESEL": "Óleo diesel", "ÓLEO COMBUSTÍVEL": "Óleo combustível",
         "GLP": "GLP (gás de cozinha)", "QUEROSENE DE AVIAÇÃO": "Querosene de aviação (QAV)", "NAFTA": "Nafta",
         "COQUE": "Coque de petróleo", "ASFALTO": "Asfalto", "LUBRIFICANTE": "Lubrificantes",
         "OUTROS NÃO ENERGÉTICOS": "Outros não energéticos", "OUTROS ENERGÉTICOS": "Outros energéticos",
         "SOLVENTE": "Solventes", "PARAFINA": "Parafina", "QUEROSENE ILUMINANTE": "Querosene iluminante",
         "GASOLINA DE AVIAÇÃO": "Gasolina de aviação"}


def _var(a, b):
    return a / b - 1 if b else None


def preparar(dados: Path) -> dict | None:
    arq = dados / "refino.json"
    if not arq.exists():
        return None
    g = json.loads(arq.read_text("utf-8"))
    meses, ultimo = g["meses"], g["ultimo"]
    n = len(meses)
    dias = [calendar.monthrange(int(m[:4]), int(m[5:7]))[1] for m in meses]

    def bpd(serie):   # m³ no mês -> mil barris por dia
        return [v * BARRIL_POR_M3 / d / 1000 for v, d in zip(serie, dias)]

    def m3d(serie):   # m³ no mês -> mil m³ por dia
        return [v / d / 1000 for v, d in zip(serie, dias)]

    def soma(series):
        return [sum(x) for x in zip(*series)] if series else [0] * n

    def media(serie, ini, fim):
        xs = serie[max(ini, 0):max(fim, 0)]
        return sum(xs) / len(xs) if xs else 0.0

    def sem_zeros_iniciais(serie):
        i = next((k for k, v in enumerate(serie) if v), n)
        return [None] * i + serie[i:]

    refs = []
    for chave, r in g["refinarias"].items():
        cad = r["cadastro"]
        proc = soma([r["proc"][o] for o in ("nacional", "importado", "outras")])
        deriv = {p: m3d(v) for p, v in r["deriv"].items()}
        total_deriv = soma(list(r["deriv"].values()))
        proc_bpd = bpd(proc)
        imp = [100 * a / t if t else None for a, t in zip(r["proc"]["importado"], proc)]
        ultimo12 = media(proc_bpd, n - 12, n)
        antes12 = media(proc_bpd, n - 24, n - 12)
        refs.append({
            "chave": chave, "nome": cad.get("nome") or chave.title(), "sigla": cad.get("sigla") or chave.title(),
            "slug": slug(cad.get("sigla") or chave), "empresa": cad.get("empresa") or None,
            "cidade": cad.get("cidade") or None, "uf": cad.get("uf") or r["uf"], "obs": cad.get("obs") or None,
            "lat": float(cad["lat"]) if cad.get("lat") not in (None, "") else None,
            "lon": float(cad["lon"]) if cad.get("lon") not in (None, "") else None,
            "proc": proc, "proc_bpd": proc_bpd, "serie_proc": sem_zeros_iniciais(proc_bpd), "serie_imp": imp,
            "proc_mes": proc_bpd[-1], "proc12": ultimo12, "var12": _var(ultimo12, antes12),
            "pct_imp12": (100 * sum(r["proc"]["importado"][-12:]) / sum(proc[-12:])) if sum(proc[-12:]) else None,
            "ativa": sum(proc[-12:]) > 0, "operando": sum(proc[-3:]) > 0, "recente": sum(proc[-24:]) > 0,
            "_deriv_m3": r["deriv"], "deriv": deriv,
            "deriv_total": m3d(total_deriv),
            "serie_diesel": sem_zeros_iniciais(deriv.get("ÓLEO DIESEL", [0] * n)),
            "serie_gasolina": sem_zeros_iniciais(deriv.get("GASOLINA A", [0] * n)),
            "_orig": r["proc"],
        })
    ativas = sorted([x for x in refs if x["ativa"]], key=lambda x: -x["proc12"])
    total12 = sum(x["proc12"] for x in ativas)
    for i, x in enumerate(ativas, 1):
        x["posicao"] = i
        x["pct_pais"] = x["proc12"] / total12 * 100 if total12 else None
        d12 = {p: media(v, n - 12, n) for p, v in x["deriv"].items()}
        d12a = {p: media(v, n - 24, n - 12) for p, v in x["deriv"].items()}
        tot = sum(d12.values())
        x["derivados"] = sorted(
            [{"nome": NOMES.get(p, p.title()), "valor": d12[p], "pct": d12[p] / tot * 100 if tot else None,
              "var12": _var(d12[p], d12a[p]), "mes": x["deriv"][p][-1]}
             for p in d12 if d12[p] > 0], key=lambda y: -y["valor"])
        x["pct_diesel"] = next((y["pct"] for y in x["derivados"] if y["nome"] == "Óleo diesel"), None)
        x["pct_gasolina"] = next((y["pct"] for y in x["derivados"] if y["nome"] == "Gasolina A"), None)
        x["deriv12"] = tot

    # ---------- Brasil ----------
    todas = [x for x in refs]
    brasil_proc = soma([x["proc"] for x in todas])
    brasil_bpd = bpd(brasil_proc)
    nac = soma([x["_orig"]["nacional"] for x in todas])
    imp = soma([x["_orig"]["importado"] for x in todas])
    out = soma([x["_orig"]["outras"] for x in todas])
    pct_imp = [100 * a / t if t else None for a, t in zip(imp, brasil_proc)]
    pct_nac = [100 * a / t if t else None for a, t in zip(nac, brasil_proc)]
    prods = sorted({p for x in todas for p in x["_deriv_m3"]})
    deriv_br = {p: m3d(soma([x["_deriv_m3"].get(p, [0] * n) for x in todas])) for p in prods}
    d12 = {p: media(v, n - 12, n) for p, v in deriv_br.items()}
    d12a = {p: media(v, n - 24, n - 12) for p, v in deriv_br.items()}
    tot = sum(d12.values())
    derivados = sorted([{"nome": NOMES.get(p, p.title()), "valor": d12[p], "pct": d12[p] / tot * 100 if tot else None,
                         "var12": _var(d12[p], d12a[p]), "mes": deriv_br[p][-1]} for p in prods if d12[p] > 0],
                       key=lambda y: -y["valor"])
    i12 = n - 13 if n >= 13 else None   # mesmo mês do ano anterior

    def kpi(serie, mes=n - 1):
        return {"valor": serie[mes], "var12": _var(serie[mes], serie[i12]) if i12 is not None and serie[i12] else None}

    diesel, gasol = deriv_br.get("ÓLEO DIESEL", [0] * n), deriv_br.get("GASOLINA A", [0] * n)
    por_slug = {x["slug"]: x for x in refs}
    inativas = sorted([x for x in refs if not x["ativa"] and x["lat"] is not None or (not x["ativa"] and x["recente"])],
                      key=lambda x: x["sigla"])
    R = {
        "meses": meses, "ultimo": ultimo, "n_ativas": len(ativas), "n_operando": sum(1 for x in ativas if x["operando"]),
        "k": {"proc": kpi(brasil_bpd), "diesel": kpi(diesel), "gasolina": kpi(gasol),
              "imp": {"valor": pct_imp[-1], "antes": pct_imp[i12] if i12 is not None else None},
              "proc12": media(brasil_bpd, n - 12, n)},
        "serie_proc": brasil_bpd, "serie_imp": pct_imp, "serie_nac": pct_nac,
        "serie_diesel": diesel, "serie_gasolina": gasol,
        "ranking": ativas, "derivados": derivados, "inativas": [x for x in inativas if x["recente"]],
        "paginas": [x for x in refs if x["recente"]],
        "mapa": [{"n": x["nome"], "s": x["sigla"], "e": x["empresa"], "c": x["cidade"], "uf": x["uf"], "slug": x["slug"],
                  "lat": x["lat"], "lon": x["lon"], "v": round(x["proc12"], 1), "a": x["ativa"]}
                 for x in refs if x["lat"] is not None and x["recente"]],
        "por_slug": por_slug,
    }
    for x in refs:   # tira o que só serviu para as contas
        x.pop("_orig", None)
        x.pop("_deriv_m3", None)
        x.pop("proc", None)
    return R

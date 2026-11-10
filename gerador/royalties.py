"""Prepara os royalties (robo/royalties.py) para as páginas: totais de 12 meses, anos,
participação especial, ranking e valor por habitante."""
import json
from pathlib import Path

from robo.agregar import UFS
from robo.util import fmt, mes_extenso, slug


def dinheiro(v, curto: bool = True) -> str:
    """R$ 3,23 bi · R$ 312,4 mi · R$ 532 mil · R$ 812."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"R$ {fmt(v / 1e9, 2)} {'bi' if curto else 'bilhões'}"
    if a >= 1e6:
        return f"R$ {fmt(v / 1e6, 1)} {'mi' if curto else 'milhões'}"
    if a >= 1e3:
        return f"R$ {fmt(v / 1e3, 0)} mil"
    return f"R$ {fmt(v, 0)}"


def _var(atual, anterior):
    return atual / anterior - 1 if anterior and anterior > 0 else None


def _por_ano(meses, valores):
    anos = {}
    for m, v in zip(meses, valores):
        anos[m[:4]] = anos.get(m[:4], 0.0) + v
    return anos


def _pe_ano(pe: dict) -> dict:
    anos = {}
    for t, v in pe.items():
        anos[t[:4]] = anos.get(t[:4], 0.0) + v
    return anos


def preparar(pasta: Path) -> dict | None:
    arq = pasta / "royalties.json"
    if not arq.exists():
        return None
    r = json.loads(arq.read_text("utf-8"))
    meses, n = r["meses"], len(r["meses"])
    tris = r["trimestres"]
    ult4 = tris[-4:]
    ano_atual = r["ultimo"][:4]
    meses_ano = sum(1 for m in meses if m.startswith(ano_atual))

    def base(x):
        rr = x["r"]
        x["ultimo_mes"] = rr[-1]
        x["r12"] = sum(rr[-12:])
        x["r12_ant"] = sum(rr[-24:-12])
        x["var12"] = _var(x["r12"], x["r12_ant"])
        x["pe12"] = sum(x["pe"].get(t, 0.0) for t in ult4)
        x["total12"] = x["r12"] + x["pe12"]
        ra, pa = _por_ano(meses, rr), _pe_ano(x["pe"])
        x["anos"] = [{"ano": a, "royalties": ra.get(a, 0.0), "pe": pa.get(a, 0.0), "total": ra.get(a, 0.0) + pa.get(a, 0.0),
                      "parcial": a == ano_atual and meses_ano < 12} for a in sorted(set(ra) | set(pa), reverse=True)]
        x["pe_lista"] = [{"tri": t, "rotulo": f"{t[-1]}º tri {t[:4]}", "valor": v} for t, v in sorted(x["pe"].items(), reverse=True)]
        x["serie_ult12"] = [{"mes": m, "valor": v} for m, v in zip(meses[-12:][::-1], rr[-12:][::-1])]
        return x

    municipios = [base(m) for m in r["municipios"] if sum(m["r"]) > 0 or m["pe"]]
    for m in municipios:
        m["per_capita"] = m["total12"] / m["populacao"] if m.get("populacao") else None
    ativos = sorted([m for m in municipios if m["total12"] > 0], key=lambda m: -m["total12"])
    for i, m in enumerate(ativos, start=1):
        m["rank"] = i
    por_uf = {}
    for m in ativos:
        por_uf.setdefault(m["uf"], []).append(m)
    for lista in por_uf.values():
        for i, m in enumerate(lista, start=1):
            m["rank_uf"] = i
    for m in municipios:
        m.setdefault("rank", None)
        m.setdefault("rank_uf", None)
    estados_r = {e["uf"]: base(e) for e in r["estados"]}
    estados = []
    for uf in sorted(set(por_uf) | set(estados_r) | {m["uf"] for m in municipios}):
        if uf not in UFS:
            continue
        art, nome = UFS[uf]
        proprio = estados_r.get(uf)
        mun_uf = sorted([m for m in municipios if m["uf"] == uf], key=lambda m: (-m["total12"], m["nome"]))
        soma_mun = sum(m["total12"] for m in mun_uf)
        for m in mun_uf:
            m["share_uf"] = m["total12"] / soma_mun * 100 if soma_mun else None
        serie_mun = [sum(m["r"][i] for m in mun_uf) for i in range(n)]
        estados.append({"uf": uf, "nome": nome, "de": f"{art} {nome}", "slug": slug(nome),
                        "proprio": proprio, "municipios": mun_uf, "n_municipios": sum(1 for m in mun_uf if m["total12"] > 0),
                        "mun_total12": soma_mun, "mun_r12": sum(m["r12"] for m in mun_uf),
                        "mun_pe12": sum(m["pe12"] for m in mun_uf), "serie_mun": serie_mun,
                        "total12": soma_mun + (proprio["total12"] if proprio else 0.0)})
    estados.sort(key=lambda e: -e["total12"])
    for m in municipios:
        e = next(x for x in estados if x["uf"] == m["uf"])
        m["estado_slug"], m["estado_nome"], m["estado_de"] = e["slug"], e["nome"], e["de"]
    brasil = {
        "mun_r12": sum(m["r12"] for m in municipios), "mun_pe12": sum(m["pe12"] for m in municipios),
        "est_r12": sum(e["r12"] for e in estados_r.values()), "est_pe12": sum(e["pe12"] for e in estados_r.values()),
        "serie_mun": [sum(m["r"][i] for m in municipios) for i in range(n)],
        "serie_est": [sum(e["r"][i] for e in estados_r.values()) for i in range(n)],
        "n_municipios": len(ativos),
    }
    brasil["serie_total"] = [a + b for a, b in zip(brasil["serie_mun"], brasil["serie_est"])]
    brasil["mun_r12_ant"] = sum(m["r12_ant"] for m in municipios)
    brasil["var12_mun"] = _var(brasil["mun_r12"], brasil["mun_r12_ant"])
    return {"meses": meses, "ultimo": r["ultimo"], "ultimo_tri": r["ultimo_tri"], "ult4": ult4,
            "municipios": municipios, "ranking": ativos, "estados": estados, "brasil": brasil,
            "mes_extenso": mes_extenso(r["ultimo"])}


def resumo_municipio(m: dict, R: dict) -> str:
    nome = f"{m['nome']} ({m['uf']})"
    partes = []
    if m["ultimo_mes"] > 0:
        partes.append(f"Em {R['mes_extenso']}, {nome} recebeu {dinheiro(m['ultimo_mes'], False)} em royalties de petróleo e gás.")
    else:
        partes.append(f"{nome} não recebeu royalties de petróleo e gás em {R['mes_extenso']}.")
    if m["r12"] > 0:
        s = f"Nos últimos 12 meses, foram {dinheiro(m['r12'], False)}"
        if m["var12"] is not None:
            s += f", {fmt(abs(m['var12']) * 100, 0)}% {'a mais' if m['var12'] >= 0 else 'a menos'} que nos 12 meses anteriores"
        partes.append(s + ".")
    if m["pe12"] > 0:
        partes.append(f"Além disso, recebeu {dinheiro(m['pe12'], False)} de participação especial nos últimos quatro trimestres.")
    if m["rank"]:
        ordem = "o município que mais recebe" if m["rank"] == 1 else f"o {m['rank']}º município que mais recebe"
        partes.append(f"É {ordem} no Brasil, entre {R['brasil']['n_municipios']}, e o {m['rank_uf']}º {m['estado_de']}.")
    if m.get("per_capita") and m["per_capita"] >= 1:
        partes.append(f"Isso dá {dinheiro(m['per_capita'], False)} por habitante por ano.")
    return " ".join(partes)


def escala(valores: list) -> tuple[list, str]:
    """Série em reais -> (série na unidade que cabe no gráfico, nome da unidade)."""
    topo = max((abs(v) for v in valores), default=0)
    if topo >= 1e8:
        return [v / 1e6 for v in valores], "R$ milhões"
    if topo >= 1e5:
        return [v / 1e3 for v in valores], "R$ mil"
    return list(valores), "R$"

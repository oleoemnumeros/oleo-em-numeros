"""Conferência automática antes de publicar.

Se algo crítico aparecer, a execução termina com erro: o site não é
atualizado e o último dado bom continua no ar até alguém olhar.
"""
from . import config
from .util import fmt, fmt_var, mes_anterior, mes_extenso


def _meses_entre(ini: str, fim: str) -> list[str]:
    meses, p = [], fim
    while p >= ini:
        meses.append(p)
        p = mes_anterior(p)
    return sorted(meses)


def conferir(base, ag) -> tuple[bool, list[str], list[str]]:
    criticos, avisos = [], []
    ultimo, ant = ag["ultimo"], ag["anterior"]
    br = ag["brasil"]

    amb = set(base[base["periodo"] == ultimo]["ambiente"])
    for falta in {"terra", "mar"} - amb:
        criticos.append(f"Não há dados de {falta} em {mes_extenso(ultimo)}.")

    vm = br["varmes_petroleo"]
    if vm is not None and abs(vm) > config.LIMITE_VARIACAO_BRASIL:
        criticos.append(f"Petróleo do Brasil variou {fmt_var(vm)} de {mes_extenso(ant)} para "
                        f"{mes_extenso(ultimo)} (limite: {fmt(config.LIMITE_VARIACAO_BRASIL * 100)}%).")

    pocos = base[(base["petroleo_bbl_d"] > 0) | (base["gas_mil_m3_d"] > 0)].groupby("periodo")["poco"].nunique()
    if ant in pocos.index and ultimo in pocos.index:
        vp = pocos[ultimo] / pocos[ant] - 1
        if abs(vp) > config.LIMITE_VARIACAO_POCOS:
            criticos.append(f"Poços ativos variaram {fmt_var(vp)} no mês.")

    num = ["petroleo_bbl_d", "gas_mil_m3_d", "agua_bbl_d"]
    negativos = int((base[num] < 0).any(axis=1).sum())
    if negativos:
        criticos.append(f"{negativos} linhas com volume negativo.")

    u = base[base["periodo"] == ultimo]
    u = u.assign(boe=u["petroleo_bbl_d"] + u["gas_mil_m3_d"] * config.M3_PARA_BBL)
    sem_op = u[u["operador"] == "Não identificado"]
    if len(sem_op):
        pct = sem_op["boe"].sum() / max(u["boe"].sum(), 1)
        por_campo = (sem_op.groupby(["campo", "bacia", "estado"])["boe"].sum()
                     .sort_values(ascending=False))
        lista = "; ".join(f"{c} ({b}, {uf}): {fmt(v)} boe/d" for (c, b, uf), v in por_campo.items())
        msg = (f"{len(por_campo)} campos sem operador identificado ({fmt(pct * 100, 1)}% da produção em boe). "
               f"Preencha dados/manuais/operador_por_campo.csv: {lista}")
        (criticos if pct > 0.05 else avisos).append(msg)

    # meses que a ANP deixou de fora nos últimos 24 meses (por ambiente)
    meses = sorted(base["periodo"].unique())
    recentes = [p for p in meses if p > mes_anterior(ultimo, 24)]
    presentes = set(zip(base["periodo"], base["ambiente"]))
    esperados = [p for p in _meses_entre(recentes[0], ultimo)] if recentes else []
    for amb in ("terra", "mar"):
        faltam = [p for p in esperados if (p, amb) not in presentes]
        if faltam:
            avisos.append(f"A ANP não publicou dados de {amb} em: {', '.join(mes_extenso(p) for p in faltam)}. "
                          "Esses meses aparecem como lacuna nos gráficos.")

    campos_u = set(base[base["periodo"] == ultimo]["campo"])
    campos_a = set(base[base["periodo"] == ant]["campo"])
    if campos_u - campos_a:
        avisos.append("Campos novos no mês: " + ", ".join(sorted(campos_u - campos_a)[:10]))
    if campos_a - campos_u:
        avisos.append("Campos que sumiram no mês: " + ", ".join(sorted(campos_a - campos_u)[:10]))

    return not criticos, criticos, avisos


def relatorio(ag, criticos, avisos) -> str:
    br = ag["brasil"]
    linhas = [
        f"# Conferência: {mes_extenso(ag['ultimo'])}",
        "",
        f"- Produção total Brasil: {fmt(br['boe'])} mil boe/d ({fmt_var(br['varmes_boe'])} no mês, "
        f"{fmt_var(br['var12_boe'])} em 12 meses)",
        f"- Petróleo Brasil: {fmt(br['petroleo'])} mil bbl/d ({fmt_var(br['varmes_petroleo'])} no mês, "
        f"{fmt_var(br['var12_petroleo'])} em 12 meses)",
        f"- Gás natural: {fmt(br['gas'], 1)} milhões m³/d",
        f"- Poços ativos: {fmt(br['pocos'])}",
        f"- Campos: {len(ag['campos'])} · Operadoras: {len(ag['operadores'])}",
        "",
        "## Resultado: " + ("PUBLICAR" if not criticos else "NÃO PUBLICAR"),
    ]
    if criticos:
        linhas += ["", "### Problemas críticos"] + [f"- {c}" for c in criticos]
    if avisos:
        linhas += ["", "### Avisos"] + [f"- {a}" for a in avisos]
    linhas += ["", "### Operadoras (mil boe/d · petróleo mil bbl/d)",
               "; ".join(f"{o['nome']} {fmt(o['boe'], 1)} · {fmt(o['petroleo'], 1)}" for o in ag["operadores"])]
    linhas += ["", "Antes de divulgar, confira 3 números contra o Painel Dinâmico da ANP."]
    return "\n".join(linhas) + "\n"

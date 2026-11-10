"""Resultados trimestrais (CVM) das operadoras listadas, prontos para as páginas.

Junta a cada trimestre a produção operada média do trimestre pela ANP, quando os
três meses estão completos.
"""
import json
from pathlib import Path

from robo.util import fmt


CAMPOS = ["receita", "ebit", "da", "ebitda", "margem_ebitda", "lucro", "capex", "caixa", "divida_bruta",
          "divida_liquida", "arrendamentos", "ebitda_12m", "alavancagem", "alavancagem_com_arrend"]


def rotulo(tri: str) -> str:
    """'2026T3' -> '3T26'."""
    return f"{tri[-1]}T{tri[2:4]}"


def reais(v) -> str:
    """Valor em R$ milhões, escrito como R$ 8,4 bi ou R$ 786 mi."""
    if v is None:
        return "—"
    return f"R$ {fmt(v / 1000, 1)} bi" if abs(v) >= 1000 else f"R$ {fmt(v, 0)} mi"


def _producao_trimestral(serie: dict) -> dict:
    meses = dict(zip(serie["periodo"], serie["boe"]))
    saida = {}
    for per in serie["periodo"]:
        ano, mes = per[:4], int(per[5:7])
        tri = f"{ano}T{(mes - 1) // 3 + 1}"
        if tri in saida:
            continue
        trio = [meses.get(f"{ano}-{m:02d}") for m in range(3 * ((mes - 1) // 3) + 1, 3 * ((mes - 1) // 3) + 4)]
        if all(v is not None for v in trio):
            saida[tri] = sum(trio) / 3
    return saida


def carregar_financeiro(pasta: Path, ag: dict, trimestres: int = 8) -> dict:
    arq = pasta / "financeiro.json"
    if not arq.exists():
        return {}
    dados = json.loads(arq.read_text("utf-8"))
    por_nome = {o["nome"]: o for o in ag["operadores"]}
    saida = {}
    for grupo, e in dados.items():
        o = por_nome.get(grupo)
        if not o or not e["trimestres"]:
            continue
        prod = _producao_trimestral(o["serie"])
        tris = []
        for t in e["trimestres"][-trimestres:][::-1]:
            completo = {k: t.get(k) for k in CAMPOS}         # campo ausente vira None (vira "—" na página)
            tris.append({**completo, "trimestre": t["trimestre"], "rotulo": rotulo(t["trimestre"]),
                         "producao": prod.get(t["trimestre"])})
        saida[grupo] = {"ticker": e["ticker"], "empresa_cvm": e["empresa_cvm"], "trimestres": tris,
                        "ultimo": tris[0], "slug": o["slug"], "nome": grupo}
    return saida

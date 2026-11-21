"""Resumo semanal do Óleo em Números: preços dos combustíveis nos postos, a partir do levantamento semanal da ANP.

Lê dados/processados/precos_semanais.json (robo/precos_semanais.py) e monta, para cada semana, a manchete, as tabelas
(Brasil, estados e capitais), os textos prontos para o X e o LinkedIn e a imagem do resumo (familia_card).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from robo.agregar import UFS
from robo.util import fmt

from .familia_card import gera_card, seta

SEMANAS_NO_SITE = 26          # páginas semanais publicadas (meio ano); as mais antigas saem do site
PRINCIPAIS = ["Gasolina comum", "Etanol", "Diesel S10"]
ORDEM = ["Gasolina comum", "Gasolina aditivada", "Etanol", "Diesel S10", "Diesel S500", "GNV", "Gás de cozinha (13 kg)"]
UNIDADE = {"GNV": "m³", "Gás de cozinha (13 kg)": "botijão"}
LIMITE_X, PESO_LINK_X = 280, 23


def carregar(pasta: Path) -> dict | None:
    arq = pasta / "precos_semanais.json"
    if not arq.exists():
        return None
    S = json.loads(arq.read_text("utf-8"))
    return S if S.get("semanas") else None


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def dia(iso: str) -> str:
    return _d(iso).strftime("%d/%m/%Y")


def dia_curto(iso: str) -> str:
    return _d(iso).strftime("%d/%m")


def reais(v, casas: int = 2) -> str:
    return "—" if v is None else f"R$ {fmt(v, casas)}"


def _ano_antes(semanas: list[dict], i: int) -> dict | None:
    """A semana de um ano antes (a mais próxima de 364 dias, com folga de 4 dias)."""
    alvo = _d(semanas[i]["fim"]).toordinal() - 364
    melhor = min(semanas[:i], key=lambda w: abs(_d(w["fim"]).toordinal() - alvo), default=None)
    return melhor if melhor and abs(_d(melhor["fim"]).toordinal() - alvo) <= 4 else None


def centavos(dif: float) -> str:
    c = round(abs(dif) * 100)
    return "1 centavo" if c == 1 else f"{c} centavos"


def manchete(produto: str, p: float, p0: float | None) -> str:
    nome = {"Gasolina comum": "Gasolina", "Diesel S10": "Diesel S10"}.get(produto, produto)
    if p0 is None or round(abs(p - p0) * 100) == 0:
        return f"{nome} fica estável em {reais(p)} na semana"
    return (f"{nome} sobe {centavos(p - p0)} e vai a {reais(p)} na semana" if p > p0
            else f"{nome} cai {centavos(p - p0)} e fica em {reais(p)} na semana")


def montar(S: dict, i: int, site_url: str) -> dict:
    """Tudo o que a página da semana i precisa."""
    semanas = S["semanas"]
    w = semanas[i]
    w0 = semanas[i - 1] if i else None
    w12 = _ano_antes(semanas, i)
    url = f"{site_url}/resumo/semana/{w['fim']}/"
    brasil = []
    for prod in ORDEM:
        b = w["brasil"].get(prod)
        if not b or b.get("preco") is None:
            continue
        p0 = (w0 or {}).get("brasil", {}).get(prod, {}).get("preco") if w0 else None
        p12 = (w12 or {}).get("brasil", {}).get(prod, {}).get("preco") if w12 else None
        brasil.append({"produto": prod, "unidade": UNIDADE.get(prod, "litro"), "preco": b["preco"], "min": b.get("min"),
                       "max": b.get("max"), "postos": b.get("postos"), "ant": p0,
                       "dif": None if p0 is None else b["preco"] - p0,
                       "var": None if not p0 else b["preco"] / p0 - 1,
                       "var12": None if not p12 else b["preco"] / p12 - 1})
    por = {b["produto"]: b for b in brasil}
    g = por.get("Gasolina comum") or (brasil[0] if brasil else None)
    titulo = manchete(g["produto"], g["preco"], g["ant"]) if g else f"Preços dos combustíveis na semana de {dia(w['fim'])}"

    # estados: gasolina mais cara e mais barata, maiores altas e quedas na semana
    gas = [(uf, v.get("Gasolina comum")) for uf, v in w.get("estados", {}).items() if v.get("Gasolina comum")]
    gas.sort(key=lambda x: x[1])
    ant_est = (w0 or {}).get("estados", {})
    mov = [(uf, p - ant_est[uf]["Gasolina comum"]) for uf, p in gas if ant_est.get(uf, {}).get("Gasolina comum")]
    mov.sort(key=lambda x: x[1])
    estados = [{"uf": uf, "precos": {p: w["estados"][uf].get(p) for p in PRINCIPAIS},
                "dif": next((d for u, d in mov if u == uf), None)} for uf, _ in sorted(gas, key=lambda x: -x[1])]
    capitais = []
    ant_cap = (w0 or {}).get("capitais", {})
    for nome, v in w.get("capitais", {}).items():
        if v.get("Gasolina comum"):
            a = ant_cap.get(nome, {}).get("Gasolina comum")
            capitais.append({"nome": nome, "precos": {p: v.get(p) for p in PRINCIPAIS},
                             "dif": None if a is None else v["Gasolina comum"] - a})
    capitais.sort(key=lambda c: -c["precos"]["Gasolina comum"])

    # textos prontos
    def linha_prod(b):
        t = f"{b['produto']}: {reais(b['preco'])}"
        if b["dif"] is not None:
            t += " (estável)" if round(abs(b["dif"]) * 100) == 0 else f" ({'+' if b['dif'] > 0 else '−'}R$ {fmt(abs(b['dif']), 2)})"
        return t
    princ = [por[p] for p in PRINCIPAIS if p in por]
    periodo = f"{dia_curto(w['inicio'])} a {dia(w['fim'])}"
    cabeca = f"{titulo} ({periodo})."
    resto = " ".join(linha_prod(b) + "." for b in princ[1:])
    extra = ""
    if capitais:
        extra = f"Mais cara entre as capitais: {capitais[0]['nome'].split('/')[0]}, {reais(capitais[0]['precos']['Gasolina comum'])}."
    post_x = f"{cabeca}\n\n{url}"
    for corpo in ([cabeca, resto, extra], [cabeca, resto], [cabeca]):
        t = " ".join(x for x in corpo if x) + f"\n\n{url}"
        if len(t.replace(url, "x" * PESO_LINK_X)) <= LIMITE_X:
            post_x = t
            break
    L = [f"Preços dos combustíveis nos postos, semana de {periodo}", "", titulo + ".", ""]
    L += [linha_prod(b) for b in brasil if b["produto"] in PRINCIPAIS + ["Gás de cozinha (13 kg)"]]
    def nome(uf):
        return UFS.get(uf, ("", uf))[1]
    if gas:
        L += ["", f"Gasolina mais cara: {nome(gas[-1][0])}, {reais(gas[-1][1])}. Mais barata: {nome(gas[0][0])}, {reais(gas[0][1])}."]
    if mov and round(mov[-1][1], 2) > 0:
        L.append(f"Maior alta na semana: {nome(mov[-1][0])} (+R$ {fmt(mov[-1][1], 2)}).")
    if mov and round(mov[0][1], 2) < 0:
        L.append(f"Maior queda na semana: {nome(mov[0][0])} (−R$ {fmt(abs(mov[0][1]), 2)}).")
    L += ["", "Preço por estado, capital e município:", url, "",
          "Fonte: ANP, Levantamento de Preços de Combustíveis, organizado pelo Óleo em Números."]
    return {"w": w, "i": i, "url": url, "manchete": titulo, "periodo": periodo, "brasil": brasil, "principais": princ,
            "estados": estados, "capitais": capitais, "post_x": post_x, "post": "\n".join(L),
            "tem_ano": w12 is not None, "ano_antes": w12["fim"] if w12 else None}


def por_municipio(S: dict, n: int = 8) -> dict:
    """Preços da semana mais recente em cada município pesquisado, com a semana anterior e as últimas n semanas.
    Chave: (UF, slug do município), a mesma das páginas de preço por município."""
    semanas = [w for w in S["semanas"] if w.get("municipios")]
    if not semanas:
        return {}
    w, w0 = semanas[-1], (semanas[-2] if len(semanas) > 1 else None)
    out = {}
    for chave, precos in w["municipios"].items():
        uf, sl = chave.split("/", 1)
        ant = (w0 or {}).get("municipios", {}).get(chave, {})
        hist = [{"fim": x["fim"], "precos": x["municipios"].get(chave, {})} for x in semanas[-n:]]
        out[(uf, sl)] = {"inicio": w["inicio"], "fim": w["fim"], "periodo": f"{dia_curto(w['inicio'])} a {dia(w['fim'])}",
                         "precos": {p: precos.get(p) for p in PRINCIPAIS},
                         "dif": {p: (precos[p] - ant[p]) if precos.get(p) is not None and ant.get(p) is not None else None
                                 for p in PRINCIPAIS},
                         "hist": [h for h in reversed(hist) if h["precos"].get("Gasolina comum")]}
    return out


def ranking_municipios(S: dict, i: int, n: int = 10) -> dict | None:
    """Gasolina nos municípios na semana i: mais caras, mais baratas, maiores altas e quedas."""
    w = S["semanas"][i]
    if not w.get("municipios"):
        return None
    w0 = S["semanas"][i - 1] if i else {}
    linhas = []
    for chave, precos in w["municipios"].items():
        g = precos.get("Gasolina comum")
        if g is None:
            continue
        a = w0.get("municipios", {}).get(chave, {}).get("Gasolina comum")
        uf, sl = chave.split("/", 1)
        linhas.append({"uf": uf, "slug": sl, "preco": g, "dif": None if a is None else g - a})
    if not linhas:
        return None
    caras = sorted(linhas, key=lambda x: -x["preco"])[:n]
    baratas = sorted(linhas, key=lambda x: x["preco"])[:n]
    mov = [x for x in linhas if x["dif"] is not None]
    altas = [x for x in sorted(mov, key=lambda x: -x["dif"]) if round(x["dif"], 2) > 0][:n]
    quedas = [x for x in sorted(mov, key=lambda x: x["dif"]) if round(x["dif"], 2) < 0][:n]
    return {"caras": caras, "baratas": baratas, "altas": altas, "quedas": quedas, "total": len(linhas)}


def card(S: dict, i: int, destino: Path, site_url: str) -> bool:
    """Imagem do resumo semanal: gasolina, etanol e diesel S10, com as barras da gasolina nas últimas 13 semanas."""
    semanas, w = S["semanas"], S["semanas"][i]
    w0 = semanas[i - 1] if i else None
    nums = []
    for p in PRINCIPAIS:
        b = w["brasil"].get(p)
        if not b or b.get("preco") is None:
            continue
        a = (w0 or {}).get("brasil", {}).get(p, {}).get("preco") if w0 else None
        if a is None:
            var, boa = "", None
        else:
            dif = round(b["preco"] - a, 2)
            var = "igual à semana anterior" if dif == 0 else f"{seta(dif)} R$ {fmt(abs(dif), 2)} na semana"
            boa = None if dif == 0 else dif < 0                      # preço caindo é boa notícia para quem abastece
        nums.append(({"Gasolina comum": "Gasolina (litro)", "Etanol": "Etanol (litro)", "Diesel S10": "Diesel S10 (litro)"}[p],
                     reais(b["preco"]), var, boa))
    janela = semanas[max(0, i - 12):i + 1]
    barras = [(dia_curto(x["fim"]), [x["brasil"].get("Gasolina comum", {}).get("preco")]) for x in janela]
    host = site_url.split("//")[-1]
    return gera_card(destino, "Óleo em Números", "#17758C",
                     f"Combustíveis nos postos · semana até {dia(w['fim'])}", nums, barras, None,
                     f"Gasolina comum (R$/litro), semana a semana · Fonte: ANP · {host}/resumo/semana/{w['fim']}/",
                     [fmt(v[0], 2) if v[0] is not None else "" for _, v in barras])

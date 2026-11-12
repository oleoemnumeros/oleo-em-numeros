"""Dados da página /mapa/: junta os limites e as coordenadas da ANP (dados/processados/geo.json,
feito por robo/mapa.py) com os nossos campos, empresas e poços em produção.

Saída, em publico/dados/mapa/:
  campos.json  limites dos campos, empresas (para o filtro) e a lista [slug, nome, UF] de campos dos poços
  pocos.json   um ponto por poço em produção, com empresa e produção do último mês
O detalhe de produção de cada poço vem do arquivo do campo em dados/pocos/, carregado ao clicar.
"""
import json
from pathlib import Path

from robo.ler import _chave_poco
from robo.mapa import chave_campo
from robo.util import nome_campo

DESCONHECIDO = "Não identificado"


def _json(caminho: Path, obj) -> None:
    caminho.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def gerar_mapa(dados: Path, saida: Path, ag: dict) -> dict | None:
    geo_arq = dados / "geo.json"
    indice_arq = saida / "dados" / "pocos" / "indice.json"
    if not geo_arq.exists() or not indice_arq.exists():
        return None
    geo = json.loads(geo_arq.read_text("utf-8"))
    indice = json.loads(indice_arq.read_text("utf-8"))
    por_chave = {chave_campo(c["chave"]): c for c in ag["campos"]}

    grupos: dict[str, dict] = {}

    def grupo(nome: str) -> dict:
        nome = nome or DESCONHECIDO
        return grupos.setdefault(nome, {"n": nome, "c": 0, "boe": 0.0, "pt": 0, "slug": None})

    # ---------- limites dos campos ----------
    campos, vistos = [], set()
    for p in geo["campos"]:
        c = por_chave.get(p["k"])
        if c:
            g = grupo(c["grupo"])
            g["slug"] = c["operador_slug"]
            if p["k"] not in vistos:        # um campo pode ter mais de um polígono: conta uma vez
                g["c"] += 1
                g["boe"] += c["boe"]
                vistos.add(p["k"])
            campos.append({"s": c["slug"], "n": c["nome"], "g": c["grupo"], "b": round(c["boe"]), "w": c["pocos"],
                           "r": p["g"]})
        else:
            campos.append({"s": None, "n": nome_campo(p["n"]), "op": p["op"], "et": p["et"], "r": p["g"]})

    # ---------- poços em produção ----------
    slugs: list[str] = []
    ordem = {}
    unicos: dict[str, dict] = {}
    for nome, cslug, ativo, boe in indice["pocos"]:
        if not ativo:
            continue
        k = _chave_poco(nome)
        gnome = indice["campos"][cslug]["g"] or DESCONHECIDO
        if cslug not in ordem:
            ordem[cslug] = len(slugs)
            slugs.append([cslug, indice["campos"][cslug]["n"], indice["campos"][cslug]["uf"]])
        w = unicos.setdefault(k, {"n": nome, "g": gnome, "ci": [], "boe": 0})
        w["ci"].append(ordem[cslug])
        w["boe"] += boe
    for w in unicos.values():
        grupo(w["g"])["pt"] += 1

    nomes_grupos = sorted((g for g in grupos.values() if g["c"] or g["pt"]), key=lambda g: (-g["boe"], -g["pt"], g["n"]))
    gi = {g["n"]: i for i, g in enumerate(nomes_grupos)}
    for c in campos:
        c["gi"] = gi.get(c.pop("g", None) or "", -1) if c["s"] else -1

    pontos = []
    for k, w in sorted(unicos.items(), key=lambda kv: kv[1]["boe"]):
        xy = geo["pocos"].get(k)
        if xy:
            pontos.append([w["n"], xy[0], xy[1], gi[w["g"]], w["ci"][0] if len(w["ci"]) == 1 else w["ci"], w["boe"]])

    pasta = saida / "dados" / "mapa"
    pasta.mkdir(parents=True, exist_ok=True)
    _json(pasta / "campos.json", {"ultimo": ag["ultimo"], "slugs": slugs, "campos": campos,
                                  "grupos": [{"n": g["n"], "c": g["c"], "pt": g["pt"], "s": g["slug"]} for g in nomes_grupos]})
    _json(pasta / "pocos.json", {"pocos": pontos})
    return {"grupos": [{"i": i, "n": g["n"], "c": g["c"], "pt": g["pt"]} for i, g in enumerate(nomes_grupos)],
            "n_campos": len({c["s"] for c in campos if c["s"]}), "n_areas": len(campos),
            "n_sem_producao": sum(1 for c in campos if not c["s"]),
            "pocos_ativos": len(unicos), "pocos_no_mapa": len(pontos),
            "kb": round(sum(f.stat().st_size for f in pasta.iterdir()) / 1e3)}

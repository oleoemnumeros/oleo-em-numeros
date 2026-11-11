"""Transforma a base poço a poço nos números que o site mostra."""
from datetime import datetime, timezone

import pandas as pd

from . import config, grupos
from .util import mes_anterior, nome_bonito, nome_campo, sem_acento, slug, variacao

UFS = {
    "AC": ("do", "Acre"), "AL": ("de", "Alagoas"), "AM": ("do", "Amazonas"), "AP": ("do", "Amapá"),
    "BA": ("da", "Bahia"), "CE": ("do", "Ceará"), "DF": ("do", "Distrito Federal"),
    "ES": ("do", "Espírito Santo"), "GO": ("de", "Goiás"), "MA": ("do", "Maranhão"),
    "MG": ("de", "Minas Gerais"), "MS": ("de", "Mato Grosso do Sul"), "MT": ("de", "Mato Grosso"),
    "PA": ("do", "Pará"), "PB": ("da", "Paraíba"), "PE": ("de", "Pernambuco"), "PI": ("do", "Piauí"),
    "PR": ("do", "Paraná"), "RJ": ("do", "Rio de Janeiro"), "RN": ("do", "Rio Grande do Norte"),
    "RO": ("de", "Rondônia"), "RR": ("de", "Roraima"), "RS": ("do", "Rio Grande do Sul"),
    "SC": ("de", "Santa Catarina"), "SE": ("de", "Sergipe"), "SP": ("de", "São Paulo"),
    "TO": ("do", "Tocantins"),
}
_POR_NOME = {nome.lower(): (art, nome, uf) for uf, (art, nome) in UFS.items()}


# "Bacia de Santos", mas "Bacia do Recôncavo" e "Bacia Potiguar"
PREP_BACIA = {"reconcavo": "do", "espirito santo": "do", "solimoes": "do", "parnaiba": "do", "ceara": "do",
              "amazonas": "do", "parana": "do", "tucano sul": "do", "tucano": "do", "para-maranhao": "do",
              "sao francisco": "do", "potiguar": "", "foz do amazonas": "da"}


def estado_info(valor: str) -> dict:
    """Aceita sigla ou nome. Devolve sigla, nome e a expressão 'da Bahia'."""
    v = str(valor).strip()
    if v.upper() in UFS:
        art, nome = UFS[v.upper()]
        return {"uf": v.upper(), "nome": nome, "de": f"{art} {nome}"}
    achado = _POR_NOME.get(v.lower())
    if achado:
        art, nome, uf = achado
        return {"uf": uf, "nome": nome, "de": f"{art} {nome}"}
    return {"uf": v, "nome": nome_bonito(v), "de": f"de {nome_bonito(v)}"}


# boe (barril de óleo equivalente), na convenção da ANP: 1.000 m³ de gás = 1 m³ de óleo = 6,2898 barris.
BOE_POR_MIL_M3 = config.M3_PARA_BBL


def _boe(petroleo_bbl_d, gas_mil_m3_d):
    return petroleo_bbl_d + gas_mil_m3_d * BOE_POR_MIL_M3


def _ativos(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["petroleo_bbl_d"] > 0) | (df["gas_mil_m3_d"] > 0)]


def _soma_mensal(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    chaves = ["periodo"] + (por or [])
    g = df.groupby(chaves).agg(
        petroleo=("petroleo_bbl_d", "sum"),
        gas=("gas_mil_m3_d", "sum"),
        agua=("agua_bbl_d", "sum"),
    )
    g["boe"] = _boe(g["petroleo"], g["gas"])
    pocos = _ativos(df).groupby(chaves)["poco"].nunique().rename("pocos")
    return g.join(pocos).fillna({"pocos": 0}).reset_index()


def _bsw(agua, petroleo):
    total = agua + petroleo
    return agua / total * 100 if total > 0 else None


def _serie(tab: pd.DataFrame, meses: list[str], coluna: str, escala: float = 1.0,
           completo=lambda per: True) -> list:
    """Série mensal. Mês sem dado do ambiente na ANP vira None (lacuna no gráfico), não zero."""
    s = tab.set_index("periodo")[coluna].reindex(meses).fillna(0.0) / escala
    return [round(float(x), 4) if completo(p) else None for p, x in zip(meses, s)]


def agregar(base: pd.DataFrame, regras_grupo: list | None = None) -> dict:
    # Empresas do mesmo grupo econômico (3R Potiguar, Enauta... -> Brava Energia) somam juntas.
    regras = grupos.carregar() if regras_grupo is None else regras_grupo
    base = base.assign(grupo=base["operador"].map(lambda o: grupos.grupo_de(o, regras)))
    nomes_grupos = {g for _, g in regras}

    def ng(nome):  # nome do grupo como escrito na tabela (ex.: 'PRIO'); senão, a grafia da ANP
        return nome if nome in nomes_grupos else nome_bonito(nome)
    ultimo = base["periodo"].max()
    # Meses em que a ANP publicou dados de cada ambiente. Há trimestres de terra com
    # um mês faltando no arquivo; esses meses aparecem como lacuna, nunca como zero.
    presentes = set(zip(base["periodo"], base["ambiente"]))

    def completo_para(ambientes):
        return lambda per: all((per, a) in presentes for a in ambientes)
    ant, ano_ant = mes_anterior(ultimo, 1), mes_anterior(ultimo, 12)
    todos_meses = sorted(base["periodo"].unique())
    m24 = [p for p in todos_meses if p > mes_anterior(ultimo, 24)]
    m36 = [p for p in todos_meses if p > mes_anterior(ultimo, 36)]
    m60 = [p for p in todos_meses if p > mes_anterior(ultimo, 60)]

    # ---------- Brasil ----------
    br = _soma_mensal(base)
    terra = _soma_mensal(base[base["ambiente"] == "terra"]).set_index("periodo")["boe"]
    br["terra_pct"] = br["periodo"].map(terra).fillna(0) / br["boe"] * 100   # participação em boe
    b = br.set_index("periodo")

    def br_val(col, per):
        return float(b.at[per, col]) if per in b.index else None

    brasil = {
        "serie": {
            "periodo": m24,
            "boe": _serie(br, m24, "boe", 1000, completo_para({"terra", "mar"})),             # mil boe/d
            "petroleo": _serie(br, m24, "petroleo", 1000, completo_para({"terra", "mar"})),   # mil bbl/d
            "gas": _serie(br, m24, "gas", 1000, completo_para({"terra", "mar"})),             # milhões m³/d
            "agua": _serie(br, m24, "agua", 1000, completo_para({"terra", "mar"})),
        },
        "boe": br_val("boe", ultimo) / 1000,               # mil boe/d
        "petroleo": br_val("petroleo", ultimo) / 1000,     # mil bbl/d
        "gas": br_val("gas", ultimo) / 1000,               # milhões m³/d
        "pocos": int(br_val("pocos", ultimo)),
        "terra_pct": br_val("terra_pct", ultimo),
        "var12_boe": variacao(br_val("boe", ultimo), br_val("boe", ano_ant)),
        "var12_petroleo": variacao(br_val("petroleo", ultimo), br_val("petroleo", ano_ant)),
        "var12_gas": variacao(br_val("gas", ultimo), br_val("gas", ano_ant)),
        "var12_pocos": variacao(br_val("pocos", ultimo), br_val("pocos", ano_ant)),
        "var12_terra_pp": (br_val("terra_pct", ultimo) - br_val("terra_pct", ano_ant))
        if br_val("terra_pct", ano_ant) is not None else None,
        "varmes_boe": variacao(br_val("boe", ultimo), br_val("boe", ant)),
        "varmes_petroleo": variacao(br_val("petroleo", ultimo), br_val("petroleo", ant)),
        "boe_terra": float(terra.get(ultimo, 0.0)) / 1000,
        "varmes_terra": variacao(terra.get(ultimo), terra.get(ant)),
    }
    total_boe = br_val("boe", ultimo) or 1.0

    # ---------- Campos ----------
    cam = _soma_mensal(base, ["campo"])
    meta_u = (base.sort_values("periodo").groupby("campo")
              .agg(bacia=("bacia", "last"), estado=("estado", "last"), operador=("operador", "last"),
                   grupo=("grupo", "last")))
    amb_campo = base.groupby("campo")["ambiente"].agg(lambda s: s.value_counts().idxmax())
    cam_u = cam[cam["periodo"] == ultimo].set_index("campo")
    cam_ant = cam[cam["periodo"] == ant].set_index("campo")
    cam_aa = cam[cam["periodo"] == ano_ant].set_index("campo")

    def v(tab, campo, col):
        return float(tab.at[campo, col]) if campo in tab.index else None

    campos = []
    pocos_u = base[base["periodo"] == ultimo].assign(
        boe=lambda d: _boe(d["petroleo_bbl_d"], d["gas_mil_m3_d"]))
    b_ant = base[base["periodo"] == ant]
    pocos_ant = (b_ant.assign(boe=_boe(b_ant["petroleo_bbl_d"], b_ant["gas_mil_m3_d"]))
                 .groupby(["campo", "poco"])["boe"].sum())
    for campo in cam_u.index:
        boe, pet, agua, gas, np_ = (v(cam_u, campo, k) for k in ("boe", "petroleo", "agua", "gas", "pocos"))
        boe_aa, pet_aa, agua_aa, np_aa = (v(cam_aa, campo, k) for k in ("boe", "petroleo", "agua", "pocos"))
        boe_ant = v(cam_ant, campo, "boe")
        meta = meta_u.loc[campo]
        serie = cam[cam["campo"] == campo].set_index("periodo").reindex(m36).fillna(0.0)
        tem_dado = [(p, amb_campo[campo]) in presentes for p in m36]
        bsw_serie = [round(_bsw(a, p), 3) if ok and (a + p) > 0 else None
                     for a, p, ok in zip(serie["agua"], serie["petroleo"], tem_dado)]
        pet_serie = [round(float(x), 2) if ok else None for x, ok in zip(serie["petroleo"], tem_dado)]
        boe_serie = [round(float(x), 2) if ok else None for x, ok in zip(serie["boe"], tem_dado)]
        top = pocos_u[pocos_u["campo"] == campo].sort_values("boe", ascending=False).head(8)
        top_pocos = []
        for _, r in top.iterrows():
            anterior = pocos_ant.get((campo, r["poco"]))
            top_pocos.append({
                "poco": r["poco"], "boe": r["boe"], "petroleo": r["petroleo_bbl_d"],
                "gas": r["gas_mil_m3_d"], "agua": r["agua_bbl_d"],
                "bsw": _bsw(r["agua_bbl_d"], r["petroleo_bbl_d"]),
                "horas": None if pd.isna(r["horas"]) else r["horas"],
                "varmes": variacao(r["boe"], anterior) if anterior else None,
                "novo": bool(anterior is None or anterior == 0),
            })
        info = estado_info(meta["estado"])
        campos.append({
            "nome": nome_campo(campo), "chave": campo, "slug": slug(campo),
            "bacia": nome_bonito(meta["bacia"]), "bacia_slug": slug(meta["bacia"]),
            "estado": info, "ambiente": amb_campo[campo],
            "operador": nome_bonito(meta["operador"]),            # empresa registrada na ANP
            "grupo": ng(meta["grupo"]), "operador_slug": slug(meta["grupo"]),
            # boe/d é o número principal; petróleo (bbl/d) e gás (mil m³/d) vêm junto
            "boe": boe, "petroleo": pet, "gas": gas, "agua": agua, "pocos": int(np_ or 0),
            "gas_pct": (boe - pet) / boe * 100 if boe else None,
            "bsw": _bsw(agua, pet), "por_poco": boe / np_ if np_ else None,
            "var12": variacao(boe, boe_aa), "var12_petroleo": variacao(pet, pet_aa),
            "var12_gas": variacao(gas, v(cam_aa, campo, "gas")),
            "var12_agua": variacao(agua, agua_aa), "var12_pocos": variacao(np_, np_aa),
            "var12_porpoco": variacao(boe / np_ if np_ else None, boe_aa / np_aa if np_aa else None),
            "bsw_pp": (_bsw(agua, pet) - _bsw(agua_aa, pet_aa))
            if pet_aa is not None and _bsw(agua_aa, pet_aa) is not None and _bsw(agua, pet) is not None else None,
            "varmes": variacao(boe, boe_ant),
            "delta_mes": boe - (boe_ant or 0.0),
            "anterior_mes": boe_ant,
            "serie": {"periodo": m36, "boe": boe_serie, "petroleo": pet_serie, "bsw": bsw_serie},
            "top_pocos": top_pocos,
        })
    campos.sort(key=lambda c: c["boe"], reverse=True)

    # posição no estado e ambiente; vizinhos na mesma bacia
    for c in campos:
        mesmos = [x for x in campos if x["estado"]["uf"] == c["estado"]["uf"] and x["ambiente"] == c["ambiente"]]
        c["rank_estado"] = next(k for k, x in enumerate(mesmos) if x is c) + 1
        bacia = [x for x in campos if x["bacia_slug"] == c["bacia_slug"]]
        i = next(k for k, x in enumerate(bacia) if x is c)
        ini = max(0, min(i - 2, len(bacia) - 5))
        c["vizinhos"] = [{"nome": x["nome"], "slug": x["slug"], "boe": x["boe"], "bsw": x["bsw"],
                          "por_poco": x["por_poco"], "eu": x is c} for x in bacia[ini:ini + 5]]

    # ---------- Operadores ----------
    # agrupadas por grupo econômico; a coluna se chama "operador" para os modelos do site
    base_g = base.drop(columns=["operador"]).rename(columns={"grupo": "operador"})
    op = _soma_mensal(base_g, ["operador"])
    op_u = op[op["periodo"] == ultimo].sort_values("boe", ascending=False)
    operadores = []
    terra_op = _soma_mensal(base_g[base_g["ambiente"] == "terra"], ["operador"])
    terra_op = terra_op[terra_op["periodo"] == ultimo].set_index("operador")["boe"]
    ent_u = (pocos_u.groupby(["grupo", "operador"])["boe"].sum().sort_values(ascending=False))
    total_terra = brasil["boe_terra"] * 1000 or 1.0
    for rank, (_, r) in enumerate(op_u.iterrows(), start=1):
        nome = r["operador"]
        hist = op[op["operador"] == nome].set_index("periodo")

        def h(per, col):
            return float(hist.at[per, col]) if per in hist.index else None

        meus = [c for c in campos if c["grupo"] == ng(nome)]
        entidades = [{"nome": nome_bonito(e), "boe": float(v_) / 1000}
                     for (g, e), v_ in ent_u.items() if g == nome]
        por_bacia = {}
        for c in meus:
            por_bacia[c["bacia"]] = por_bacia.get(c["bacia"], 0.0) + c["boe"]
        tot, pet = r["boe"], r["petroleo"]
        tot_terra = float(terra_op.get(nome, 0.0))
        operadores.append({
            "nome": ng(nome), "slug": slug(nome), "rank": rank,
            "boe": tot / 1000, "petroleo": pet / 1000, "gas": r["gas"] / 1000, "pocos": int(r["pocos"]),
            "bsw": _bsw(r["agua"], pet), "share": tot / total_boe * 100,
            "terra_pct": tot_terra / tot * 100 if tot else 0.0,
            "share_terra": tot_terra / total_terra * 100,
            "var12": variacao(tot, h(ano_ant, "boe")), "var12_petroleo": variacao(pet, h(ano_ant, "petroleo")),
            "var12_gas": variacao(r["gas"], h(ano_ant, "gas")),
            "var12_pocos": variacao(r["pocos"], h(ano_ant, "pocos")),
            "bsw_pp": (_bsw(r["agua"], pet) - _bsw(h(ano_ant, "agua"), h(ano_ant, "petroleo")))
            if h(ano_ant, "petroleo") and _bsw(r["agua"], pet) is not None else None,
            "varmes": variacao(tot, h(ant, "boe")),
            # lacuna só quando falta um ambiente que pesa mais de 2% na produção da operadora
            "serie": {"periodo": m60, "boe": _serie(
                op[op["operador"] == nome], m60, "boe", 1000,
                completo_para({a for a, peso in (("terra", tot_terra), ("mar", tot - tot_terra))
                               if tot and peso / tot > 0.02}))},
            "campos": meus, "n_campos": len(meus), "entidades": entidades,
            "bacias": sorted(({"nome": k, "pct": v_ / tot * 100 if tot else 0} for k, v_ in por_bacia.items()),
                             key=lambda x: -x["pct"]),
        })

    # ---------- Estados e bacias ----------
    base_r = base_g.assign(uf=base_g["estado"].map(lambda v: estado_info(v)["uf"]),
                           bacia=base_g["bacia"].map(nome_bonito))

    def regioes(chave: str, de_campo) -> list[dict]:
        """Uma entrada por estado (chave 'uf') ou bacia (chave 'bacia'), na ordem da produção total."""
        tab = _soma_mensal(base_r, [chave])
        terra_t = _soma_mensal(base_r[base_r["ambiente"] == "terra"], [chave])
        terra_u = terra_t[terra_t["periodo"] == ultimo].set_index(chave)["boe"]
        por_op = _soma_mensal(base_r[base_r["periodo"].isin([ultimo, ano_ant])], [chave, "operador"])
        u = tab[tab["periodo"] == ultimo].sort_values("boe", ascending=False)
        saida = []
        for rank, (_, r) in enumerate(u.iterrows(), start=1):
            k = r[chave]
            hist = tab[tab[chave] == k].set_index("periodo")

            def h(per, col):
                return float(hist.at[per, col]) if per in hist.index else None

            tot, pet = r["boe"], r["petroleo"]
            tt = float(terra_u.get(k, 0.0))
            meus = [c for c in campos if de_campo(c) == k]
            ou = por_op[(por_op["periodo"] == ultimo) & (por_op[chave] == k)].sort_values("boe", ascending=False)
            oa = por_op[(por_op["periodo"] == ano_ant) & (por_op[chave] == k)].set_index("operador")["boe"]
            ops = [{"nome": ng(o), "slug": slug(o), "boe": b_ / 1000, "pct": b_ / tot * 100 if tot else 0.0,
                    "var12": variacao(b_, oa.get(o))} for o, b_ in zip(ou["operador"], ou["boe"]) if b_ > 0]
            saida.append({
                "chave": k, "rank": rank,
                "boe": tot / 1000, "petroleo": pet / 1000, "gas": r["gas"] / 1000, "pocos": int(r["pocos"]),
                "bsw": _bsw(r["agua"], pet), "share": tot / total_boe * 100,
                "terra_pct": tt / tot * 100 if tot else 0.0, "terra": tt / tot > 0.98 if tot else False,
                "var12": variacao(tot, h(ano_ant, "boe")), "var12_petroleo": variacao(pet, h(ano_ant, "petroleo")),
                "var12_gas": variacao(r["gas"], h(ano_ant, "gas")), "var12_pocos": variacao(r["pocos"], h(ano_ant, "pocos")),
                "varmes": variacao(tot, h(ant, "boe")),
                "serie": {"periodo": m60, "boe": _serie(
                    tab[tab[chave] == k], m60, "boe", 1000,
                    completo_para({a for a, peso in (("terra", tt), ("mar", tot - tt)) if tot and peso / tot > 0.02}))},
                "operadores": ops, "campos": meus, "n_campos": len(meus),
            })
        return saida

    estados = regioes("uf", lambda c: c["estado"]["uf"])
    for e in estados:
        info = estado_info(e["chave"])
        valido = info["uf"] in UFS
        e.update(nome=info["nome"] if valido else "Não informado", uf=info["uf"], de=info["de"],
                 slug=slug(info["nome"]) if valido else None)
        e["artigo"] = {"da": "a", "do": "o"}.get(info["de"].split(" ")[0], "")
        por_bacia = {}
        for c in e["campos"]:
            por_bacia[c["bacia"]] = por_bacia.get(c["bacia"], 0.0) + c["boe"]
        e["cruzado"] = sorted(({"nome": k, "slug": slug(k), "pct": v_ / (e["boe"] * 1000) * 100 if e["boe"] else 0}
                               for k, v_ in por_bacia.items()), key=lambda x: -x["pct"])
    bacias = regioes("bacia", lambda c: c["bacia"])
    for b_ in bacias:
        nome = b_["chave"]
        prep = PREP_BACIA.get(sem_acento(nome).lower(), "de")
        b_.update(nome=nome, slug=slug(nome), titulo=f"Bacia {prep} {nome}".replace("  ", " "))
        por_uf = {}
        for c in b_["campos"]:
            por_uf[c["estado"]["uf"]] = por_uf.get(c["estado"]["uf"], 0.0) + c["boe"]
        b_["cruzado"] = sorted(({"nome": estado_info(k)["nome"] if k in UFS else "Não informado",
                                 "slug": slug(estado_info(k)["nome"]) if k in UFS else None,
                                 "pct": v_ / (b_["boe"] * 1000) * 100 if b_["boe"] else 0}
                                for k, v_ in por_uf.items()), key=lambda x: -x["pct"])
    por_slug_e = {e["uf"]: e["slug"] for e in estados}
    por_slug_b = {b_["nome"]: b_["slug"] for b_ in bacias}
    for c in campos:
        c["estado_slug"] = por_slug_e.get(c["estado"]["uf"])
        c["bacia_slug"] = por_slug_b.get(c["bacia"])

    # ---------- Rankings de campos (mês contra mês anterior) ----------
    def ranking(filtro):
        lista = [c for c in campos if filtro(c) and c["anterior_mes"] is not None]
        altas = sorted([c for c in lista if c["delta_mes"] > 0], key=lambda c: -c["delta_mes"])[:10]
        quedas = sorted([c for c in lista if c["delta_mes"] < 0], key=lambda c: c["delta_mes"])[:10]
        return {"altas": altas, "quedas": quedas}

    rankings = {
        "todos": ranking(lambda c: True),
        "terra": ranking(lambda c: c["ambiente"] == "terra"),
        "mar": ranking(lambda c: c["ambiente"] == "mar"),
    }

    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ultimo": ultimo, "anterior": ant, "ano_anterior": ano_ant,
        "brasil": brasil, "bacias": bacias, "estados": estados, "campos": campos,
        "operadores": operadores, "rankings": rankings,
    }

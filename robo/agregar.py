"""Transforma a base poço a poço nos números que o site mostra."""
from datetime import datetime, timezone

import pandas as pd

from . import grupos
from .util import mes_anterior, nome_bonito, slug, variacao

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


def _ativos(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["petroleo_bbl_d"] > 0) | (df["gas_mil_m3_d"] > 0)]


def _soma_mensal(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    chaves = ["periodo"] + (por or [])
    g = df.groupby(chaves).agg(
        petroleo=("petroleo_bbl_d", "sum"),
        gas=("gas_mil_m3_d", "sum"),
        agua=("agua_bbl_d", "sum"),
    )
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
    terra = _soma_mensal(base[base["ambiente"] == "terra"]).set_index("periodo")["petroleo"]
    br["terra_pct"] = br["periodo"].map(terra).fillna(0) / br["petroleo"] * 100
    b = br.set_index("periodo")

    def br_val(col, per):
        return float(b.at[per, col]) if per in b.index else None

    brasil = {
        "serie": {
            "periodo": m24,
            "petroleo": _serie(br, m24, "petroleo", 1000, completo_para({"terra", "mar"})),   # mil bbl/d
            "gas": _serie(br, m24, "gas", 1000, completo_para({"terra", "mar"})),             # milhões m³/d
            "agua": _serie(br, m24, "agua", 1000, completo_para({"terra", "mar"})),
        },
        "petroleo": br_val("petroleo", ultimo) / 1000,
        "gas": br_val("gas", ultimo) / 1000,
        "pocos": int(br_val("pocos", ultimo)),
        "terra_pct": br_val("terra_pct", ultimo),
        "var12_petroleo": variacao(br_val("petroleo", ultimo), br_val("petroleo", ano_ant)),
        "var12_gas": variacao(br_val("gas", ultimo), br_val("gas", ano_ant)),
        "var12_pocos": variacao(br_val("pocos", ultimo), br_val("pocos", ano_ant)),
        "var12_terra_pp": (br_val("terra_pct", ultimo) - br_val("terra_pct", ano_ant))
        if br_val("terra_pct", ano_ant) is not None else None,
        "varmes_petroleo": variacao(br_val("petroleo", ultimo), br_val("petroleo", ant)),
        "petroleo_terra": float(terra.get(ultimo, 0.0)) / 1000,
        "varmes_terra": variacao(terra.get(ultimo), terra.get(ant)),
    }
    total_pet = br_val("petroleo", ultimo) or 1.0

    # ---------- Bacias ----------
    bac = _soma_mensal(base, ["bacia"])
    bac_u = bac[bac["periodo"] == ultimo].sort_values("petroleo", ascending=False)
    amb_bacia = base[base["periodo"] == ultimo].groupby("bacia")["ambiente"].agg(lambda s: set(s))
    bacias = []
    for _, r in bac_u.iterrows():
        anterior = bac[(bac["bacia"] == r["bacia"]) & (bac["periodo"] == ano_ant)]["petroleo"]
        bacias.append({
            "nome": nome_bonito(r["bacia"]),
            "slug": slug(r["bacia"]),
            "petroleo": r["petroleo"] / 1000,
            "terra": amb_bacia.get(r["bacia"], set()) == {"terra"},
            "var12": variacao(r["petroleo"], anterior.iloc[0] if len(anterior) else None),
        })

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
    pocos_u = base[base["periodo"] == ultimo]
    pocos_ant = base[base["periodo"] == ant].set_index(["campo", "poco"])["petroleo_bbl_d"]
    for campo in cam_u.index:
        pet, agua, gas, np_ = (v(cam_u, campo, k) for k in ("petroleo", "agua", "gas", "pocos"))
        pet_aa, agua_aa, np_aa = (v(cam_aa, campo, k) for k in ("petroleo", "agua", "pocos"))
        meta = meta_u.loc[campo]
        serie = cam[cam["campo"] == campo].set_index("periodo").reindex(m36).fillna(0.0)
        tem_dado = [(p, amb_campo[campo]) in presentes for p in m36]
        bsw_serie = [round(_bsw(a, p), 3) if ok and (a + p) > 0 else None
                     for a, p, ok in zip(serie["agua"], serie["petroleo"], tem_dado)]
        pet_serie = [round(float(x), 2) if ok else None for x, ok in zip(serie["petroleo"], tem_dado)]
        top = pocos_u[pocos_u["campo"] == campo].sort_values("petroleo_bbl_d", ascending=False).head(8)
        top_pocos = []
        for _, r in top.iterrows():
            anterior = pocos_ant.get((campo, r["poco"]))
            top_pocos.append({
                "poco": r["poco"], "petroleo": r["petroleo_bbl_d"], "agua": r["agua_bbl_d"],
                "bsw": _bsw(r["agua_bbl_d"], r["petroleo_bbl_d"]),
                "horas": None if pd.isna(r["horas"]) else r["horas"],
                "varmes": variacao(r["petroleo_bbl_d"], anterior) if anterior else None,
                "novo": bool(anterior is None or anterior == 0),
            })
        info = estado_info(meta["estado"])
        campos.append({
            "nome": nome_bonito(campo), "chave": campo, "slug": slug(campo),
            "bacia": nome_bonito(meta["bacia"]), "bacia_slug": slug(meta["bacia"]),
            "estado": info, "ambiente": amb_campo[campo],
            "operador": nome_bonito(meta["operador"]),            # empresa registrada na ANP
            "grupo": ng(meta["grupo"]), "operador_slug": slug(meta["grupo"]),
            "petroleo": pet, "gas": gas, "agua": agua, "pocos": int(np_ or 0),
            "bsw": _bsw(agua, pet), "por_poco": pet / np_ if np_ else None,
            "var12": variacao(pet, pet_aa), "var12_gas": variacao(gas, v(cam_aa, campo, "gas")),
            "var12_agua": variacao(agua, agua_aa), "var12_pocos": variacao(np_, np_aa),
            "var12_porpoco": variacao(pet / np_ if np_ else None, pet_aa / np_aa if np_aa else None),
            "bsw_pp": (_bsw(agua, pet) - _bsw(agua_aa, pet_aa))
            if pet_aa is not None and _bsw(agua_aa, pet_aa) is not None and _bsw(agua, pet) is not None else None,
            "varmes": variacao(pet, v(cam_ant, campo, "petroleo")),
            "delta_mes": pet - (v(cam_ant, campo, "petroleo") or 0.0),
            "anterior_mes": v(cam_ant, campo, "petroleo"),
            "serie": {"periodo": m36, "petroleo": pet_serie, "bsw": bsw_serie},
            "top_pocos": top_pocos,
        })
    campos.sort(key=lambda c: c["petroleo"], reverse=True)

    # posição no estado e ambiente; vizinhos na mesma bacia
    for c in campos:
        mesmos = [x for x in campos if x["estado"]["uf"] == c["estado"]["uf"] and x["ambiente"] == c["ambiente"]]
        c["rank_estado"] = next(k for k, x in enumerate(mesmos) if x is c) + 1
        bacia = [x for x in campos if x["bacia_slug"] == c["bacia_slug"]]
        i = next(k for k, x in enumerate(bacia) if x is c)
        ini = max(0, min(i - 2, len(bacia) - 5))
        c["vizinhos"] = [{"nome": x["nome"], "slug": x["slug"], "petroleo": x["petroleo"], "bsw": x["bsw"],
                          "por_poco": x["por_poco"], "eu": x is c} for x in bacia[ini:ini + 5]]

    # ---------- Operadores ----------
    # agrupadas por grupo econômico; a coluna se chama "operador" para os modelos do site
    base_g = base.drop(columns=["operador"]).rename(columns={"grupo": "operador"})
    op = _soma_mensal(base_g, ["operador"])
    op_u = op[op["periodo"] == ultimo].sort_values("petroleo", ascending=False)
    operadores = []
    terra_op = _soma_mensal(base_g[base_g["ambiente"] == "terra"], ["operador"])
    terra_op = terra_op[terra_op["periodo"] == ultimo].set_index("operador")["petroleo"]
    ent_u = (base[base["periodo"] == ultimo].groupby(["grupo", "operador"])["petroleo_bbl_d"].sum()
             .sort_values(ascending=False))
    total_terra = brasil["petroleo_terra"] * 1000 or 1.0
    for rank, (_, r) in enumerate(op_u.iterrows(), start=1):
        nome = r["operador"]
        hist = op[op["operador"] == nome].set_index("periodo")

        def h(per, col):
            return float(hist.at[per, col]) if per in hist.index else None

        meus = [c for c in campos if c["grupo"] == ng(nome)]
        entidades = [{"nome": nome_bonito(e), "petroleo": float(v_) / 1000}
                     for (g, e), v_ in ent_u.items() if g == nome]
        por_bacia = {}
        for c in meus:
            por_bacia[c["bacia"]] = por_bacia.get(c["bacia"], 0.0) + c["petroleo"]
        pet = r["petroleo"]
        pet_terra = float(terra_op.get(nome, 0.0))
        operadores.append({
            "nome": ng(nome), "slug": slug(nome), "rank": rank,
            "petroleo": pet / 1000, "gas": r["gas"] / 1000, "pocos": int(r["pocos"]),
            "bsw": _bsw(r["agua"], pet), "share": pet / total_pet * 100,
            "terra_pct": pet_terra / pet * 100 if pet else 0.0,
            "share_terra": pet_terra / total_terra * 100,
            "var12": variacao(pet, h(ano_ant, "petroleo")), "var12_gas": variacao(r["gas"], h(ano_ant, "gas")),
            "var12_pocos": variacao(r["pocos"], h(ano_ant, "pocos")),
            "bsw_pp": (_bsw(r["agua"], pet) - _bsw(h(ano_ant, "agua"), h(ano_ant, "petroleo")))
            if h(ano_ant, "petroleo") and _bsw(r["agua"], pet) is not None else None,
            "varmes": variacao(pet, h(ant, "petroleo")),
            # lacuna só quando falta um ambiente que pesa mais de 2% na produção da operadora
            "serie": {"periodo": m60, "petroleo": _serie(
                op[op["operador"] == nome], m60, "petroleo", 1000,
                completo_para({a for a, peso in (("terra", pet_terra), ("mar", pet - pet_terra))
                               if pet and peso / pet > 0.02}))},
            "campos": meus, "n_campos": len(meus), "entidades": entidades,
            "bacias": sorted(({"nome": k, "pct": v_ / pet * 100 if pet else 0} for k, v_ in por_bacia.items()),
                             key=lambda x: -x["pct"]),
        })

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
        "brasil": brasil, "bacias": bacias, "campos": campos,
        "operadores": operadores, "rankings": rankings,
    }

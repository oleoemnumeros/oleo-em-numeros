"""Gera o site estático a partir de dados/processados/agregados.json.

Uso:
    python -m gerador.gerar                        # produção, em publico/
    python -m gerador.gerar --teste                # mostra a faixa de dados fictícios
    python -m gerador.gerar --links-limpos         # links sem 'index.html' (servidor web)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
from html import unescape
from urllib.parse import urljoin
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from robo import config
from robo.agregar import PREP_BACIA
from robo.util import MESES, MESES_CURTOS, fmt, fmt_inteligente, fmt_pp, fmt_var, mes_curto, mes_extenso, sem_acento, slug

from .graficos import linha
from .financeiro import carregar_financeiro, reais
from .gas import preparar as preparar_gas
from .precos import UFS
from .precos import preparar as preparar_precos
from .semanal import SEMANAS_NO_SITE, dia, dia_curto
from .semanal import card as card_semana
from .semanal import carregar as carregar_semanal
from .semanal import montar as montar_semana
from .semanal import centavos, por_municipio, ranking_municipios
from .refino import preparar as preparar_refino
from .resumo import _mes_antes, manchete as resumo_manchete, montar as montar_resumo, resumo_dados, resumo_meses  # noqa: F401
from .mapa import gerar_mapa
from .pocos import gerar_pocos
from .royalties import dinheiro, escala, preparar as preparar_royalties, resumo_municipio
from .familia_card import TEXTO_COMPARTILHE, bloco_imagem, gera_card, sem_imagem, seta
from .familia import carregar as carregar_familia, chave_cidade, continuar as continuar_cartoes

AQUI = Path(__file__).resolve().parent
ICONE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30"><path d="M15 2c4.5 5.6 8 10.2 8 14.6a8 8 0 0 1-16 0'
         'C7 12.2 10.5 7.6 15 2z" fill="#0F4C5C"/><rect x="10.5" y="16" width="2.2" height="6" rx="1" fill="#fff"/>'
         '<rect x="13.9" y="12.5" width="2.2" height="9.5" rx="1" fill="#fff"/><rect x="17.3" y="14.5" width="2.2" '
         'height="7.5" rx="1" fill="#fff"/></svg>')


# ---------------- textos automáticos ----------------

DESCONHECIDO = "Não identificado"  # campos sem operadora informada nos dados da ANP


def _mais_menos(v):
    if v is None:
        return None
    if abs(v) < 0.0005:
        return "praticamente o mesmo"
    return f"{fmt(abs(v) * 100, 1)}% a {'mais' if v > 0 else 'menos'}"


def titulo_bacia(nome: str) -> str:
    """'Recôncavo' -> 'Bacia do Recôncavo'; 'Santos' -> 'Bacia de Santos'; 'Potiguar' -> 'Bacia Potiguar'."""
    prep = PREP_BACIA.get(sem_acento(str(nome)).lower(), "de")
    return f"Bacia {prep} {nome}".replace("  ", " ")


def pct(v, casas: int = 1) -> str:
    """Percentual, ou travessão quando não há valor (antes saía '—%')."""
    return "—" if v is None else fmt(v, casas) + "%"


def _parte_nacional(share) -> str:
    return "menos de 0,1%" if share is not None and share < 0.05 else f"{fmt(share, 1)}%"


def titulo_tendencia(nome, v):
    if v is None:
        return nome
    if abs(v) < 0.005:
        return f"{nome} estável em 12 meses"
    return f"{nome} {'sobe' if v > 0 else 'cai'} {fmt(abs(v) * 100, 1)}% em 12 meses"


def resumo_campo(c, ag):
    partes = [f"Em {mes_extenso(ag['ultimo'])}, o {c['nome']} produziu {fmt(c['boe'])} barris de óleo equivalente "
              f"por dia (boe/d)"]
    mm = _mais_menos(c["var12"])
    partes[0] += f", {mm} que um ano antes." if mm else "."
    if c["petroleo"] and c["gas"]:
        partes.append(f"Foram {fmt(c['petroleo'])} barris de petróleo e {fmt_inteligente(c['gas'])} mil m³ de gás "
                      f"natural por dia; o gás responde por {fmt(c['gas_pct'], 0)}% do total.")
    elif c["gas"]:
        partes.append(f"É um campo de gás: {fmt_inteligente(c['gas'])} mil m³ por dia, sem petróleo no mês.")
    if c["agua"] and c["petroleo"]:
        partes.append(f"Para cada barril de petróleo saíram {fmt(c['agua'] / c['petroleo'], 1)} barris de água "
                      f"(BSW de {fmt(c['bsw'], 1)}%).")
    lugar = "em terra" if c["ambiente"] == "terra" else "no mar"
    ordem = "o maior" if c["rank_estado"] == 1 else f"o {c['rank_estado']}º maior"
    partes.append(f"É {ordem} campo {lugar} {c['estado']['de']}.")
    return " ".join(partes)


def aviso_campo(c, campos) -> str:
    """Explica os campos que a ANP divide por contrato (Búzios e Búzios ECO são a mesma área, com os mesmos
    poços) e o sufixo AnC. Devolve '' para os demais."""
    chave = c["chave"].upper()
    por_chave = {x["chave"].upper(): x for x in campos}
    if chave.startswith("ANC_"):
        return ("AnC é a sigla de Área não Contratada: área da União, sem contrato próprio, "
                "cuja produção a ANP informa como um campo à parte.")
    if chave.endswith("_ECO"):
        eco, base = c, por_chave.get(chave[:-4])
    else:
        eco, base = por_chave.get(chave + "_ECO"), c
    if eco is None:
        return ""
    if base is None:
        return (f"ECO é a sigla de Excedente da Cessão Onerosa, contrato de partilha da produção. "
                f"A ANP informa a produção do {eco['nome']} como um campo à parte.")
    texto = (f"ECO é a sigla de Excedente da Cessão Onerosa. A ANP informa a área de {base['nome']} em dois campos, "
             f"um para cada contrato: {base['nome']} (cessão onerosa) e {eco['nome']} (excedente, em partilha da "
             f"produção). Os poços são os mesmos nos dois e a produção de cada poço é dividida entre os contratos.")
    soma = base["boe"] + eco["boe"]
    texto += f" Somados, os dois produziram {fmt(soma)} boe/d no mês"
    if all(soma > x["boe"] for x in campos if x is not base and x is not eco):
        texto += ", mais que qualquer outro campo do país"
    return texto + "."


def _volume(mil, unidade_mil: str, unidade: str) -> str:
    """Abaixo de 1 mil, escreve em unidades: '870 barris' em vez de '0,87 mil barris'."""
    return f"{fmt(mil * 1000)} {unidade}" if mil is not None and 0 < mil < 1 else f"{fmt_inteligente(mil)} {unidade_mil}"


def resumo_operadora(o, ag):
    desconhecido = o["nome"] == DESCONHECIDO
    quem = (f"os {o['n_campos']} campos cuja operadora não aparece nos dados da ANP" if desconhecido
            else f"os campos operados pela {o['nome']}")
    partes = [f"Em {mes_extenso(ag['ultimo'])}, {quem} produziram "
              f"{_volume(o['boe'], 'mil barris', 'barris')} de óleo equivalente por dia (boe/d, com a parte dos sócios)"]
    mm = _mais_menos(o["var12"])
    partes[0] += f", {mm} que um ano antes." if mm else "."
    gas = (f"{fmt(o['gas'] * 1000)} mil m³" if o["gas"] is not None and 0 < o["gas"] < 1
           else f"{fmt_inteligente(o['gas'])} milhões de m³")
    partes.append(f"Foram {_volume(o['petroleo'], 'mil barris', 'barris')} de petróleo e {gas} de gás natural por dia.")
    if desconhecido:
        partes.append(f"Juntos, respondem por {_parte_nacional(o['share'])} da produção nacional. Esses campos ficam "
                      "fora do ranking de operadoras até que a operadora seja identificada.")
    else:
        ordem = "a maior" if o["rank"] == 1 else f"a {o['rank']}ª maior"
        partes.append(f"É {ordem} operadora do país: opera {_parte_nacional(o['share'])} da produção nacional.")
    if o["terra_pct"] >= 50 and o["share_terra"] >= 1 and not desconhecido:
        partes.append(f"Opera {fmt(o['share_terra'], 0)}% de tudo o que é produzido em terra no Brasil.")
    if o["campos"] and o["boe"]:
        top = o["campos"][0]
        pct = top["boe"] / (o["boe"] * 1000) * 100
        if len(o["campos"]) > 1:
            dono = "desses campos" if desconhecido else "da empresa"
            partes.append(f"O maior campo é o {top['nome']}, com {fmt(pct, 0)}% do total {dono}.")
    return " ".join(partes)


def resumo_regiao(r, ag, tipo):
    """Texto do 'Em resumo' das páginas de estado e de bacia."""
    # "a Bahia", "o Rio de Janeiro", "São Paulo", "a Bacia de Santos"
    sujeito = f"{r['artigo']} {r['nome']}".strip() if tipo == "estado" else f"a {r['titulo']}"
    coisa = "estado produtor" if tipo == "estado" else "bacia produtora"
    partes = [f"Em {mes_extenso(ag['ultimo'])}, {sujeito} produziu "
              f"{fmt_inteligente(r['boe'])} mil barris de óleo equivalente por dia (boe/d)"]
    mm = _mais_menos(r["var12"])
    partes[0] += f", {mm} que um ano antes." if mm else "."
    partes.append(f"Foram {fmt_inteligente(r['petroleo'])} mil barris de petróleo e {fmt_inteligente(r['gas'])} "
                  f"milhões de m³ de gás natural por dia, em {r['n_campos']} {'campo' if r['n_campos'] == 1 else 'campos'} "
                  f"e {fmt(r['pocos'])} poços ativos.")
    genero = "o maior" if tipo == "estado" else "a maior"
    ordem = genero if r["rank"] == 1 else (f"o {r['rank']}º maior" if tipo == "estado" else f"a {r['rank']}ª maior")
    partes.append(f"É {ordem} {coisa} do país, com {fmt(r['share'], 1)}% da produção nacional.")
    if r["terra"]:
        partes.append("Toda a produção vem de campos em terra.")
    elif r["terra_pct"] >= 1:
        partes.append(f"{fmt(r['terra_pct'], 0)}% vêm de campos em terra; o resto, do mar.")
    else:
        partes.append("Praticamente toda a produção vem do mar.")
    conhecidos = [o for o in r["operadores"] if o["nome"] != DESCONHECIDO]
    if conhecidos:
        o = conhecidos[0]
        if len(r["operadores"]) == 1:
            partes.append(f"A única operadora é a {o['nome']}.")
        elif len(conhecidos) == 1:
            partes.append(f"A única operadora identificada é a {o['nome']}, com {fmt(o['pct'], 0)}% do total.")
        else:
            partes.append(f"A maior operadora é a {o['nome']}, com {fmt(o['pct'], 0)}% do total, entre "
                          f"{len(conhecidos)} empresas.")
    if r["campos"]:
        c = r["campos"][0]
        partes.append(f"O maior campo é o {c['nome']}, com {fmt(c['boe'])} boe/d.")
    return " ".join(partes)


def destaques(ag, u):
    lista = []
    altas = ag["rankings"]["todos"]["altas"]
    nome_ant = MESES[int(ag["anterior"][5:]) - 1]
    if altas:
        c = altas[0]
        lista.append({"tag": "Maior alta do mês", "link": u(f"campos/{c['slug']}/"),
                      "titulo": f"{c['nome']} cresce {fmt((c['varmes'] or 0) * 100, 0)}% sobre {nome_ant}",
                      "texto": f"{titulo_bacia(c['bacia'])} · {c['grupo']} · {fmt(c['boe'])} boe/d em "
                               f"{mes_extenso(ag['ultimo'])}."})
    br = ag["brasil"]
    if br["varmes_terra"] is not None:
        alta_terra = ag["rankings"]["terra"]["altas"]
        verbo = "sobe" if br["varmes_terra"] > 0 else "cai"
        texto = f"{fmt(br['boe_terra'], 1)} mil boe/d em {mes_extenso(ag['ultimo'])}."
        if alta_terra:
            texto += f" Maior alta em terra: {alta_terra[0]['nome']}."
        lista.append({"tag": "Produção em terra", "link": u("rankings/terra/"),
                      "titulo": f"Produção em terra {verbo} {fmt(abs(br['varmes_terra']) * 100, 1)}% no mês",
                      "texto": texto})
    candidatas = [o for o in ag["operadores"] if o["var12"] is not None and o["boe"] >= 1 and o["rank"]]
    if candidatas:
        o = max(candidatas, key=lambda x: x["var12"])
        verbo = "cresce" if o["var12"] > 0 else "recua"
        lista.append({"tag": "Operadora em destaque", "link": u(f"operadoras/{o['slug']}/"),
                      "titulo": f"{o['nome']} {verbo} {fmt(abs(o['var12']) * 100, 1)}% em 12 meses",
                      "texto": f"{fmt_inteligente(o['boe'])} mil boe/d em {mes_extenso(ag['ultimo'])}, "
                               f"{o['rank']}ª maior do país."})
    return lista


# ---------------- carga ----------------

def carregar(pasta: Path) -> dict:
    ag = json.loads((pasta / "agregados.json").read_text("utf-8"))
    # "Não identificado" não é uma empresa: fica no fim da lista, sem posição no ranking
    ag["operadores"].sort(key=lambda o: (o["nome"] == DESCONHECIDO, -(o["boe"] or 0)))
    pos = 0
    for o in ag["operadores"]:
        if o["nome"] == DESCONHECIDO:
            o["rank"] = None
        else:
            pos += 1
            o["rank"] = pos
    ag["n_operadoras"] = pos
    por_slug = {c["slug"]: c for c in ag["campos"]}
    for o in ag["operadores"]:
        o["campos"] = [por_slug[s] for s in o["campos"] if s in por_slug]
    for recorte in ag["rankings"].values():
        for lado in ("altas", "quedas"):
            recorte[lado] = [por_slug[s] for s in recorte[lado] if s in por_slug]
    for chave in ("estados", "bacias"):
        for r in ag.get(chave, []):
            r["campos"] = [por_slug[s] if isinstance(s, str) else s for s in r.get("campos", []) if not isinstance(s, str) or s in por_slug]
    return ag


def carregar_noticias(pasta: Path, ag: dict):
    """Manchetes do robô de notícias, com o dia em horário de Brasília e links para as páginas citadas."""
    arq = pasta / "noticias.json"
    if not arq.exists():
        return [], {}, {}, ""
    itens = json.loads(arq.read_text("utf-8"))
    op_slug = {o["nome"]: o["slug"] for o in ag["operadores"]}
    campo_nome = {c["slug"]: c["nome"] for c in ag["campos"]}
    brt = timezone(timedelta(hours=-3))
    por_op, por_campo = defaultdict(list), defaultdict(list)
    for n in itens:
        d = datetime.fromisoformat(n["data"]).astimezone(brt)
        n["dia"] = d.strftime("%d/%m/%Y")
        n["tags"] = ([(e, f"operadoras/{op_slug[e]}/") for e in n.get("empresas", []) if e in op_slug]
                     + [(campo_nome[c], f"campos/{c}/") for c in n.get("campos", []) if c in campo_nome])
        for e in n.get("empresas", []):
            por_op[e].append(n)
        for c in n.get("campos", []):
            por_campo[c].append(n)
    fontes = sorted({n["fonte"] for n in itens})
    texto = ", ".join(fontes[:-1]) + " e " + fontes[-1] if len(fontes) > 1 else "".join(fontes)
    return itens, por_op, por_campo, texto


# ---------------- geração ----------------

def card_resumo(ag: dict, d: dict, destino: Path, site_url: str) -> bool:
    """Imagem do resumo (resumo/AAAA-MM/card.png): produção total, petróleo e gás, com as barras dos últimos 13 meses."""
    per, ant = d["per"], d["ant"]

    def var(v):
        if v is None:
            return "", None
        return f"{seta(round(v * 100, 1))} {fmt(abs(v) * 100, 1)}% sobre {mes_curto(ant)}", (None if round(v * 100, 1) == 0 else v > 0)
    serie = ag["brasil"]["serie"]
    idx = serie["periodo"].index(per)
    janela = list(zip(serie["periodo"], serie["boe"]))[max(0, idx - 12):idx + 1]
    barras = [(MESES_CURTOS[int(p[5:]) - 1], [v / 1000 if v is not None else None]) for p, v in janela]
    host = site_url.split("//")[-1]
    return gera_card(destino, "Óleo em Números", "#17758C", f"Produção de petróleo e gás · {mes_extenso(per)}",
                     [n for n in (("Petróleo e gás (boe/d)", f"{fmt(d['boe'] / 1000, 2)} mi", *var(d["boe_mes"])),
                                  ("Petróleo (barris/d)", f"{fmt(d['petroleo'] / 1000, 2)} mi", *var(d["petroleo_mes"])) if d.get("petroleo") else None,
                                  ("Gás natural (m³/d)", f"{fmt(d['gas'], 1)} mi", *var(d["gas_mes"])) if d.get("gas") else None) if n],
                     barras, None, f"Milhões de boe/d, mês a mês · Fonte: ANP · {host}/resumo/{per}/",
                     [fmt(v[0], 2) if v[0] is not None else "" for _, v in barras])


LLMS_TXT = '# Óleo em Números\n\n> Dados públicos de petróleo e gás do Brasil da ANP e da CVM, organizados por poço, campo, bacia, estado e operadora, além de royalties, preços de combustíveis, refino e gás natural.\n\nProdução total em boe (barril de óleo equivalente) e produção operada: 100% do campo fica com a operadora, como a ANP publica. Dados de produção têm defasagem de cerca de dois meses.\n\n## Principais páginas\n- [Resumo do mês](__BASE__/resumo/): produção nacional, ranking e variações\n- [Campos](__BASE__/campos/): produção, poços e operadora de cada campo\n- [Bacias](__BASE__/bacias/)\n- [Estados](__BASE__/estados/)\n- [Operadoras](__BASE__/operadoras/): produção por empresa e grupo econômico\n- [Poços](__BASE__/pocos/): série mensal de produção de cada poço desde 2018\n- [Royalties](__BASE__/royalties/): valores recebidos por estados e municípios\n- [Preços dos combustíveis](__BASE__/precos/): gasolina, etanol, diesel e GNV por município\n- [Refino](__BASE__/refino/): petróleo processado e derivados por refinaria\n- [Gás natural](__BASE__/gas/) e [queima de gás](__BASE__/gas/queima/)\n- [Resultados das empresas listadas](__BASE__/resultados/): dados da CVM\n- [Mapa](__BASE__/mapa/): campos e poços\n- [Notícias](__BASE__/noticias/)\n- [Sobre e metodologia](__BASE__/sobre/): fontes, defasagens e fórmulas\n\n## Fontes e atualização\n- ANP (dados abertos), CVM (ITR e DFP) e IBGE (população). Atualização automática, em geral mensal.\n- Preços de combustíveis cobrem cerca de 400 municípios pesquisados pela ANP e mostram só médias, nunca postos.\n\n## Como citar\n- Cite o nome do portal e o link da página usada. Os dados são públicos e os links levam à fonte oficial.\n- Cada página mostra o período dos dados. Informe o período ao usar um número.\n- O portal é independente e não tem ligação com governo, empresas ou instituições financeiras.\n\n## Família em Números\nPortais independentes de dados públicos do Brasil: Óleo em Números (https://oleoemnumeros.com.br), Comex em Números (https://comexemnumeros.com.br), Voo em Números (https://vooemnumeros.com.br) e Economia em Números (https://economiaemnumeros.com.br).\n\n## Contato\n- Erros, sugestões e pedidos de dados: __BASE__/contato/\n'


def gerar(dados: Path, saida: Path, teste: bool = False, limpos: bool = False, url: str | None = None) -> list[str]:
    ag = carregar(dados)
    site = {
        "nome": "Óleo em Números",
        "url": (url or os.environ.get("SITE_URL") or "https://oleoemnumeros.com.br").rstrip("/"),
        "teste": teste,
        "previa": os.environ.get("SITE_PREVIA", "0") == "1",
        "contato": os.environ.get("SITE_CONTATO") or "oleoemnumeros@gmail.com",  # variável do GitHub, se houver, prevalece
    }
    env = Environment(loader=FileSystemLoader(AQUI / "modelos"), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(fmt=fmt, fmt_var=fmt_var, fmt_pp=fmt_pp, fmt_inteligente=fmt_inteligente,
                       mes_extenso=mes_extenso, linha=linha, titulo_tendencia=titulo_tendencia,
                       titulo_bacia=titulo_bacia, pct=pct, DESCONHECIDO=DESCONHECIDO)

    # o endereço do CSS leva o hash do conteúdo: quem abriu o site pouco antes de uma atualização não fica com o estilo antigo
    env.globals["v_css"] = hashlib.md5((AQUI / "estatico" / "estilo.css").read_bytes()).hexdigest()[:8]
    env.globals["v_js"] = hashlib.md5((AQUI / "estatico" / "familia.js").read_bytes()).hexdigest()[:8]
    env.globals["v_busca"] = hashlib.md5((AQUI / "estatico" / "familia-busca.js").read_bytes()).hexdigest()[:8] + "-" + str(ag.get("gerado_em", ""))[:10]
    env.globals["v_comp"] = hashlib.md5((AQUI / "estatico" / "familia-compartilhar.js").read_bytes()).hexdigest()[:8]
    env.globals["tem_mapa"] = (dados / "geo.json").exists() and (dados / "base_pocos.csv.gz").exists()

    if saida.exists():
        shutil.rmtree(saida)
    saida.mkdir(parents=True)
    paginas = []

    noticias, noticias_op, noticias_campo, fontes_noticias = carregar_noticias(dados, ag)
    env.globals.update(fontes_noticias=fontes_noticias, reais=reais, dinheiro=dinheiro)
    R = preparar_royalties(dados)
    G = preparar_gas(dados, ag)
    env.globals.update(tem_gas=bool(G))
    RF = preparar_refino(dados)
    env.globals.update(tem_refino=bool(RF))
    PR = preparar_precos(dados)
    S = carregar_semanal(dados)                       # preços da semana (levantamento semanal da ANP)
    SEM = por_municipio(S) if S else {}
    NOMES_MUN = {(m["uf"], slug(m["nome"])): m for m in (PR or {}).get("municipios", [])}
    env.globals.update(tem_precos=bool(PR))
    env.globals.update(ufs_royalties={e["uf"] for e in R["estados"]} if R else set())
    env.globals.update(slug_royalties_uf={e["uf"]: e["slug"] for e in R["estados"]} if R else {})
    financeiro = carregar_financeiro(dados, ag)
    flags = {"gas": bool(G), "refino": bool(RF), "precos": bool(PR), "royalties": bool(R), "resultados": bool(financeiro),
             "mapa": bool(env.globals["tem_mapa"])}
    env.globals.update(tem_royalties=bool(R), tem_resultados=bool(financeiro),
                       continuar=lambda secao, caminho: continuar_cartoes(secao, caminho, flags))
    for o in ag["operadores"]:
        o["financeiro"] = financeiro.get(o["nome"])

    busca: list = []

    def escrever(caminho: str, modelo: str, raiz: str | None = None, **ctx):
        raiz = "../" * caminho.count("/") if raiz is None else raiz

        def u(alvo: str) -> str:
            destino = raiz + alvo
            if alvo == "" or alvo.endswith("/"):
                destino += "" if limpos else "index.html"
            return destino or "./"

        html = env.get_template(modelo).render(ag=ag, site=site, caminho=caminho, raiz=raiz, u=u, **ctx)
        if not (site["teste"] or site["previa"]) and not ctx.get("noindex"):
            html = com_trilha_ld(html, f"{site['url']}/{caminho}")
        arquivo = saida / (caminho + "index.html" if caminho == "" or caminho.endswith("/") else caminho)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(html, "utf-8")
        if not caminho.endswith(".html") and not ctx.get("noindex"):
            paginas.append(caminho)
            if ctx.get("titulo"):   # índice da busca do cabeçalho (familia-busca.js)
                busca.append([str(ctx["titulo"]).split(" | ")[0], "/" + caminho, TIPO_BUSCA.get(caminho.split("/")[0], "página")])
        return u

    mes = mes_extenso(ag["ultimo"])
    try:
        d_home = resumo_dados(ag, ag["ultimo"])
        resumo_home = {"per": ag["ultimo"], "manchete": resumo_manchete(d_home)} if d_home else None
    except Exception as e:  # noqa: BLE001 - o resumo é um extra: nunca trava a publicação do site
        print(f"AVISO: resumo do mês indisponível ({type(e).__name__}: {e}); a home sai sem o card.")
        resumo_home = None
    raiz_u = lambda alvo: alvo + ("" if limpos else "index.html") if (alvo == "" or alvo.endswith("/")) else alvo  # noqa: E731
    escrever("", "inicio.html", secao="inicio", titulo=f"{site['nome']}: produção de petróleo e gás no Brasil",
             descricao="Produção de petróleo e gás do Brasil por operadora, campo e poço, royalties por município e "
                       "resultados das listadas. Dados oficiais da ANP e da CVM.",
             destaques=destaques(ag, raiz_u), noticias=noticias[:8], resumo=resumo_home)

    escrever("operadoras/", "listas.html", secao="operadoras", tipo="operadoras", itens=ag["operadores"],
             titulo="Operadoras de petróleo e gás no Brasil", h1="Operadoras",
             sub=f"Todas as {ag['n_operadoras']} operadoras com produção em {mes}, da maior para a menor"
                 + (", e os campos sem operadora identificada." if ag["n_operadoras"] < len(ag["operadores"]) else "."),
             descricao=f"Ranking das operadoras de petróleo e gás do Brasil por produção. Dados da ANP de {mes}.")
    for o in ag["operadores"]:
        o["resumo"] = resumo_operadora(o, ag)
        escrever(f"operadoras/{o['slug']}/", "operadora.html", secao="operadoras", o=o,
                 noticias=noticias_op.get(o["nome"], [])[:6],
                 titulo=f"{o['nome']}: produção de petróleo e gás",
                 descricao=f"Produção mensal de petróleo e gás da {o['nome']}, campo a campo. "
                           f"{fmt_inteligente(o['boe'])} mil boe/d operados em {mes}. Dados da ANP.")

    escrever("campos/", "listas.html", secao="campos", tipo="campos", itens=ag["campos"],
             titulo="Campos de petróleo e gás do Brasil", h1="Campos produtores",
             sub=f"Todos os {len(ag['campos'])} campos com produção em {mes}, do maior para o menor. "
                 "A ANP divide alguns por contrato: Búzios, por exemplo, aparece como Búzios e Búzios ECO "
                 "(excedente da cessão onerosa).",
             descricao=f"Lista dos campos de petróleo e gás em produção no Brasil, com operadora, bacia e "
                       f"produção de {mes}. Dados da ANP.")
    for c in ag["campos"]:
        c["resumo"] = resumo_campo(c, ag)
        c["aviso"] = aviso_campo(c, ag["campos"])
        lugar = "terra" if c["ambiente"] == "terra" else "mar"
        escrever(f"campos/{c['slug']}/", "campo.html", secao="campos", c=c,
                 noticias=noticias_campo.get(c["slug"], [])[:5],
                 queima=(G["historico_campo"].get(c["slug"]) if G else None),
                 titulo=f"{c['nome']} ({c['bacia']}, {c['estado']['uf']}): produção de petróleo e gás",
                 descricao=f"Produção mensal de petróleo, gás e água do {c['nome']}, campo em {lugar} na "
                           f"{titulo_bacia(c['bacia'])}"
                           + (f" operado pela {c['operador']}" if c["operador"] != DESCONHECIDO else "")
                           + f". Dado mais recente: {mes}.")

    nomes = {"todos": "Rankings do mês", "terra": "Rankings do mês: campos em terra",
             "mar": "Rankings do mês: campos no mar"}
    for recorte, h1 in nomes.items():
        caminho = "rankings/" if recorte == "todos" else f"rankings/{recorte}/"
        escrever(caminho, "rankings.html", secao="rankings", recorte=recorte, r=ag["rankings"][recorte], h1=h1,
                 titulo=f"{h1}: maiores altas e quedas de produção",
                 descricao=f"Campos de petróleo{({'todos': '', 'terra': ' em terra', 'mar': ' no mar'})[recorte]} "
                           f"que mais cresceram e mais caíram em {mes}. Dados da ANP.")

    # ---------- estados e bacias ----------
    for tipo, chave, pasta, nome_lista in (("estado", "estados", "estados", "Estados"), ("bacia", "bacias", "bacias", "Bacias")):
        regs = ag.get(chave, [])
        if not regs or "operadores" not in regs[0]:
            continue
        for r in regs:
            if not r.get("slug"):
                continue
            r["resumo"] = resumo_regiao(r, ag, tipo)
            vistos, nots = set(), []
            for c in r["campos"]:
                for n in noticias_campo.get(c["slug"], []):
                    if n["link"] not in vistos:
                        vistos.add(n["link"])
                        nots.append(n)
            nots.sort(key=lambda n: n["data"], reverse=True)
            if tipo == "estado":
                titulo = f"Produção de petróleo e gás {r['de']}"
                h1 = f"Petróleo e gás {r['de']}"
                desc = (f"Produção de petróleo e gás natural {r['de']} mês a mês: {fmt_inteligente(r['boe'])} mil boe/d em "
                        f"{mes}, por empresa e por campo. Dados da ANP.")
            else:
                titulo = f"{r['titulo']}: produção de petróleo e gás"
                h1 = r["titulo"]
                desc = (f"Produção de petróleo e gás natural da {r['titulo']} mês a mês: {fmt_inteligente(r['boe'])} mil boe/d "
                        f"em {mes}, por empresa e por campo. Dados da ANP.")
            escrever(f"{pasta}/{r['slug']}/", "regiao.html", secao=pasta, r=r, tipo=tipo, pasta=pasta,
                     nome_lista=nome_lista, todos=regs, noticias=nots[:6], titulo=titulo, h1=h1, descricao=desc)
        if tipo == "estado":
            escrever("estados/", "regioes.html", secao="estados", tipo=tipo, pasta=pasta, itens=regs,
                     titulo="Produção de petróleo e gás por estado", h1="Produção por estado",
                     sub=f"Os {len(regs)} estados com produção de petróleo ou gás em {mes}, do maior para o menor.",
                     descricao=f"Ranking dos estados produtores de petróleo e gás do Brasil em {mes}: produção, "
                               f"empresas e campos de cada um. Dados da ANP.")
        else:
            escrever("bacias/", "regioes.html", secao="bacias", tipo=tipo, pasta=pasta, itens=regs,
                     titulo="Produção de petróleo e gás por bacia", h1="Produção por bacia",
                     sub=f"As {len(regs)} bacias sedimentares com produção em {mes}, da maior para a menor.",
                     descricao=f"Ranking das bacias produtoras de petróleo e gás do Brasil em {mes}: Santos, Campos, "
                               f"Recôncavo, Potiguar e outras, com empresas e campos. Dados da ANP.")

    escrever("pocos/", "pocos.html", secao="pocos", titulo="Produção por poço",
             descricao="Busque qualquer poço de petróleo e gás do Brasil e veja a produção mês a mês desde 2018: "
                       "petróleo, gás, água e BSW. Dados da ANP.")
    base_csv = dados / "base_pocos.csv.gz"
    if base_csv.exists():
        r = gerar_pocos(base_csv, saida, ag)
        print(f"Busca de poços: {r['pocos']} poços ({r['ativos']} ativos) em {r['campos']} campos, {r['mb']} MB; "
              f"índice {r['indice_mb']} MB; maior campo {r['maior']}")
    M = gerar_mapa(dados, saida, ag) if env.globals["tem_mapa"] else None
    if M:
        print(f"Mapa: {M['n_areas']} áreas ({M['n_sem_producao']} sem produção), {M['pocos_no_mapa']} de "
              f"{M['pocos_ativos']} poços ativos com coordenada, {len(M['grupos'])} empresas, {M['kb']} KB")
        escrever("mapa/", "mapa.html", secao="mapa", M=M,
                 titulo="Mapa dos campos e poços de petróleo e gás do Brasil",
                 descricao=f"Mapa com os limites oficiais de {M['n_campos']} campos produtores e a posição de "
                           f"{fmt(M['pocos_no_mapa'])} poços em produção, com filtro por empresa e a produção de cada poço. "
                           "Dados da ANP.")
    if financeiro:
        ordem = sorted(financeiro.values(), key=lambda e: -(e["ultimo"].get("receita") or 0))
        escrever("resultados/", "resultados.html", secao="resultados",
                 titulo="Resultados das petroleiras listadas: PETR4, PRIO3, BRAV3 e RECV3",
                 descricao="Receita, EBITDA, lucro, dívida e capex por trimestre de Petrobras, PRIO, Brava e PetroRecôncavo, "
                           "a partir das demonstrações entregues à CVM, ao lado da produção operada pela ANP.",
                 empresas=ordem)
    # ---------- gás por destino ----------
    if G:
        escrever("gas/", "gas.html", secao="gas", G=G, titulo="Gás natural no Brasil: queima, reinjeção e aproveitamento",
                 descricao=f"Quanto gás natural o Brasil queima, reinjeta, consome nas plataformas e envia ao mercado, por mês "
                           f"desde 2000 e por estado. Aproveitamento de {fmt(G['aprov'], 1)}% em {mes_extenso(G['ultimo'])}. Dados da ANP.")
        if G["queima"]:
            Q = G["queima"]
            escrever("gas/queima/", "gas_queima.html", secao="gas", G=G,
                     titulo="Campos que mais queimam gás natural no Brasil",
                     descricao=f"Ranking mensal dos {Q['n']} campos com maior queima de gás natural, com operadora e variação: "
                               f"{Q['linhas'][0]['campo']} lidera em {mes_extenso(Q['mes'])}. Dados da ANP.")
    # ---------- refino ----------
    if RF:
        top = RF["ranking"][0]
        escrever("refino/", "refino.html", secao="refino", R=RF,
                 titulo="Refino de petróleo no Brasil: refinarias, processamento e derivados",
                 descricao=f"Quanto petróleo cada refinaria brasileira processa, a parte de petróleo importado e os derivados produzidos, "
                           f"mês a mês desde 1990, com mapa e ranking. {top['sigla']} lidera, com {fmt(top['proc12'], 0)} mil barris por dia. "
                           f"Dados da ANP até {mes_extenso(RF['ultimo'])}.")
        for x in RF["paginas"]:
            de = f"{x['cidade']} ({x['uf']})" if x["cidade"] else x["uf"]
            partes = [f"A {x['sigla']}, {'de ' + x['empresa'] + ', ' if x['empresa'] else ''}fica em {de}."]
            if x["ativa"] and x["operando"]:
                partes.append(f"Processou em média {fmt(x['proc12'], 0)} mil barris de petróleo por dia nos últimos 12 meses"
                              f"{', ' + fmt(x['pct_pais'], 1) + '% do total do país' if x.get('pct_pais') else ''}"
                              f"{', a ' + str(x['posicao']) + 'ª maior do Brasil' if x.get('posicao') else ''}.")
                if x["pct_imp12"] is not None:
                    partes.append(f"{fmt(x['pct_imp12'], 0)}% da carga foi petróleo importado.")
                if x["derivados"]:
                    partes.append(f"O principal derivado é {x['derivados'][0]['nome'].lower()} ({fmt(x['derivados'][0]['pct'], 0)}% da produção).")
            else:
                partes.append("Não registrou processamento nos últimos 3 meses." if x["ativa"] else "Não registrou processamento nos últimos 12 meses.")
            escrever(f"refino/{x['slug']}/", "refinaria.html", secao="refino", R=RF, e=x, resumo=" ".join(partes),
                     titulo=f"{x['sigla']}: processamento de petróleo e derivados produzidos",
                     descricao=" ".join(partes)[:300] + " Dados da ANP.")
    # ---------- preços dos combustíveis ----------
    if PR:
        gb = PR["brasil"]["GASOLINA"]["k"]
        escrever("precos/", "precos.html", secao="precos", P=PR,
                 titulo="Preço dos combustíveis no Brasil: gasolina, etanol e diesel por estado e município",
                 descricao=f"Preço médio da gasolina, do etanol e do diesel nos postos, por estado e por município, mês a mês. "
                           f"Gasolina comum a R$ {fmt(gb['valor'], 2)} por litro em {mes_extenso(gb['mes'])}. Dados do levantamento da ANP.")
        roy = {(m["uf"], slug(m["nome"])): f"royalties/{m['estado_slug']}/{m['slug']}/" for m in R["municipios"]} if R else {}
        for e in PR["estados"]:
            gp = e["p"]["GASOLINA"]["k"]
            partes = [f"Em {mes_extenso(gp['mes'])}, o preço médio da gasolina comum {e['de']} foi de R$ {fmt(gp['valor'], 2)} por litro, "
                      f"{fmt(abs(gp['valor'] / gb['valor'] - 1) * 100, 1)}% {'acima' if gp['valor'] >= gb['valor'] else 'abaixo'} da média do Brasil (R$ {fmt(gb['valor'], 2)})."]
            if gp["var12"] is not None:
                partes.append(f"Em 12 meses, {'subiu' if gp['var12'] >= 0 else 'caiu'} {fmt(abs(gp['var12']) * 100, 1)}%.")
            if "ETANOL" in e["p"]:
                partes.append(f"O etanol custou R$ {fmt(e['p']['ETANOL']['k']['valor'], 2)}"
                              + (f", {fmt(e['razao_ult'], 0)}% do preço da gasolina." if e.get("razao_ult") is not None else "."))
            escrever(f"precos/{e['slug']}/", "precos_estado.html", secao="precos", P=PR, e=e, resumo=" ".join(partes),
                     titulo=f"Preço da gasolina {e['de']}: média, etanol e diesel por município",
                     descricao=" ".join(partes)[:300])
            for m in e["municipios"]:
                b = m["p"]
                gm = b["GASOLINA"]["k"]
                partes = [f"Em {mes_extenso(gm['mes'])}, o preço médio da gasolina comum em {m['nome']} ({m['uf']}) foi de R$ {fmt(gm['valor'], 2)} por litro."]
                if m["vs_uf"] is not None:
                    partes.append(f"Isso é {fmt(abs(m['vs_uf']) * 100, 1)}% {'acima' if m['vs_uf'] >= 0 else 'abaixo'} da média {e['de']} "
                                  f"(R$ {fmt(m['ref_uf'], 2)}) e {fmt(abs(m['vs_br']) * 100, 1)}% {'acima' if m['vs_br'] >= 0 else 'abaixo'} da média do Brasil.")
                if gm["var12"] is not None:
                    partes.append(f"Em 12 meses, {'subiu' if gm['var12'] >= 0 else 'caiu'} {fmt(abs(gm['var12']) * 100, 1)}%.")
                if "ETANOL" in b and m.get("razao_ult") is not None:
                    partes.append(f"O etanol custou R$ {fmt(b['ETANOL']['k']['valor'], 2)}, {fmt(m['razao_ult'], 0)}% do preço da gasolina"
                                  f"{': abaixo de 70%, ele costuma compensar.' if m['razao_ult'] < 70 else '.'}")
                if "DIESEL S10" in b:
                    partes.append(f"O diesel S10 saiu a R$ {fmt(b['DIESEL S10']['k']['valor'], 2)}.")
                sem = SEM.get((m["uf"], slug(m["nome"])))
                titulo_m = f"Preço da gasolina em {m['nome']} ({m['uf']}): média, etanol e diesel"
                if sem and sem["precos"].get("Gasolina comum"):
                    g_s, d_s = sem["precos"]["Gasolina comum"], sem["dif"]["Gasolina comum"]
                    frase = f"Na semana de {sem['periodo']}, a gasolina comum custou em média R$ {fmt(g_s, 2)} em {m['nome']}"
                    if d_s is not None and round(abs(d_s) * 100):
                        frase += f", {'alta' if d_s > 0 else 'queda'} de {centavos(d_s)} na semana"
                    partes.insert(0, frase + ".")
                    titulo_m = f"Preço da gasolina em {m['nome']} ({m['uf']}) nesta semana: R$ {fmt(g_s, 2)}"
                escrever(f"precos/{e['slug']}/{m['slug']}/", "precos_municipio.html", secao="precos", P=PR, m=m, resumo=" ".join(partes),
                         royalties_path=roy.get((m["uf"], slug(m["nome"]))), sem=sem, dia=dia,
                         titulo=titulo_m, descricao=" ".join(partes)[:300])
    # ---------- royalties ----------
    if R:
        prod = {e["uf"]: e["slug"] for e in ag.get("estados", []) if e.get("slug")}
        campos_uf: dict = {}
        for c in sorted(ag["campos"], key=lambda x: -(x.get("boe") or 0)):
            uf_c = (c.get("estado") or {}).get("uf")
            if uf_c and (c.get("boe") or 0) > 0 and len(campos_uf.setdefault(uf_c, [])) < 6:
                campos_uf[uf_c].append({"nome": c["nome"], "slug": c["slug"], "boe": c["boe"]})
        valores, unidade = escala(R["brasil"]["serie_total"])
        R["brasil"]["grafico"] = {"valores": valores, "unidade": unidade}
        ult = R["mes_extenso"]
        escrever("royalties/", "royalties_brasil.html", secao="royalties", R=R, B=R["brasil"],
                 titulo="Royalties de petróleo por município e estado",
                 descricao=f"Quanto cada município e cada estado recebe de royalties de petróleo e participação especial, "
                           f"mês a mês, com ranking e valor por habitante. Dados da ANP até {ult}.")
        for e in R["estados"]:
            valores, unidade = escala(e["serie_mun"])
            e["grafico"] = {"valores": valores, "unidade": unidade}
            escrever(f"royalties/{e['slug']}/", "royalties_estado.html", secao="royalties", R=R, e=e,
                     prod_estado=prod.get(e["uf"]), campos_estado=campos_uf.get(e["uf"], []),
                     titulo=f"Royalties de petróleo {e['de']} em {R['ultimo'][:4]}: quanto cada município recebe",
                     descricao=f"Royalties de petróleo e gás recebidos pelos municípios {e['de']}: "
                               f"{dinheiro(e['mun_total12'], False)} em 12 meses. Ranking, mês a mês e por habitante. Dados da ANP.")
        familia = carregar_familia(Path(dados) / "familia", rede=not teste and os.environ.get("OLEO_SEM_REDE") != "1")
        indice = sorted([m["nome"], m["uf"], f"/royalties/{m['estado_slug']}/{m['slug']}/"] for m in R["municipios"])
        (saida / "royalties").mkdir(parents=True, exist_ok=True)
        (saida / "royalties" / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, separators=(",", ":")), "utf-8")
        # preço dos combustíveis do mesmo município (só quando o nome é único no estado: não arriscamos ligar a cidade errada)
        nomes_preco: dict = {}
        for x in (PR["municipios"] if PR else []):
            nomes_preco.setdefault((x["uf"], slug(x["nome"])), []).append(x)
        for m in R["municipios"]:
            m["familia"] = familia.get(chave_cidade(m["nome"], m["uf"]), [])
            cand = nomes_preco.get((m["uf"], slug(m["nome"])), [])
            preco = cand[0] if len(cand) == 1 else None
            m["resumo"] = resumo_municipio(m, R)
            valores, unidade = escala(m["r"])
            m["grafico"] = {"valores": valores, "unidade": unidade}
            escrever(f"royalties/{m['estado_slug']}/{m['slug']}/", "royalties_municipio.html", secao="royalties", R=R, m=m,
                     prod_estado=prod.get(m["uf"]), campos_estado=campos_uf.get(m["uf"], []),
                     preco=preco, preco_path=f"precos/{preco['estado_slug']}/{preco['slug']}/" if preco else None,
                     titulo=(f"Quanto {m['nome']} ({m['uf']}) recebe de royalties em {R['ultimo'][:4]}: {dinheiro(m['r12'])} em 12 meses"
                             if m["r12"] > 0 else f"Quanto {m['nome']} ({m['uf']}) recebe de royalties de petróleo em {R['ultimo'][:4]}"),
                     descricao=f"{m['nome']} ({m['uf']}) recebeu {dinheiro(m['r12'], False)} em royalties de petróleo nos "
                               f"últimos 12 meses. Valores mês a mês desde 2018, participação especial e ranking. Dados da ANP.")

    escrever("noticias/", "noticias.html", secao="noticias", titulo="Notícias do setor de petróleo e gás",
             descricao="Manchetes diárias de petróleo e gás, com links para os números de cada empresa e campo.",
             noticias=noticias[:200], noindex=True)
    escrever("sobre/", "sobre.html", secao="sobre", titulo="Sobre e metodologia",
             descricao="De onde vêm os dados do Óleo em Números e como os números são calculados.")
    escrever("sugestoes/", "sugestoes.html", secao="sugestoes", titulo="Sugestões e críticas",
             descricao="Envie uma sugestão, crítica ou correção para o Óleo em Números. A mensagem chega por e-mail e é lida por uma pessoa.")
    escrever("contato/", "contato.html", secao="contato", titulo="Contato",
             descricao="Fale com o Óleo em Números: e-mail, formulário de sugestões, erros em dados, imprensa e parcerias.")
    escrever("privacidade/", "privacidade.html", secao="privacidade", titulo="Política de privacidade",
             descricao="Como o Óleo em Números trata dados: o que coleta, anúncios do Google AdSense, cookies e seus direitos pela LGPD.")
    try:
        meses_resumo = resumo_meses(ag)
    except Exception as e:  # noqa: BLE001
        print(f"AVISO: resumos mensais indisponíveis ({type(e).__name__}: {e}); o site sai sem eles.")
        meses_resumo = []
    itens_resumo = []
    for i, per in enumerate(meses_resumo):
        try:
            R = montar_resumo(ag, per, site["url"])
            d = R["d"]
            idx = ag["brasil"]["serie"]["periodo"].index(per)
            tem_img = card_resumo(ag, d, saida / "resumo" / per / "card.png", site["url"])
            img_html = Markup(bloco_imagem(f"oleo-em-numeros-resumo-{per}.png",
                                           f"Produção de petróleo e gás do Brasil em {mes_extenso(per)}") if tem_img else "")
            escrever(f"resumo/{per}/", "resumo.html", secao="resumo", R=R, d=d, manchete=R["manchete"],
                     img_html=img_html, texto_compartilhe=TEXTO_COMPARTILHE,
                     og_imagem=f"{site['url']}/resumo/{per}/card.png" if tem_img else None,
                     post=R["linkedin"], post_x=R["x"],
                     serie_boe=ag["brasil"]["serie"]["boe"][:idx + 1], serie_per=ag["brasil"]["serie"]["periodo"][:idx + 1],
                     campos_altas=R["altas"], campos_quedas=R["quedas"],
                     anterior=meses_resumo[i - 1] if i else None,
                     proximo=meses_resumo[i + 1] if i + 1 < len(meses_resumo) else None,
                     titulo=R["manchete"] if mes_extenso(per) in R["manchete"] else f"{R['manchete']} | Resumo de {mes_extenso(per)}",
                     descricao=f"Resumo de {mes_extenso(per)}: produção de petróleo e gás do Brasil, maiores altas e quedas, "
                               "operadoras em destaque e bacias. Dados oficiais da ANP.")
            itens_resumo.append({"per": per, "mes": mes_extenso(per), "manchete": R["manchete"]})
        except Exception as e:  # noqa: BLE001 - um mês com dado estranho não derruba o site
            print(f"AVISO: resumo de {per} não gerado ({type(e).__name__}: {e}).")
    # resumo semanal: preços dos combustíveis nos postos (levantamento semanal da ANP)
    semanas_itens = []
    if S:
        sem = S["semanas"]
        inicio = max(0, len(sem) - SEMANAS_NO_SITE)
        publicadas = [w["fim"] for w in sem[inicio:]]
        for i in range(inicio, len(sem)):
            fim = sem[i]["fim"]
            try:
                W = montar_semana(S, i, site["url"])
                for e in W["estados"]:
                    e["nome"] = UFS.get(e["uf"], ("", e["uf"]))[1]
                    e["slug"] = slug(e["nome"]) if e["uf"] in UFS else None
                RM = ranking_municipios(S, i)
                if RM:   # nome bonito e link para a página de preço da cidade, quando ela existe
                    for lista in (RM["caras"], RM["baratas"], RM["altas"], RM["quedas"]):
                        for x in lista:
                            pm = NOMES_MUN.get((x["uf"], x["slug"]))
                            x["nome"] = pm["nome"] if pm else x["slug"].replace("-", " ").title().replace(" De ", " de ").replace(" Do ", " do ").replace(" Da ", " da ").replace(" Dos ", " dos ").replace(" Das ", " das ")
                            x["link"] = f"precos/{pm['estado_slug']}/{pm['slug']}/" if pm and PR else None
                W["municipios"] = RM
                tem_img = card_semana(S, i, saida / "resumo" / "semana" / fim / "card.png", site["url"])
                img_html = Markup(bloco_imagem(f"oleo-em-numeros-combustiveis-{fim}.png",
                                               f"Preços dos combustíveis nos postos na semana até {dia(fim)}") if tem_img else "")
                k = publicadas.index(fim)
                escrever(f"resumo/semana/{fim}/", "resumo_semana.html", secao="resumo", W=W, img_html=img_html,
                         texto_compartilhe=TEXTO_COMPARTILHE, dia_curto=dia_curto,
                         og_imagem=f"{site['url']}/resumo/semana/{fim}/card.png" if tem_img else None,
                         anterior=publicadas[k - 1] if k else None,
                         proximo=publicadas[k + 1] if k + 1 < len(publicadas) else None,
                         titulo=f"{W['manchete']} | Combustíveis, semana até {dia(fim)}",
                         descricao=f"Preço médio da gasolina, do etanol e do diesel nos postos do Brasil na semana de {W['periodo']}: "
                                   "estados, capitais e variação na semana. Dados da ANP.")
                semanas_itens.append({"fim": fim, "periodo": W["periodo"], "manchete": W["manchete"]})
            except Exception as e:  # noqa: BLE001 - uma semana com dado estranho não derruba o site
                print(f"AVISO: resumo semanal de {fim} não gerado ({type(e).__name__}: {e}).")
    if itens_resumo or semanas_itens:
        escrever("resumo/", "resumos.html", secao="resumo", itens=list(reversed(itens_resumo)),
                 semanas=list(reversed(semanas_itens)),
                 titulo="Resumos da semana e do mês: combustíveis e produção de petróleo e gás",
                 descricao="Resumo semanal dos preços dos combustíveis nos postos e resumo mensal da produção de petróleo e gás "
                           "do Brasil. Dados oficiais da ANP.")
    escrever("404.html", "nao_encontrada.html", raiz="/", secao="", titulo="Página não encontrada",
             descricao="Página não encontrada.", noindex=True, erro=True)

    shutil.copy(AQUI / "estatico" / "estilo.css", saida / "estilo.css")
    shutil.copy(AQUI / "estatico" / "familia.js", saida / "familia.js")
    shutil.copy(AQUI / "estatico" / "familia-compartilhar.js", saida / "familia-compartilhar.js")
    shutil.copy(AQUI / "estatico" / "familia-busca.js", saida / "familia-busca.js")
    (saida / "busca-geral.json").write_text(json.dumps(busca, ensure_ascii=False, separators=(",", ":")), "utf-8")
    shutil.copy(AQUI / "estatico" / "og.png", saida / "og.png")
    if M:
        shutil.copytree(AQUI / "estatico" / "leaflet", saida / "leaflet")
    (saida / "icone.svg").write_text(ICONE, "utf-8")
    data = datetime.fromisoformat(ag["gerado_em"]).date().isoformat()
    urls = "".join(f"<url><loc>{site['url']}/{p}</loc><lastmod>{data}</lastmod></url>" for p in paginas)
    (saida / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        "utf-8")
    # /dados/pocos/ é só a pasta dos JSON da busca de poços (não é página): o "$" bloqueia apenas esse endereço,
    # sem impedir o acesso aos arquivos dentro dela.
    robots = ("User-agent: *\nDisallow: /\n" if (teste or site["previa"]) else
              f"User-agent: *\nAllow: /\nDisallow: /dados/pocos/$\nSitemap: {site['url']}/sitemap.xml\n")
    (saida / "robots.txt").write_text(robots, "utf-8")
    if not (teste or site["previa"]):
        (saida / "llms.txt").write_text(LLMS_TXT.replace("__BASE__", site["url"]), "utf-8")
    return paginas


_TRILHA = re.compile(r'<nav class="trilha"[^>]*>(.*?)</nav>', re.S)
_ELO = re.compile(r'<a href="([^"]*)"[^>]*>(.*?)</a>|<span aria-current="page">(.*?)</span>', re.S)


def com_trilha_ld(html: str, endereco: str) -> str:
    """Repete a trilha visível (nav.trilha) como BreadcrumbList em JSON-LD, para o Google mostrar o caminho no resultado."""
    m = _TRILHA.search(html)
    if not m:
        return html
    elos = []
    for href, rotulo, atual in _ELO.findall(m.group(1)):
        nome = unescape(re.sub(r"<[^>]+>", "", rotulo or atual)).strip()
        if not nome:
            continue
        alvo = urljoin(endereco, href).replace("index.html", "") if href else endereco
        elos.append({"@type": "ListItem", "position": len(elos) + 1, "name": nome, "item": alvo})
    if len(elos) < 2:
        return html
    ld = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": elos}, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("</head>", f'<script type="application/ld+json">{ld}</script>\n</head>', 1)


TIPO_BUSCA = {"campos": "campo", "bacias": "bacia", "estados": "estado", "operadoras": "operadora", "resumo": "resumo",
              "royalties": "royalties", "rankings": "ranking", "refino": "refino", "precos": "preços", "gas": "gás"}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--dados", type=Path, default=config.PROCESSADOS)
    p.add_argument("--saida", type=Path, default=config.RAIZ / "publico")
    p.add_argument("--teste", action="store_true")
    p.add_argument("--links-limpos", action="store_true")
    p.add_argument("--url")
    a = p.parse_args(argv)
    paginas = gerar(a.dados, a.saida, teste=a.teste, limpos=a.links_limpos, url=a.url)
    print(f"{len(paginas)} páginas geradas em {a.saida}")


if __name__ == "__main__":
    main()

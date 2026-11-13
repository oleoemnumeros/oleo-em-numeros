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
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from robo import config
from robo.agregar import PREP_BACIA
from robo.util import MESES, fmt, fmt_inteligente, fmt_pp, fmt_var, mes_extenso, sem_acento

from .graficos import linha
from .financeiro import carregar_financeiro, reais
from .mapa import gerar_mapa
from .pocos import gerar_pocos
from .royalties import dinheiro, escala, preparar as preparar_royalties, resumo_municipio

AQUI = Path(__file__).resolve().parent
ICONE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30"><path d="M15 2c4.5 5.6 8 10.2 8 14.6a8 8 0 0 1-16 0'
         'C7 12.2 10.5 7.6 15 2z" fill="#B45309"/><rect x="10.5" y="16" width="2.2" height="6" rx="1" fill="#fff"/>'
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
    env.globals["tem_mapa"] = (dados / "geo.json").exists() and (dados / "base_pocos.csv.gz").exists()

    if saida.exists():
        shutil.rmtree(saida)
    saida.mkdir(parents=True)
    paginas = []

    noticias, noticias_op, noticias_campo, fontes_noticias = carregar_noticias(dados, ag)
    env.globals.update(fontes_noticias=fontes_noticias, reais=reais, dinheiro=dinheiro)
    R = preparar_royalties(dados)
    env.globals.update(ufs_royalties={e["uf"] for e in R["estados"]} if R else set())
    financeiro = carregar_financeiro(dados, ag)
    for o in ag["operadores"]:
        o["financeiro"] = financeiro.get(o["nome"])

    def escrever(caminho: str, modelo: str, raiz: str | None = None, **ctx):
        raiz = "../" * caminho.count("/") if raiz is None else raiz

        def u(alvo: str) -> str:
            destino = raiz + alvo
            if alvo == "" or alvo.endswith("/"):
                destino += "" if limpos else "index.html"
            return destino or "./"

        html = env.get_template(modelo).render(ag=ag, site=site, caminho=caminho, raiz=raiz, u=u, **ctx)
        arquivo = saida / (caminho + "index.html" if caminho == "" or caminho.endswith("/") else caminho)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(html, "utf-8")
        if not caminho.endswith(".html") and not ctx.get("noindex"):
            paginas.append(caminho)
        return u

    mes = mes_extenso(ag["ultimo"])
    raiz_u = lambda alvo: alvo + ("" if limpos else "index.html") if (alvo == "" or alvo.endswith("/")) else alvo  # noqa: E731
    escrever("", "inicio.html", secao="inicio", titulo=f"{site['nome']}: produção de petróleo e gás no Brasil",
             descricao="Produção de petróleo e gás do Brasil por operadora, campo e poço, royalties por município e "
                       "resultados das listadas. Dados oficiais da ANP e da CVM.",
             destaques=destaques(ag, raiz_u), noticias=noticias[:8])

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
    # ---------- royalties ----------
    if R:
        prod = {e["uf"]: e["slug"] for e in ag.get("estados", []) if e.get("slug")}
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
                     prod_estado=prod.get(e["uf"]),
                     titulo=f"Royalties de petróleo {e['de']}: municípios e estado",
                     descricao=f"Royalties de petróleo e gás recebidos pelos municípios {e['de']}: "
                               f"{dinheiro(e['mun_total12'], False)} em 12 meses. Ranking, mês a mês e por habitante. Dados da ANP.")
        for m in R["municipios"]:
            m["resumo"] = resumo_municipio(m, R)
            valores, unidade = escala(m["r"])
            m["grafico"] = {"valores": valores, "unidade": unidade}
            escrever(f"royalties/{m['estado_slug']}/{m['slug']}/", "royalties_municipio.html", secao="royalties", R=R, m=m,
                     prod_estado=prod.get(m["uf"]),
                     titulo=f"Royalties de {m['nome']} ({m['uf']}): quanto o município recebe",
                     descricao=f"{m['nome']} ({m['uf']}) recebeu {dinheiro(m['r12'], False)} em royalties de petróleo nos "
                               f"últimos 12 meses. Valores mês a mês desde 2018, participação especial e ranking. Dados da ANP.")

    escrever("noticias/", "noticias.html", secao="noticias", titulo="Notícias do setor de petróleo e gás",
             descricao="Manchetes diárias de petróleo e gás, com links para os números de cada empresa e campo.",
             noticias=noticias[:200], noindex=True)
    escrever("sobre/", "sobre.html", secao="sobre", titulo="Sobre e metodologia",
             descricao="De onde vêm os dados do Óleo em Números e como os números são calculados.")
    escrever("404.html", "nao_encontrada.html", raiz="/", secao="", titulo="Página não encontrada",
             descricao="Página não encontrada.", noindex=True, erro=True)

    shutil.copy(AQUI / "estatico" / "estilo.css", saida / "estilo.css")
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
    return paginas


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

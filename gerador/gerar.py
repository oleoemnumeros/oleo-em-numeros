"""Gera o site estático a partir de dados/processados/agregados.json.

Uso:
    python -m gerador.gerar                        # produção, em publico/
    python -m gerador.gerar --teste                # mostra a faixa de dados fictícios
    python -m gerador.gerar --links-limpos         # links sem 'index.html' (servidor web)
"""
import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from robo import config
from robo.util import MESES, fmt, fmt_inteligente, fmt_pp, fmt_var, mes_extenso

from .graficos import linha

AQUI = Path(__file__).resolve().parent
ICONE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30"><path d="M15 2c4.5 5.6 8 10.2 8 14.6a8 8 0 0 1-16 0'
         'C7 12.2 10.5 7.6 15 2z" fill="#B45309"/><rect x="10.5" y="16" width="2.2" height="6" rx="1" fill="#fff"/>'
         '<rect x="13.9" y="12.5" width="2.2" height="9.5" rx="1" fill="#fff"/><rect x="17.3" y="14.5" width="2.2" '
         'height="7.5" rx="1" fill="#fff"/></svg>')


# ---------------- textos automáticos ----------------

def _mais_menos(v):
    if v is None:
        return None
    return f"{fmt(abs(v) * 100, 1)}% a {'mais' if v > 0 else 'menos'}"


def titulo_tendencia(nome, v):
    if v is None:
        return nome
    if abs(v) < 0.005:
        return f"{nome} estável em 12 meses"
    return f"{nome} {'sobe' if v > 0 else 'cai'} {fmt(abs(v) * 100, 1)}% em 12 meses"


def resumo_campo(c, ag):
    partes = [f"Em {mes_extenso(ag['ultimo'])}, o {c['nome']} produziu {fmt(c['petroleo'])} barris de petróleo por dia"]
    mm = _mais_menos(c["var12"])
    partes[0] += f", {mm} que um ano antes." if mm else "."
    if c["agua"] and c["petroleo"]:
        partes.append(f"Para cada barril de petróleo saíram {fmt(c['agua'] / c['petroleo'], 1)} barris de água "
                      f"(BSW de {fmt(c['bsw'], 1)}%).")
    lugar = "em terra" if c["ambiente"] == "terra" else "no mar"
    ordem = "o maior" if c["rank_estado"] == 1 else f"o {c['rank_estado']}º maior"
    partes.append(f"É {ordem} campo {lugar} {c['estado']['de']}.")
    return " ".join(partes)


def resumo_operadora(o, ag):
    partes = [f"Em {mes_extenso(ag['ultimo'])}, a {o['nome']} produziu {fmt_inteligente(o['petroleo'])} mil barris "
              f"de petróleo por dia"]
    mm = _mais_menos(o["var12"])
    partes[0] += f", {mm} que um ano antes." if mm else "."
    ordem = "a maior" if o["rank"] == 1 else f"a {o['rank']}ª maior"
    partes.append(f"É {ordem} operadora do país e responde por {fmt(o['share'], 1)}% da produção nacional.")
    if o["terra_pct"] >= 50 and o["share_terra"] >= 1:
        partes.append(f"Produz {fmt(o['share_terra'], 0)}% do petróleo extraído em terra no Brasil.")
    if o["campos"] and o["petroleo"]:
        top = o["campos"][0]
        pct = top["petroleo"] / (o["petroleo"] * 1000) * 100
        if len(o["campos"]) > 1:
            partes.append(f"O maior campo é o {top['nome']}, com {fmt(pct, 0)}% do total da empresa.")
    return " ".join(partes)


def destaques(ag, u):
    lista = []
    altas = ag["rankings"]["todos"]["altas"]
    nome_ant = MESES[int(ag["anterior"][5:]) - 1]
    if altas:
        c = altas[0]
        lista.append({"tag": "Maior alta do mês", "link": u(f"campos/{c['slug']}/"),
                      "titulo": f"{c['nome']} cresce {fmt((c['varmes'] or 0) * 100, 0)}% sobre {nome_ant}",
                      "texto": f"Bacia {c['bacia']} · {c['operador']} · {fmt(c['petroleo'])} bbl/d em "
                               f"{mes_extenso(ag['ultimo'])}."})
    br = ag["brasil"]
    if br["varmes_terra"] is not None:
        alta_terra = ag["rankings"]["terra"]["altas"]
        verbo = "sobe" if br["varmes_terra"] > 0 else "cai"
        texto = f"{fmt(br['petroleo_terra'], 1)} mil bbl/d em {mes_extenso(ag['ultimo'])}."
        if alta_terra:
            texto += f" Maior alta em terra: {alta_terra[0]['nome']}."
        lista.append({"tag": "Produção em terra", "link": u("rankings/terra/"),
                      "titulo": f"Petróleo em terra {verbo} {fmt(abs(br['varmes_terra']) * 100, 1)}% no mês",
                      "texto": texto})
    candidatas = [o for o in ag["operadores"] if o["var12"] is not None and o["petroleo"] >= 1]
    if candidatas:
        o = max(candidatas, key=lambda x: x["var12"])
        verbo = "cresce" if o["var12"] > 0 else "recua"
        lista.append({"tag": "Operadora em destaque", "link": u(f"operadoras/{o['slug']}/"),
                      "titulo": f"{o['nome']} {verbo} {fmt(abs(o['var12']) * 100, 1)}% em 12 meses",
                      "texto": f"{fmt_inteligente(o['petroleo'])} mil bbl/d em {mes_extenso(ag['ultimo'])}, "
                               f"{o['rank']}ª maior do país."})
    return lista


# ---------------- carga ----------------

def carregar(pasta: Path) -> dict:
    ag = json.loads((pasta / "agregados.json").read_text("utf-8"))
    por_slug = {c["slug"]: c for c in ag["campos"]}
    for o in ag["operadores"]:
        o["campos"] = [por_slug[s] for s in o["campos"] if s in por_slug]
    for recorte in ag["rankings"].values():
        for lado in ("altas", "quedas"):
            recorte[lado] = [por_slug[s] for s in recorte[lado] if s in por_slug]
    return ag


# ---------------- geração ----------------

def gerar(dados: Path, saida: Path, teste: bool = False, limpos: bool = False, url: str | None = None) -> list[str]:
    ag = carregar(dados)
    site = {
        "nome": "Óleo em Números",
        "url": (url or os.environ.get("SITE_URL") or "https://oleoemnumeros.com.br").rstrip("/"),
        "teste": teste,
        "previa": os.environ.get("SITE_PREVIA", "0") == "1",
        "contato": os.environ.get("SITE_CONTATO", ""),
    }
    env = Environment(loader=FileSystemLoader(AQUI / "modelos"), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(fmt=fmt, fmt_var=fmt_var, fmt_pp=fmt_pp, fmt_inteligente=fmt_inteligente,
                       mes_extenso=mes_extenso, linha=linha, titulo_tendencia=titulo_tendencia)

    if saida.exists():
        shutil.rmtree(saida)
    saida.mkdir(parents=True)
    paginas = []

    def escrever(caminho: str, modelo: str, **ctx):
        raiz = "../" * caminho.count("/")

        def u(alvo: str) -> str:
            destino = raiz + alvo
            if alvo == "" or alvo.endswith("/"):
                destino += "" if limpos else "index.html"
            return destino or "./"

        html = env.get_template(modelo).render(ag=ag, site=site, caminho=caminho, raiz=raiz, u=u, **ctx)
        arquivo = saida / (caminho + "index.html" if caminho == "" or caminho.endswith("/") else caminho)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(html, "utf-8")
        if not caminho.endswith(".html"):
            paginas.append(caminho)
        return u

    mes = mes_extenso(ag["ultimo"])
    raiz_u = lambda alvo: alvo + ("" if limpos else "index.html") if (alvo == "" or alvo.endswith("/")) else alvo  # noqa: E731
    escrever("", "inicio.html", secao="inicio", titulo="Óleo em Números",
             descricao=f"Produção de petróleo e gás do Brasil por operadora, campo e bacia, com dados oficiais da "
                       f"ANP. Dado mais recente: {mes}.",
             destaques=destaques(ag, raiz_u))

    escrever("operadoras/", "listas.html", secao="operadoras", tipo="operadoras", itens=ag["operadores"],
             titulo="Operadoras de petróleo e gás no Brasil", h1="Operadoras",
             sub=f"Todas as {len(ag['operadores'])} operadoras com produção em {mes}, da maior para a menor.",
             descricao=f"Ranking das operadoras de petróleo e gás do Brasil por produção. Dados da ANP de {mes}.")
    for o in ag["operadores"]:
        o["resumo"] = resumo_operadora(o, ag)
        escrever(f"operadoras/{o['slug']}/", "operadora.html", secao="operadoras", o=o,
                 titulo=f"{o['nome']}: produção de petróleo e gás",
                 descricao=f"Produção mensal de petróleo e gás da {o['nome']}, campo a campo. "
                           f"{fmt_inteligente(o['petroleo'])} mil bbl/d em {mes}. Dados da ANP.")

    escrever("campos/", "listas.html", secao="campos", tipo="campos", itens=ag["campos"],
             titulo="Campos de petróleo e gás do Brasil", h1="Campos produtores",
             sub=f"Todos os {len(ag['campos'])} campos com produção em {mes}, do maior para o menor.",
             descricao=f"Lista dos campos de petróleo e gás em produção no Brasil, com operadora, bacia e "
                       f"produção de {mes}. Dados da ANP.")
    for c in ag["campos"]:
        c["resumo"] = resumo_campo(c, ag)
        lugar = "terra" if c["ambiente"] == "terra" else "mar"
        escrever(f"campos/{c['slug']}/", "campo.html", secao="campos", c=c,
                 titulo=f"{c['nome']} ({c['bacia']}, {c['estado']['uf']}): produção de petróleo e gás",
                 descricao=f"Produção mensal de petróleo, gás e água do {c['nome']}, campo em {lugar} na bacia "
                           f"{c['bacia']} operado pela {c['operador']}. Dado mais recente: {mes}.")

    nomes = {"todos": "Rankings do mês", "terra": "Rankings do mês: campos em terra",
             "mar": "Rankings do mês: campos no mar"}
    for recorte, h1 in nomes.items():
        caminho = "rankings/" if recorte == "todos" else f"rankings/{recorte}/"
        escrever(caminho, "rankings.html", secao="rankings", recorte=recorte, r=ag["rankings"][recorte], h1=h1,
                 titulo=f"{h1}: maiores altas e quedas de produção",
                 descricao=f"Campos de petróleo que mais cresceram e mais caíram em {mes}. Dados da ANP.")

    escrever("sobre/", "sobre.html", secao="sobre", titulo="Sobre e metodologia",
             descricao="De onde vêm os dados do Óleo em Números e como os números são calculados.")
    escrever("404.html", "nao_encontrada.html", secao="", titulo="Página não encontrada",
             descricao="Página não encontrada.")

    shutil.copy(AQUI / "estatico" / "estilo.css", saida / "estilo.css")
    (saida / "icone.svg").write_text(ICONE, "utf-8")
    data = datetime.fromisoformat(ag["gerado_em"]).date().isoformat()
    urls = "".join(f"<url><loc>{site['url']}/{p}</loc><lastmod>{data}</lastmod></url>" for p in paginas)
    (saida / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        "utf-8")
    robots = "User-agent: *\nDisallow: /\n" if (teste or site["previa"]) else f"User-agent: *\nAllow: /\nSitemap: {site['url']}/sitemap.xml\n"
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

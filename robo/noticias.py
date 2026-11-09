"""Manchetes do setor, coletadas todo dia de fontes com RSS público.

Guarda só título, fonte, data e link (nada do texto das matérias) e marca
as empresas e os campos citados, para cada página do site mostrar as
notícias que falam dela.

Uso:
    python -m robo.noticias            # coleta, marca e grava
    python -m robo.noticias --testar   # só mostra o que cada fonte devolve
"""
import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from . import config
from .util import sem_acento

HISTORICO = config.DADOS / "noticias" / "historico.json"     # guardado no cache entre execuções
SAIDA = config.PROCESSADOS / "noticias.json"                  # o que o site usa
DIAS_GUARDADOS = 180
DIAS_NO_SITE = 90

# Fontes testadas a partir do GitHub em nov/2026. Se uma cair, as outras seguem.
# tipo "govbr": a página de notícias do gov.br (sem RSS), lida como lista de manchetes.
FONTES = [
    {"nome": "ANP", "url": "https://www.gov.br/anp/pt-br/canais_atendimento/imprensa/noticias-comunicados", "tipo": "govbr"},
    {"nome": "MME", "url": "https://www.gov.br/mme/pt-br/assuntos/noticias", "tipo": "govbr"},
    {"nome": "eixos", "url": "https://eixos.com.br/feed/"},
    {"nome": "Petronotícias", "url": "https://petronoticias.com.br/feed/"},
    {"nome": "Agência Brasil", "url": "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml"},
    {"nome": "InfoMoney", "url": "https://www.infomoney.com.br/feed/"},
    {"nome": "Money Times", "url": "https://www.moneytimes.com.br/feed/"},
]

# Empresas e como aparecem nas manchetes. Nomes que também são palavras comuns
# ('Brava', 'Origem', 'Total') só valem na forma completa ou com maiúscula.
EMPRESAS = {
    "Petrobras": ["Petrobras"],
    "PRIO": ["PRIO", "Prio", "PetroRio", "Petro Rio"],
    "Brava Energia": ["Brava Energia", "Brava", "3R Petroleum", "3R", "Enauta"],
    "PetroRecôncavo": ["PetroRecôncavo", "PetroReconcavo", "Petro Recôncavo"],
    "Equinor Brasil": ["Equinor"],
    "TotalEnergies EP": ["TotalEnergies"],
    "Shell Brasil": ["Shell"],
    "Eneva": ["Eneva"],
    "Origem Energia": ["Origem Energia"],
    "Seacrest Petróleo": ["Seacrest"],
    "Karoon Brasil": ["Karoon"],
    "Perenco Brasil": ["Perenco"],
    "Trident Energy": ["Trident Energy", "Trident"],
    "BW Energy": ["BW Energy"],
    "Carmo": ["Carmo Energy"],
    "Alvopetro": ["Alvopetro"],
}

# Campos conhecidos que a imprensa cita sem a palavra "campo" antes.
CAMPOS_FAMOSOS = ["Búzios", "Tupi", "Mero", "Sépia", "Atapu", "Itapu", "Sapinhoá", "Bacalhau", "Peregrino",
                  "Jubarte", "Marlim", "Roncador", "Frade", "Wahoo", "Albacora Leste", "Berbigão", "Parque das Baleias",
                  "Polvo", "Tubarão Martelo", "Papa-Terra", "Atlanta", "Manati", "Urucu", "Miranga", "Candeias",
                  "Canto do Amaro"]

PALAVRAS_DO_SETOR = ["petroleo", "gas natural", "pre-sal", "pre sal", "poco", "pocos", "offshore", "onshore",
                     "fpso", "plataforma", "barris", "boe", "bacia", "exploracao", "producao de oleo", "upstream",
                     "royalties", "e&p", "oleo e gas", "campo de", "campos de", "leilao de areas", "refino"]

USER_AGENT = "OleoEmNumeros/1.0 (+https://oleoemnumeros.com.br)"


def _texto(el) -> str:
    if el is None:
        return ""
    t = "".join(el.itertext()) if len(el) else (el.text or "")
    return html.unescape(re.sub(r"<[^>]+>", " ", t)).strip()


def _filho(item, *nomes):
    for f in item:
        tag = f.tag.rsplit("}", 1)[-1].lower()
        if tag in nomes:
            return f
    return None


def _data(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        d = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def ler_feed(conteudo: bytes, fonte: str) -> list[dict]:
    """Lê RSS 2.0, RSS 1.0 (RDF, usado pelo gov.br) e Atom."""
    raiz = ET.fromstring(conteudo)
    itens = []
    for item in raiz.iter():
        tag = item.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        titulo = re.sub(r"\s+", " ", _texto(_filho(item, "title")))
        link_el = _filho(item, "link")
        link = (link_el.get("href") if link_el is not None and link_el.get("href") else _texto(link_el)).strip()
        if not link:
            link = (item.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") or "").strip()
        data = _data(_texto(_filho(item, "pubdate", "date", "published", "updated", "issued")))
        resumo = _texto(_filho(item, "description", "summary"))[:600]
        if titulo and link.startswith("http"):
            itens.append({"titulo": titulo, "link": link, "fonte": fonte,
                          "data": data.isoformat() if data else None, "_resumo": resumo})
    return itens


def ler_govbr(pagina: str, base: str, fonte: str) -> list[dict]:
    """Lista de notícias de um portal gov.br: cada bloco 'conteudo' tem link, título e data."""
    itens = []
    for bloco in re.split(r'class="(?:conteudo|tileItem)', pagina)[1:]:
        m = re.search(r'<a[^>]+href="(' + re.escape(base) + r'/[^"#?]+)"[^>]*>(.*?)</a>', bloco, re.S)
        if not m:
            continue
        titulo = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        d = re.search(r"(\d{2})/(\d{2})/(\d{4})(?:\D+(\d{1,2})h(\d{2}))?", bloco)
        data = None
        if d:
            dia, mes, ano, hh, mm = d.groups()
            data = datetime(int(ano), int(mes), int(dia), int(hh or 12), int(mm or 0),
                            tzinfo=timezone(timedelta(hours=-3))).astimezone(timezone.utc).isoformat()
        if titulo:
            itens.append({"titulo": titulo, "link": m.group(1), "fonte": fonte, "data": data, "_resumo": ""})
    return itens


NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36")


def baixar_fonte(f: dict) -> tuple[list[dict], str]:
    ua = NAVEGADOR if f.get("navegador") or f.get("tipo") == "govbr" else USER_AGENT
    try:
        r = requests.get(f["url"], headers={"User-Agent": ua, "Accept": "application/rss+xml, application/xml, text/html, */*"},
                         timeout=25)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        if f.get("tipo") == "govbr":
            return ler_govbr(r.text, f["url"], f["nome"]), "ok"
        return ler_feed(r.content, f["nome"]), "ok"
    except (requests.RequestException, ET.ParseError) as e:
        return [], f"{type(e).__name__}: {str(e)[:120]}"


def _norm(t: str) -> str:
    return " " + re.sub(r"[^0-9a-z&]+", " ", sem_acento(t).casefold()) + " "


def _regex_nome(nome: str, flags=0) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(nome) + r"(?![\w-])", flags)


def _caixa_alta(t: str) -> bool:
    letras = [c for c in t if c.isalpha()]
    return len(letras) > 10 and sum(c.isupper() for c in letras) / len(letras) > 0.7


def marcar(item: dict, campos: list[dict]) -> dict:
    """Empresas e campos citados no título ou no resumo do feed."""
    texto = f"{item['titulo']} {item.get('_resumo', '')}"
    n = _norm(texto)
    # manchete toda em maiúsculas (Petronotícias): compara sem diferenciar caixa
    fl = re.I if _caixa_alta(item["titulo"]) else 0
    empresas = [e for e, nomes in EMPRESAS.items() if any(_regex_nome(x, fl).search(texto) for x in nomes)]
    do_setor = bool(empresas or any(f" {p} " in n for p in PALAVRAS_DO_SETOR) or _regex_nome("ANP").search(texto))
    achados = []
    for c in campos:
        nome_n = _norm(c["nome"]).strip()
        if not nome_n:
            continue
        # "campo de X" vale sempre; o nome solto (Búzios, Mero) só em matéria do setor
        if re.search(r" campos? (?:de |do |da )?" + re.escape(nome_n) + " ", n) or (
                do_setor and c["nome"] in CAMPOS_FAMOSOS_NORMAIS and _regex_nome(c["nome"], fl).search(texto)):
            achados.append(c["slug"])
    relevante = bool(do_setor or achados)
    return {**item, "empresas": empresas, "campos": sorted(set(achados)), "relevante": relevante}


CAMPOS_FAMOSOS_NORMAIS = set()


def _campos_do_site() -> list[dict]:
    """Nome e slug dos campos, do último agregados.json. Nomes de campo vêm em maiúsculas da ANP."""
    arq = config.PROCESSADOS / "agregados.json"
    if not arq.exists():
        return []
    ag = json.loads(arq.read_text("utf-8"))
    famosos = {sem_acento(x).casefold(): x for x in CAMPOS_FAMOSOS}
    saida = []
    for c in ag["campos"]:
        nome = c["nome"]
        chave = sem_acento(nome).casefold()
        if chave in famosos:
            nome = famosos[chave]          # grafia como a imprensa escreve, para casar com maiúscula
            CAMPOS_FAMOSOS_NORMAIS.add(nome)
        saida.append({"nome": nome, "slug": c["slug"]})
    return saida


def coletar(fontes=FONTES, historico: Path = HISTORICO, saida: Path = SAIDA, agora=None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    antigos = json.loads(historico.read_text("utf-8")) if historico.exists() else []
    por_link = {i["link"]: i for i in antigos}
    campos = _campos_do_site()
    status = {}
    for f in fontes:
        itens, st = baixar_fonte(f)
        status[f["nome"]] = f"{st} · {len(itens)} itens"
        for i in itens:
            if i["link"] in por_link:
                continue
            i["data"] = i["data"] or agora.isoformat()
            i["coletado"] = agora.isoformat()
            por_link[i["link"]] = marcar(i, campos)
    limite = (agora - timedelta(days=DIAS_GUARDADOS)).isoformat()
    todos = sorted((i for i in por_link.values() if i["data"] >= limite), key=lambda i: i["data"], reverse=True)
    historico.parent.mkdir(parents=True, exist_ok=True)
    historico.write_text(json.dumps(todos, ensure_ascii=False), "utf-8")

    # remarca tudo com a lista de campos atual (campos novos, grafias corrigidas)
    todos = [marcar(i, campos) for i in todos]
    limite_site = (agora - timedelta(days=DIAS_NO_SITE)).isoformat()
    site = [{k: v for k, v in i.items() if not k.startswith("_") and k != "relevante"}
            for i in todos if i["relevante"] and i["data"] >= limite_site and i["data"] <= (agora + timedelta(hours=6)).isoformat()]
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(site, ensure_ascii=False), "utf-8")
    return {"status": status, "total": len(todos), "no_site": len(site)}


def depurar_govbr() -> str:
    """Mostra o HTML em volta do primeiro link de notícia (para ajustar o leitor)."""
    saida = []
    for f in FONTES:
        if f.get("tipo") != "govbr":
            continue
        r = requests.get(f["url"], headers={"User-Agent": NAVEGADOR}, timeout=25)
        t = r.text
        links = [m.start() for m in re.finditer(r'href="' + re.escape(f["url"]) + r'/[a-z0-9-]{15,}', t)]
        contagem = {k: t.count(k) for k in ("tileItem", "tileHeadline", "summary url", "documentByLine", "icon-day",
                                                 "data-noticia", "card", "titulo")}
        saida.append(f"### {f['nome']} HTTP {r.status_code} tamanho {len(t)} links de notícia: {len(links)} {contagem}")
        if links:
            i = links[min(2, len(links) - 1)]
            saida.append(re.sub(r"\s+", " ", t[max(0, i - 1200): i + 1200]))
    return "\n".join(saida)


def testar() -> str:
    linhas = ["## Fontes de notícias", "", "| Fonte | Resultado | Exemplos |", "|---|---|---|"]
    for f in FONTES:
        itens, st = baixar_fonte(f)
        ex = " · ".join(i["titulo"][:70] + (f" ({i['data'][:10]})" if i["data"] else "") for i in itens[:3])
        linhas.append(f"| {f['nome']} | {st} · {len(itens)} itens | {ex} |")
    return "\n".join(linhas) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--testar", action="store_true")
    a = p.parse_args(argv)
    if a.testar:
        print(testar())
        return 0
    r = coletar()
    print("Notícias: " + "; ".join(f"{k}: {v}" for k, v in r["status"].items()))
    print(f"Histórico: {r['total']} manchetes · no site: {r['no_site']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

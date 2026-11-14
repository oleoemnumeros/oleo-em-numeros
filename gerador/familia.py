"""Links cruzados por município com os outros portais da Família em Números.

Cada portal publica a lista das próprias páginas de município. Na geração, lemos a dos outros
(guardando uma cópia em dados/familia/ para o caso de um site estar fora do ar) e ligamos só as
cidades que têm página lá. Sem rede e sem cópia, o site sai sem esses links.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from pathlib import Path

INDICES = {
    "comex": ("https://comexemnumeros.com.br/municipios/indice.json", "Exportações e importações", "Comex em Números",
              "o que as empresas da cidade vendem e compram do exterior"),
    "economia": ("https://economiaemnumeros.com.br/municipios/busca.json", "Economia", "Economia em Números",
                 "população, PIB, Bolsa Família e contas da prefeitura"),
    "voo": ("https://vooemnumeros.com.br/aeroportos/indice.json", "Voos", "Voo em Números",
            "atrasos e cancelamentos no aeroporto da cidade"),
}


def chave_cidade(nome: str, uf: str) -> str:
    t = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t) + "-" + str(uf).strip().upper()


def carregar(pasta: Path, timeout: float = 15, rede: bool = True) -> dict:
    """{chave_cidade: [{"rotulo", "portal", "descricao", "url"}, ...]}"""
    out: dict = {}
    for nome, (url, rot, portal, desc) in INDICES.items():
        cache = pasta / f"{nome}.json"
        itens = None
        try:
            if not rede:
                raise OSError("sem rede")
            req = urllib.request.Request(url, headers={"User-Agent": "oleoemnumeros"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                itens = json.loads(r.read().decode("utf-8"))
            pasta.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(itens, ensure_ascii=False, separators=(",", ":")), "utf-8")
        except Exception:  # noqa: BLE001
            if cache.exists():
                try:
                    itens = json.loads(cache.read_text("utf-8"))
                except Exception:  # noqa: BLE001
                    itens = None
        base = "/".join(url.split("/", 3)[:3])
        for it in itens or []:
            try:
                cid, uf, caminho = it[0], it[1], it[2]
            except (IndexError, TypeError, KeyError):
                continue
            link = caminho if str(caminho).startswith("http") else base + str(caminho)
            out.setdefault(chave_cidade(cid, uf), []).append({"rotulo": rot, "portal": portal, "descricao": desc, "url": link})
    return out


# ---------------------------------------------------------------- Continue explorando (fim de cada página)
# Para incluir um novo portal, acrescente uma linha aqui e na lista do rodapé (modelos/base.html).
PORTAIS = {
    "oleo": ("Óleo em Números", "", "#F59E0B"),
    "comex": ("Comex em Números", "https://comexemnumeros.com.br", "#37C0A5"),
    "voo": ("Voo em Números", "https://vooemnumeros.com.br", "#A58BF0"),
    "economia": ("Economia em Números", "https://economiaemnumeros.com.br", "#7EA3FF"),
}
CX_PETROLEO = ("comex", "/produto/2709/", "O petróleo na balança comercial", "Quanto o Brasil exporta de petróleo bruto e para onde vai")
CX_DERIVADOS = ("comex", "/produto/2710/", "Derivados no comércio exterior", "Diesel, gasolina e outros: o que o Brasil vende e compra")
CX_GAS = ("comex", "/produto/2711/", "Gás natural e GLP no comércio exterior", "O que o Brasil importa e exporta")
EC_INFLACAO = ("economia", "/inflacao/", "Inflação: IPCA", "No Brasil e em 16 áreas do IBGE, com o peso de cada grupo")
EC_CAMBIO = ("economia", "/cambio/", "Dólar", "O petróleo é negociado em dólar: a cotação e o histórico")
EC_MUN = ("economia", "/municipios/", "PIB e contas da sua cidade", "População, PIB e de onde vem o dinheiro da prefeitura")
EC_FISCAL = ("economia", "/contas-publicas/", "Contas públicas", "Resultado fiscal e dívida do governo")
EC_JUROS = ("economia", "/juros/", "Selic, Copom e Boletim Focus", "Calendário, atas e o que o mercado espera")
VOO_HOME = ("voo", "/", "Os voos no Brasil", "Pontualidade e cancelamentos por companhia e aeroporto")
# seções do próprio site: chave -> (caminho, título, descrição, flag que precisa estar ligada)
NO_SITE = {
    "royalties": ("royalties/", "Royalties por município", "Quanto cada cidade e cada estado recebe do petróleo", "royalties"),
    "precos": ("precos/", "Preços de combustíveis", "Gasolina, diesel e etanol nos postos, por estado e município", "precos"),
    "refino": ("refino/", "Refino de petróleo", "Quanto cada refinaria processa e produz", "refino"),
    "gas": ("gas/", "Gás natural", "Queima, reinjeção e aproveitamento", "gas"),
    "mapa": ("mapa/", "Mapa dos campos e poços", "Cada campo produtor e a produção de cada poço", "mapa"),
    "resultados": ("resultados/", "Petroleiras listadas", "Resultado trimestral de PETR4, PRIO3, BRAV3 e RECV3", "resultados"),
    "pocos": ("pocos/", "Produção por poço", "Busque um poço e veja a produção mês a mês", ""),
    "rankings": ("rankings/", "Rankings", "Quem mais produz e quem mais cresce", ""),
    "campos": ("campos/", "Campos produtores", "Do maior ao menor, com operadora e bacia", ""),
    "operadoras": ("operadoras/", "Operadoras", "Quem produz o petróleo e o gás do Brasil", ""),
    "bacias": ("bacias/", "Bacias", "De Santos ao Recôncavo, bacia a bacia", ""),
}
POR_SECAO = {
    "inicio": [CX_PETROLEO, EC_CAMBIO, "precos", "refino"],   # royalties e listadas já são cartões da própria página inicial
    "operadoras": [CX_PETROLEO, "campos", "mapa", EC_CAMBIO],
    "campos": [CX_PETROLEO, "operadoras", "mapa", EC_CAMBIO],
    "bacias": [CX_PETROLEO, "campos", "mapa", EC_CAMBIO],
    "estados": [CX_PETROLEO, "royalties", "campos", EC_MUN],
    "pocos": [CX_PETROLEO, "campos", "mapa", "operadoras"],
    "mapa": [CX_PETROLEO, "campos", "pocos", EC_CAMBIO],
    "rankings": [CX_PETROLEO, "campos", "operadoras", EC_CAMBIO],
    "refino": [CX_DERIVADOS, "precos", VOO_HOME, "gas"],
    "precos": [EC_INFLACAO, CX_DERIVADOS, "refino", "royalties"],
    "gas": [CX_GAS, "refino", "royalties", EC_CAMBIO],
    "royalties": [EC_MUN, EC_FISCAL, CX_PETROLEO, "campos"],
    "resultados": [EC_JUROS, CX_PETROLEO, "operadoras", EC_CAMBIO],
}


def continuar(secao: str, caminho: str, flags: dict) -> list:
    """Cartões do fim da página: [{"portal", "nome", "cor", "caminho"|"url", "titulo", "desc"}], no máximo 4.
    Seção do próprio site só entra se a página existe (flags) e não é a página atual; cada clique vira o evento
    continue/oleo-<destino> no GoatCounter."""
    cartoes = []
    for it in POR_SECAO.get(secao, []):
        if isinstance(it, str):
            cam, titulo, desc, flag = NO_SITE[it]
            if (flag and not flags.get(flag)) or it == secao or cam == caminho:
                continue
            portal, alvo = "oleo", {"caminho": cam}
        else:
            portal, cam, titulo, desc = it
            alvo = {"url": PORTAIS[portal][1] + cam}
        nome, _, cor = PORTAIS[portal]
        cartoes.append({"portal": portal, "nome": nome, "cor": cor, "titulo": titulo, "desc": desc, **alvo})
        if len(cartoes) == 4:
            break
    return cartoes

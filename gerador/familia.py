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

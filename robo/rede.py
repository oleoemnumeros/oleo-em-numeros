"""Acesso ao gov.br quando ele bloqueia os servidores do GitHub.

Desde novembro de 2026 o www.gov.br recusa a conexão vinda dos servidores do GitHub (erro de TLS em qualquer página).
Este módulo intercepta toda requisição GET feita com `requests` pelos robôs: se a conexão direta com um destino da
lista falhar, ela é refeita por um caminho alternativo, sem mudar nada nos robôs.

1. Ponte própria (RELAY_URL + RELAY_TOKEN, segredos do GitHub): um Cloudflare Worker do projeto que busca o arquivo
   e devolve os bytes, igual ao original. Serve para páginas e para arquivos (zip, csv, xls, pdf).
2. Sem a ponte, só para páginas: o leitor público r.jina.ai devolve o HTML da página, e os robôs acham os links
   normalmente. Arquivos não passam por ele.

É ligado por `robo/__init__.py`; em testes, nada muda enquanto a conexão direta funciona.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote, urlparse

import requests

DESTINOS = {"www.gov.br", "ftp.ibge.gov.br"}          # só estes passam pelo caminho alternativo
LEITOR = "https://r.jina.ai/"
_ARQUIVO = re.compile(r"\.(zip|csv|xlsx?|pdf|txt|json|geojson|kml|kmz|rar|7z|gz)(\?|$)", re.I)
_original = requests.sessions.Session.request


class PonteNecessaria(requests.ConnectionError):
    """Arquivo do gov.br que só passa pela ponte do projeto. É um erro de conexão: os robôs já sabem lidar com ele."""
_avisados: set[str] = set()


def _aviso(chave: str, msg: str) -> None:
    if chave not in _avisados:
        _avisados.add(chave)
        print(msg)


def _alternativo(self, url: str, kw: dict):
    relay, token = os.environ.get("RELAY_URL", "").strip(), os.environ.get("RELAY_TOKEN", "").strip()
    timeout = max(kw.get("timeout") or 0, 300) if isinstance(kw.get("timeout"), (int, float)) else 300
    if relay and token:
        _aviso("relay", "gov.br recusou a conexão direta: usando a ponte do projeto (RELAY_URL).")
        return _original(self, "GET", f"{relay.rstrip('/')}/?url={quote(url, safe='')}",
                         headers={"X-Token": token}, timeout=timeout, stream=kw.get("stream", False))
    if not _ARQUIVO.search(urlparse(url).path):
        _aviso("leitor", "gov.br recusou a conexão direta e não há ponte configurada: lendo a página pela r.jina.ai.")
        r = _original(self, "GET", LEITOR + url, headers={"X-Return-Format": "html"}, timeout=timeout)
        r.url = url                                      # os robôs resolvem links relativos a partir daqui
        return r
    return None


def _request(self, method, url, *args, **kw):
    try:
        return _original(self, method, url, *args, **kw)
    except (requests.ConnectionError, requests.Timeout) as erro:
        if str(method).upper() != "GET" or urlparse(str(url)).hostname not in DESTINOS or args:
            raise
        r = _alternativo(self, str(url), kw)
        if r is None:
            raise PonteNecessaria(f"gov.br recusou a conexão e o arquivo precisa da ponte do projeto (RELAY_URL): {url}") from erro
        return r


def instalar() -> None:
    """Liga o caminho alternativo para todas as requisições do processo (idempotente)."""
    if requests.sessions.Session.request is not _request:
        requests.sessions.Session.request = _request

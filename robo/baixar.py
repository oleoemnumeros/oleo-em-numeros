"""Baixa da ANP os arquivos de produção por poço.

O robô lê os links das páginas de dados abertos (os nomes de arquivo mudam
de um ano para outro), baixa só o que é novo ou do ano corrente (a ANP revisa
os arquivos no mesmo endereço) e registra tudo num manifesto.
"""
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from . import config


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
    return s


def _get(sessao, url, tentativas=4, **kw):
    for i in range(tentativas):
        try:
            r = sessao.get(url, timeout=120, **kw)
            if r.status_code == 200:
                return r
            erro = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            erro = str(e)
        time.sleep(2 ** i * 3)
    raise RuntimeError(f"Falha ao baixar {url}: {erro}")


def listar_arquivos(sessao=None) -> list[str]:
    """Devolve as URLs de arquivos de produção encontradas nas páginas da ANP."""
    sessao = sessao or _sessao()
    padroes = [re.compile(p, re.I) for p in config.PADROES_LINKS]
    urls = []
    for pagina in config.PAGINAS_ANP:
        html = _get(sessao, pagina).text
        p = _Links()
        p.feed(html)
        for href in p.links:
            url = urljoin(pagina, href.strip())
            url = re.sub(r"/@@download/file$", "", url)
            if any(rx.search(urlparse(url).path) for rx in padroes) and url not in urls:
                urls.append(url)
    return urls


def _ano_do_arquivo(nome: str) -> int | None:
    anos = [int(a) for a in re.findall(r"(20\d{2})", nome)]
    return max(anos) if anos else None


def _nome_local(url: str) -> str:
    partes = [unquote(p) for p in urlparse(url).path.split("/") if p]
    # guarda a pasta-mãe (ex.: '2023', 'pm', 'pt') para não colidir nomes iguais
    nome = "_".join(partes[-2:]) if len(partes) >= 2 else partes[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", nome)


def baixar_tudo(destino: Path = config.BRUTOS, ano_inicial: int = config.ANO_INICIAL) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    manifesto_path = destino / "manifesto.json"
    manifesto = json.loads(manifesto_path.read_text("utf-8")) if manifesto_path.exists() else {}
    sessao = _sessao()
    urls = listar_arquivos(sessao)
    if not urls:
        raise RuntimeError("Nenhum arquivo de produção encontrado nas páginas da ANP. O layout das páginas mudou?")

    ano_atual = date.today().year
    novos, iguais, pulados = [], [], []
    for url in urls:
        nome = _nome_local(url)
        ano = _ano_do_arquivo(nome)
        if ano is not None and ano < ano_inicial:
            pulados.append(nome)
            continue
        alvo = destino / nome
        recente = ano is None or ano >= ano_atual - 1
        if alvo.exists() and not recente:
            iguais.append(nome)
            continue
        conteudo = _get(sessao, url).content
        sha = hashlib.sha256(conteudo).hexdigest()
        if alvo.exists() and manifesto.get(nome, {}).get("sha256") == sha:
            iguais.append(nome)
            continue
        alvo.write_bytes(conteudo)
        manifesto[nome] = {
            "url": url,
            "sha256": sha,
            "bytes": len(conteudo),
            "baixado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        novos.append(nome)
        time.sleep(1)  # educação com o servidor da ANP

    manifesto_path.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), "utf-8")
    return {"novos": novos, "sem_mudanca": iguais, "fora_do_periodo": pulados}


if __name__ == "__main__":
    print(json.dumps(baixar_tudo(), ensure_ascii=False, indent=2))

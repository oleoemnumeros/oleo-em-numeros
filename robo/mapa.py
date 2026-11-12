"""Dados do mapa: limites dos campos e coordenadas dos poços, dos shapefiles que a ANP publica.

Baixa os dois arquivos da página de dados georreferenciados, simplifica os polígonos
(a precisão de ~40 m basta para um mapa de página) e guarda só os poços que aparecem na nossa
base de produção. Resultado: dados/processados/geo.json, que o gerador do site lê.

Uso:
    python -m robo.mapa                # baixa, processa e grava geo.json
    python -m robo.mapa --sem-baixar   # usa os arquivos já baixados em dados/geo
    python -m robo.mapa --so-baixar    # só baixa (ferramenta de manutenção)
"""
import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd

from . import config
from .baixar import _Links, _get, _sessao
from .ler import _chave_poco
from .util import sem_acento

PAGINA = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/dados-georreferenciados-das-bacias-sedimentares-brasileiras"
PASTA = config.DADOS / "geo"
ARQUIVOS = {"campos": r"campos?-?(de-)?producao\.zip$", "pocos": r"shapefile-de-pocos\.zip$"}
TOLERANCIA = 0.0004   # graus (~45 m): quanto de detalhe dos limites se perde
BRASIL = (-75.0, -34.0, -34.0, 6.0)   # lon_min, lat_min, lon_max, lat_max: descarta coordenada fora do país


def chave_campo(nome) -> str:
    return re.sub(r"[^a-z0-9]", "", sem_acento(str(nome)).casefold())


def baixar(destino: Path = PASTA) -> dict:
    """Baixa os dois shapefiles. Os nomes dos arquivos são lidos da página da ANP."""
    destino.mkdir(parents=True, exist_ok=True)
    s = _sessao()
    p = _Links()
    p.feed(_get(s, PAGINA).text)
    links = [re.sub(r"/@@download/file$", "", urljoin(PAGINA, h.strip())) for h in p.links]
    saida = {}
    for tipo, padrao in ARQUIVOS.items():
        url = next((u for u in links if re.search(padrao, u, re.I)), None)
        if not url:
            raise RuntimeError(f"Link do shapefile de {tipo} não encontrado em {PAGINA}")
        arq = destino / f"{tipo}.zip"
        arq.write_bytes(_get(s, url).content)
        saida[tipo] = url
        print(f"{tipo}: {arq.stat().st_size / 1e6:.1f} MB de {url}")
    return saida


def _ler(zip_path: Path):
    import shapefile  # pyshp
    pasta = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(pasta)
    shp = next(pasta.rglob("*.shp"))
    for enc in ("utf-8", "latin-1"):
        try:
            r = shapefile.Reader(str(shp), encoding=enc)
            list(r.records())
            return r
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"Não consegui ler {zip_path}")


def _dist2(p, a, b):
    """Distância ao quadrado do ponto p ao segmento a-b (em graus, basta para simplificar)."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (p[0] - ax) ** 2 + (p[1] - ay) ** 2
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return (p[0] - ax - t * dx) ** 2 + (p[1] - ay - t * dy) ** 2


def simplificar(pts: list, tol: float = TOLERANCIA) -> list:
    """Douglas-Peucker sem recursão. Mantém o primeiro e o último ponto."""
    n = len(pts)
    if n < 3:
        return list(pts)
    manter = [False] * n
    manter[0] = manter[-1] = True
    pilha = [(0, n - 1)]
    tol2 = tol * tol
    while pilha:
        i, j = pilha.pop()
        melhor, k = 0.0, None
        for m in range(i + 1, j):
            d = _dist2(pts[m], pts[i], pts[j])
            if d > melhor:
                melhor, k = d, m
        if k is not None and melhor > tol2:
            manter[k] = True
            pilha += [(i, k), (k, j)]
    return [p for p, ok in zip(pts, manter) if ok]


def _anel(pts: list, tol: float = TOLERANCIA) -> list | None:
    """Anel fechado simplificado e arredondado. Parte o anel no ponto mais distante do primeiro,
    porque Douglas-Peucker não funciona quando o início e o fim são o mesmo ponto."""
    pts = [tuple(p) for p in pts]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    m = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    a = simplificar(pts[:m + 1], tol)
    b = simplificar(pts[m:] + [pts[0]], tol)
    anel = a[:-1] + b[:-1]
    anel = [[round(x, 4), round(y, 4)] for x, y in anel]
    dedup = [anel[0]] + [p for i, p in enumerate(anel[1:], 1) if p != anel[i - 1]]
    return dedup if len(dedup) >= 3 else None


def _area(pts) -> float:
    """Área com sinal (fórmula do cadarço): negativa = sentido horário = anel externo no shapefile."""
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


def campos_de(r) -> list[dict]:
    cols = [f[0] for f in r.fields[1:]]
    saida = []
    for sh, rec in zip(r.iterShapes(), r.iterRecords()):
        d = dict(zip(cols, rec))
        pts, partes = sh.points, list(sh.parts) + [len(sh.points)]
        aneis = [pts[partes[i]:partes[i + 1]] for i in range(len(partes) - 1)]
        externos = [a for a in aneis if len(a) > 3 and _area(a) < 0] or aneis   # ignora buracos
        rings = [x for x in (_anel(a) for a in externos) if x]
        if not rings:
            continue
        saida.append({"k": chave_campo(d.get("NOM_CAMPO", "")), "n": str(d.get("NOM_CAMPO", "")).strip(),
                      "op": str(d.get("OPERADOR_C", "")).strip(), "et": str(d.get("ETAPA", "")).strip(),
                      "loc": str(d.get("Localizaca", "")).strip(), "bacia": str(d.get("NOM_BACIA", "")).strip(),
                      "g": rings})
    return saida


def pocos_de(r, nomes: set) -> dict:
    """{nome normalizado do poço: [lon, lat]} só dos poços que estão na nossa base de produção.
    A produção às vezes usa o nome do poço dado pela operadora (POCO_OPERA), por isso as duas colunas valem."""
    cols = [f[0] for f in r.fields[1:]]
    lon0, lat0, lon1, lat1 = BRASIL
    saida = {}
    for rec in r.iterRecords():
        d = dict(zip(cols, rec))
        try:
            lat, lon = float(d.get("LAT_DD")), float(d.get("LONG_DD"))
        except (TypeError, ValueError):
            continue
        if not (lon0 <= lon <= lon1 and lat0 <= lat <= lat1) or (lat == 0 and lon == 0):
            continue
        for col in ("POCO", "POCO_OPERA"):
            k = _chave_poco(d.get(col) or "")
            if k and k in nomes and k not in saida:
                saida[k] = [round(lon, 5), round(lat, 5)]
    return saida


def processar(pasta: Path, base_csv: Path, saida: Path) -> dict:
    nomes = {_chave_poco(p) for p in pd.read_csv(base_csv, usecols=["poco"], dtype=str)["poco"].dropna().unique()}
    campos = campos_de(_ler(pasta / "campos.zip"))
    pocos = pocos_de(_ler(pasta / "pocos.zip"), nomes)
    saida.write_text(json.dumps({"campos": campos, "pocos": pocos}, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return {"campos": len(campos), "pocos": len(pocos), "poços na base": len(nomes),
            "kb": round(saida.stat().st_size / 1e3)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sem-baixar", action="store_true")
    p.add_argument("--so-baixar", action="store_true")
    a = p.parse_args(argv)
    if not a.sem_baixar:
        baixar()
    if a.so_baixar:
        return 0
    r = processar(PASTA, config.PROCESSADOS / "base_pocos.csv.gz", config.PROCESSADOS / "geo.json")
    print(f"Mapa: {r['campos']} limites de campo, {r['pocos']} poços com coordenada "
          f"(de {r['poços na base']} na base de produção), geo.json com {r['kb']} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

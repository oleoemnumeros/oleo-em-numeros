"""Diagnóstico (não publica nada): o que a ANP oferece de dados geográficos de campos e poços.

Lê a página de dados georreferenciados, baixa os shapefiles de campos de produção e de poços,
mostra as colunas e mede quanto dos nossos campos e poços (da base de produção) aparece neles.
Rodar pelo GitHub (Actions > Explorar dados geográficos): daqui o site da ANP não responde.
"""
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin

from . import config
from .baixar import _Links, _get, _sessao
from .ler import _chave_poco, consolidar
from .util import sem_acento

PAGINA = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/dados-georreferenciados-das-bacias-sedimentares-brasileiras"


def _k(t):
    return re.sub(r"[^a-z0-9]", "", sem_acento(str(t)).casefold())


def _ler_shape(zip_path: Path, destino: Path):
    import shapefile  # pyshp
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(destino)
    shp = next(destino.rglob("*.shp"))
    for enc in ("utf-8", "latin-1"):
        try:
            r = shapefile.Reader(str(shp), encoding=enc)
            list(r.records())
            return r, shp
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"Não consegui ler {shp}")


def _resumo(nome: str, r, shp: Path) -> None:
    print(f"\n### {nome}: {shp.name}")
    print(f"tipo de geometria: {r.shapeTypeName} | registros: {len(r)} | bbox: {[round(x, 3) for x in r.bbox]}")
    prj = shp.with_suffix(".prj")
    print("projeção:", (prj.read_text(errors='replace')[:140] if prj.exists() else "sem .prj"))
    print("colunas:", ", ".join(f"{f[0]}({f[1]})" for f in r.fields[1:]))
    cols = [f[0] for f in r.fields[1:]]
    for rec in list(r.iterRecords())[:3]:
        print("  exemplo:", dict(zip(cols, rec)))


def _melhor_coluna(r, nossos: set, chave=_k):
    """Qual coluna de texto mais combina com os nossos nomes."""
    cols = [f[0] for f in r.fields[1:]]
    recs = list(r.iterRecords())
    melhor = (0, None, set())
    for i, c in enumerate(cols):
        vals = {chave(x[i]) for x in recs if isinstance(x[i], str) and x[i].strip()}
        achou = vals & nossos
        if len(achou) > melhor[0]:
            melhor = (len(achou), c, vals)
    return melhor


def main() -> None:
    s = _sessao()
    html = _get(s, PAGINA).text
    p = _Links()
    p.feed(html)
    links = []
    for h in p.links:
        u = re.sub(r"/@@download/file$", "", urljoin(PAGINA, h.strip()))
        if re.search(r"\.(zip|csv|xlsx?|json|geojson|kml)$", u, re.I) and u not in links:
            links.append(u)
    print("## Arquivos encontrados na página da ANP")
    for u in links:
        print(" -", unquote(u))

    def achar(rx):
        return next((u for u in links if re.search(rx, u, re.I) and "metadado" not in u.lower()), None)

    u_campos, u_pocos = achar(r"campos?[-_ ]?de[-_ ]?producao|campos"), achar(r"pocos")
    print("\ncampos:", u_campos, "\npoços:", u_pocos)

    base, _ = consolidar(config.BRUTOS, config.MANUAIS / "operador_por_campo.csv", config.ANO_INICIAL)
    ult = base[base["periodo"] == base["periodo"].max()]
    ativos = ult[(ult["petroleo_bbl_d"].fillna(0) + ult["gas_mil_m3_d"].fillna(0)) > 0]
    campos_nossos = {_k(c): c for c in ativos["campo"].unique()}
    pocos_nossos = {_chave_poco(p): p for p in ativos["poco"].unique()}
    print(f"\nNossa base em {base['periodo'].max()}: {len(campos_nossos)} campos e {len(pocos_nossos)} poços ativos")

    tmp = Path(tempfile.mkdtemp())
    for nome, url, nossos, chave in (("CAMPOS", u_campos, campos_nossos, _k), ("POÇOS", u_pocos, pocos_nossos, _chave_poco)):
        if not url:
            print(f"\n### {nome}: link não encontrado")
            continue
        z = tmp / f"{nome}.zip"
        z.write_bytes(_get(s, url).content)
        print(f"\n{nome}: {z.stat().st_size / 1e6:.1f} MB")
        r, shp = _ler_shape(z, tmp / nome)
        _resumo(nome, r, shp)
        n, col, vals = _melhor_coluna(r, set(nossos), chave)
        print(f"coluna que casa com os nossos nomes: {col} -> {n} de {len(nossos)} ({n / max(len(nossos), 1):.0%})")
        sem = [nossos[k] for k in nossos if k not in vals]
        print(f"nossos sem correspondência ({len(sem)}):", sem[:40])
        if nome == "CAMPOS":
            pts = sum(len(sh.points) for sh in r.iterShapes())
            print(f"vértices no total: {pts:,}")
        else:
            cols = [f[0] for f in r.fields[1:]]
            print("colunas que parecem situação/operador/campo:", [c for c in cols if re.search(r"sit|oper|camp|categ|tipo|ambient|terra|mar", c, re.I)])


if __name__ == "__main__":
    sys.exit(main())

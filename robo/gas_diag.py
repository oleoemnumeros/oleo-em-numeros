"""Diagnóstico (ferramenta de manutenção): onde a ANP publica o gás por destino e como são os arquivos."""
import io
import re
import sys
from urllib.parse import unquote, urljoin

from .baixar import _Links, _get, _sessao

BASE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/"
PAGINAS = [BASE + "dados-abertos",
           BASE + "dados-abertos/producao-de-petroleo-e-gas-natural-por-estado-e-localizacao",
           BASE + "dados-abertos/producao-de-gas-natural", BASE + "dados-abertos/producao-de-petroleo-e-gas-natural",
           BASE + "dados-abertos/producao-de-petroleo-e-gas-natural-por-estado",
           BASE + "dados-abertos/producao-de-petroleo-e-gas-natural-por-localizacao",
           BASE + "publicacoes/boletins-anp/boletins/boletim-mensal-da-producao-de-petroleo-e-gas-natural"]
MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def links(s, url):
    try:
        p = _Links()
        p.feed(_get(s, url).text)
    except Exception as e:  # noqa: BLE001
        print(f"  (falhou: {str(e)[:120]})")
        return None
    return [re.sub(r"/@@download/file$", "", urljoin(url, h.strip())) for h in p.links]


def main() -> None:
    s = _sessao()
    achados = []
    for pg in PAGINAS:
        print(f"\n## {pg}")
        ls = links(s, pg)
        if ls is None:
            continue
        for u in dict.fromkeys(ls):
            if re.search(r"ppgn|queima|reinje|consumo|gas-natural|gas_natural|bmppgn", u, re.I) and not u.endswith("#"):
                print(" -", unquote(u))
                achados.append(u)
    csvs = [u for u in dict.fromkeys(achados) if re.search(r"\.(csv|xlsx?)$", u, re.I) and "metadado" not in u.lower()]
    print("\n## Arquivos de dados candidatos:", len(csvs))
    for u in csvs:
        try:
            r = _get(s, u)
            txt = r.content[:600].decode("latin-1", "replace")
            print(f"\n### {unquote(u)}  ({len(r.content) / 1e3:.0f} KB)\n{txt}")
        except Exception as e:  # noqa: BLE001
            print(f"\n### {unquote(u)}: falhou {str(e)[:100]}")

    print("\n## Boletim mensal (PDF)")
    import pdfplumber
    for ano, mes in ((2026, "outubro"), (2026, "setembro")):
        url = f"{BASE}publicacoes/boletins-anp/arquivos-bmppgn/{ano}/{mes}.pdf"
        try:
            r = _get(s, url)
        except Exception as e:  # noqa: BLE001
            print(f"{mes}/{ano}: indisponível ({str(e)[:80]})")
            continue
        print(f"{mes}/{ano}: {len(r.content) / 1e6:.1f} MB")
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            print("páginas:", len(pdf.pages))
            for i, pg in enumerate(pdf.pages):
                t = pg.extract_text() or ""
                if re.search(r"queima.*por campo|campos? .*queima|Queima de g[aá]s natural", t, re.I):
                    print(f"\n--- página {i + 1} ---\n{t[:2500]}")
                    print("tabelas:", [[row for row in tb[:4]] for tb in pg.extract_tables()][:2])
        break


if __name__ == "__main__":
    sys.exit(main())

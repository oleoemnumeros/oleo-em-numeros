"""Preços dos combustíveis na semana (ANP, Levantamento de Preços de Combustíveis, resumo semanal).

Toda semana a ANP publica uma planilha com os preços médios de revenda do Brasil, das regiões, dos estados, das
capitais e dos municípios pesquisados. O robô baixa as planilhas novas (desde ANO_INICIAL), guarda de cada uma só o
resumo do Brasil, dos estados, das capitais e dos municípios em dados/precos/semanal/AAAA-MM-DD.json (a data é o último dia da semana)
e junta tudo em dados/processados/precos_semanais.json, que o gerador usa no resumo semanal do site.

Se o gov.br recusar a conexão (ele às vezes bloqueia os servidores do GitHub), o robô lê a página e as planilhas
pelo leitor público r.jina.ai, que devolve cada aba como texto, e monta o mesmo resumo a partir dele.

Uso:
    python -m robo.precos_semanais                # baixa as semanas que faltam e processa
    python -m robo.precos_semanais --sem-baixar   # só processa o que já está guardado
    python -m robo.precos_semanais --listar       # imprime as planilhas encontradas na página da ANP
"""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

import pandas as pd

from . import config
from .baixar import _Links, _get, _sessao
from .util import sem_acento, slug

PAGINA = ("https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/"
          "levantamento-de-precos-de-combustiveis-ultimas-semanas-pesquisadas")
PASTA = config.DADOS / "precos" / "semanal"
MUNICIPIOS_SEMANAS = 13      # semanas com preço por município no arquivo que vai para o site
ANO_INICIAL = 2025           # 2025 em diante: dá a comparação com a mesma semana do ano anterior
ORCAMENTO_S = 10 * 60        # tempo máximo baixando por execução; o resto fica para a próxima
PRODUTOS = {                 # nome na planilha -> nome no site
    "GASOLINA COMUM": "Gasolina comum", "GASOLINA ADITIVADA": "Gasolina aditivada", "ETANOL HIDRATADO": "Etanol",
    "OLEO DIESEL S10": "Diesel S10", "OLEO DIESEL": "Diesel S500", "GNV": "GNV", "GLP": "Gás de cozinha (13 kg)",
}
UF = {"ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA", "CEARA": "CE", "DISTRITO FEDERAL": "DF",
      "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA", "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS",
      "MINAS GERAIS": "MG", "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE", "PIAUI": "PI",
      "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR",
      "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE", "TOCANTINS": "TO"}
CAPITAL = {"AC": "Rio Branco", "AL": "Maceió", "AP": "Macapá", "AM": "Manaus", "BA": "Salvador", "CE": "Fortaleza",
           "DF": "Brasília", "ES": "Vitória", "GO": "Goiânia", "MA": "São Luís", "MT": "Cuiabá", "MS": "Campo Grande",
           "MG": "Belo Horizonte", "PA": "Belém", "PB": "João Pessoa", "PR": "Curitiba", "PE": "Recife", "PI": "Teresina",
           "RJ": "Rio de Janeiro", "RN": "Natal", "RS": "Porto Alegre", "RO": "Porto Velho", "RR": "Boa Vista",
           "SC": "Florianópolis", "SP": "São Paulo", "SE": "Aracaju", "TO": "Palmas"}


LEITOR = "https://r.jina.ai/"   # leitor público usado quando o gov.br bloqueia o acesso direto
_SO_LEITOR = False             # vira True quando o acesso direto falha nesta execução


def _leitor(url: str) -> str:
    import requests
    erro = ""
    for i in range(3):
        try:
            r = requests.get(LEITOR + url, timeout=180, headers={"Accept": "text/plain"})
            if r.status_code == 200 and r.text.strip():
                return r.text
            erro = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            erro = str(e)
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"Leitor também falhou para {url}: {erro}")


def fontes() -> dict[str, str]:
    """Planilhas de resumo semanal na página da ANP: nome do arquivo -> URL (as de ANO_INICIAL em diante)."""
    global _SO_LEITOR
    try:
        p = _Links()
        p.feed(_get(_sessao(), PAGINA, tentativas=2).text)
        links = p.links
    except RuntimeError as e:
        print(f"Acesso direto ao gov.br falhou ({e}); lendo pela r.jina.ai.")
        _SO_LEITOR = True
        links = re.findall(r"\((https?://www\.gov\.br/anp/[^)\s]+?\.xlsx?)\)", _leitor(PAGINA))
    out = {}
    for h in links:
        u = re.sub(r"/@@download/file$", "", urljoin(PAGINA, h.strip()))
        nome = unquote(u).rsplit("/", 1)[-1]
        ano = re.search(r"/arquivos-lpc/(\d{4})/", u)
        if re.search(r"resumo[_-]?semanal", nome, re.I) and nome.lower().endswith((".xlsx", ".xls")) and ano and int(ano[1]) >= ANO_INICIAL:
            out[nome] = u
    return out


def _produto(v) -> str | None:
    t = sem_acento(str(v)).upper().strip()
    if t.startswith("GLP"):
        return PRODUTOS["GLP"]
    return PRODUTOS.get(t)


def _aba(xl: pd.ExcelFile, nome: str) -> pd.DataFrame | None:
    """Lê a aba achando a linha do cabeçalho (a que tem PRODUTO e PREÇO MÉDIO REVENDA)."""
    aba = next((a for a in xl.sheet_names if sem_acento(a).upper().strip() == nome), None)
    if aba is None:
        return None
    bruto = pd.read_excel(xl, aba, header=None)
    for i in range(min(30, len(bruto))):
        linha = [sem_acento(str(v)).upper().strip() for v in bruto.iloc[i].tolist()]
        if "PRODUTO" in linha and any(c.startswith("PRECO MEDIO REVENDA") for c in linha):
            df = bruto.iloc[i + 1:].copy()
            df.columns = linha
            return df.dropna(how="all")
    return None


def _data(v) -> pd.Timestamp:
    """Data da planilha: vem como data do Excel ou como texto (dd/mm/aaaa ou aaaa-mm-dd)."""
    if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v.strip()):
        return pd.to_datetime(v.strip()[:10])
    return pd.to_datetime(v, dayfirst=True)


STRICT = [(b"http://purl.oclc.org/ooxml/spreadsheetml/main", b"http://schemas.openxmlformats.org/spreadsheetml/2006/main"),
          (b"http://purl.oclc.org/ooxml/officeDocument/relationships", b"http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
          (b"http://purl.oclc.org/ooxml/drawingml/main", b"http://schemas.openxmlformats.org/drawingml/2006/main")]


def _abrir(caminho: Path) -> pd.ExcelFile:
    """Abre a planilha. Algumas vêm no formato "Strict Open XML" ou com vínculos para outras planilhas, que o leitor
    não entende: aí lê uma cópia convertida para o formato comum e sem os vínculos."""
    try:
        xl = pd.ExcelFile(caminho)
        if xl.sheet_names:
            return xl
    except (TypeError, KeyError, ValueError):
        pass
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(caminho) as zi, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            if item.filename.startswith("xl/externalLinks/"):
                continue
            dados = zi.read(item.filename)
            if item.filename.endswith((".xml", ".rels")):
                for strict, comum in STRICT:
                    dados = dados.replace(strict, comum)
                dados = dados.replace(b' conformance="strict"', b"")
            if item.filename == "xl/workbook.xml":
                dados = re.sub(rb"<externalReferences>.*?</externalReferences>", b"", dados, flags=re.S)
            elif item.filename == "xl/_rels/workbook.xml.rels":
                dados = re.sub(rb"<Relationship[^>]*externalLink[^>]*/>", b"", dados)
            elif item.filename == "[Content_Types].xml":
                dados = re.sub(rb"<Override[^>]*externalLink[^>]*/>", b"", dados)
            zo.writestr(item, dados)
    buf.seek(0)
    return pd.ExcelFile(buf)


def resumir(caminho: Path) -> dict:
    """Uma planilha semanal -> {inicio, fim, brasil, estados, capitais}, com preço médio, mínimo, máximo e postos."""
    xl = _abrir(caminho)
    br = _aba(xl, "BRASIL")
    if br is None or br.empty:
        raise ValueError(f"{caminho.name}: aba BRASIL não encontrada")
    ini, fim = _data(br["DATA INICIAL"].iloc[0]), _data(br["DATA FINAL"].iloc[0])
    pm = next(c for c in br.columns if c.startswith("PRECO MEDIO REVENDA"))

    def linha(r):
        def num(c):
            col = next((k for k in r.index if k.startswith(c)), None)
            try:
                return round(float(r[col]), 3) if col is not None and pd.notna(r[col]) else None
            except (TypeError, ValueError):
                return None
        return {"preco": num("PRECO MEDIO REVENDA"), "min": num("PRECO MINIMO REVENDA"), "max": num("PRECO MAXIMO REVENDA"),
                "postos": int(num("NUMERO DE POSTOS") or 0)}

    out = {"inicio": ini.date().isoformat(), "fim": fim.date().isoformat(), "brasil": {}, "estados": {}, "capitais": {},
           "municipios": {}}
    for _, r in br.iterrows():
        p = _produto(r["PRODUTO"])
        if p and pd.notna(r[pm]):
            out["brasil"][p] = linha(r)
    est = _aba(xl, "ESTADOS")
    if est is not None:
        col = next((c for c in est.columns if c.startswith("ESTADO")), None)
        for _, r in est.iterrows():
            p, uf = _produto(r["PRODUTO"]), UF.get(sem_acento(str(r[col])).upper().strip()) if col else None
            if p and uf and pd.notna(r[pm]):
                out["estados"].setdefault(uf, {})[p] = linha(r)["preco"]
    cap = _aba(xl, "CAPITAIS")
    if cap is not None:
        ce = next((c for c in cap.columns if c.startswith("ESTADO")), None)
        cm = next((c for c in cap.columns if c.startswith("MUNICIPIO")), None)
        for _, r in cap.iterrows():
            p = _produto(r["PRODUTO"])
            uf = UF.get(sem_acento(str(r[ce])).upper().strip()) if ce else None
            if p and uf and cm and pd.notna(r[pm]):
                cidade = CAPITAL.get(uf) or str(r[cm]).strip().title()
                out["capitais"].setdefault(f"{cidade}/{uf}", {})[p] = linha(r)["preco"]
    mun = _aba(xl, "MUNICIPIOS")
    if mun is not None:   # chave "UF/slug-do-municipio", a mesma das páginas de preço por município
        ce = next((c for c in mun.columns if c.startswith("ESTADO")), None)
        cm = next((c for c in mun.columns if c.startswith("MUNICIPIO")), None)
        for _, r in mun.iterrows():
            p = _produto(r["PRODUTO"])
            uf = UF.get(sem_acento(str(r[ce])).upper().strip()) if ce else None
            if p and uf and cm and pd.notna(r[pm]) and pd.notna(r[cm]):
                out["municipios"].setdefault(f"{uf}/{slug(str(r[cm]).strip())}", {})[p] = linha(r)["preco"]
    return out


VERSAO = 2   # sobe quando o resumo de cada semana passa a guardar algo novo (2: municípios); as semanas antigas são baixadas de novo


_LINHA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2})/(\d{1,2})/(\d{4}) (.+?) "
                    r"(ETANOL HIDRATADO|GASOLINA ADITIVADA|GASOLINA COMUM|GLP|GNV|OLEO DIESEL S10|OLEO DIESEL)\s+(\d+) (R\$/\S+) "
                    r"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)\s*$")
_REGIOES = ("CENTRO OESTE ", "CENTRO-OESTE ", "NORDESTE ", "NORTE ", "SUDESTE ", "SUL ")
_ESTADOS_LONGOS = sorted(UF, key=len, reverse=True)


def _data_txt(m: str, d: str, a: str, esperado: str | None):
    """O leitor mostra a data como mês/dia/ano; confere com a data do nome do arquivo quando dá."""
    import datetime as _dt
    mdy = _dt.date(int(a), int(m), int(d)) if int(m) <= 12 else None
    dmy = _dt.date(int(a), int(d), int(m)) if int(d) <= 12 else None
    if esperado and dmy and dmy.isoformat() == esperado:
        return dmy
    return mdy or dmy


def _estado_e_resto(meio: str):
    s = sem_acento(meio).upper().strip()
    for r in _REGIOES:
        if s.startswith(r):
            s = s[len(r):]
            break
    for nome in _ESTADOS_LONGOS:
        if s == nome or s.startswith(nome + " "):
            return UF[nome], s[len(nome):].strip()
    return None, s


def resumir_texto(texto: str, nome_arquivo: str = "") -> dict:
    """O mesmo resumo de `resumir`, a partir do texto que o leitor devolve (uma seção "# [Sheet N: _ABA_]" por aba)."""
    datas = re.findall(r"(\d{4})[-_](\d{2})[-_](\d{1,2})", nome_arquivo)
    esp_ini = f"{datas[0][0]}-{datas[0][1]}-{int(datas[0][2]):02d}" if datas else None
    esp_fim = f"{datas[-1][0]}-{datas[-1][1]}-{int(datas[-1][2]):02d}" if len(datas) > 1 else None
    out = {"inicio": None, "fim": None, "brasil": {}, "estados": {}, "capitais": {}, "municipios": {}}
    aba = None
    for linha in texto.splitlines():
        cab = re.match(r"^#\s*\[Sheet \d+:\s*_?([A-ZÇÃÕÁÉÍÓÚÂÊÔ ]+?)_?\]", linha.strip())
        if cab:
            aba = sem_acento(cab.group(1)).upper().strip()
            continue
        m = _LINHA.match(linha.strip())
        if not m or not aba:
            continue
        g = m.groups()
        ini, fim = _data_txt(g[0], g[1], g[2], esp_ini), _data_txt(g[3], g[4], g[5], esp_fim)
        out["inicio"], out["fim"] = out["inicio"] or ini.isoformat(), out["fim"] or fim.isoformat()
        p = _produto(g[7])
        if not p:
            continue
        reg = {"preco": round(float(g[10]), 3), "min": round(float(g[12]), 3), "max": round(float(g[13]), 3), "postos": int(g[8])}
        if aba == "BRASIL":
            out["brasil"][p] = reg
            continue
        uf, resto = _estado_e_resto(g[6])
        if not uf:
            continue
        if aba == "ESTADOS":
            out["estados"].setdefault(uf, {})[p] = reg["preco"]
        elif aba == "CAPITAIS":
            out["capitais"].setdefault(f"{CAPITAL.get(uf) or resto.title()}/{uf}", {})[p] = reg["preco"]
        elif aba == "MUNICIPIOS" and resto:
            out["municipios"].setdefault(f"{uf}/{slug(resto)}", {})[p] = reg["preco"]
    if not out["brasil"] or not out["fim"]:
        raise ValueError(f"{nome_arquivo or 'texto'}: não achei a aba BRASIL no texto do leitor")
    return out


def baixar(destino: Path = PASTA) -> int:
    """Baixa e resume as semanas que ainda não estão guardadas. Devolve quantas entraram."""
    destino.mkdir(parents=True, exist_ok=True)
    vistos_arq = destino / "vistos.json"
    vistos = json.loads(vistos_arq.read_text("utf-8")) if vistos_arq.exists() else {}
    if vistos.get("_versao") != VERSAO:
        vistos = {"_versao": VERSAO}
    s, t0, novos = _sessao(), time.time(), 0
    for nome, url in sorted(fontes().items(), key=lambda kv: kv[0], reverse=True):
        if nome in vistos:
            continue
        if time.time() - t0 > ORCAMENTO_S:
            print("Orçamento de tempo esgotado; o resto fica para a próxima execução.")
            break
        try:
            r = None
            if not _SO_LEITOR:
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        arq = Path(tmp) / nome
                        arq.write_bytes(_get(s, url, tentativas=2).content)
                        r = resumir(arq)
                except RuntimeError:
                    pass
            if r is None:
                r = resumir_texto(_leitor(url), nome)
                time.sleep(4)   # o leitor gratuito aceita poucas leituras por minuto
            (destino / f"{r['fim']}.json").write_text(json.dumps(r, ensure_ascii=False), "utf-8")
            vistos[nome] = r["fim"]
            novos += 1
            print(f"{nome}: semana de {r['inicio']} a {r['fim']}")
        except Exception as e:  # noqa: BLE001 - uma planilha ruim não derruba as outras
            print(f"AVISO: {nome}: {type(e).__name__}: {e}", file=sys.stderr)
        vistos_arq.write_text(json.dumps(vistos, ensure_ascii=False, indent=0), "utf-8")
    return novos


def processar(pasta: Path = PASTA, saida: Path | None = None) -> dict | None:
    """Junta as semanas guardadas em ordem (mais antiga primeiro)."""
    semanas = sorted((json.loads(f.read_text("utf-8")) for f in pasta.glob("????-??-??.json")), key=lambda x: x["fim"])
    semanas = [w for w in semanas if w.get("brasil")]
    if not semanas:
        return None
    for w in semanas[:-MUNICIPIOS_SEMANAS]:   # no arquivo do site, municípios só das últimas semanas (fica leve)
        w.pop("municipios", None)
    r = {"semanas": semanas, "ultima": semanas[-1]["fim"]}
    if saida:
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(json.dumps(r, ensure_ascii=False), "utf-8")
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-baixar", action="store_true")
    ap.add_argument("--listar", action="store_true")
    a = ap.parse_args()
    if a.listar:
        for nome, u in sorted(fontes().items()):
            print(nome, u)
        return
    if not a.sem_baixar:
        print(f"Semanas novas: {baixar()}")
    r = processar(PASTA, config.PROCESSADOS / "precos_semanais.json")
    if r is None:
        raise SystemExit("Sem semanas de preços guardadas")
    print(f"Preços semanais: {len(r['semanas'])} semanas, até {r['ultima']}")


if __name__ == "__main__":
    main()

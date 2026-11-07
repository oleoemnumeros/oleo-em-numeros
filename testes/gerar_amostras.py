"""Gera arquivos FICTÍCIOS nos dois formatos da ANP, para testar o robô sem internet.

Os nomes (Operadora A, Campo Exemplo 3...) e os números são inventados.
Formato por poço: ZIP mensal (jan/2022 a dez/2023), ISO-8859-1, ';', decimal ','.
Formato mensal: CSV anual (mar) e trimestral (terra), 2023 a set/2026, UTF-8 com BOM,
',' como separador, decimal ',', cabeçalhos entre colchetes, sem coluna de operador.

Uso: python -m testes.gerar_amostras [pasta]   (padrão: testes/amostras)
"""
import calendar
import csv
import io
import math
import random
import sys
import zipfile
from pathlib import Path

M3_PARA_BBL = 6.289811

# campo: (bacia, estado sigla, estado nome, ambiente, operador, poços, bbl/d por poço, bsw inicial, gás por bbl)
CAMPOS = {
    "CAMPO EXEMPLO 1":  ("SANTOS", "RJ", "Rio de Janeiro", "mar", "OPERADORA A", 20, 4500, 0.10, 0.20),
    "CAMPO EXEMPLO 11": ("SANTOS", "SP", "São Paulo", "mar", "OPERADORA A", 30, 5000, 0.08, 0.25),
    "CAMPO EXEMPLO 14": ("SANTOS", "RJ", "Rio de Janeiro", "mar", "OPERADORA A", 25, 5300, 0.15, 0.22),
    "CAMPO EXEMPLO 12": ("CAMPOS", "RJ", "Rio de Janeiro", "mar", "OPERADORA C", 25, 1800, 0.55, 0.10),
    "CAMPO EXEMPLO 15": ("CAMPOS", "RJ", "Rio de Janeiro", "mar", "OPERADORA B", 15, 2500, 0.45, 0.12),
    "CAMPO EXEMPLO 5":  ("SERGIPE-ALAGOAS", "SE", "Sergipe", "mar", "OPERADORA K", 3, 1100, 0.30, 0.15),
    "CAMPO EXEMPLO 2":  ("POTIGUAR", "RN", "Rio Grande do Norte", "terra", "OPERADORA K", 120, 20, 0.78, 0.03),
    "CAMPO EXEMPLO 4":  ("POTIGUAR", "RN", "Rio Grande do Norte", "terra", "OPERADORA K", 80, 18, 0.80, 0.03),
    "CAMPO EXEMPLO 3":  ("RECÔNCAVO", "BA", "Bahia", "terra", "OPERADORA K", 60, 15, 0.82, 0.05),
    "CAMPO EXEMPLO 6":  ("RECÔNCAVO", "BA", "Bahia", "terra", "OPERADORA K", 50, 13, 0.85, 0.05),
    "CAMPO EXEMPLO 8":  ("RECÔNCAVO", "BA", "Bahia", "terra", "OPERADORA K", 30, 12, 0.88, 0.05),
    "CAMPO EXEMPLO 9":  ("RECÔNCAVO", "BA", "Bahia", "terra", "OPERADORA L", 40, 11, 0.83, 0.04),
    "CAMPO EXEMPLO 10": ("RECÔNCAVO", "BA", "Bahia", "terra", "OPERADORA L", 25, 9, 0.79, 0.04),
    "CAMPO EXEMPLO 20": ("SERGIPE-ALAGOAS", "SE", "Sergipe", "terra", "OPERADORA C", 70, 10, 0.86, 0.04),
    "CAMPO EXEMPLO 21": ("ESPÍRITO SANTO", "ES", "Espírito Santo", "terra", "OPERADORA C", 40, 8, 0.75, 0.04),
}


def _meses(ini, fim):
    a, m = ini
    while (a, m) <= fim:
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def _pocos():
    rnd = random.Random(42)
    lista = []
    n = 1
    for campo, (bacia, uf, est, amb, op, qtd, taxa, bsw0, rgo) in CAMPOS.items():
        prefixo = "7-EXP" if amb == "terra" else "8-EXM"
        for i in range(qtd):
            inicio = rnd.choice([(2021, 1)] * 6 + [(2023, rnd.randint(1, 12)), (2025, rnd.randint(1, 12))])
            lista.append({
                "poco": f"{prefixo}-{n:04d}-{uf}", "campo": campo, "bacia": bacia, "uf": uf, "estado": est,
                "ambiente": amb, "operador": op, "taxa": taxa * rnd.uniform(0.4, 1.8), "bsw0": bsw0,
                "rgo": rgo, "inicio": inicio, "declinio": rnd.uniform(0.04, 0.14),
                "fase": rnd.uniform(0, 6.28), "parada": rnd.random() < 0.05,
            })
            n += 1
    return lista


def _producao(p, ano, mes):
    """Taxas diárias de um poço num mês: (óleo, condensado, gás mil m³/d, água, horas)."""
    if (ano, mes) < p["inicio"]:
        return None
    idade = (ano - p["inicio"][0]) * 12 + mes - p["inicio"][1]
    fator = math.exp(-p["declinio"] * idade / 12) * (1 + 0.06 * math.sin(idade / 2.3 + p["fase"]))
    oleo = p["taxa"] * fator
    if p["parada"] and mes in (3, 4) and ano == 2026:
        return (0.0, 0.0, 0.0, 0.0, 0)
    if (ano, mes) >= (2026, 9) and p["fase"] > 4.8:
        oleo *= 1.6  # intervenções no último mês: dá altas no ranking
    bsw = min(0.95, p["bsw0"] + 0.0012 * idade)
    agua = oleo * bsw / (1 - bsw)
    cond = oleo * 0.02 if p["ambiente"] == "mar" else 0.0
    gas = oleo * p["rgo"]
    dias = calendar.monthrange(ano, mes)[1]
    horas = dias * 24 if p["ambiente"] == "mar" else int(dias * 24 * random.Random(f"{p['poco']}{ano}{mes}").uniform(0.82, 1))
    return oleo, cond, gas, agua, horas


def _br(x, casas=4):
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _formato_por_poco(pocos, ano, mes) -> bytes:
    cab = ["Estado", "Bacia", "Nome Poço ANP", "Nome Poço Operador", "Campo", "Operador", "Número do Contrato",
           "Período", "Óleo (bbl/dia)", "Condensado (bbl/dia)", "Petróleo (bbl/dia)",
           "Gás Natural (Mm³/dia) Assoc", "Gás Natural (Mm³/dia) N Assoc", "Gás Natural (Mm³/dia) Total",
           "Volume Gás Royalties (m³/mês)", "Água (bbl/dia)", "Instalação Destino", "Tipo Instalação",
           "Tempo de Produção (hs por mês)", "Período da Carga", "Corrente", "Grau API"]
    arquivos = {}
    for chave in ("Terra", "Mar", "PreSal"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(cab)
        for p in pocos:
            if chave == "Terra" and p["ambiente"] != "terra":
                continue
            if chave == "Mar" and p["ambiente"] != "mar":
                continue
            if chave == "PreSal" and p["bacia"] != "SANTOS":
                continue
            r = _producao(p, ano, mes)
            if r is None:
                continue
            oleo, cond, gas, agua, horas = r
            w.writerow([p["estado"], p["bacia"].title(), p["poco"], p["poco"].replace("-", ""), p["campo"],
                        p["operador"].title(), "48000000000000", f"{ano}/{mes:02d}", _br(oleo), _br(cond),
                        _br(oleo + cond), _br(gas), "0", _br(gas), _br(gas * 30 * 1000 * 0.9, 2), _br(agua),
                        "INSTALACAO EXEMPLO", "UEP" if p["ambiente"] == "mar" else "ESTACAO", horas,
                        f"{ano}/{mes:02d}", "Corrente Exemplo", "28,5"])
        arquivos[chave] = buf.getvalue().encode("latin-1")
    return arquivos


def _formato_mensal(pocos, meses, ambiente) -> bytes:
    cab = ["[Ano]", "[Mês/Ano]", "[Estado]", "[Bacia]", "[Campo]", "[Poço]", "[Ambiente]", "[Instalação]",
           "[Produção de Óleo (m³)]", "[Produção de Condensado (m³)]", "[Produção de Gás Associado (Mm³)]",
           "[Produção de Gás Não Associado (Mm³)]", "[Produção de Água (m³)]", "[Injeção de Gás (Mm³)]",
           "[Injeção de Água para Recuperação Secundária (m³)]", "[Injeção de Água para Descarte (m³)]"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(cab)
    for ano, mes in meses:
        dias = calendar.monthrange(ano, mes)[1]
        for p in pocos:
            if p["ambiente"] != ambiente:
                continue
            r = _producao(p, ano, mes)
            if r is None:
                continue
            oleo, cond, gas, agua, _ = r
            vol = lambda taxa: taxa * dias / M3_PARA_BBL  # noqa: E731
            w.writerow([ano, f"{mes:02d}/{ano}", p["uf"], p["bacia"].title(), p["campo"], p["poco"],
                        "Terra" if ambiente == "terra" else "Mar", "INSTALACAO EXEMPLO",
                        _br(vol(oleo), 3), _br(vol(cond), 3), _br(gas * dias, 3), "0", _br(vol(agua), 3),
                        "", "", ""])
        # um poço injetor por mês, sem produção (o robô deve ignorar)
        w.writerow([ano, f"{mes:02d}/{ano}", "BA", "Recôncavo", "CAMPO EXEMPLO 3", "7-INJ-0001-BA",
                    "Terra", "", "0", "0", "0", "0", "0", "", _br(12000.5, 3), ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def gerar(pasta: Path):
    pasta.mkdir(parents=True, exist_ok=True)
    pocos = _pocos()
    for ano, mes in _meses((2022, 1), (2023, 12)):
        nome_zip = f"{ano}_producao-{ano}-{mes:02d}.zip"
        with zipfile.ZipFile(pasta / nome_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for chave, conteudo in _formato_por_poco(pocos, ano, mes).items():
                z.writestr(f"{ano}_{mes:02d}_producao_{chave}.csv", conteudo)
    for ano in (2023, 2024, 2025, 2026):
        fim = (ano, 9) if ano == 2026 else (ano, 12)
        (pasta / f"pm_producao-mar-{ano}.csv").write_bytes(_formato_mensal(pocos, list(_meses((ano, 1), fim)), "mar"))
        for tri in range(1, 5):
            m0 = (tri - 1) * 3 + 1
            meses = [(ano, m) for m in range(m0, m0 + 3) if (ano, m) <= fim]
            if meses and ano >= 2024:
                (pasta / f"pt_producao_por_poco_terra_{ano}_{tri}_trim.csv").write_bytes(
                    _formato_mensal(pocos, meses, "terra"))
    return pasta


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "amostras"
    print(f"Amostras fictícias em {gerar(destino)}")

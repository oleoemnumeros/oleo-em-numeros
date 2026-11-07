"""Funções pequenas usadas pelo robô e pelo gerador do site."""
import calendar
import math
import re
import unicodedata

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MENOS = "−"  # sinal de menos tipográfico


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar_cabecalho(texto: str) -> str:
    """'[Produção de Óleo (m³)]' -> 'producao de oleo (m3)'."""
    t = str(texto).strip().strip("﻿").strip("[]").strip()
    t = t.replace("³", "3").replace("²", "2")
    t = sem_acento(t).lower()
    return re.sub(r"\s+", " ", t)


def slug(texto: str) -> str:
    t = sem_acento(str(texto)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t or "sem-nome"


_PEQUENAS = {"de", "da", "do", "das", "dos", "e", "em"}
_SIGLAS = {"AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
           "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
           "ANP", "S.A.", "SA", "LTDA", "LTDA.", "II", "III", "IV"}


def nome_bonito(texto) -> str:
    """'MARLIM LESTE' -> 'Marlim Leste'. Mantém siglas (UF, S.A.) e códigos com números."""
    if texto is None or (isinstance(texto, float) and math.isnan(texto)):
        return ""
    s = str(texto).strip()
    if not s.isupper():
        return s  # já vem em caixa mista: respeita como a ANP escreveu
    saida = []
    for i, p in enumerate(s.split()):
        baixa = p.lower()
        if p in _SIGLAS or any(ch.isdigit() for ch in p):
            saida.append(p)
        elif i > 0 and baixa in _PEQUENAS:
            saida.append(baixa)
        else:
            saida.append(baixa[:1].upper() + baixa[1:])
    return " ".join(saida)


_MILHAR = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


def numero_br(valor) -> float:
    """Converte '3.127,441', '74,4653', '0', '' e números já convertidos."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return 0.0 if (isinstance(valor, float) and math.isnan(valor)) else float(valor)
    s = str(valor).strip().replace(" ", "").replace(" ", "")
    if s in ("", "-", "nan", "NaN", "None"):
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif _MILHAR.match(s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def periodo(valor) -> str | None:
    """Aceita '2021/05', '05/2021', '2021-05', 'mai/2021', '01/05/2021'. Devolve 'AAAA-MM'."""
    if valor is None:
        return None
    s = str(valor).strip().lower()
    if not s or s == "nan":
        return None
    m = re.match(r"^(\d{4})[/-](\d{1,2})$", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})[/-](\d{4})$", s)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}"
    m = re.match(r"^([a-zç]{3})[a-zç]*[/-](\d{2,4})$", sem_acento(s))
    if m:
        curtos = [sem_acento(x) for x in MESES_CURTOS]
        if m.group(1) in curtos:
            ano = int(m.group(2))
            ano = ano + 2000 if ano < 100 else ano
            return f"{ano:04d}-{curtos.index(m.group(1)) + 1:02d}"
    return None


def dias_no_mes(per: str) -> int:
    ano, mes = map(int, per.split("-"))
    return calendar.monthrange(ano, mes)[1]


def mes_anterior(per: str, n: int = 1) -> str:
    ano, mes = map(int, per.split("-"))
    total = ano * 12 + (mes - 1) - n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def mes_extenso(per: str) -> str:
    ano, mes = map(int, per.split("-"))
    return f"{MESES[mes - 1]} de {ano}"


def mes_curto(per: str) -> str:
    ano, mes = map(int, per.split("-"))
    return f"{MESES_CURTOS[mes - 1]}/{str(ano)[2:]}"


def fmt(valor, casas: int = 0) -> str:
    """Formata no padrão brasileiro: 3612.4 -> '3.612'; casas=1 -> '3.612,4'."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    texto = f"{abs(valor):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return (MENOS + texto) if valor < 0 and float(texto.replace(".", "").replace(",", ".")) != 0 else texto


def fmt_inteligente(valor) -> str:
    """Escolhe casas decimais pelo tamanho: 2651 -> '2.651'; 31.4 -> '31,4'; 0.86 -> '0,86'."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    a = abs(valor)
    casas = 0 if a >= 100 else 1 if a >= 10 else 2 if a > 0 else 0
    return fmt(valor, casas)


def fmt_var(frac, casas: int = 1) -> str:
    """0.041 -> '+4,1%'; -0.031 -> '−3,1%'."""
    if frac is None or (isinstance(frac, float) and (math.isnan(frac) or math.isinf(frac))):
        return "—"
    pct = frac * 100
    texto = fmt(abs(pct), casas)
    if texto in ("0", "0,0"):
        return "0,0%"
    return ("+" if pct > 0 else MENOS) + texto + "%"


def fmt_pp(delta_pp, casas: int = 1) -> str:
    """Variação em pontos percentuais: 1.04 -> '+1,0 p.p.'."""
    if delta_pp is None or (isinstance(delta_pp, float) and math.isnan(delta_pp)):
        return "—"
    texto = fmt(abs(delta_pp), casas)
    if texto in ("0", "0,0"):
        return "0,0 p.p."
    return ("+" if delta_pp > 0 else MENOS) + texto + " p.p."


def variacao(atual, anterior):
    if anterior is None or atual is None:
        return None
    try:
        if anterior == 0 or math.isnan(anterior) or math.isnan(atual):
            return None
    except TypeError:
        return None
    return atual / anterior - 1

"""Lê os arquivos brutos da ANP e devolve uma base única, poço a poço, mês a mês.

A ANP publica a produção por poço em dois formatos:

* Formato "por poço" (ZIP mensal, até 2023): taxas diárias em bbl/dia e
  mil m³/dia, com coluna de operador. Vem em três arquivos por mês: Terra,
  Mar e PreSal. O PreSal é um subconjunto do Mar e é ignorado para não contar
  a mesma produção duas vezes.
* Formato "fase de desenvolvimento e produção" (CSV anual ou trimestral,
  2019 em diante): volumes do mês em m³ e mil m³, cabeçalhos entre colchetes.
  Pode não trazer o operador; nesse caso ele é preenchido pelo campo.

Saída: uma linha por poço e mês, sempre em taxas diárias:
petróleo, óleo e condensado em bbl/d; gás em mil m³/d; água em bbl/d.
"""
import csv
import io
import itertools
import re
import zipfile
from pathlib import Path

import pandas as pd

from . import config
from .agregar import estado_info
from .util import dias_no_mes, normalizar_cabecalho, numero_br, periodo, sem_acento

COLUNAS = ["periodo", "estado", "bacia", "campo", "poco", "operador", "ambiente",
           "oleo_bbl_d", "condensado_bbl_d", "petroleo_bbl_d", "gas_mil_m3_d",
           "agua_bbl_d", "horas", "formato", "arquivo"]


class FormatoDesconhecido(Exception):
    pass


def _texto(conteudo: bytes) -> str:
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return conteudo.decode(cod)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _separador(texto: str) -> str:
    primeira = texto.splitlines()[0] if texto else ""
    return ";" if primeira.count(";") >= primeira.count(",") else ","


def _achar(colunas: list[str], *palavras: str, opcional: bool = False) -> str | None:
    """Acha a coluna pelo nome normalizado.

    Prioridade: nome idêntico; depois nome que começa pela primeira palavra e
    contém as demais; por fim nome que contém todas. Assim 'oleo' não pega
    'petroleo' e 'periodo' não pega 'periodo da carga'.
    """
    alvo = " ".join(palavras)
    for c in colunas:
        if c == alvo:
            return c
    for c in colunas:
        if c.startswith(palavras[0]) and all(p in c for p in palavras[1:]):
            return c
    for c in colunas:
        if all(p in c for p in palavras):
            return c
    if opcional:
        return None
    raise FormatoDesconhecido(f"Coluna com {palavras} não encontrada. Colunas: {colunas}")


def _num(serie: pd.Series) -> pd.Series:
    return serie.map(numero_br).astype(float)


def _ler_formato_por_poco(df: pd.DataFrame, nome: str) -> pd.DataFrame:
    c = list(df.columns)
    ambiente = "terra" if "terra" in nome.lower() else "mar"
    oleo = _num(df[_achar(c, "oleo", "bbl")])
    cond_col = _achar(c, "condensado", "bbl", opcional=True)
    cond = _num(df[cond_col]) if cond_col else 0.0
    pet_col = _achar(c, "petroleo", "bbl", opcional=True)
    petroleo = _num(df[pet_col]) if pet_col else oleo + cond
    gas_total = _achar(c, "gas natural", "total", opcional=True)
    if gas_total:
        gas = _num(df[gas_total])
    else:
        gas = _num(df[_achar(c, "gas", "assoc")]) + _num(df[_achar(c, "gas", "n assoc")])
    horas_col = _achar(c, "tempo de producao", opcional=True)
    return pd.DataFrame({
        "periodo": df[_achar(c, "periodo")].map(periodo),
        "estado": df[_achar(c, "estado")].str.strip(),
        "bacia": df[_achar(c, "bacia")].str.strip(),
        "campo": df[_achar(c, "campo")].str.strip(),
        "poco": df[_achar(c, "nome poco anp")].str.strip(),
        "operador": df[_achar(c, "operador")].str.strip() if _achar(c, "operador", opcional=True) else None,
        "ambiente": ambiente,
        "oleo_bbl_d": oleo,
        "condensado_bbl_d": cond,
        "petroleo_bbl_d": petroleo,
        "gas_mil_m3_d": gas,
        "agua_bbl_d": _num(df[_achar(c, "agua", "bbl")]),
        "horas": _num(df[horas_col]) if horas_col else float("nan"),
        "formato": "por_poco",
    })


def _ler_formato_mensal(df: pd.DataFrame, nome: str) -> pd.DataFrame:
    c = list(df.columns)
    per_col = _achar(c, "mes/ano", opcional=True) or _achar(c, "periodo", opcional=True) or _achar(c, "mes")
    per = df[per_col].map(periodo)
    if per.isna().all() and _achar(c, "ano", opcional=True):
        raise FormatoDesconhecido(f"Não consegui ler o período em {nome}: exemplo {df[per_col].iloc[0]!r}")
    dias = per.map(lambda p: dias_no_mes(p) if isinstance(p, str) else float("nan"))
    m3 = config.M3_PARA_BBL

    def vol(*palavras):
        col = _achar(c, *palavras, opcional=True)
        return _num(df[col]) if col else pd.Series(0.0, index=df.index)

    oleo = vol("producao de oleo") * m3 / dias
    cond = vol("producao de condensado") * m3 / dias
    gas = (vol("producao de gas associado") + vol("producao de gas nao associado")) / dias
    agua = vol("producao de agua") * m3 / dias
    amb_col = _achar(c, "ambiente", opcional=True)
    if amb_col:
        ambiente = df[amb_col].map(lambda v: "terra" if "terra" in str(v).lower() else "mar")
    else:
        ambiente = "terra" if "terra" in nome.lower() else "mar"
    op_col = _achar(c, "operador", opcional=True) or _achar(c, "concessionario", opcional=True)
    return pd.DataFrame({
        "periodo": per,
        "estado": df[_achar(c, "estado")].str.strip(),
        "bacia": df[_achar(c, "bacia")].str.strip(),
        "campo": df[_achar(c, "campo")].str.strip(),
        "poco": df[_achar(c, "poco")].str.strip(),
        "operador": df[op_col].str.strip() if op_col else None,
        "ambiente": ambiente,
        "oleo_bbl_d": oleo,
        "condensado_bbl_d": cond,
        "petroleo_bbl_d": oleo + cond,
        "gas_mil_m3_d": gas,
        "agua_bbl_d": agua,
        "horas": float("nan"),
        "formato": "mensal",
    })


def _limpar(texto: str) -> str:
    """Corrige dois vícios dos arquivos antigos da ANP (2018 a 2021):
    1. linhas de título antes do cabeçalho ('ANP - Agência Nacional do Petróleo...');
    2. linhas inteiras embrulhadas em aspas, com as aspas internas dobradas:
       "2018,01/2018,...,""122,119"",0,..."  ->  2018,01/2018,...,"122,119",0,..."""
    linhas = texto.splitlines()
    for i, linha in enumerate(linhas[:30]):
        n = normalizar_cabecalho(linha)
        if "campo" in n and ("periodo" in n or "mes/ano" in n or "estado" in n):
            linhas = linhas[i:]
            break
    saida = []
    for linha in linhas:
        s = linha.rstrip()
        if len(s) > 2 and s[0] == '"' and s[-1] == '"' and '""' in s and s.count('"') % 2 == 0:
            interno = s[1:-1]
            if '"' not in interno.replace('""', ""):
                s = interno.replace('""', '"')
        saida.append(s)
    return "\n".join(saida)


def _ler_tabela(texto: str) -> pd.DataFrame:
    """Lê o CSV. Trata o cabeçalho em duas linhas que a ANP usa no formato por poço:
    'Nome Poço' sobre 'ANP | Operador' e 'Gás Natural (Mm³/dia)' sobre 'Assoc | N Assoc | Total'
    (células mescladas na planilha original)."""
    texto = _limpar(texto)
    sep = _separador(texto)
    linhas = list(itertools.islice(csv.reader(io.StringIO(texto), delimiter=sep), 2))
    if len(linhas) == 2:
        l1, l2 = linhas
        tem_vazio = any(not v.strip() for v in l1)
        sub = [v for v in l2 if v.strip()]
        if tem_vazio and sub and not any(re.search(r"\d", v) for v in l2):
            nomes, atual, vistos = [], "", {}
            for i in range(max(len(l1), len(l2))):
                topo = l1[i].strip() if i < len(l1) else ""
                atual = topo or atual
                baixo = l2[i].strip() if i < len(l2) else ""
                nome = f"{atual} {baixo}".strip() or f"coluna {i}"
                if nome in vistos:
                    vistos[nome] += 1
                    nome = f"{nome} {vistos[nome]}"
                else:
                    vistos[nome] = 0
                nomes.append(nome)
            return pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False, header=None,
                               skiprows=2, names=nomes, index_col=False)
    return pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False)


def ler_csv(conteudo: bytes, nome: str) -> pd.DataFrame:
    if "presal" in nome.lower().replace("-", "").replace("_", "").replace("é", "e"):
        return pd.DataFrame(columns=COLUNAS)  # subconjunto do Mar: evita dupla contagem
    texto = _texto(conteudo)
    df = _ler_tabela(texto)
    df.columns = [normalizar_cabecalho(x) for x in df.columns]
    colunas = list(df.columns)
    if _achar(colunas, "bbl/dia", opcional=True):
        out = _ler_formato_por_poco(df, nome)
    elif _achar(colunas, "producao de oleo", opcional=True):
        out = _ler_formato_mensal(df, nome)
    else:
        raise FormatoDesconhecido(f"{nome}: formato não reconhecido. Colunas: {colunas[:12]}")
    out["arquivo"] = nome
    out = out[out["periodo"].notna()]
    # linhas só de injeção não têm produção: ficam de fora
    prod = out[["petroleo_bbl_d", "gas_mil_m3_d", "agua_bbl_d"]].sum(axis=1)
    return out[prod > 0][COLUNAS]


def _membros(caminho: Path):
    if caminho.suffix.lower() == ".zip":
        with zipfile.ZipFile(caminho) as z:
            for membro in z.namelist():
                if membro.lower().endswith(".csv"):
                    yield Path(membro).name, z.read(membro)
    elif caminho.suffix.lower() == ".csv":
        yield caminho.name, caminho.read_bytes()


def ler_arquivo(caminho: Path) -> tuple[pd.DataFrame, list[dict]]:
    """Lê um arquivo bruto. Um membro com problema não derruba os outros:
    o erro fica registrado (com o começo do arquivo) para a conferência."""
    partes, registros = [], []
    try:
        membros = list(_membros(caminho))
    except zipfile.BadZipFile as e:
        return pd.DataFrame(columns=COLUNAS), [{"arquivo": caminho.name, "membro": "", "erro": f"ZIP inválido: {e}"}]
    for nome, conteudo in membros:
        reg = {"arquivo": caminho.name, "membro": nome}
        try:
            df = ler_csv(conteudo, nome)
            if df.empty:
                reg["formato"] = "ignorado (pré-sal)" if "sal" in nome.lower() else "sem produção"
            else:
                reg.update(formato=df["formato"].iloc[0], linhas=len(df),
                           periodos=f"{df['periodo'].min()} a {df['periodo'].max()}")
                partes.append(df)
        except Exception as e:  # noqa: BLE001 - registra qualquer falha de leitura
            reg["erro"] = f"{type(e).__name__}: {e}"[:600]
            reg["inicio"] = _texto(conteudo[:3000]).splitlines()[:3]
        registros.append(reg)
    df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUNAS)
    return df, registros


def _operador_por_campo(base: pd.DataFrame) -> dict:
    """Último operador conhecido de cada campo, a partir das linhas que trazem operador."""
    com_op = base[base["operador"].notna() & (base["operador"] != "")]
    if com_op.empty:
        return {}
    ultimo = com_op.sort_values("periodo").groupby("campo")["operador"].last()
    return ultimo.to_dict()


def consolidar(pasta: Path = config.BRUTOS, manual: Path = config.MANUAIS / "operador_por_campo.csv",
               ano_inicial: int = config.ANO_INICIAL) -> tuple[pd.DataFrame, list[dict]]:
    """Devolve a base poço a poço e o registro de leitura de cada arquivo."""
    arquivos = sorted(p for p in pasta.iterdir() if p.suffix.lower() in (".zip", ".csv"))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum arquivo bruto em {pasta}")
    partes, leitura = [], []
    for a in arquivos:
        df, regs = ler_arquivo(a)
        partes.append(df)
        leitura.extend(regs)
    base = pd.concat([p for p in partes if not p.empty] or [pd.DataFrame(columns=COLUNAS)], ignore_index=True)
    base = base[base["periodo"] >= f"{ano_inicial}-01"]
    if base.empty:
        return base, leitura

    # Onde os dois formatos cobrem o mesmo mês e ambiente, fica o formato por poço,
    # que é o publicado pela ANP em taxa diária e traz o operador.
    cobertura = base.groupby(["periodo", "ambiente"])["formato"].agg(set)
    duplos = {k for k, v in cobertura.items() if len(v) > 1}
    if duplos:
        chave = list(zip(base["periodo"], base["ambiente"]))
        manter = [not (k in duplos and f == "mensal") for k, f in zip(chave, base["formato"])]
        base = base[manter]

    # Mesma coisa escrita de jeitos diferentes nos dois formatos ('RECÔNCAVO' e
    # 'Recôncavo', 'Bahia' e 'BA'): unifica pela grafia mais frequente.
    for col in ("bacia", "campo", "operador"):
        valido = base[col].notna() & (base[col] != "")
        chave = base.loc[valido, col].map(lambda s: sem_acento(str(s)).casefold().strip())
        preferida = base.loc[valido, col].groupby(chave).agg(lambda s: s.value_counts().idxmax())
        base.loc[valido, col] = chave.map(preferida)
    base["estado"] = base["estado"].map(lambda v: estado_info(v)["uf"])

    mapa = _operador_por_campo(base)
    if manual.exists():
        m = pd.read_csv(manual, dtype=str).dropna()
        conhecidos = sorted(set(base["operador"].dropna()) - {""})
        chave = lambda s: sem_acento(str(s)).casefold().strip()  # noqa: E731
        por_campo = {chave(c): c for c in base["campo"].dropna().unique()}
        for campo, op in zip(m["campo"].str.strip(), m["operador"].str.strip()):
            # 'Equinor' na planilha manual vira 'Equinor Brasil Energia' se for o único nome da ANP que começa assim
            iguais = [k for k in conhecidos if chave(k).startswith(chave(op))]
            mapa[por_campo.get(chave(campo), campo)] = iguais[0] if len(iguais) == 1 else op
    faltando = base["operador"].isna() | (base["operador"] == "")
    base.loc[faltando, "operador"] = base.loc[faltando, "campo"].map(mapa)
    base["operador"] = base["operador"].fillna("Não identificado")

    # um poço aparece uma vez por mês; se a ANP repetir a linha, soma
    num = ["oleo_bbl_d", "condensado_bbl_d", "petroleo_bbl_d", "gas_mil_m3_d", "agua_bbl_d", "horas"]
    chaves = ["periodo", "estado", "bacia", "campo", "poco", "operador", "ambiente"]
    base = base.groupby(chaves, as_index=False, dropna=False)[num].sum(min_count=1)
    return base.sort_values(["periodo", "campo", "poco"]).reset_index(drop=True), leitura

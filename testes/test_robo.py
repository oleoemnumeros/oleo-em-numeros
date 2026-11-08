"""Testes do robô e do gerador com dados fictícios nos dois formatos da ANP."""
import io
import zipfile

import pytest

from gerador.gerar import gerar
from robo.agregar import agregar
from robo.conferir import conferir
from robo.ler import consolidar, ler_csv
from robo.util import fmt, fmt_var, numero_br, periodo
from testes.gerar_amostras import gerar as gerar_amostras


@pytest.fixture(scope="module")
def pasta(tmp_path_factory):
    return gerar_amostras(tmp_path_factory.mktemp("brutos"))


@pytest.fixture(scope="module")
def base(pasta):
    base, leitura = consolidar(pasta, manual=pasta / "nao_existe.csv")
    assert not [r for r in leitura if r.get("erro")]
    return base


def test_numeros_no_padrao_brasileiro():
    assert numero_br("3.127,441") == pytest.approx(3127.441)
    assert numero_br("74,4653") == pytest.approx(74.4653)
    assert numero_br("1.234") == 1234
    assert numero_br("") == 0
    assert numero_br("12.5") == 12.5


@pytest.mark.parametrize("entrada,saida", [
    ("2021/05", "2021-05"), ("05/2021", "2021-05"), ("2021-5", "2021-05"),
    ("mai/2021", "2021-05"), ("01/05/2021", "2021-05"), ("", None),
])
def test_periodos(entrada, saida):
    assert periodo(entrada) == saida


def test_formatacao():
    assert fmt(3612.4) == "3.612"
    assert fmt(31.44, 1) == "31,4"
    assert fmt_var(0.041) == "+4,1%"
    assert fmt_var(-0.031) == "−3,1%"


def test_presal_nao_conta_duas_vezes():
    assert ler_csv(b"qualquer", "2023_12_producao_PreSal.csv").empty


def test_conversao_m3_mensal_para_bbl_dia():
    csv = ("﻿[Ano],[Mês/Ano],[Estado],[Bacia],[Campo],[Poço],[Ambiente],[Produção de Óleo (m³)],"
           "[Produção de Água (m³)]\r\n2024,01/2024,BA,Recôncavo,CAMPO X,7-X-1-BA,Terra,\"493,000\",\"0\"\r\n")
    df = ler_csv(csv.encode("utf-8"), "producao_por_poco_terra_2024_1_trim.csv")
    # 493 m³ em janeiro (31 dias) = 493 × 6,289811 / 31 ≈ 100 bbl/d
    assert df.iloc[0]["petroleo_bbl_d"] == pytest.approx(100.03, abs=0.01)
    assert df.iloc[0]["ambiente"] == "terra"


def test_base_continua_na_troca_de_formato(base):
    """Dez/2023 (formato por poço) e jan/2024 (formato mensal) devem ficar próximos."""
    total = base.groupby("periodo")["petroleo_bbl_d"].sum()
    assert abs(total["2024-01"] / total["2023-12"] - 1) < 0.03


def test_sobreposicao_prefere_formato_por_poco(base):
    linhas_2023 = base[base["periodo"].str.startswith("2023")]
    assert linhas_2023["horas"].notna().any()  # só o formato por poço traz horas


def test_operador_preenchido_pelo_campo(base):
    assert (base["operador"] != "Não identificado").all()
    assert set(base[base["campo"] == "CAMPO EXEMPLO 3"]["operador"]) == {"Operadora K"}


def test_estado_unificado_em_sigla(base):
    assert set(base["estado"]) <= {"RJ", "SP", "SE", "RN", "BA", "ES"}


def test_injetores_ficam_de_fora(base):
    assert not base["poco"].str.startswith("7-INJ").any()


def test_agregados_e_conferencia(base):
    ag = agregar(base)
    assert ag["ultimo"] == "2026-09"
    soma_op = sum(o["petroleo"] for o in ag["operadores"])
    assert soma_op == pytest.approx(ag["brasil"]["petroleo"], rel=1e-9)
    soma_campos = sum(c["petroleo"] for c in ag["campos"]) / 1000
    assert soma_campos == pytest.approx(ag["brasil"]["petroleo"], rel=1e-9)
    ok, criticos, _ = conferir(base, ag)
    assert ok, criticos


def test_boe_soma_petroleo_e_gas_pela_regra_da_anp(base):
    ag = agregar(base)
    br = ag["brasil"]
    # mil boe/d = mil bbl/d de petróleo + (milhões m³/d de gás × 6,289811)
    assert br["boe"] == pytest.approx(br["petroleo"] + br["gas"] * 6.289811, rel=1e-9)
    assert sum(o["boe"] for o in ag["operadores"]) == pytest.approx(br["boe"], rel=1e-9)
    assert sum(c["boe"] for c in ag["campos"]) / 1000 == pytest.approx(br["boe"], rel=1e-9)
    assert sum(o["share"] for o in ag["operadores"]) == pytest.approx(100)
    # listas e rankings ordenados pela produção total, não só pelo petróleo
    assert [c["boe"] for c in ag["campos"]] == sorted((c["boe"] for c in ag["campos"]), reverse=True)
    assert [o["boe"] for o in ag["operadores"]] == sorted((o["boe"] for o in ag["operadores"]), reverse=True)
    for c in ag["rankings"]["todos"]["altas"]:
        assert c["delta_mes"] == pytest.approx(c["boe"] - c["anterior_mes"])
    assert br["serie"]["boe"][-1] == pytest.approx(br["boe"], rel=1e-3)


def test_conferencia_trava_queda_brusca(base):
    alterada = base.copy()
    ultimo = alterada["periodo"].max()
    alterada.loc[alterada["periodo"] == ultimo, "petroleo_bbl_d"] *= 0.5
    ok, criticos, _ = conferir(alterada, agregar(alterada))
    assert not ok and any("Petróleo do Brasil" in c for c in criticos)


def test_gera_site(base, tmp_path):
    import json
    from robo.executar import _leve
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(_leve(agregar(base)), ensure_ascii=False, default=str), "utf-8")
    paginas = gerar(proc, tmp_path / "site", teste=True)
    assert "campos/campo-exemplo-3/" in paginas
    html = (tmp_path / "site" / "campos" / "campo-exemplo-3" / "index.html").read_text("utf-8")
    assert "Campo Exemplo 3" in html and "<svg" in html
    assert (tmp_path / "site" / "sitemap.xml").exists()


def test_cabecalho_em_duas_linhas():
    """Formato real da ANP: 'Nome Poço' e 'Gás Natural' são células mescladas sobre subcolunas."""
    linhas = [
        "Estado;Bacia;Nome Poço;;Campo;Operador;Número do Contrato;Período;Óleo (bbl/dia);Condensado (bbl/dia);"
        "Petróleo (bbl/dia);Gás Natural (Mm³/dia);;;Volume Gás Royalties (Mm³/dia);Água (bbl/dia);Instalação Destino",
        ";;ANP;Operador;;;;;;;;Assoc;N Assoc;Total;;;",
        "Bahia;Recôncavo;7-XX-1-BA;XX1;CAMPO X;Operadora Y;480000;2023/01;10,5;0;10,5;1,2;0;1,2;0;40,1;ESTACAO",
    ]
    df = ler_csv("\r\n".join(linhas).encode("latin-1"), "2023_01_producao_Terra.csv")
    assert len(df) == 1
    r = df.iloc[0]
    assert r["poco"] == "7-XX-1-BA" and r["periodo"] == "2023-01"
    assert r["petroleo_bbl_d"] == 10.5 and r["gas_mil_m3_d"] == 1.2 and r["agua_bbl_d"] == 40.1


def test_mes_sem_dado_vira_lacuna(base):
    """Se a ANP não publicar um mês de terra, o gráfico mostra lacuna, não zero."""
    sem_abril = base[~((base["periodo"] == "2026-04") & (base["ambiente"] == "terra"))]
    ag = agregar(sem_abril)
    c = next(x for x in ag["campos"] if x["slug"] == "campo-exemplo-3")
    i = c["serie"]["periodo"].index("2026-04")
    assert c["serie"]["petroleo"][i] is None and c["serie"]["petroleo"][i - 1] > 0
    j = ag["brasil"]["serie"]["periodo"].index("2026-04")
    assert ag["brasil"]["serie"]["petroleo"][j] is None
    _, _, avisos = conferir(sem_abril, ag)
    assert any("abril de 2026" in a for a in avisos)


def test_arquivos_antigos_com_titulo_e_linhas_entre_aspas():
    """Vícios reais dos arquivos de 2018 a 2021 da ANP."""
    mensal = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,Produção de Óleo (m³),Produção de Condensado (m³),"
        "Produção de Gás Associado (Mm³),Produção de Gás Não Associado (Mm³),Produção de Água (m³),Injeção de Gás (Mm³)\r\n"
        '"2018,01/2018,Alagoas,Alagoas,ANAMBÉ,1-BRSA-313D-AL,Terra,Estação Coletora de Furado,""122,119"",0,""137,4942"",0,""2080,497"","\r\n'
        "2018,01/2018,Alagoas,Alagoas,ANAMBÉ,1-BRSA-513D-AL,Terra,Não Informado,0,0,0,0,0,\r\n"
    )
    df = ler_csv(mensal.encode("utf-8"), "2018_producao-terra-2018-1trim.csv")
    assert len(df) == 1
    assert df.iloc[0]["petroleo_bbl_d"] == pytest.approx(122.119 * 6.289811 / 31)

    por_poco = (
        "ANP - Agência Nacional do Petroleo, Gás Natural e Biocombustíveis;;;;;;;;;;;;;;;;\r\n"
        "Estado;Bacia;Nome Poço;;Campo;Operador;Número do Contrato;Período;Óleo (bbl/dia);Condensado (bbl/dia);"
        "Petróleo (bbl/dia);Gás Natural (Mm³/dia);;;Volume Gás Royalties (Mm³/dia);Água (bbl/dia);Instalação Destino\r\n"
        ";;ANP;Operador;;;;;;;;Assoc;N Assoc;Total;;;\r\n"
        "Bahia;Recôncavo;7-XX-1-BA;XX1;CAMPO X;Operadora Y;480000;2018/01;10,5;0;10,5;1,2;0;1,2;0;40,1;ESTACAO\r\n"
    )
    df = ler_csv(por_poco.encode("latin-1"), "2018_producao_terra.csv")
    assert len(df) == 1 and df.iloc[0]["periodo"] == "2018-01" and df.iloc[0]["operador"] == "Operadora Y"


def test_grupo_economico_soma_empresas(base):
    from robo.grupos import grupo_de
    regras = [("operadora k", "Grupo KL"), ("operadora l*", "Grupo KL")]
    assert grupo_de("Operadora K", regras) == "Grupo KL" and grupo_de("Operadora A", regras) == "Operadora A"
    ag = agregar(base, regras_grupo=regras)
    g = next(o for o in ag["operadores"] if o["nome"] == "Grupo KL")
    sem = agregar(base, regras_grupo=[])
    soma = sum(o["petroleo"] for o in sem["operadores"] if o["nome"] in ("Operadora K", "Operadora L"))
    assert g["petroleo"] == pytest.approx(soma)
    assert {e["nome"] for e in g["entidades"]} == {"Operadora K", "Operadora L"}
    c = next(x for x in ag["campos"] if x["slug"] == "campo-exemplo-9")
    assert c["grupo"] == "Grupo KL" and c["operador"] == "Operadora L" and c["operador_slug"] == "grupo-kl"


def test_operador_do_campo_e_o_que_mais_produz_no_ultimo_mes():
    import pandas as pd
    from robo.ler import _operador_por_campo
    b = pd.DataFrame({
        "periodo": ["2022-12", "2023-12", "2023-12", "2023-12"],
        "campo": ["ALBACORA"] * 4,
        "operador": ["Petrobras", "Petrobras", "Petro Rio Jaguar", None],
        "petroleo_bbl_d": [20000.0, 15000.0, 300.0, 50.0],
        "gas_mil_m3_d": [100.0, 80.0, 1.0, 0.0],
    })
    # a linha da empresa vizinha vem por último, mas produz pouco
    assert _operador_por_campo(b) == {"ALBACORA": "Petrobras"}


def test_campo_dividido_entre_empresas_fica_dividido_por_poco():
    import pandas as pd
    from robo.ler import _operador_por_campo, _operador_por_poco
    b = pd.DataFrame({
        "periodo": ["2019-05", "2023-12", "2023-12", "2023-12"],
        "campo": ["GOMO"] * 4,
        "poco": ["7-GO-0001-BA", "7-GO-0010-BA", "7-GO-0020-BA", "7-GO-0030-BA"],
        "operador": ["Petrobras", "3R Bahia", "3R Bahia", "PetroRecôncavo"],
        "petroleo_bbl_d": [10.0, 50.0, 32.0, 65.0],
        "gas_mil_m3_d": [0.0] * 4,
    })
    por_poco = _operador_por_poco(b)
    assert por_poco[("GOMO", "7go0030ba")] == "PetroRecôncavo"
    assert por_poco[("GOMO", "7go0010ba")] == "3R Bahia"
    # poço que só aparece antes do último mês segue o operador do campo
    assert ("GOMO", "7go0001ba") not in por_poco
    assert _operador_por_campo(b) == {"GOMO": "3R Bahia"}

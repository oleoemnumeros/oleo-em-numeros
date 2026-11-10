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


def test_planilha_manual_aceita_comentario_e_campo_por_poco(pasta, tmp_path):
    manual = tmp_path / "manual.csv"
    manual.write_text("# comentário\ncampo,operador\nCAMPO EXEMPLO 3,por poço\nCAMPO EXEMPLO 4,Operadora Nova\n",
                      encoding="utf-8")
    b, _ = consolidar(pasta, manual=manual)
    recentes = b[b["periodo"] >= "2024-01"]
    assert set(recentes[recentes["campo"] == "CAMPO EXEMPLO 3"]["operador"]) == {"Operadora K"}
    if (recentes["campo"] == "CAMPO EXEMPLO 4").any():
        assert set(recentes[recentes["campo"] == "CAMPO EXEMPLO 4"]["operador"]) == {"Operadora Nova"}
    assert not (b["operador"] == "por poço").any()


# ---------------- notícias ----------------

RSS_TESTE = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>PRIO conclui reparo no campo de Polvo</title><link>https://x.com/1</link>
<pubDate>Mon, 09 Nov 2026 10:00:00 -0300</pubDate><description>&lt;p&gt;Resumo&lt;/p&gt;</description></item>
<item><title>Prefeitura de Búzios inaugura praça</title><link>https://x.com/2</link></item>
<item><title>Mero detalhe: inflação cai</title><link>https://x.com/3</link></item>
<item><title>PRODUÇÃO DE PETRÓLEO DA BRAVA ENERGIA CRESCE</title><link>https://x.com/4</link></item>
</channel></rss>""".encode()


def test_feed_rss_rdf_e_govbr():
    from robo.noticias import ler_feed, ler_govbr
    itens = ler_feed(RSS_TESTE, "t")
    assert len(itens) == 4 and itens[0]["data"] == "2026-11-09T13:00:00+00:00"
    rdf = b"""<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"><item rdf:about="https://g/1">
    <title>ANP divulga boletim</title><link>https://g/1</link><dc:date>2026-11-03T14:00:00-03:00</dc:date></item></rdf:RDF>"""
    assert ler_feed(rdf, "anp")[0]["data"] == "2026-11-03T17:00:00+00:00"
    base = "https://www.gov.br/anp/pt-br/noticias"
    html = (f'<li><div class="conteudo"><h2 class="titulo"><a href="{base}/anp-aprova">ANP aprova &quot;X&quot;</a></h2>'
            '<span class="descricao"><span class="data"> 06/11/2026 </span></span></div></li>')
    g = ler_govbr(html, base, "ANP")
    assert g[0]["titulo"] == 'ANP aprova "X"' and g[0]["data"].startswith("2026-11-06")


def test_marcar_empresas_e_campos_sem_falso_positivo():
    from robo import noticias as N
    N.CAMPOS_FAMOSOS_NORMAIS.update({"Búzios", "Mero"})
    campos = [{"nome": "POLVO", "slug": "polvo"}, {"nome": "Búzios", "slug": "buzios"}, {"nome": "Mero", "slug": "mero"}]
    m = [N.marcar(i, campos) for i in N.ler_feed(RSS_TESTE, "t")]
    assert m[0]["empresas"] == ["PRIO"] and m[0]["campos"] == ["polvo"] and m[0]["relevante"]
    assert not m[1]["relevante"] and m[1]["campos"] == []      # cidade de Búzios, sem petróleo
    assert not m[2]["relevante"] and m[2]["campos"] == []      # "mero" adjetivo
    assert m[3]["relevante"] and m[3]["empresas"] == ["Brava Energia"]   # manchete toda em maiúsculas


def test_coleta_guarda_historico_sem_duplicar(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from robo import noticias as N
    monkeypatch.setattr(N, "baixar_fonte", lambda f: (N.ler_feed(RSS_TESTE, f["nome"]), "ok"))
    monkeypatch.setattr(N, "_campos_do_site", lambda: [])
    agora = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)
    for _ in range(2):
        r = N.coletar(fontes=[{"nome": "T"}], historico=tmp_path / "h.json", saida=tmp_path / "n.json", agora=agora)
    assert r["total"] == 4 and r["no_site"] == 2


# ---------------- busca de poços ----------------

def test_dados_da_busca_de_pocos(base, tmp_path):
    import json
    from gerador.pocos import gerar_pocos
    ag = agregar(base)
    csv = tmp_path / "base.csv.gz"
    base.to_csv(csv, index=False, compression="gzip")
    r = gerar_pocos(csv, tmp_path / "site", ag)
    pasta = tmp_path / "site" / "dados" / "pocos"
    idx = json.loads((pasta / "indice.json").read_text("utf-8"))
    assert r["pocos"] == len(idx["pocos"]) > 0
    ultimo = base["periodo"].max()
    ativos = base[(base["periodo"] == ultimo) & ((base["petroleo_bbl_d"] > 0) | (base["gas_mil_m3_d"] > 0))]["poco"].nunique()
    assert r["ativos"] == ativos
    # a série de um poço bate com a base no último mês
    nome, cslug = idx["pocos"][0][0], idx["pocos"][0][1]
    campo = json.loads((pasta / f"{cslug}.json").read_text("utf-8"))
    w = next(p for p in campo["pocos"] if p["n"] == nome)
    esperado = base[(base["poco"] == nome) & (base["periodo"] == ultimo)]["petroleo_bbl_d"].sum()
    assert w["p"][-1] == pytest.approx(esperado, abs=0.05)
    assert idx["campos"][cslug]["pg"] == 1


# ---------------- resultados trimestrais (CVM) ----------------

def _cvm_fake(tmp_path):
    """ITR e DFP fictícios da PRIO: acumulados no ano, como a CVM entrega."""
    import pandas as pd
    linhas = []
    acum = {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}     # receita de 100 por trimestre etc.
    for fim, k in acum.items():
        doc = "DFP" if fim == "12-31" else "ITR"
        base = dict(CNPJ_CIA="10.629.105/0001-68", DT_REFER=f"2025-{fim}", VERSAO="1", DENOM_CIA="PRIO S.A.",
                    ESCALA_MOEDA="MIL", ORDEM_EXERC="ÚLTIMO", DT_INI_EXERC="2025-01-01", DT_FIM_EXERC=f"2025-{fim}", DOC=doc)
        for dem, conta, desc, v in (("DRE_con", "3.01", "Receita", 100_000 * k), ("DRE_con", "3.05", "EBIT", 40_000 * k),
                                    ("DRE_con", "3.11", "Lucro", 20_000 * k), ("DVA_con", "7.04.01", "Depreciação", 10_000 * k),
                                    ("DFC_MI_con", "6.02.01", "Aquisição de imobilizado", -30_000 * k),
                                    ("DFC_MI_con", "6.02.02", "Aquisição de controlada", -500_000 * k)):
            linhas.append({**base, "DEM": dem, "CD_CONTA": conta, "DS_CONTA": desc, "VL_CONTA": str(v)})
        for dem, conta, v in (("BPA_con", "1.01.01", 50_000), ("BPA_con", "1.01.02", 10_000),
                              ("BPP_con", "2.01.04", 100_000), ("BPP_con", "2.02.01", 400_000)):
            linhas.append({**base, "DEM": dem, "CD_CONTA": conta, "DS_CONTA": "x", "VL_CONTA": str(v), "DT_INI_EXERC": ""})
    # versão antiga reapresentada do 2º tri, que deve ser ignorada
    linhas.append({**linhas[6], "VERSAO": "0", "VL_CONTA": "999999999"})
    arq = tmp_path / "itr_2025.csv.gz"
    pd.DataFrame(linhas).to_csv(arq, index=False, compression="gzip")
    return [arq]


def test_cvm_trimestres_saem_da_diferenca_dos_acumulados(tmp_path):
    from robo.cvm import calcular, carregar
    r = calcular(carregar(_cvm_fake(tmp_path)))
    t = {x["trimestre"]: x for x in r["PRIO"]["trimestres"]}
    assert list(t) == ["2025T1", "2025T2", "2025T3", "2025T4"]
    for q in t.values():                     # R$ milhões
        assert q["receita"] == pytest.approx(100) and q["lucro"] == pytest.approx(20)
        assert q["ebitda"] == pytest.approx(50) and q["margem_ebitda"] == pytest.approx(50)
        assert q["capex"] == pytest.approx(30)                   # compra de controlada fica fora
        assert q["divida_liquida"] == pytest.approx(440)
    assert t["2025T4"]["alavancagem"] == pytest.approx(440 / 200)
    assert "alavancagem" not in t["2025T3"]                       # precisa de 4 trimestres

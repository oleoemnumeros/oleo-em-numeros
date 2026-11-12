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
        for dem, conta, desc, v in (("BPA_con", "1.01.01", "Caixa", 50_000), ("BPA_con", "1.01.02", "Aplicações", 10_000),
                                    ("BPP_con", "2.01.04", "Empréstimos", 100_000), ("BPP_con", "2.02.01", "Empréstimos", 400_000),
                                    ("BPP_con", "2.02.01.03", "Financiamento por Arrendamento", 20_000),
                                    ("BPP_con", "2.02.02.02", "Outros", 70_000),
                                    ("BPP_con", "2.02.02.02.03", "Arrendamentos mercantis", 30_000)):
            linhas.append({**base, "DEM": dem, "CD_CONTA": conta, "DS_CONTA": desc, "VL_CONTA": str(v), "DT_INI_EXERC": ""})
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
        assert q["divida_liquida"] == pytest.approx(420)          # sem o arrendamento que estava nos empréstimos
        assert q["arrendamentos"] == pytest.approx(50)            # 20 dentro dos empréstimos + 30 em outras obrigações
    assert t["2025T4"]["alavancagem"] == pytest.approx(420 / 200)
    assert t["2025T4"]["alavancagem_com_arrend"] == pytest.approx(470 / 200)
    assert "alavancagem" not in t["2025T3"]                       # precisa de 4 trimestres


# ---------------- estados e bacias ----------------

def test_estados_e_bacias_somam_o_brasil(base, tmp_path):
    import json
    from robo.executar import _leve
    from gerador.gerar import gerar
    ag = agregar(base)
    br = ag["brasil"]["boe"]
    for chave in ("estados", "bacias"):
        regs = ag[chave]
        assert sum(r["boe"] for r in regs) == pytest.approx(br, rel=1e-9)
        assert sum(r["share"] for r in regs) == pytest.approx(100)
        assert [r["rank"] for r in regs] == list(range(1, len(regs) + 1))
        for r in regs:
            assert sum(o["boe"] for o in r["operadores"]) == pytest.approx(r["boe"], rel=1e-9)
            assert sum(c["boe"] for c in r["campos"]) / 1000 == pytest.approx(r["boe"], rel=1e-9)
            assert sum(x["pct"] for x in r["cruzado"]) == pytest.approx(100)
    assert all(c["estado_slug"] and c["bacia_slug"] for c in ag["campos"])
    nomes = {b["slug"]: b["titulo"] for b in ag["bacias"]}
    assert nomes.get("reconcavo", "Bacia do Recôncavo") == "Bacia do Recôncavo"
    assert nomes.get("potiguar", "Bacia Potiguar") == "Bacia Potiguar"
    # páginas
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "agregados.json").write_text(json.dumps(_leve(ag), ensure_ascii=False, default=str), "utf-8")
    paginas = gerar(tmp_path / "proc", tmp_path / "site", teste=True)
    e = ag["estados"][0]
    assert f"estados/{e['slug']}/" in paginas and "bacias/" in paginas and "estados/" in paginas
    html = (tmp_path / "site" / "estados" / e["slug"] / "index.html").read_text("utf-8")
    assert e["resumo"] if "resumo" in e else True
    assert "Em resumo" in html and e["campos"][0]["nome"] in html


# ---------------- royalties ----------------

def _pasta_royalties(tmp_path):
    """Arquivos fictícios no formato da ANP: CSV de 2025 e a planilha de 2026."""
    import pandas as pd
    p = tmp_path / "roy"
    p.mkdir()
    # 2025: competência (produção) e caixa (pagamento) em número serial do Excel; decimal com vírgula
    jan25, fev25 = 45658, 45689
    (p / "mun_2025.csv").write_text(
        "competencia;caixa;municipio;estado;royalties minimos;royalties excedentes\n"
        f"45597;{jan25};marica-rj;rj;1000000,50;9000000\n"
        f"45627;{fev25};marica-rj;rj;1000000;10000000\n"
        f"45597;{jan25};parati-rj;rj;100000;0\n"
        f"45597;{jan25};pojuca-ba;ba;50000;1000\n", encoding="latin-1")
    (p / "est_2025.csv").write_text(
        "competencia;caixa;bahia;rio de janeiro\n"
        f"45597;{jan25};200000;5000000\n;;;\n", encoding="latin-1")
    # 2026: aba TOTAL com resumo, estados, somatório por UF (deve ser ignorado) e municípios
    linhas = [[None] * 8 for _ in range(4)]
    linhas += [[None, "BENEFICIÁRIOS", "Royalties até 5%", "Royalties excedentes a 5%", "Total - JANEIRO", "a", "b", "Total - FEVEREIRO"],
               [None, "Estados", 1, 1, 999, 1, 1, 999],
               [None, "Municípios", 1, 1, 999, 1, 1, 999],
               [None, "TOTAL", 1, 1, 999, 1, 1, 999],
               [None, "ESTADOS", "x", "y", "Total - JANEIRO", "x", "y", "Total - FEVEREIRO"],
               [None, "BAHIA", 1, 1, 300000, 1, 1, 310000],
               [None, "RIO DE JANEIRO", 1, 1, 6000000, 1, 1, 6100000],
               [None, "TOTAL", 1, 1, 6300000, 1, 1, 6410000],
               [None, "Somatório dos Municípios por Unidade da Federação", "x", "y", "Total - JANEIRO", "x", "y", "Total - FEVEREIRO"],
               [None, "BAHIA", 1, 1, 7777777, 1, 1, 7777777],
               [None, "RIO DE JANEIRO", 1, 1, 8888888, 1, 1, 8888888],
               [None, "MUNICÍPIOS", "x", "y", "Total - JANEIRO", "x", "y", "Total - FEVEREIRO"],
               ["RJ", "MARICA-RJ", 1, 1, 12000000, 1, 1, 13000000],
               ["BA", "POJUCA-BA", 1, 1, 60000, 1, 1, 0],
               [None, "TOTAL", 1, 1, 12060000, 1, 1, 13000000],
               [None, "MUNICÍPIOS", "x", "y", "Total - JANEIRO", "x", "y", "Total - FEVEREIRO"],
               [None, "Depósitos judiciais", 1, 1, 5555, 1, 1, 5555]]
    with pd.ExcelWriter(p / "tabela_2026.xlsx") as w:
        pd.DataFrame([["CONCESSÃO"]]).to_excel(w, sheet_name="CONCESSÃO", header=False, index=False)
        pd.DataFrame(linhas).to_excel(w, sheet_name="TOTAL", header=False, index=False)
    (p / "pe_municipio.csv").write_text("competencia;caixa;municipio;estado;valor\n"
                                        "45901;45962;marica;rio de janeiro;40000000\n", encoding="latin-1")
    return p


def test_royalties_lidos_somados_e_com_nome_oficial(tmp_path):
    from robo import royalties as RY
    r = RY.montar(_pasta_royalties(tmp_path))
    meses = r["meses"]
    assert meses[0] == "2018-01" and r["ultimo"] == "2026-02"
    m = {x["nome"]: x for x in r["municipios"]}
    assert set(m) == {"Maricá", "Paraty", "Pojuca"}                      # nome do IBGE, com acento; Parati -> Paraty
    i = meses.index
    assert m["Maricá"]["r"][i("2025-01")] == pytest.approx(10000000.50)   # até 5% + excedente
    assert m["Maricá"]["r"][i("2026-02")] == pytest.approx(13000000)
    assert m["Maricá"]["pe"] == {"2025T3": 40000000}
    e = {x["uf"]: x for x in r["estados"]}
    # o bloco "Somatório dos Municípios por UF" não entra como dinheiro do estado
    assert e["RJ"]["r"][i("2026-01")] == pytest.approx(6000000)
    assert e["BA"]["r"][i("2026-02")] == pytest.approx(310000)
    assert sum(x["r"][i("2026-01")] for x in r["municipios"]) == pytest.approx(12060000)   # sem depósitos judiciais


def test_paginas_de_royalties(base, tmp_path):
    import json
    from robo import royalties as RY
    from robo.executar import _leve
    from gerador.gerar import gerar
    r = RY.montar(_pasta_royalties(tmp_path))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "royalties.json").write_text(json.dumps(r, ensure_ascii=False), "utf-8")
    (proc / "agregados.json").write_text(json.dumps(_leve(agregar(base)), ensure_ascii=False, default=str), "utf-8")
    paginas = gerar(proc, tmp_path / "site", teste=True)
    assert "royalties/" in paginas and "royalties/rio-de-janeiro/" in paginas
    assert "royalties/rio-de-janeiro/marica/" in paginas and "royalties/bahia/pojuca/" in paginas
    html = (tmp_path / "site" / "royalties" / "rio-de-janeiro" / "marica" / "index.html").read_text("utf-8")
    assert "Royalties de petróleo de Maricá (RJ)" in html and "Em resumo" in html
    assert "É o município que mais recebe" in html


def test_grafico_desenha_lacuna_e_rotulos_do_eixo():
    """Mês sem dado quebra a linha (antes ela ligava os vizinhos) e o eixo mostra 0,25 e 2,5 por inteiro."""
    from gerador.graficos import _casas, _ticks, linha
    per = [f"2025-{m:02d}" for m in range(1, 7)]
    svg = str(linha([10, 12, None, 14, 15, 16], per, "Teste, em bbl/d"))
    larga = svg.split("</svg>")[0]
    assert larga.count("<polyline") == 2
    assert "De janeiro de 2025 a junho de 2025" in larga and "Máximo: 16,0" in larga
    assert _casas(_ticks(0, 1)) == 2 and _casas([0, 2.5, 5]) == 1 and _casas([0, 1000, 2000]) == 0


def test_nomes_compostos_e_valores_em_reais():
    from gerador.royalties import dinheiro
    from robo.util import nome_bonito
    assert nome_bonito("PAPA-TERRA") == "Papa-Terra"
    assert nome_bonito("SABIÁ BICO-DE-OSSO") == "Sabiá Bico-de-Osso"
    assert dinheiro(1840.6) == "R$ 1.841" and dinheiro(532_000) == "R$ 532 mil" and dinheiro(3.2e9) == "R$ 3,20 bi"


def test_campos_divididos_por_contrato_tem_nome_legivel_e_aviso():
    from gerador.gerar import aviso_campo
    from robo.util import nome_campo
    assert nome_campo("BÚZIOS_ECO") == "Búzios ECO" and nome_campo("SÉPIA_ECO") == "Sépia ECO"
    assert nome_campo("AnC_MERO") == "Mero AnC" and nome_campo("AnC_Jubarte_Sudoeste") == "Jubarte Sudoeste AnC"
    assert nome_campo("AnC_NORTE_ATAPU") == "Norte Atapu AnC" and nome_campo("AnC_ARGONAUTA_ME1") == "Argonauta ME1 AnC"
    assert nome_campo("PAPA-TERRA") == "Papa-Terra" and nome_campo("TUPI") == "Tupi" and nome_campo(None) == ""

    def campo(chave, nome, boe):
        return {"chave": chave, "nome": nome, "boe": boe}
    buzios, eco, tupi = campo("BÚZIOS", "Búzios", 400_000), campo("BÚZIOS_ECO", "Búzios ECO", 1_100_000), campo("TUPI", "Tupi", 1_064_000)
    todos = [eco, tupi, buzios]
    for c in (buzios, eco):  # os dois lados explicam a divisão e somam as duas partes
        aviso = aviso_campo(c, todos)
        assert "Búzios (cessão onerosa)" in aviso and "Búzios ECO (excedente" in aviso
        assert "1.500.000 boe/d" in aviso and "mais que qualquer outro campo do país" in aviso
    # se a soma não é a maior, não afirma que é
    assert "mais que qualquer" not in aviso_campo(buzios, todos + [campo("X", "X", 2_000_000)])
    assert aviso_campo(tupi, todos) == ""  # campo comum não ganha aviso
    assert "Área não Contratada" in aviso_campo(campo("AnC_MERO", "Mero AnC", 1), todos)
    assert "Excedente da Cessão Onerosa" in aviso_campo(eco, [eco, tupi])  # ECO sem o campo-base na lista


def test_operadora_nao_identificada_fica_fora_do_ranking_e_404_usa_a_raiz(base, tmp_path):
    import json
    from robo.executar import _leve
    ag = _leve(agregar(base))
    falso = dict(ag["operadores"][-1], nome="Não identificado", slug="nao-identificado", boe=999.0)
    ag["operadores"].insert(0, falso)
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(ag, ensure_ascii=False, default=str), "utf-8")
    gerar(proc, tmp_path / "site", limpos=True)
    html = (tmp_path / "site" / "operadoras" / "nao-identificado" / "index.html").read_text("utf-8")
    assert "fora do ranking" in html and "maior operadora do país" not in html
    lista = (tmp_path / "site" / "operadoras" / "index.html").read_text("utf-8")
    assert lista.index("nao-identificado") > lista.index("<td>1</td>")
    erro = (tmp_path / "site" / "404.html").read_text("utf-8")
    assert 'href="/estilo.css?v=' in erro and 'rel="canonical"' not in erro and "noindex" in erro


def _zip_shape(pasta, nome, tipo, campos, registros, formas):
    """Cria um shapefile minúsculo e o compacta como a ANP entrega."""
    import shapefile
    w = shapefile.Writer(str(pasta / nome), shapeType=tipo, encoding="utf-8")
    for c in campos:
        w.field(c, "C", size=60)
    for forma, rec in zip(formas, registros):
        w.poly(forma) if tipo == shapefile.POLYGON else w.point(*forma)
        w.record(*rec)
    w.close()
    destino = pasta / f"{nome}.zip"
    with zipfile.ZipFile(destino, "w") as z:
        for ext in ("shp", "shx", "dbf"):
            z.write(pasta / f"{nome}.{ext}", f"{nome}.{ext}")
    return destino


def test_mapa_simplifica_limites_e_casa_pocos_pelos_dois_nomes(tmp_path):
    import json
    import shapefile
    from robo.mapa import processar, simplificar
    # linha quase reta perde o ponto do meio; curva de verdade mantém
    assert simplificar([(0, 0), (1, 0.00001), (2, 0)], 0.0004) == [(0, 0), (2, 0)]
    assert len(simplificar([(0, 0), (1, 1), (2, 0)], 0.0004)) == 3

    externo = [[-40.0, -10.0], [-40.0, -9.0], [-39.5, -8.99999], [-39.0, -9.0], [-39.0, -10.0], [-40.0, -10.0]]   # sentido horário
    buraco = [[-39.8, -9.8], [-39.2, -9.8], [-39.2, -9.2], [-39.8, -9.2], [-39.8, -9.8]]                          # anti-horário
    campos = ["NOM_CAMPO", "OPERADOR_C", "ETAPA", "Localizaca", "NOM_BACIA"]
    _zip_shape(tmp_path, "campos", shapefile.POLYGON, campos,
               [("BÚZIOS_ECO", "Petróleo Brasileiro S.A.", "Produção", "Mar", "Santos")], [[externo, buraco]])
    pocos = ["POCO", "POCO_OPERA", "LAT_DD", "LONG_DD"]
    _zip_shape(tmp_path, "pocos", shapefile.POINT, pocos,
               [("7-AAA-1-BA", "7AAA1BA", "-9.5", "-39.5"),         # casa pelo nome da ANP
                ("9-XXX-9-BA", "3MG0002BA", "-9.6", "-39.6"),       # casa pelo nome da operadora
                ("7-FORA-1-BA", "7FORA1BA", "-9.5", "-39.5"),       # não está na nossa base
                ("7-ZERO-1-BA", "7ZERO1BA", "0", "0"),              # coordenada vazia
                ("7-LONGE-1-BA", "7LONGE1BA", "40", "10")],         # fora do Brasil
               [(-39.5, -9.5), (-39.6, -9.6), (-39.5, -9.5), (0, 0), (10, 40)])
    zips = tmp_path / "zips"
    zips.mkdir()
    for n in ("campos", "pocos"):
        (zips / f"{n}.zip").write_bytes((tmp_path / f"{n}.zip").read_bytes())
    base_csv = tmp_path / "base.csv"
    pd_ = __import__("pandas")
    pd_.DataFrame({"poco": ["7-AAA-1-BA", "3MG0002BA", "7-ZERO-1-BA", "7-LONGE-1-BA"]}).to_csv(base_csv, index=False)
    saida = tmp_path / "geo.json"
    r = processar(zips, base_csv, saida)
    geo = json.loads(saida.read_text("utf-8"))
    assert r["campos"] == 1 and set(geo["pocos"]) == {"7aaa1ba", "3mg0002ba"}
    c = geo["campos"][0]
    assert c["k"] == "buzioseco" and c["et"] == "Produção" and c["loc"] == "Mar"
    assert len(c["g"]) == 1, "o buraco não vira polígono à parte"
    anel = c["g"][0]
    assert len(anel) == 4 and [-39.5, -9.0] not in anel, "o ponto quase alinhado some, os 4 cantos ficam (sem repetir o primeiro)"


def test_mapa_junta_limites_empresas_e_pocos_e_so_aparece_com_dados(base, tmp_path):
    import json
    from robo.executar import _leve
    from robo.ler import _chave_poco
    from robo.mapa import chave_campo
    ag = _leve(agregar(base))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(ag, ensure_ascii=False, default=str), "utf-8")
    gerar(proc, tmp_path / "s1", limpos=True)    # sem dados geográficos: sem página e sem item de menu
    assert not (tmp_path / "s1" / "mapa").exists()
    assert 'href="mapa/"' not in (tmp_path / "s1" / "index.html").read_text("utf-8")

    base.to_csv(proc / "base_pocos.csv.gz", index=False, compression="gzip")
    c0 = ag["campos"][0]
    ult = base["periodo"].max()
    ativos = base[(base["campo"] == c0["chave"]) & (base["periodo"] == ult) &
                  (base["petroleo_bbl_d"].fillna(0) + base["gas_mil_m3_d"].fillna(0) > 0)]["poco"].tolist()
    assert ativos
    quadrado = [[[-40.0, -10.0], [-40.0, -9.0], [-39.0, -9.0], [-39.0, -10.0]]]
    geo = {"campos": [{"k": chave_campo(c0["chave"]), "n": c0["chave"], "op": "X S.A.", "et": "Produção", "loc": "Terra",
                       "bacia": "", "g": quadrado},
                      {"k": "antigo", "n": "ANTIGO", "op": "Y Ltda.", "et": "Desenvolvimento", "loc": "Mar", "bacia": "",
                       "g": quadrado}],
           "pocos": {_chave_poco(ativos[0]): [-39.5, -9.5]}}
    (proc / "geo.json").write_text(json.dumps(geo), "utf-8")
    gerar(proc, tmp_path / "s2", limpos=True)
    site = tmp_path / "s2"
    pagina = (site / "mapa" / "index.html").read_text("utf-8")
    assert "leaflet.js" in pagina and 'id="empresa"' in pagina and 'id="campo"' in pagina and (site / "leaflet" / "leaflet.js").exists()
    assert 'href="mapa/"' in (site / "index.html").read_text("utf-8")
    assert "/mapa/" in (site / "sitemap.xml").read_text("utf-8")
    dados = json.loads((site / "dados" / "mapa" / "campos.json").read_text("utf-8"))
    com, sem = dados["campos"]
    assert com["s"] == c0["slug"] and com["gi"] >= 0 and com["b"] == round(c0["boe"])
    assert sem["s"] is None and sem["gi"] == -1 and sem["op"] == "Y Ltda."
    assert dados["grupos"][com["gi"]]["n"] == c0["grupo"]
    pocos = json.loads((site / "dados" / "mapa" / "pocos.json").read_text("utf-8"))["pocos"]
    assert len(pocos) == 1 and pocos[0][0] == ativos[0] and pocos[0][1:3] == [-39.5, -9.5]
    assert dados["slugs"][pocos[0][4] if isinstance(pocos[0][4], int) else pocos[0][4][0]][0] == c0["slug"]

"""Testes do robô e do gerador com dados fictícios nos dois formatos da ANP."""
import io
import re
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
    # rodapé da Família em Números: os quatro portais, e o próprio marcado como "você está aqui"
    for nome in ("Óleo em Números", "Comex em Números", "Voo em Números", "Economia em Números"):
        assert nome in html
    assert 'href="https://economiaemnumeros.com.br/"' in html
    assert html.count("você está aqui") == 1 and 'href="https://oleoemnumeros.com.br/"' not in html.split("Família em Números")[-1]
    # botão Compartilhar (WhatsApp): o arquivo é copiado e toda página o carrega
    assert (tmp_path / "site" / "familia-compartilhar.js").exists()
    assert "familia-compartilhar.js?v=" in html and "familia-compartilhar.js?v=" in (tmp_path / "site" / "index.html").read_text("utf-8")
    # página de sugestões e críticas, com o link no rodapé
    sug = (tmp_path / "site" / "sugestoes" / "index.html").read_text("utf-8")
    assert 'action="https://formsubmit.co/' in sug and 'name="mensagem"' in sug and 'name="_honey"' in sug
    assert 'name="_next" value="' in sug and "/sugestoes/#enviado" in sug and 'id="enviado"' in sug
    assert sug.count("<h1") == 1 and "[Óleo em Números] Sugestão ou crítica" in sug
    assert "sugestoes/" in html and "Sugestões e críticas" in html
    assert "sugestoes/" in (tmp_path / "site" / "sitemap.xml").read_text("utf-8")
    # privacidade e contato: páginas próprias, com e-mail do portal e links no rodapé
    priv = (tmp_path / "site" / "privacidade" / "index.html").read_text("utf-8")
    for trecho in ("Política de privacidade", "GoatCounter", "Google AdSense", "adssettings.google.com", "Lei nº 13.709/2018", "OpenStreetMap", "Última atualização"):
        assert trecho in priv
    assert priv.count("<h1") == 1 and "{{" not in priv and "__" not in priv.split("</main>")[0]
    cont = (tmp_path / "site" / "contato" / "index.html").read_text("utf-8")
    assert "mailto:" in cont and "sugestoes/" in cont and cont.count("<h1") == 1 and "__" not in cont.split("</main>")[0]
    assert "privacidade/" in html and "contato/" in html
    assert 'href="https://x.com/familianumeros"' in html and "Siga no X" in html
    sitemap = (tmp_path / "site" / "sitemap.xml").read_text("utf-8")
    assert "privacidade/" in sitemap and "contato/" in sitemap
    # imagem de prévia do link (WhatsApp, Instagram, X, LinkedIn)
    assert '<meta property="og:image" content="' in html and html.count('og.png"') == 1
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    og = (tmp_path / "site" / "og.png").read_bytes()
    assert og[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(og[16:20], "big") == 1200 and int.from_bytes(og[20:24], "big") == 630


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
    assert "Quanto Maricá (RJ) recebe de royalties de petróleo" in html and "Em resumo" in html
    assert "É o município que mais recebe" in html


def test_links_da_familia_por_municipio(base, tmp_path, monkeypatch):
    """Cidade com página no Comex/Economia ganha o quadro "Mais sobre"; o site publica a própria lista."""
    import json
    from robo import royalties as RY
    from robo.executar import _leve
    from gerador.gerar import gerar
    monkeypatch.setenv("OLEO_SEM_REDE", "1")
    r = RY.montar(_pasta_royalties(tmp_path))
    proc = tmp_path / "proc"
    (proc / "familia").mkdir(parents=True)
    (proc / "royalties.json").write_text(json.dumps(r, ensure_ascii=False), "utf-8")
    (proc / "agregados.json").write_text(json.dumps(_leve(agregar(base)), ensure_ascii=False, default=str), "utf-8")
    (proc / "familia" / "comex.json").write_text('[["Maricá","RJ","/municipio/marica-rj/"]]', "utf-8")
    (proc / "familia" / "economia.json").write_text('[["Marica","RJ","/municipios/rio-de-janeiro/marica/","x"]]', "utf-8")
    gerar(proc, tmp_path / "site", teste=True)
    html = (tmp_path / "site" / "royalties" / "rio-de-janeiro" / "marica" / "index.html").read_text("utf-8")
    assert "Mais sobre Maricá (RJ)" in html
    assert "https://comexemnumeros.com.br/municipio/marica-rj/" in html
    assert "https://economiaemnumeros.com.br/municipios/rio-de-janeiro/marica/" in html
    pojuca = (tmp_path / "site" / "royalties" / "bahia" / "pojuca" / "index.html").read_text("utf-8")
    assert "Mais sobre" not in pojuca
    indice = json.loads((tmp_path / "site" / "royalties" / "indice.json").read_text("utf-8"))
    assert ["Maricá", "RJ", "/royalties/rio-de-janeiro/marica/"] in indice


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
    # llms.txt: só no site real, com links absolutos do domínio
    llms = (tmp_path / "site" / "llms.txt").read_text("utf-8")
    assert llms.startswith("# Óleo em Números") and "__BASE__" not in llms and "/campos/)" in llms


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


_PAG_QUEIMA = """Boletim da Produção de Petróleo e Gás Natural
Queima de gás natural por campo
Ano Mês
2026 02 - Fevereiro
20 campos com maior queima de gás
Campo Bacia Localização Operador Queima de Gás (Mm³/d)

{c1} Santos Mar PETROBRAS 745,30
Albacora Leste Campos Mar PRIO FORTE S.A 271,48
Rio Urucu Solimões Terra PETROBRAS 31,32
Campo Qualquer Espírito Santo Mar OUTRA EMPRESA S.A. 1.234
Total 3.591,58
"""
_PAG_NAC = """Histórico da movimentação de gás natural (Mm³/d)
Mês/Ano Consumo Queima Injeção Disponível Produção Aproveitamento
02/2026 20,00 4,00 60,00 16,00 100,00 96,00%
01/2026 19,00 5,00 60,00 16,00 100,00 95,00%
"""


import json  # noqa: E402


def _csv_gas(pasta, valores):
    """Um CSV por destino no formato da ANP: ; como separador, vírgula decimal, BOM e nomes em maiúsculas."""
    colunas = {"producao": "PRODUÇÃO", "queima": "QUEIMADO", "reinjecao": "REINJETADO", "consumo": "CONSUMO", "disponivel": "DISPONÍVEL"}
    for dest, col in colunas.items():
        linhas = ["ANO;MÊS;GRANDE REGIÃO;UNIDADE DA FEDERAÇÃO;PRODUTO;LOCALIZAÇÃO;" + col]
        for ano, mes, uf, loc, v in valores[dest]:
            linhas.append(f"{ano};{mes};REGIÃO X;{uf};GÁS NATURAL;{loc};{str(v).replace('.', ',')}")
        (pasta / f"{dest}.csv").write_text("\n".join(linhas), "utf-8-sig")


def _gas_sintetico(pasta, campo1="Búzios"):
    # 2026-01 (31 dias) e 2026-02 (28 dias): o RJ no mar e o Amazonas em terra
    v = {"producao": [(2026, "JAN", "RIO DE JANEIRO", "MAR", 3100), (2026, "FEV", "RIO DE JANEIRO", "MAR", 2800),
                      (2026, "JAN", "AMAZONAS", "TERRA", 310), (2026, "FEV", "AMAZONAS", "TERRA", 280)],
         "queima": [(2026, "JAN", "RIO DE JANEIRO", "MAR", 124), (2026, "FEV", "RIO DE JANEIRO", "MAR", 112),
                    (2026, "JAN", "AMAZONAS", "TERRA", 31), (2026, "FEV", "AMAZONAS", "TERRA", 28)],
         "reinjecao": [(2026, "JAN", "RIO DE JANEIRO", "MAR", 1550), (2026, "FEV", "RIO DE JANEIRO", "MAR", 1400)],
         "consumo": [(2026, "JAN", "RIO DE JANEIRO", "MAR", 310), (2026, "FEV", "RIO DE JANEIRO", "MAR", 280),
                     (2026, "JAN", "AMAZONAS", "TERRA", 31), (2026, "FEV", "AMAZONAS", "TERRA", 28)],
         "disponivel": [(2026, "JAN", "RIO DE JANEIRO", "MAR", 9999), (2026, "FEV", "RIO DE JANEIRO", "MAR", 9999)]}
    _csv_gas(pasta, v)
    boletins = {"2026-02": [_PAG_QUEIMA.format(c1=campo1), _PAG_NAC], "2026-01": ["capa sem a tabela"]}
    (pasta / "boletins.json").write_text(json.dumps(boletins, ensure_ascii=False), "utf-8")


def test_gas_balanco_fecha_e_boletim_e_lido(tmp_path):
    from robo.gas import ler_queima_boletim, processar
    _gas_sintetico(tmp_path)
    r = processar(tmp_path, tmp_path / "gas.json")
    assert r["meses"] == ["2026-01", "2026-02"] and r["ultimo"] == "2026-02"
    b = r["brasil"]
    # média diária: fevereiro tem 28 dias, janeiro 31
    assert b["producao"] == [round(3410 / 31, 2), round(3080 / 28, 2)]
    assert b["queima"][1] == round(140 / 28, 2) and b["reinjecao"][1] == round(1400 / 28, 2)
    # o gás disponível é o resto do balanço (a coluna 'disponível' do CSV, 9999, é ignorada)
    assert b["disponivel"][1] == round((3080 - 140 - 1400 - 308) / 28, 2)
    soma = sum(b[k][1] for k in ("queima", "reinjecao", "consumo", "disponivel"))
    assert abs(soma - b["producao"][1]) < 0.05
    assert [e["uf"] for e in r["estados"]] == ["RJ", "AM"] and r["estados"][0]["nome"] == "Rio de Janeiro"
    assert set(r["ambientes"]) == {"MAR", "TERRA"}
    # ranking: nomes de duas palavras, bacia de duas palavras, terra, número com milhar, linha de total ignorada
    q = r["queima"]["meses"]["2026-02"]
    assert [x["campo"] for x in q] == ["Campo Qualquer", "Búzios", "Albacora Leste", "Rio Urucu"]
    por = {x["campo"]: x for x in q}
    assert por["Campo Qualquer"]["bacia"] == "Espírito Santo" and por["Campo Qualquer"]["queima"] == 1234
    assert por["Albacora Leste"]["bacia"] == "Campos" and por["Rio Urucu"]["ambiente"] == "TERRA"
    assert q[0]["posicao"] == 1 and q[0]["queima"] == 1234
    assert "2026-01" not in r["queima"]["meses"]            # boletim sem a tabela é pulado sem erro
    assert r["queima"]["nacional"]["2026-02"]["queima"] == 4.0 and r["queima"]["nacional"]["2026-02"]["producao"] == 100.0
    assert ler_queima_boletim(["texto qualquer"]) == []


def test_gas_paginas_so_com_dados_e_com_ranking_e_cartao_no_campo(base, tmp_path):
    from robo.executar import _leve
    from robo.gas import processar
    ag = _leve(agregar(base))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(ag, ensure_ascii=False, default=str), "utf-8")
    gerar(proc, tmp_path / "s1", limpos=True)    # sem dados de gás: sem páginas e sem item de menu
    assert not (tmp_path / "s1" / "gas").exists()
    assert 'href="gas/"' not in (tmp_path / "s1" / "index.html").read_text("utf-8")

    c0 = ag["campos"][0]
    brutos = tmp_path / "gas"
    brutos.mkdir()
    _gas_sintetico(brutos, campo1=c0["chave"].replace("_", " "))
    processar(brutos, proc / "gas.json")
    gerar(proc, tmp_path / "s2", limpos=True)
    site = tmp_path / "s2"
    pagina = (site / "gas" / "index.html").read_text("utf-8")
    assert "Aproveitamento" in pagina and "Rio de Janeiro" in pagina and "queima" in pagina.lower()
    assert 'href="gas/"' in (site / "index.html").read_text("utf-8") and "/gas/" in (site / "sitemap.xml").read_text("utf-8")
    rank = (site / "gas" / "queima" / "index.html").read_text("utf-8")
    assert "Campo Qualquer" in rank and f'href="../../campos/{c0["slug"]}/"' in rank
    assert "Boletim Mensal" in rank and "1.234" in rank
    campo = (site / "campos" / c0["slug"] / "index.html").read_text("utf-8")
    assert "Queima de gás no" in campo and "745" in campo


def _csv_refino(pasta):
    # RLAM virou REFMAT, e o arquivo de derivados repete o "ex-" no nome da Potiguar; fevereiro tem 28 dias
    (pasta / "processamento.csv").write_text(
        "\ufeffANO;MÊS;UNIDADE DA FEDERAÇÃO;REFINARIA;MATÉRIA PRIMA;PROCESSADO\r\n"
        "1999;DEZ;BAHIA;RLAM;PETRÓLEO NACIONAL;500000\r\n"
        "2026;JAN;BAHIA;REFMAT;PETRÓLEO NACIONAL;1550000\r\n2026;JAN;BAHIA;REFMAT;PETRÓLEO IMPORTADO;310000\r\n"
        "2026;FEV;BAHIA;REFMAT;PETRÓLEO NACIONAL;1400000\r\n2026;FEV;BAHIA;REFMAT;PETRÓLEO IMPORTADO;140000\r\n"
        "2026;JAN;RIO GRANDE DO NORTE;3R POTIGUAR (ex-RPCC);PETRÓLEO NACIONAL;62000\r\n"
        "2026;FEV;RIO GRANDE DO NORTE;3R POTIGUAR (ex-RPCC);PETRÓLEO NACIONAL;56000\r\n"
        "2026;JAN;SÃO PAULO;UNIVEN;OUTRAS CARGAS;0\r\n2026;FEV;SÃO PAULO;UNIVEN;OUTRAS CARGAS;0\r\n", "utf-8")
    (pasta / "derivados.csv").write_text(
        "\ufeffANO;MÊS;UNIDADE DA FEDERAÇÃO;REFINARIA;PRODUTO;PRODUÇÃO\r\n"
        "2026;JAN;BAHIA;REFMAT;ÓLEO DIESEL;620000,5\r\n2026;JAN;BAHIA;REFMAT;GASOLINA A;310000\r\n"
        "2026;FEV;BAHIA;REFMAT;ÓLEO DIESEL;560000\r\n2026;FEV;BAHIA;REFMAT;GASOLINA A;280000\r\n"
        "2026;JAN;RIO GRANDE DO NORTE;3R POTIGUAR (ex-3R POTIGUAR (ex-RPCC));ÓLEO DIESEL;9000\r\n"
        "2026;FEV;RIO GRANDE DO NORTE;3R POTIGUAR (ex-3R POTIGUAR (ex-RPCC));ÓLEO DIESEL;8400\r\n", "utf-8")


def test_refino_junta_nomes_converte_unidades_e_ignora_refinaria_parada(tmp_path):
    from robo.refino import canonica, processar
    assert canonica("RLAM") == "REFMAT" and canonica("REMAN") == "REAM"
    assert canonica("3R POTIGUAR (ex-3R POTIGUAR (ex-RPCC))") == canonica("RPCC") == "3R POTIGUAR"
    _csv_refino(tmp_path)
    r = processar(tmp_path, tmp_path / "refino.json", cadastro=tmp_path / "nao-existe.csv")
    assert r["ultimo"] == "2026-02" and set(r["refinarias"]) == {"REFMAT", "3R POTIGUAR"}   # Univen só tem zeros
    m = r["refinarias"]["REFMAT"]
    assert m["proc"]["nacional"][-1] == 1400000 and m["proc"]["importado"][-1] == 140000
    assert m["deriv"]["ÓLEO DIESEL"][0] == 620000     # vírgula decimal lida e arredondada
    assert r["refinarias"]["3R POTIGUAR"]["deriv"]["ÓLEO DIESEL"] == [9000, 8400]
    assert r["refinarias"]["REFMAT"]["proc"]["nacional"][0] == 1550000 and len(r["meses"]) == 2


def test_refino_paginas_so_com_dados_com_mapa_e_ranking(base, tmp_path):
    from robo.executar import _leve
    from robo.refino import processar
    ag = _leve(agregar(base))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(ag, ensure_ascii=False, default=str), "utf-8")
    gerar(proc, tmp_path / "s1", limpos=True)
    assert not (tmp_path / "s1" / "refino").exists()
    assert 'href="refino/"' not in (tmp_path / "s1" / "index.html").read_text("utf-8")

    brutos = tmp_path / "refino"
    brutos.mkdir()
    _csv_refino(brutos)
    cad = tmp_path / "refinarias.csv"
    cad.write_text("anp,nome,sigla,empresa,cidade,uf,lat,lon,obs\n"
                   "REFMAT,Refinaria de Mataripe,Mataripe,Acelen,São Francisco do Conde,BA,-12.71,-38.48,antiga RLAM\n", "utf-8")
    processar(brutos, proc / "refino.json", cadastro=cad)
    gerar(proc, tmp_path / "s2", limpos=True)
    site = tmp_path / "s2"
    pagina = (site / "refino" / "index.html").read_text("utf-8")
    assert "Mataripe" in pagina and "Ranking das refinarias" in pagina and "-12.71" in pagina    # coordenada no mapa
    assert "Potiguar" in pagina and "mil barris/dia" in pagina
    assert 'href="refino/"' in (site / "index.html").read_text("utf-8") and "/refino/" in (site / "sitemap.xml").read_text("utf-8")
    ref = (site / "refino" / "mataripe" / "index.html").read_text("utf-8")
    assert "Acelen" in ref and "Óleo diesel" in ref and "São Francisco do Conde" in ref
    assert (site / "refino" / "3r-potiguar" / "index.html").exists()    # sem cadastro: nome da ANP


def test_grafico_oferece_so_as_janelas_menores_que_a_serie():
    from gerador.graficos import linha
    per = [f"{2020 + m // 12}-{m % 12 + 1:02d}" for m in range(40)]
    g = str(linha(list(range(1, 41)), per, "Teste"))
    assert 'data-p="total"' in g and 'data-p="12"' in g and 'data-p="36"' in g and 'data-p="60"' not in g
    assert g.count('data-p="12"') == 1 and g.count("<svg") == 6            # total, 1 ano e 3 anos, cada um em duas versões
    curto = str(linha(list(range(1, 11)), per[:10], "Teste"))
    assert 'data-p="12"' not in curto and curto.count("<svg") == 2         # série de 10 meses: só o total (o JS não cria botões)
    assert "&quot;i&quot;:&quot;2020-01&quot;" in curto and 'data-g="' in curto   # dados e geometria para o cursor
    # janela só com um ponto com dado não vira gráfico
    esparso = str(linha([None] * 38 + [1, 2], per, "Teste"))
    assert 'data-p="12"' in esparso


_CAB_PRECOS = ("Regiao - Sigla;Estado - Sigla;Municipio;Revenda;CNPJ da Revenda;Nome da Rua;Numero Rua;Complemento;Bairro;Cep;"
               "Produto;Data da Coleta;Valor de Venda;Valor de Compra;Unidade de Medida;Bandeira")


def _csv_precos(caminho, mes, base_gasolina=6.0):
    linhas = [_CAB_PRECOS]
    for uf, mun in (("RS", "SANTANA DO LIVRAMENTO"), ("SP", "SAO PAULO")):
        for k in range(4):   # 4 postos
            for prod, v in (("GASOLINA", base_gasolina + k / 10), ("ETANOL", base_gasolina * 0.68 + k / 20), ("DIESEL S10", base_gasolina + 1)):
                linhas.append(f"S;{uf};{mun};POSTO SECRETO {k} LTDA; 00.000.000/000{k}-00;RUA DO SEGREDO;{k};;CENTRO;00000-000;"
                              f"{prod};{15 + k}/{mes[5:7]}/{mes[:4]};{str(round(v, 3)).replace('.', ',')};;R$ / litro;BANDEIRA SECRETA")
    linhas.append(f"S;SP;SAO PAULO;POSTO ERRO LTDA; 1;RUA;1;;X;00000-000;GASOLINA;10/{mes[5:7]}/{mes[:4]};599,0;;R$ / litro;X")   # erro de digitação
    linhas.append(f"S;SP;SAO PAULO;POSTO GLP LTDA; 2;RUA;1;;X;00000-000;GLP;10/{mes[5:7]}/{mes[:4]};100,0;;R$ / 13 kg;X")       # fora do escopo
    caminho.write_text("﻿" + "\r\n".join(linhas) + "\r\n", "utf-8")


def test_precos_fontes_resumo_e_media_sem_expor_postos(tmp_path):
    from robo.precos import _chaves, processar, resumir
    base = "https://x/dados-abertos/arquivos/shpc/"
    ch = _chaves([base + "dsas/ca/ca-2022-02.zip", base + "dsas/ca/ca-2023-01.zip", base + "dsas/ca/ca-2018-01.csv",
                  base + "dsan/2026/10-gasolina-etanol.csv", base + "dsan/2026/04-dados-abertos-precos-gasolina-etanol",
                  base + "dsan/2026/02-cados-abertos-preco-gasolina-etanol.csv", base + "dsan/2025/precos-diesel-gnv-12.csv",
                  base + "dsan/2026/09-dados-abertos-precos-2026-09-diesel-gnv.csv", base + "dsan/2023/precos-outra-coisa-01.csv", base + "dsas/ca/precos-semestrais-ca.zip"])
    assert set(ch) == {"ca-2022-02", "dsan-2026-10-gasolina-etanol", "dsan-2026-04-gasolina-etanol", "dsan-2026-02-gasolina-etanol",
                       "dsan-2025-12-diesel-gnv", "dsan-2026-09-diesel-gnv", "ca-2022-01"}    # semestral de 2023 em diante e antes de 2019 ficam de fora
    agg = tmp_path / "agg"
    agg.mkdir()
    for mes in ("2026-09", "2026-10"):
        _csv_precos(tmp_path / f"{mes}.csv", mes, 6.0 if mes == "2026-09" else 6.3)
        resumir(tmp_path / f"{mes}.csv").to_csv(agg / f"dsan-{mes}-x.csv.gz", index=False, compression="gzip")
    bruto = (agg / "dsan-2026-10-x.csv.gz").read_bytes()
    import gzip
    texto = gzip.decompress(bruto).decode("utf-8")
    assert "SECRETO" not in texto and "SEGREDO" not in texto and "BANDEIRA" not in texto    # o resumo não guarda posto nem endereço
    r = processar(agg, tmp_path / "precos.json")
    assert r["meses"] == ["2026-09", "2026-10"] and r["produtos"] == ["GASOLINA", "ETANOL", "DIESEL S10"]    # GLP fora; erro de R$ 599 descartado
    g = r["brasil"]["GASOLINA"]
    assert g["n"] == [8, 8] and abs(g["m"][1] - 6.45) < 1e-9
    sp = next(m for m in r["municipios"] if m["uf"] == "SP")
    assert sp["serie"]["GASOLINA"]["n"] == [4, 4] and abs(sp["serie"]["GASOLINA"]["m"][1] - 6.45) < 1e-9    # média de 6,3 / 6,4 / 6,5 / 6,6
    assert sp["ultimo"]["GASOLINA"] == {"min": 6.3, "max": 6.6, "postos": 4}
    rs = next(m for m in r["municipios"] if m["uf"] == "RS")
    assert rs["nome"] == "Sant'Ana do Livramento"       # a ANP escreve Santana; o nome oficial é do IBGE


def test_precos_paginas_so_com_dados_sem_posto_e_com_busca(base, tmp_path):
    from robo.executar import _leve
    from robo.precos import processar, resumir
    ag = _leve(agregar(base))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(ag, ensure_ascii=False, default=str), "utf-8")
    gerar(proc, tmp_path / "s1", limpos=True)
    assert not (tmp_path / "s1" / "precos").exists() and 'href="precos/"' not in (tmp_path / "s1" / "index.html").read_text("utf-8")

    agg = tmp_path / "agg"
    agg.mkdir()
    for mes in ("2026-09", "2026-10"):
        _csv_precos(tmp_path / f"{mes}.csv", mes)
        resumir(tmp_path / f"{mes}.csv").to_csv(agg / f"{mes}.csv.gz", index=False, compression="gzip")
    processar(agg, proc / "precos.json")
    gerar(proc, tmp_path / "s2", limpos=True)
    site = tmp_path / "s2"
    pagina = (site / "precos" / "index.html").read_text("utf-8")
    assert "Preço dos combustíveis no Brasil" in pagina and "Gasolina mais barata" in pagina and 'id="busca-mun"' in pagina
    assert 'href="precos/"' in (site / "index.html").read_text("utf-8") and "/precos/" in (site / "sitemap.xml").read_text("utf-8")
    mun = (site / "precos" / "sao-paulo" / "sao-paulo" / "index.html").read_text("utf-8")
    assert "Preço da gasolina em São Paulo (SP)" in mun and "R$ 6,15" in mun and "Etanol" in mun
    assert (site / "precos" / "rio-grande-do-sul" / "sant-ana-do-livramento" / "index.html").exists()
    todo = "".join(p.read_text("utf-8") for p in (site / "precos").rglob("index.html"))
    assert "SECRETO" not in todo and "SEGREDO" not in todo and "BANDEIRA" not in todo


def test_continue_explorando_so_liga_a_paginas_que_existem_e_nunca_a_propria():
    from gerador.familia import continuar
    sem = continuar("precos", "precos/", {})
    assert [c for c in sem if c["portal"] == "oleo"] == []              # sem refino nem royalties, só cartões de outros portais
    assert any(c["url"] == "https://comexemnumeros.com.br/produto/2710/" for c in sem)
    com = continuar("precos", "precos/", {"refino": True, "royalties": True, "precos": True})
    assert {c["caminho"] for c in com if c["portal"] == "oleo"} == {"refino/", "royalties/"}
    assert all(c.get("caminho") != "precos/" for c in com) and len(com) <= 4
    assert continuar("sobre", "sobre/", {}) == []


# ---------------- resumo mensal ----------------

def _ag_sintetico(boe, per0="2025-01"):
    from gerador.gerar import _mes_antes
    periodos = [_mes_antes(per0, -i) for i in range(len(boe))]
    serie = {"periodo": periodos, "boe": boe, "petroleo": [None if b is None else b * 0.45 for b in boe], "gas": [None if b is None else b / 12 for b in boe]}
    return {"ultimo": periodos[-1], "brasil": {"serie": serie}, "operadores": [], "bacias": []}


def test_resumo_meses_e_manchete():
    from gerador.gerar import _mes_antes, resumo_dados, resumo_manchete
    assert _mes_antes("2026-01", 1) == "2025-12" and _mes_antes("2026-03", 12) == "2025-03"
    base_ = [4000 + i for i in range(13)]
    # sobe e é recorde (há 13 meses de histórico)
    m = resumo_manchete(resumo_dados(_ag_sintetico(base_ + [4100]), "2026-02"))
    assert "sobe" in m and "maior em 14 meses" in m and "4,10 milhões de boe/d" in m
    # cai: sem a palavra recorde
    m = resumo_manchete(resumo_dados(_ag_sintetico(base_ + [3900]), "2026-02"))
    assert "cai 2,8%" in m and "maior" not in m
    # praticamente igual: estável, sem percentual
    m = resumo_manchete(resumo_dados(_ag_sintetico(base_ + [4012.1]), "2026-02"))
    assert "fica estável" in m and "%" not in m
    # o primeiro mês da série não tem mês anterior: sem resumo
    assert resumo_dados(_ag_sintetico(base_), "2025-01") is None


def test_paginas_de_resumo(base, tmp_path):
    import json
    from robo.executar import _leve
    proc = tmp_path / "proc"
    proc.mkdir()
    ag = _leve(agregar(base))
    (proc / "agregados.json").write_text(json.dumps(ag, ensure_ascii=False, default=str), "utf-8")
    paginas = gerar(proc, tmp_path / "site", teste=True)
    ultimo = ag["ultimo"]
    assert "resumo/" in paginas and f"resumo/{ultimo}/" in paginas
    site = tmp_path / "site"
    html = (site / "resumo" / ultimo / "index.html").read_text("utf-8")
    assert html.count("<h1") == 1 and "Operadoras em destaque" in html and "Para ir além" in html and 'data-copiar="post"' in html
    assert "Produção de petróleo e gás do Brasil" in html and f"/resumo/{ultimo}/" in html
    # kit de redes sociais: texto do X cabe em 280 (link conta 23) e o script comum põe o botão "Publicar no X"
    import html as _h
    x = _h.unescape(re.search(r'<textarea id="post-x"[^>]*>(.*?)</textarea>', html, re.S).group(1))
    assert 0 < len(re.sub(r"https?://\S+", "x" * 23, x)) <= 280 and 'data-copiar="post-x"' in html
    js = (site / "familia.js").read_text("utf-8")
    assert "https://x.com/intent/tweet?text=" in js and "data-publicar-x" in js
    assert '"#17758C"' in (site / "familia-compartilhar.js").read_text("utf-8")
    # link no menu e na home, e o mês mais recente fica no topo da lista
    assert 'href="../../resumo/index.html"' in html or 'href="../../resumo/"' in html
    home = (site / "index.html").read_text("utf-8")
    assert "resumo-home" in home and f"resumo/{ultimo}/" in home
    lista = (site / "resumo" / "index.html").read_text("utf-8")
    assert lista.index(f"resumo/{ultimo}/") == min(lista.index(f"resumo/{p.split('/')[1]}/") for p in paginas if p.startswith("resumo/") and p != "resumo/")
    assert "resumo/" in (site / "sitemap.xml").read_text("utf-8")


def test_resumo_com_buracos_na_serie():
    """Dado real tem meses sem valor (None): máximo, mínimo e variações não podem quebrar."""
    from gerador.gerar import resumo_dados, resumo_manchete
    from gerador.resumo import montar
    # buraco no começo da série (era o que derrubava o site: max() com None no histórico)
    boe = [None, None] + [4000 + i for i in range(12)] + [4100]
    ag = _ag_sintetico(boe)
    ag["brasil"]["varmes_terra"] = None
    ag["rankings"] = {"todos": {"altas": [], "quedas": []}}
    d = resumo_dados(ag, ag["ultimo"])
    assert d is not None and d["extremo"]["tipo"] == "maior" and "sobe" in resumo_manchete(d)
    assert montar(ag, ag["ultimo"], "https://x.test")["x"]
    # buraco no meio e logo antes do mês: sem mês anterior comparável, sem resumo (e sem erro)
    ag = _ag_sintetico([4000 + i for i in range(13)] + [None, 4100, 4120])
    assert resumo_dados(ag, "2026-03") is None
    assert resumo_dados(ag, "2026-02") is None
    d = resumo_dados(ag, ag["ultimo"])
    assert d is None or d["extremo"] is None or d["extremo"]["tipo"] in ("maior", "menor")


def _ag_resumo():
    """Dados sintéticos com operadoras, bacias e campos para exercitar o texto do resumo."""
    from gerador.gerar import _mes_antes
    n = 14
    periodos = [_mes_antes("2025-01", -i) for i in range(n)]

    def ent(nome, base, ult):
        boe = [base] * (n - 1) + [ult]
        return {"nome": nome, "slug": nome.lower().replace(" ", "-"), "serie": {"periodo": periodos, "boe": boe}}
    total = [5000 + 10 * i for i in range(n - 1)] + [5400]
    ops = [ent("Grande", 4000, 4350), ent("Média", 300, 330), ent("Subindo", 40, 60), ent("Caindo", 200, 150), ent("Pequena", 2, 3)]
    # a "Média" passa a "Subindo"? não: aqui "Caindo" perde uma posição para "Subindo"? 150 > 60, então só o valor muda
    bacias = [ent("Santos", 3000, 3300), ent("Campos", 1000, 990)]
    campos = [{"nome": f"Campo {i}", "slug": f"campo-{i}", "grupo": "Grande", "delta_mes": 120000 - 30000 * i, "varmes": 0.1 - 0.02 * i, "boe": 500000}
              for i in range(5)]
    quedas = [{"nome": f"Cai {i}", "slug": f"cai-{i}", "grupo": "Caindo", "delta_mes": -8000 + 1000 * i, "varmes": -0.05, "boe": 90000} for i in range(5)]
    return {"ultimo": periodos[-1], "operadores": ops, "bacias": bacias,
            "brasil": {"serie": {"periodo": periodos, "boe": total, "petroleo": [b * 0.8 for b in total], "gas": [b / 10 for b in total]},
                       "varmes_terra": -0.02, "boe_terra": 200},
            "rankings": {"todos": {"altas": campos, "quedas": quedas}}}


def test_resumo_extremo_por_sequencia_e_janela():
    from gerador.resumo import _extremo
    # maior desde 8 meses atrás (o último valor igual ou maior foi no índice 5, e o atual é o índice 13)
    v = [10, 11, 12, 13, 14, 20, 1, 2, 3, 4, 5, 6, 7, 19]
    assert _extremo(v, 13) == {"tipo": "maior", "n": 8, "janela": False}
    # recorde desde o começo da série (14 meses)
    assert _extremo(list(range(14)), 13) == {"tipo": "maior", "n": 14, "janela": True}
    # menor em 6 ou mais meses
    assert _extremo([3, 9, 9, 9, 9, 9, 9, 9, 4], 8) == {"tipo": "menor", "n": 8, "janela": False}
    # sequência curta demais: nada a dizer
    assert _extremo([5, 3, 9, 8, 7, 9, 6], 6) is None


def test_resumo_estrutura_do_texto():
    from gerador.resumo import montar
    ag = _ag_resumo()
    R = montar(ag, ag["ultimo"], "https://oleoemnumeros.com.br")
    assert "milhões de boe/d" in R["abertura"] and "alta de" in R["abertura"] and "sobre" in R["abertura"]
    # operadoras em destaque: 2 ou 3, sem repetir, e a "Pequena" (abaixo de 10 mil boe/d) fica de fora
    nomes = [o["nome"] for o in R["ops_destaque"]]
    assert 2 <= len(nomes) <= 3 and len(set(nomes)) == len(nomes) and "Pequena" not in nomes
    assert nomes[0] == "Grande" and "Caindo" in nomes
    assert all(o["texto"].endswith(".") and "mil boe/d" in o["texto"] for o in R["ops_destaque"])
    # insight: 1 ou 2 frases, citando números
    assert R["insight"] and R["insight"].count(". ") <= 1 and any(ch.isdigit() for ch in R["insight"])
    # links: rankings, produção total e campos citados
    caminhos = [k for k, _ in R["links"]]
    assert "rankings/" in caminhos and "" in caminhos and "campos/campo-0/" in caminhos
    # mês antigo: sem campos, mas com abertura e operadoras
    R0 = montar(ag, ag["brasil"]["serie"]["periodo"][-2], "https://x.test")
    assert R0 and not R0["altas"] and not R0["quedas"] and R0["abertura"]


def test_resumo_mudanca_de_posicao_aparece():
    from gerador.gerar import _mes_antes
    from gerador.resumo import montar
    ag = _ag_resumo()
    # "Subindo" passa a "Caindo": 60 contra 150 não troca; força a troca
    for o in ag["operadores"]:
        if o["nome"] == "Subindo":
            o["serie"]["boe"][-1] = 400
    R = montar(ag, ag["ultimo"], "https://x.test")
    sub = next(o for o in R["ops_destaque"] if o["nome"] == "Subindo")
    assert "posição" in sub["texto"] and "da 4ª para a 2ª" in sub["texto"]


def test_resumo_post_x_cabe_em_280_caracteres():
    from gerador.resumo import LIMITE_X, _peso_x, montar
    ag = _ag_resumo()
    # nomes de campo enormes forçam o corte dos extras
    for c in ag["rankings"]["todos"]["altas"] + ag["rankings"]["todos"]["quedas"]:
        c["nome"] = c["nome"] + " " + "Nome muito comprido " * 3
    R = montar(ag, ag["ultimo"], "https://oleoemnumeros.com.br")
    url = "https://oleoemnumeros.com.br/resumo/" + ag["ultimo"] + "/"
    assert _peso_x(R["x"], url) <= LIMITE_X and url in R["x"]
    R2 = montar(_ag_resumo(), ag["ultimo"], "https://oleoemnumeros.com.br")
    assert "Campo 0" in R2["x"] and _peso_x(R2["x"], url) <= LIMITE_X
    # LinkedIn: versão maior, com as três altas e o link
    assert len(R2["linkedin"]) > len(R2["x"]) and "Campo 2" in R2["linkedin"] and url in R2["linkedin"]


def test_busca_do_cabecalho_e_trilha_em_json_ld(base, tmp_path):
    import json
    from robo.executar import _leve
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "agregados.json").write_text(json.dumps(_leve(agregar(base)), ensure_ascii=False, default=str), "utf-8")
    site = tmp_path / "site"
    gerar(proc, site, teste=False, limpos=True, url="https://oleoemnumeros.com.br")
    html = (site / "campos" / "campo-exemplo-3" / "index.html").read_text("utf-8")
    # busca: script com o índice e a raiz relativa; índice cobre as páginas publicadas
    assert (site / "familia-busca.js").exists()
    assert 'familia-busca.js?v=' in html and 'data-indice="../../busca-geral.json?v=' in html and 'data-raiz="../../"' in html
    indice = json.loads((site / "busca-geral.json").read_text("utf-8"))
    enderecos = {i[1] for i in indice}
    assert "/campos/campo-exemplo-3/" in enderecos and "/" in enderecos
    assert all(len(i) == 3 and i[0] and (site / i[1].lstrip("/") / "index.html").exists() for i in indice)
    # trilha visível repetida em JSON-LD, com endereços absolutos
    ld = [json.loads(x) for x in __import__("re").findall(r'<script type="application/ld\+json">(.*?)</script>', html)]
    trilha = next(x for x in ld if x["@type"] == "BreadcrumbList")["itemListElement"]
    assert [e["name"] for e in trilha][0] == "Campos" and trilha[-1]["item"] == "https://oleoemnumeros.com.br/campos/campo-exemplo-3/"
    assert all(e["item"].startswith("https://oleoemnumeros.com.br/") and "index.html" not in e["item"] for e in trilha)
    # título longo dispensa o sufixo com o nome do site
    titulo = __import__("re").search(r"<title>(.*?)</title>", html).group(1)
    assert len(titulo) <= 75 and (len(titulo) <= 75 and ("| Óleo em Números" in titulo) == (len(titulo.split(" | ")[0]) <= 55))

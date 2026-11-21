"""Resumo do mês: tudo o que a página /resumo/AAAA-MM/ e os textos para as redes dizem sai daqui, sem escrever nada à mão.

Os números vêm de agregados.json (não há histórico a guardar): totais do Brasil, operadoras e bacias valem para qualquer mês
da série nacional; campos e terra só para o mês mais recente, porque a lista de campos é a de hoje e um campo que parou de
produzir não aparece nela.

O texto é montado por regras e frases-modelo, não por IA: não inventa número, não depende de chave nem de custo e dá para
testar. Para mudar o tom de uma frase, mexa nas funções `abertura`, `insight` e `operadoras_destaque`.
"""
from __future__ import annotations

from robo.util import fmt, fmt_inteligente, fmt_var, mes_extenso

DESCONHECIDO = "Não identificado"  # campos sem operadora informada nos dados da ANP

ESTAVEL = 0.005          # variação menor que 0,5% conta como estável
MIN_MESES_EXTREMO = 6    # "a maior/menor em N meses" só aparece quando N chega a isso
MIN_MESES_JANELA = 12    # recorde desde o início da série só vale com pelo menos um ano de histórico
OP_RELEVANTE = 10.0      # mil boe/d: operadoras menores que isso não entram nos destaques
LIMITE_X = 280           # caracteres do post no X
PESO_LINK_X = 23         # o X conta qualquer link como 23 caracteres


# ---------------- base ----------------

def _mes_antes(per: str, n: int) -> str:
    ano, mes = map(int, per.split("-"))
    k = ano * 12 + mes - 1 - n
    return f"{k // 12}-{k % 12 + 1:02d}"


def _na_serie(serie: dict, chave: str, per: str):
    try:
        return serie[chave][serie["periodo"].index(per)]
    except (KeyError, ValueError, IndexError):
        return None


def _razao(a, b):
    """a/b - 1, ou None quando falta valor ou a base é zero."""
    return None if a is None or not b else a / b - 1


def _sinal(x, casas=0):
    return ("+" if x > 0 else "−" if x < 0 else "") + fmt(abs(x), casas)


def _lista(nomes: list[str]) -> str:
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _ordinal(n: int) -> str:
    return f"{n}ª"


def _movimentos(itens, per: str, ant: str, ano: str, exclui=()):
    out = []
    for e in itens:
        if e["nome"] in exclui:
            continue
        v, a = _na_serie(e["serie"], "boe", per), _na_serie(e["serie"], "boe", ant)
        if v is None or a is None:
            continue
        out.append({"nome": e["nome"], "slug": e["slug"], "boe": v, "ant": a, "delta": v - a, "var": _razao(v, a),
                    "var12": _razao(v, _na_serie(e["serie"], "boe", ano))})
    return out


def _extremo(valores: list, idx: int):
    """Diz se o valor de `idx` é o maior ou o menor em N meses: {'tipo', 'n', 'janela'} ou None.

    N é quantos meses para trás o valor se sustenta como o mais alto (ou o mais baixo). Quando nenhum mês da série o supera,
    N é o tamanho da série ('janela': True) e o texto avisa que é o período coberto pelo site."""
    v = valores[idx]
    if v is None:
        return None
    melhor = None
    for tipo, bate in (("maior", lambda x: x >= v), ("menor", lambda x: x <= v)):
        j = next((k for k in range(idx - 1, -1, -1) if valores[k] is not None and bate(valores[k])), None)
        if j is None:
            n, janela = idx + 1, True
            if n < MIN_MESES_JANELA:
                continue
        else:
            n, janela = idx - j, False
            if n < MIN_MESES_EXTREMO:
                continue
        if melhor is None or n > melhor["n"]:
            melhor = {"tipo": tipo, "n": n, "janela": janela}
    return melhor


def resumo_dados(ag: dict, per: str) -> dict | None:
    serie = ag["brasil"]["serie"]
    ant, ano = _mes_antes(per, 1), _mes_antes(per, 12)
    d = {"per": per, "ant": ant, "ano": ano, "ultimo": per == ag["ultimo"]}
    for k in ("boe", "petroleo", "gas"):
        v = _na_serie(serie, k, per)
        d[k] = v
        d[k + "_mes"] = _razao(v, _na_serie(serie, k, ant))
        d[k + "_12"] = _razao(v, _na_serie(serie, k, ano))
    if d["boe"] is None or d["boe_mes"] is None:
        return None
    d["delta"] = d["boe"] - _na_serie(serie, "boe", ant)
    idx = serie["periodo"].index(per)
    d["extremo"] = _extremo(serie["boe"], idx)
    d["desde"] = serie["periodo"][0]
    d["janela"] = idx + 1
    ops = _movimentos(ag["operadores"], per, ant, ano, exclui=(DESCONHECIDO,))
    for i, o in enumerate(sorted(ops, key=lambda o: -o["boe"]), 1):
        o["pos"] = i
    for i, o in enumerate(sorted(ops, key=lambda o: -o["ant"]), 1):
        o["pos_ant"] = i
    d["operadoras"] = sorted(ops, key=lambda o: o["pos"])
    bac = _movimentos(ag.get("bacias", []), per, ant, ano)
    d["bacias"] = sorted(bac, key=lambda o: -o["boe"])
    if d["ultimo"]:
        br = ag["brasil"]
        d["terra_mes"], d["terra"] = br.get("varmes_terra"), br.get("boe_terra")
    return d


def resumo_meses(ag: dict) -> list[str]:
    """Meses com resumo: todos os da série nacional, do segundo em diante (precisa do mês anterior)."""
    return [p for p in ag["brasil"]["serie"]["periodo"][1:] if resumo_dados(ag, p)]


def campos_do_mes(ag: dict, d: dict) -> tuple[list[dict], list[dict]]:
    """Os 5 campos que mais cresceram e os 5 que mais caíram (só para o mês mais recente)."""
    if not d["ultimo"]:
        return [], []
    r = ag["rankings"]["todos"]
    return ([c for c in r["altas"] if c["delta_mes"] and c["delta_mes"] > 0][:5],
            [c for c in r["quedas"] if c["delta_mes"] and c["delta_mes"] < 0][:5])


# ---------------- textos ----------------

def _total(d: dict) -> str:
    mil = d["boe"]
    return f"{fmt(mil / 1000, 2)} {'milhões' if mil / 1000 >= 2 else 'milhão'} de boe/d"


def _comparado(v, ref: str) -> str | None:
    """'alta de 1,9% sobre agosto de 2026', 'queda de ...' ou 'estável (0,1%) sobre ...'."""
    if v is None:
        return None
    if abs(v) < ESTAVEL:
        return f"estável ({fmt_var(v)}) sobre {ref}"
    return f"{'alta' if v > 0 else 'queda'} de {fmt(abs(v) * 100, 1)}% sobre {ref}"


def frase_extremo(d: dict) -> str | None:
    e = d.get("extremo")
    if not e:
        return None
    base = f"É a {e['tipo']} produção em {e['n']} meses"
    return base + (f", o período que o site cobre (desde {mes_extenso(d['desde'])})." if e["janela"] else ".")


def manchete(d: dict) -> str:
    mes = mes_extenso(d["per"])
    v = d["boe_mes"]
    if abs(v) < ESTAVEL:
        return f"Produção de petróleo e gás do Brasil fica estável em {mes}, em {_total(d)}"
    verbo = "sobe" if v > 0 else "cai"
    e = d.get("extremo")
    base = f"Produção de petróleo e gás do Brasil {verbo} {fmt(abs(v) * 100, 1)}% em {mes}"
    if e and e["n"] >= MIN_MESES_EXTREMO and ((v > 0 and e["tipo"] == "maior") or (v < 0 and e["tipo"] == "menor")):
        return f"{base} e é a {e['tipo']} em {e['n']} meses: {_total(d)}"
    return f"{base}, para {_total(d)}"


def abertura(d: dict) -> str:
    """Parágrafo de abertura: total, variação sobre o mês anterior e sobre 12 meses, e recorde quando houver."""
    def sentido(v):
        return None if v is None else "estável" if abs(v) < ESTAVEL else "alta" if v > 0 else "queda"
    m, a = d["boe_mes"], d["boe_12"]
    texto = f"A produção de petróleo e gás do Brasil foi de {_total(d)} em {mes_extenso(d['per'])} ({fmt(d['boe'])} mil boe/d)"
    p1 = _comparado(m, mes_extenso(d["ant"]))
    if a is None:
        partes = [p1]
    elif sentido(a) == sentido(m) and sentido(a) != "estável":
        partes = [p1, f"de {fmt(abs(a) * 100, 1)}% sobre {mes_extenso(d['ano'])}"]   # mesmo sentido: "alta de X e de Y"
    else:
        partes = [p1, _comparado(a, mes_extenso(d["ano"]))]
    partes = [p for p in partes if p]
    texto += (", " + " e ".join(partes)) if partes else ""
    texto += "."
    ext = frase_extremo(d)
    return f"{texto} {ext}" if ext else texto


def operadoras_destaque(d: dict) -> list[dict]:
    """2 ou 3 operadoras com os movimentos mais relevantes: maior alta e maior queda em mil boe/d e, para a terceira vaga, a de
    maior variação percentual (a partir de 5%) ou, na falta, uma mudança de posição que mexa no topo. Operadoras abaixo de
    OP_RELEVANTE mil boe/d ficam de fora."""
    ops = [o for o in d["operadoras"] if max(o["boe"], o["ant"]) >= OP_RELEVANTE and o["var"] is not None]
    if not ops:
        return []
    escolhidas: list[tuple[dict, str]] = []

    def pega(o, motivo):
        if o and all(o is not x for x, _ in escolhidas):
            escolhidas.append((o, motivo))

    alta = max(ops, key=lambda o: o["delta"])
    queda = min(ops, key=lambda o: o["delta"])
    if alta["delta"] > 0:
        pega(alta, "alta")
    if queda["delta"] < 0:
        pega(queda, "queda")
    forte = [o for o in ops if abs(o["var"]) >= 0.05 and abs(o["delta"]) >= 1 and all(o is not x for x, _ in escolhidas)]
    if forte:
        pega(max(forte, key=lambda o: abs(o["var"])), "forte")
    if len(escolhidas) < 3:
        # mudança de posição só conta se mexe no topo (até a 5ª) ou anda duas casas ou mais
        mudou = sorted((o for o in ops if o["pos"] != o["pos_ant"] and (min(o["pos"], o["pos_ant"]) <= 5 or abs(o["pos"] - o["pos_ant"]) >= 2)),
                       key=lambda o: (-abs(o["pos"] - o["pos_ant"]), -o["boe"]))
        if mudou:
            pega(mudou[0], "posicao")
    if len(escolhidas) < 2:
        resto = [o for o in ops if all(o is not x for x, _ in escolhidas)]
        pega(max(resto, key=lambda o: abs(o["delta"])) if resto else None, "outra")

    saida = []
    for o, motivo in escolhidas[:3]:
        dados = (f"{fmt_inteligente(o['boe'])} mil boe/d, {fmt_var(o['var'])} no mês "
                 f"({_sinal(o['delta'], 1)} mil boe/d)")
        if o["pos"] != o["pos_ant"]:
            nota = f"{'subiu' if o['pos'] < o['pos_ant'] else 'caiu'} da {_ordinal(o['pos_ant'])} para a {_ordinal(o['pos'])} posição"
        elif motivo == "alta":
            nota = "maior alta entre as operadoras"
        elif motivo == "queda":
            nota = "maior queda entre as operadoras"
        elif motivo == "forte":
            nota = "variação percentual mais forte entre as operadoras" if o["var"] > 0 else "queda percentual mais forte entre as operadoras"
        else:
            nota = f"segue na {_ordinal(o['pos'])} posição"
        if motivo in ("alta", "queda") and o["pos"] != o["pos_ant"]:
            nota = ("maior alta entre as operadoras e " if motivo == "alta" else "maior queda entre as operadoras e ") + nota
        saida.append({"nome": o["nome"], "slug": o["slug"], "motivo": motivo, "pos": o["pos"], "boe": o["boe"],
                      "delta": o["delta"], "var": o["var"],
                      "texto": f"{o['nome']}: {dados}; {nota}."})
    return saida


def insight(d: dict, altas: list[dict], quedas: list[dict]) -> str:
    """Uma ou duas frases sobre o que explica o mês. Usa a primeira regra que se aplica e, se couber, mais uma."""
    from .gerar import titulo_bacia  # import tardio: gerar.py importa este módulo

    delta = d["delta"]
    achados: list[str] = []
    sobe = delta > 0
    palavra = "avanço" if sobe else "recuo"
    if abs(delta) >= 5:
        campos = (altas if sobe else quedas)[:3]
        if len(campos) == 3:
            soma = sum(abs(c["delta_mes"]) for c in campos) / 1000
            if soma / abs(delta) >= 0.5:
                nomes = _lista([c["nome"] for c in campos])
                if soma / abs(delta) > 1:
                    achados.append(f"{nomes} somaram {fmt(soma)} mil boe/d {'de alta' if sobe else 'de queda'}, mais do que o saldo do mês "
                                   f"({_sinal(delta)} mil boe/d): outros campos andaram no sentido oposto.")
                else:
                    achados.append(f"{nomes} respondem por {fmt(soma / abs(delta) * 100)}% do {palavra} de {fmt(abs(delta))} mil boe/d no mês.")
        if not achados:
            bacias = [b for b in d["bacias"] if b["delta"] * delta > 0]
            if bacias:
                b = max(bacias, key=lambda x: abs(x["delta"]))
                if b["delta"] / delta >= 0.5:
                    achados.append(f"A {titulo_bacia(b['nome'])} concentrou {fmt(min(b['delta'] / delta, 1) * 100)}% do {palavra} "
                                   f"({_sinal(b['delta'], 1)} mil boe/d).")
    tm, vm = d.get("terra_mes"), d["boe_mes"]
    if tm is not None and abs(tm) >= ESTAVEL and abs(vm) >= ESTAVEL and tm * vm < 0 and d.get("terra") is not None:
        achados.append(f"Em terra a produção {'subiu' if tm > 0 else 'caiu'} {fmt(abs(tm) * 100, 1)}% no mês, na contramão do total.")
    pm, gm = d["petroleo_mes"], d["gas_mes"]
    if pm is not None and gm is not None and abs(pm) >= ESTAVEL and abs(gm) >= ESTAVEL and pm * gm < 0:
        achados.append(f"O petróleo {'subiu' if pm > 0 else 'caiu'} {fmt(abs(pm) * 100, 1)}%, enquanto o gás natural "
                       f"{'subiu' if gm > 0 else 'caiu'} {fmt(abs(gm) * 100, 1)}%.")
    if not achados and d["operadoras"]:
        o = d["operadoras"][0]
        achados.append(f"{o['nome']} opera {fmt(o['boe'] / d['boe'] * 100)}% da produção do país.")
    return " ".join(achados[:2])


def links(d: dict, altas: list[dict], quedas: list[dict], ops_dest: list[dict]) -> list[tuple[str, str]]:
    """Links do rodapé do resumo: (caminho, rótulo)."""
    L = [("rankings/", "Rankings completos de campos"), ("", "Produção total do Brasil"), ("operadoras/", "Todas as operadoras")]
    L += [(f"campos/{c['slug']}/", f"Campo {c['nome']}") for c in (altas[:2] + quedas[:2])]
    L += [(f"operadoras/{o['slug']}/", o["nome"]) for o in ops_dest]
    vistos, saida = set(), []
    for k, rot in L:
        if k not in vistos:
            vistos.add(k)
            saida.append((k, rot))
    return saida


# ---------------- redes sociais ----------------

def _peso_x(texto: str, url: str) -> int:
    return len(texto.replace(url, "x" * PESO_LINK_X))


def post_x(site_url: str, d: dict, altas: list[dict], quedas: list[dict]) -> str:
    """Post curto para o X: cabe em 280 caracteres (o link conta 23). Tira os extras, do menos para o mais importante, até caber."""
    url = f"{site_url}/resumo/{d['per']}/"
    cabeca = (f"Produção de petróleo e gás do Brasil em {mes_extenso(d['per'])}: {fmt(d['boe'] / 1000, 2)} milhões de boe/d "
              f"({fmt_var(d['boe_mes'])} no mês")
    cabeca += f", {fmt_var(d['boe_12'])} em 12 meses)." if d["boe_12"] is not None else ")."
    e = d.get("extremo")
    extremo = f"A {e['tipo']} em {e['n']} meses." if e else ""
    campos = ""
    if altas and quedas:
        campos = (f"Altas: {altas[0]['nome']} ({_sinal(altas[0]['delta_mes'] / 1000, 0)} mil boe/d). "
                  f"Quedas: {quedas[0]['nome']} ({_sinal(quedas[0]['delta_mes'] / 1000, 0)} mil boe/d).")
    tags = ""   # posts do X sem hashtags
    for usa in ((1, 1, 1), (1, 1, 0), (1, 0, 1), (1, 0, 0), (0, 0, 0)):
        corpo = [cabeca] + ([extremo] if usa[0] and extremo else []) + ([campos] if usa[1] and campos else [])
        texto = " ".join(corpo) + f"\n\n{url}" + (f"\n{tags}" if usa[2] and tags else "")
        if _peso_x(texto, url) <= LIMITE_X:
            return texto
    return f"{cabeca}\n\n{url}"


def post_linkedin(site_url: str, d: dict, altas: list[dict], quedas: list[dict], ops_dest: list[dict], texto_insight: str) -> str:
    """Versão maior, para o LinkedIn: abertura, três altas, três quedas, operadoras, insight e link."""
    url = f"{site_url}/resumo/{d['per']}/"
    L = [f"Produção de petróleo e gás no Brasil, {mes_extenso(d['per'])}: {_total(d)}", "", abertura(d), ""]
    if altas or quedas:
        L.append("Destaques entre os campos:")
        if altas:
            L.append("• Maiores altas: " + "; ".join(f"{c['nome']} ({c['grupo']}, {_sinal(c['delta_mes'] / 1000, 1)} mil boe/d)" for c in altas[:3]))
        if quedas:
            L.append("• Maiores quedas: " + "; ".join(f"{c['nome']} ({c['grupo']}, {_sinal(c['delta_mes'] / 1000, 1)} mil boe/d)" for c in quedas[:3]))
        L.append("")
    if ops_dest:
        L.append("Operadoras em destaque:")
        L += [f"• {o['texto']}" for o in ops_dest[:3]]
        L.append("")
    if texto_insight:
        L += [texto_insight, ""]
    L += ["Resumo completo, com rankings de campos, operadoras e bacias:", url, "",
          "#petroleo #gasnatural #energia #ANP", "Fonte: ANP, dados organizados pelo Óleo em Números."]
    return "\n".join(L)


# ---------------- tudo junto ----------------

def montar(ag: dict, per: str, site_url: str) -> dict | None:
    """Tudo o que a página e as redes precisam para um mês (None se o mês não tem resumo)."""
    d = resumo_dados(ag, per)
    if not d:
        return None
    altas, quedas = campos_do_mes(ag, d)
    ops = operadoras_destaque(d)
    ins = insight(d, altas, quedas)
    return {"d": d, "manchete": manchete(d), "abertura": abertura(d), "altas": altas, "quedas": quedas, "ops_destaque": ops,
            "insight": ins, "links": links(d, altas, quedas, ops),
            "x": post_x(site_url, d, altas, quedas), "linkedin": post_linkedin(site_url, d, altas, quedas, ops, ins)}

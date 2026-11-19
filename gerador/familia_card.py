"""Família em Números: imagem do resumo (1200x630) para o X, o LinkedIn e o WhatsApp.

O mesmo arquivo vale para todos os portais (Óleo, Comex, Voo, Economia e os próximos): o desenho é um só e
muda apenas a cor do portal. Não coloque nada específico de um site aqui; cada gerador monta os números e chama
`gera_card`.

Desenho, de cima para baixo: faixa na cor do portal, nome do portal, título, até quatro números grandes com a
variação (verde quando é boa notícia, vermelho quando é ruim, cinza quando é neutra), gráfico de barras dos
últimos meses (uma ou mais séries, com legenda) e a linha da fonte com o endereço da página.

Sem o Pillow, `gera_card` devolve False e o site sai igual, só sem a imagem.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

W, H = 1200, 630
M = 60                                   # margem lateral
FUNDO, TINTA, APAGADO = (238, 242, 244), (15, 29, 43), (86, 104, 121)
BOM, RUIM = (23, 128, 58), (180, 35, 24)
LINHA = (205, 213, 220)
_PASTAS = [Path(__file__).resolve().parent.parent / "site" / "fontes",      # Comex, Voo, Economia
           Path(__file__).resolve().parent / "fontes",                       # Óleo
           Path("/usr/share/fonts/truetype/dejavu")]


def _rgb(cor) -> tuple:
    if isinstance(cor, tuple):
        return cor
    c = cor.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lum(c) -> float:
    def canal(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (canal(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def cor_texto(cor) -> tuple:
    """A cor do portal, escurecida até ficar legível como texto sobre o fundo (contraste de pelo menos 4,5)."""
    c = _rgb(cor)
    while _contraste(c, FUNDO) < 4.5 and sum(c) > 0:
        c = tuple(max(0, int(v * 0.9)) for v in c)
    return c


def _fonte(negrito: bool, tam: int):
    nome = "DejaVuSans-Bold.ttf" if negrito else "DejaVuSans.ttf"
    for p in _PASTAS:
        if (p / nome).exists():
            return ImageFont.truetype(str(p / nome), tam)
    return ImageFont.load_default()


def _cabe(d, texto: str, negrito: bool, tam: int, largura: float, minimo: int = 16):
    """Maior fonte (até `tam`) em que o texto cabe na largura."""
    while tam > minimo and d.textlength(texto, font=_fonte(negrito, tam)) > largura:
        tam -= 2
    return _fonte(negrito, tam)


def gera_card(destino: Path, portal: str, cor: str, titulo: str,
              numeros: list[tuple[str, str, str, bool | None]],
              barras: list[tuple[str, list]] | None = None,
              series: list[tuple[str, str]] | None = None,
              rodape: str = "", rotulos: list[str] | None = None) -> bool:
    """Desenha a imagem do resumo e salva em `destino` (PNG).

    portal:  "Comex em Números" (a primeira palavra sai em preto, "em Números" na cor do portal)
    cor:     cor do portal, "#1F85D0"
    numeros: até 4 itens (rótulo, valor, variação, boa?) — boa = True (verde), False (vermelho), None (cinza)
    barras:  [(rótulo do mês, [valor da série 1, valor da série 2, ...])]; None num valor deixa a barra em branco
    series:  [(nome, cor)] de cada série; sem isso, uma série só, na cor do portal, sem legenda
    rodape:  "Fonte: ... · site/resumo/AAAA-MM/"
    rotulos: texto em cima de cada barra (só com uma série), por exemplo "89,4" — útil quando as barras variam pouco
    Com uma série só, os meses anteriores saem num tom mais claro e o mês do resumo, na cor cheia.
    """
    if Image is None:
        return False
    base_cor, txt_cor = _rgb(cor), cor_texto(cor)
    im = Image.new("RGB", (W, H), FUNDO)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 12], fill=base_cor)

    # nome do portal e título
    primeiro, _, resto = portal.partition(" ")
    f = _fonte(True, 34)
    d.text((M, 40), primeiro + " ", font=f, fill=TINTA)
    d.text((M + d.textlength(primeiro + " ", font=f), 40), resto, font=f, fill=txt_cor)
    d.text((M, 95), titulo, font=_cabe(d, titulo, True, 46, W - 2 * M, 28), fill=TINTA)

    # números grandes; sem gráfico, descem e ganham mais espaço
    numeros = numeros[:4]
    tem_barras = bool(barras) and any(v is not None for _, vs in barras for v in vs)
    if numeros:
        # com gráfico: uma linha de números; sem gráfico: números maiores, em duas linhas quando são quatro
        cols = len(numeros) if (tem_barras or len(numeros) <= 3) else 2
        linhas = -(-len(numeros) // cols)
        larg = (W - 2 * M) / cols
        tv = 44 if tem_barras else 56
        topos = [175] if tem_barras else ([255] if linhas == 1 else [185, 385])
        for k, (rot, val, var, boa) in enumerate(numeros):
            x, y0 = M + (k % cols) * larg, topos[k // cols]
            d.text((x, y0), rot, font=_cabe(d, rot, False, 26, larg - 16), fill=APAGADO)
            d.text((x, y0 + 35), val, font=_cabe(d, val, True, tv, larg - 16, 24), fill=TINTA)
            if var:
                cor_var = APAGADO if boa is None else (BOM if boa else RUIM)
                d.text((x, y0 + 35 + tv + 14), var, font=_cabe(d, var, True, 24, larg - 16), fill=cor_var)

    # barras dos últimos meses (aceitam valores negativos, com a linha do zero)
    if tem_barras:
        cores = [_rgb(c) for _, c in series] if series else [base_cor]
        claro = tuple(int(c + (f - c) * 0.45) for c, f in zip(cores[0], FUNDO))   # meses anteriores, série única
        if series and len(series) > 1:
            lx = W - M
            for nome, c in reversed(series):
                fl = _fonte(False, 18)
                lx -= d.textlength(nome, font=fl)
                d.text((lx, 318), nome, font=fl, fill=APAGADO)
                lx -= 22
                d.rectangle([lx, 321, lx + 14, 335], fill=_rgb(c))
                lx -= 24
        topo, fim = (372 if rotulos else 350), H - 75
        vals = [v for _, vs in barras for v in vs if v is not None]
        mx, mn = max(max(vals), 0), min(min(vals), 0)
        amp = (mx - mn) or 1
        zero = topo + (mx / amp) * (fim - topo)
        n, ns = len(barras), max(len(vs) for _, vs in barras)
        passo = (W - 2 * M) / n
        bw = passo * (0.6 if ns == 1 else 0.68 / ns)
        if mn < 0:
            d.line([M, zero, W - M, zero], fill=LINHA, width=2)
        for k, (rot, vs) in enumerate(barras):
            centro = M + k * passo + passo / 2
            x = centro - (bw * len(vs) + 2 * (len(vs) - 1)) / 2
            for j, v in enumerate(vs):
                if v is not None:
                    h = (v / amp) * (fim - topo)
                    y_a, y_b = (zero - h, zero) if v >= 0 else (zero, zero - h)
                    if y_b - y_a < 2:
                        y_a = y_b - 2
                    ultimo = k == n - 1
                    cor_b = cores[j % len(cores)] if (ns > 1 or ultimo) else claro
                    d.rounded_rectangle([x, y_a, x + bw, y_b], radius=3, fill=cor_b)
                    if rotulos and ns == 1 and k < len(rotulos) and rotulos[k]:
                        d.text((x + bw / 2, min(y_a, zero) - 6), rotulos[k], font=_fonte(ultimo, 16),
                               fill=TINTA if ultimo else APAGADO, anchor="ms")
                x += bw + 2
            ultimo = k == n - 1
            d.text((centro, fim + 26), rot, font=_fonte(ultimo, 18), fill=TINTA if ultimo else APAGADO, anchor="ms")

    if rodape:
        d.text((M, H - 28), rodape, font=_cabe(d, rodape, False, 20, W - 2 * M, 14), fill=APAGADO, anchor="lm")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.save(destino, optimize=True)
    return True


def seta(v: float) -> str:
    """'▲' para alta, '▼' para queda, '=' para zero."""
    return "▲" if v > 0 else ("▼" if v < 0 else "=")


# ---------------- bloco "Imagem para o post" no "Compartilhe este resumo" ----------------
# A imagem fica ao lado da página do resumo, como card.png (resumo/AAAA-MM/card.png), e é também a prévia do link
# (og:image) quando alguém cola o endereço no X, no LinkedIn ou no WhatsApp.

def bloco_imagem(nome_download: str, alt: str) -> str:
    """HTML da imagem com o botão "Baixar imagem". Vai antes dos textos prontos."""
    return ('<!--fam-img--><h3>Imagem para o post</h3>'
            f'<img src="card.png" alt="{html.escape(alt)}" width="1200" height="630" loading="lazy" '
            'style="display:block;width:100%;height:auto;border-radius:8px;border:1px solid rgba(128,128,128,.3)">'
            f'<div style="margin-top:8px"><a class="fam-btn" href="card.png" download="{html.escape(nome_download)}" '
            'style="text-decoration:none" data-goatcounter-click="baixar-imagem-resumo" data-goatcounter-title="Baixar imagem do resumo">'
            '<span>Baixar imagem</span></a></div><!--/fam-img-->')


def sem_imagem(pagina: str) -> str:
    """Tira o bloco da imagem quando o card.png não foi gerado (sem Pillow ou sem dados)."""
    return re.sub(r"<!--fam-img-->.*?<!--/fam-img-->", "", pagina, flags=re.S)


TEXTO_COMPARTILHE = "Imagem e textos prontos: a imagem serve para qualquer rede; o texto curto é para o X e o maior, para o LinkedIn, o WhatsApp ou o e-mail."

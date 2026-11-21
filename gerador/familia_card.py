"""Família em Números: imagem do resumo (1200x630) para o X, o LinkedIn e o WhatsApp.

O mesmo arquivo vale para todos os portais (Óleo, Comex, Voo, Economia e os próximos): o desenho é um só e
muda apenas a cor do portal. Não coloque nada específico de um site aqui; cada gerador monta os números e chama
`gera_card`.

Desenho, de cima para baixo: faixa na cor do portal, nome do portal, título, até quatro números grandes com a
variação (verde quando é boa notícia, vermelho quando é ruim, cinza quando é neutra), gráfico de barras dos
últimos meses (uma ou mais séries, com legenda) e a linha da fonte com o endereço da página.

`gera_card` grava duas imagens com os mesmos números: a horizontal (1200x630, X, LinkedIn, WhatsApp e prévia
do link) e a vertical do Instagram (1080x1350), ao lado, com "-instagram" no nome (card.png -> card-instagram.png).

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


def _barras(d, barras, series, rotulos, base_cor, x0, x1, topo, fim, y_legenda, tam):
    """Barras dos últimos meses dentro da caixa (x0..x1, topo..fim); aceitam negativos, com a linha do zero."""
    cores = [_rgb(c) for _, c in series] if series else [base_cor]
    claro = tuple(int(c + (f - c) * 0.45) for c, f in zip(cores[0], FUNDO))   # meses anteriores, série única
    if series and len(series) > 1:
        lx = x1
        fl = _fonte(False, tam)
        for nome, c in reversed(series):
            lx -= d.textlength(nome, font=fl)
            d.text((lx, y_legenda), nome, font=fl, fill=APAGADO)
            lx -= tam + 4
            d.rectangle([lx, y_legenda + 3, lx + tam - 4, y_legenda + tam - 1], fill=_rgb(c))
            lx -= tam + 6
    if rotulos:
        topo += tam + 4
    vals = [v for _, vs in barras for v in vs if v is not None]
    mx, mn = max(max(vals), 0), min(min(vals), 0)
    amp = (mx - mn) or 1
    zero = topo + (mx / amp) * (fim - topo)
    n, ns = len(barras), max(len(vs) for _, vs in barras)
    passo = (x1 - x0) / n
    bw = passo * (0.6 if ns == 1 else 0.68 / ns)
    if mn < 0:
        d.line([x0, zero, x1, zero], fill=LINHA, width=2)
    for k, (rot, vs) in enumerate(barras):
        centro = x0 + k * passo + passo / 2
        x = centro - (bw * len(vs) + 2 * (len(vs) - 1)) / 2
        ultimo = k == n - 1
        for j, v in enumerate(vs):
            if v is not None:
                h = (v / amp) * (fim - topo)
                y_a, y_b = (zero - h, zero) if v >= 0 else (zero, zero - h)
                if y_b - y_a < 2:
                    y_a = y_b - 2
                cor_b = cores[j % len(cores)] if (ns > 1 or ultimo) else claro
                d.rounded_rectangle([x, y_a, x + bw, y_b], radius=3, fill=cor_b)
                if rotulos and ns == 1 and k < len(rotulos) and rotulos[k]:
                    d.text((x + bw / 2, min(y_a, zero) - 6), rotulos[k], font=_cabe(d, rotulos[k], ultimo, tam - 2, passo - 2, 10),
                           fill=TINTA if ultimo else APAGADO, anchor="ms")
            x += bw + 2
        d.text((centro, fim + tam + 8), rot, font=_fonte(ultimo, tam), fill=TINTA if ultimo else APAGADO, anchor="ms")


def dominio(portal: str) -> str:
    """'Comex em Números' -> 'comexemnumeros.com.br'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", portal).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s) + ".com.br"


def _instagram(destino: Path, portal: str, cor: str, titulo: str, numeros, barras, series, rodape, rotulos) -> None:
    """Versão vertical (1080x1350) para o feed do Instagram: título grande, números um embaixo do outro, barras e o site."""
    w, h, m = 1080, 1350, 70
    base_cor, txt_cor = _rgb(cor), cor_texto(cor)
    im = Image.new("RGB", (w, h), FUNDO)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, 16], fill=base_cor)
    primeiro, _, resto = portal.partition(" ")
    f = _fonte(True, 44)
    d.text((m, 62), primeiro + " ", font=f, fill=TINTA)
    d.text((m + d.textlength(primeiro + " ", font=f), 62), resto, font=f, fill=txt_cor)
    tit, _, sub = titulo.partition(" · ")
    d.text((m, 150), tit, font=_cabe(d, tit, True, 72, w - 2 * m, 36), fill=TINTA)
    if sub:
        d.text((m, 240), sub, font=_cabe(d, sub, False, 44, w - 2 * m, 24), fill=APAGADO)
    numeros = numeros[:4]
    tem_barras = bool(barras) and any(v is not None for _, vs in barras for v in vs)
    passo, y = (150, 335) if tem_barras else (210, 360)
    for rot, val, var, boa in numeros:
        d.text((m, y), rot, font=_cabe(d, rot, False, 32, w - 2 * m), fill=APAGADO)
        fv = _cabe(d, val, True, 76, (w - 2 * m) * 0.55, 30)
        d.text((m, y + 42), val, font=fv, fill=TINTA)
        if var:
            cor_var = APAGADO if boa is None else (BOM if boa else RUIM)
            xv = m + d.textlength(val, font=fv) + 28
            d.text((xv, y + 42 + 76 - 8), var, font=_cabe(d, var, True, 32, w - m - xv, 16), fill=cor_var, anchor="ls")
        y += passo
    if tem_barras:
        _barras(d, barras, series, rotulos, base_cor, m, w - m, y + 40, h - 190, y + 4, 24)
    d.text((m, h - 112), dominio(portal), font=_fonte(True, 40), fill=txt_cor)
    fonte = re.sub(r"\s·\s\S+\.com\.br\S*$", "", rodape or "")
    if fonte:
        d.text((m, h - 50), fonte, font=_cabe(d, fonte, False, 26, w - 2 * m, 14), fill=APAGADO, anchor="lm")
    im.save(destino.with_name(destino.stem + "-instagram.png"), optimize=True)


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

    if tem_barras:
        _barras(d, barras, series, rotulos, base_cor, M, W - M, 350, H - 75, 318, 18)
    if rodape:
        d.text((M, H - 28), rodape, font=_cabe(d, rodape, False, 20, W - 2 * M, 14), fill=APAGADO, anchor="lm")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.save(destino, optimize=True)
    _instagram(destino, portal, cor, titulo, numeros, barras, series, rodape, rotulos)
    return True


def seta(v: float) -> str:
    """'▲' para alta, '▼' para queda, '=' para zero."""
    return "▲" if v > 0 else ("▼" if v < 0 else "=")


# ---------------- bloco "Imagem para o post" no "Compartilhe este resumo" ----------------
# A imagem fica ao lado da página do resumo, como card.png (resumo/AAAA-MM/card.png), e é também a prévia do link
# (og:image) quando alguém cola o endereço no X, no LinkedIn ou no WhatsApp.

def bloco_imagem(nome_download: str, alt: str) -> str:
    """HTML do "Compartilhe este resumo": imagem do X/LinkedIn com "Baixar imagem" e a parte do Instagram (imagem vertical,
    legenda pronta e botões). Vai antes dos textos do X e do LinkedIn. A legenda do Instagram é montada no navegador
    (familia.js) a partir do texto do LinkedIn, com o site no lugar do link, que no Instagram não é clicável.
    Se card-instagram.png faltar, a parte do Instagram some sozinha (onerror)."""
    nome_ig = re.sub(r"\.png$", "", nome_download) + "-instagram.png"
    estilo_txt = "width:100%;font:inherit;font-size:.9rem;padding:10px;border-radius:8px;border:1px solid rgba(128,128,128,.4);background:transparent;color:inherit"
    return ('<!--fam-img--><h3>Imagem para o X e o LinkedIn</h3>'
            f'<img src="card.png" alt="{html.escape(alt)}" width="1200" height="630" loading="lazy" '
            'style="display:block;width:100%;height:auto;border-radius:8px;border:1px solid rgba(128,128,128,.3)">'
            f'<div style="margin-top:8px"><a class="fam-btn" href="card.png" download="{html.escape(nome_download)}" '
            'style="text-decoration:none" data-goatcounter-click="baixar-imagem-resumo" data-goatcounter-title="Baixar imagem do resumo">'
            '<span>Baixar imagem</span></a></div>'
            '<div data-fam-ig><h3>Para o Instagram</h3>'
            f'<img src="card-instagram.png" alt="{html.escape(alt)} (formato do Instagram)" width="1080" height="1350" loading="lazy" '
            'onerror="var b=this.closest(\'[data-fam-ig]\');if(b)b.remove()" '
            'style="display:block;width:min(100%,340px);height:auto;border-radius:8px;border:1px solid rgba(128,128,128,.3)">'
            f'<textarea id="post-ig" class="fam-post" readonly rows="8" style="{estilo_txt};margin-top:8px"></textarea>'
            '<div style="margin-top:8px"><button type="button" class="fam-btn" hidden data-compartilhar-ig="card-instagram.png" '
            f'data-nome="{html.escape(nome_ig)}"><span>Compartilhar no Instagram</span></button> '
            '<button type="button" class="fam-btn" data-copiar="post-ig"><span>Copiar legenda</span></button> '
            f'<a class="fam-btn" href="card-instagram.png" download="{html.escape(nome_ig)}" style="text-decoration:none" '
            'data-goatcounter-click="baixar-imagem-instagram" data-goatcounter-title="Baixar imagem do Instagram"><span>Baixar imagem</span></a> '
            '<span data-copiar-msg class="small"></span></div>'
            '<p class="small">No celular, "Compartilhar no Instagram" copia a legenda e abre o compartilhamento com a imagem: '
            'escolha o Instagram e cole a legenda. No computador, baixe a imagem e copie a legenda.</p></div><!--/fam-img-->')


def sem_imagem(pagina: str) -> str:
    """Tira o bloco da imagem quando o card.png não foi gerado (sem Pillow ou sem dados)."""
    return re.sub(r"<!--fam-img-->.*?<!--/fam-img-->", "", pagina, flags=re.S)


TEXTO_COMPARTILHE = "Imagens e textos prontos para o X, o LinkedIn e o Instagram (o texto maior serve também para o WhatsApp e o e-mail)."

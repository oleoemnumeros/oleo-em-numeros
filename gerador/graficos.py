"""Gráficos em SVG gerados no servidor: abrem rápido, funcionam sem JavaScript
e o Google consegue ler os rótulos.

Cada gráfico sai em duas versões (larga e celular), para que as letras
continuem legíveis na tela pequena; o CSS mostra uma ou outra.
"""
import math
from html import escape

from markupsafe import Markup

from robo.util import fmt, mes_curto, mes_extenso

TINTA, CINZA, GRADE, EIXO = "#14161A", "#5B616B", "#EEF0F2", "#CDD1D6"


def _trechos(pares):
    """Separa a série em trechos contínuos: mês sem dado vira lacuna, não uma reta ligando os vizinhos."""
    trechos, atual = [], []
    for i, v in pares:
        if atual and i != atual[-1][0] + 1:
            trechos.append(atual)
            atual = []
        atual.append((i, v))
    if atual:
        trechos.append(atual)
    return trechos


def _valor(v, sufixo=""):
    a = abs(v)
    return fmt(v, 0 if a >= 100 else 1 if a >= 10 or sufixo == "%" else 2 if a > 0 else 0) + sufixo


def descrever(pares, periodos, rotulo: str, sufixo: str = "") -> str:
    """Texto alternativo do gráfico para leitores de tela: período, último valor, máximo e mínimo."""
    i0, iu = pares[0][0], pares[-1][0]
    imax, vmax = max(pares, key=lambda p: p[1])
    imin, vmin = min(pares, key=lambda p: p[1])
    return (f"{rotulo}. De {mes_extenso(periodos[i0])} a {mes_extenso(periodos[iu])}. "
            f"Último valor: {_valor(pares[-1][1], sufixo)}. Máximo: {_valor(vmax, sufixo)}, em {mes_extenso(periodos[imax])}. "
            f"Mínimo: {_valor(vmin, sufixo)}, em {mes_extenso(periodos[imin])}.")


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    bruto = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(bruto))
    passo = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= bruto), default=10 * mag)
    ini = math.floor(lo / passo) * passo
    fim = math.ceil(hi / passo) * passo
    ticks, t = [], ini
    while t <= fim + passo / 1000:
        ticks.append(round(t, 10))
        t += passo
    return ticks


def _casas(ticks):
    """Casas decimais que o passo da grade pede: 2,5 -> 1 casa; 0,25 -> 2 (antes saía '0,3' e '3')."""
    passo = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    for c in range(4):
        if abs(round(passo, c) - passo) < 1e-9 * max(1.0, abs(passo)):
            return c
    return 3


def _svg(pares, periodos, rotulo, cor, largura, altura, sufixo, area, zero, fonte, classe, n_ticks, passo_x):
    vals = [v for _, v in pares]
    lo, hi = (0.0 if zero else min(vals)), max(vals)
    folga = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1
    ticks = _ticks(lo if zero else lo - folga, hi + folga, n_ticks)
    y0, y1 = ticks[0], ticks[-1]
    casas = _casas(ticks)
    esq = 14 + fonte * 0.62 * max(len(fmt(t, casas) + sufixo) for t in ticks)
    dir_, topo, base = largura - 12, fonte + 12, altura - fonte - 22
    n = len(periodos)
    x = lambda i: esq + i * (dir_ - esq) / max(n - 1, 1)  # noqa: E731
    y = lambda v: base - (v - y0) / (y1 - y0) * (base - topo)  # noqa: E731
    p = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico {classe}" role="img" '
         f'aria-label="{escape(descrever(pares, periodos, rotulo, sufixo))}">']
    for t in ticks:
        traco = EIXO if t == y0 else GRADE
        p.append(f'<line x1="{esq:.0f}" y1="{y(t):.1f}" x2="{dir_}" y2="{y(t):.1f}" stroke="{traco}" stroke-width="1"/>')
        p.append(f'<text x="{esq - 8:.0f}" y="{y(t) + fonte * 0.35:.1f}" text-anchor="end" font-size="{fonte}" '
                 f'fill="{CINZA}">{fmt(t, casas)}{sufixo}</text>')
    for trecho in _trechos(pares):
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in trecho)
        if len(trecho) == 1:
            i, v = trecho[0]
            p.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.5" fill="{cor}"/>')
            continue
        if area:
            p.append(f'<polygon points="{x(trecho[0][0]):.1f},{base} {pts} {x(trecho[-1][0]):.1f},{base}" '
                     f'fill="{cor}" fill-opacity="0.08"/>')
        p.append(f'<polyline points="{pts}" fill="none" stroke="{cor}" stroke-width="3" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    ui, uv = pares[-1]
    p.append(f'<circle cx="{x(ui):.1f}" cy="{y(uv):.1f}" r="5" fill="{cor}"/>')
    rot = fmt(uv, 1 if abs(uv) < 100 else 0) + sufixo
    p.append(f'<text x="{x(ui) - 8:.1f}" y="{y(uv) - 12:.1f}" text-anchor="end" font-size="{fonte + 2}" '
             f'font-weight="700" fill="{TINTA}">{rot}</text>')
    marcas = list(range(0, n, passo_x))
    if marcas and (n - 1) - marcas[-1] < passo_x * 0.6:
        marcas.pop()
    marcas.append(n - 1)
    for i in marcas:
        ancora = "start" if i == 0 else "end" if i == n - 1 else "middle"
        p.append(f'<text x="{x(i):.1f}" y="{altura - 8}" text-anchor="{ancora}" font-size="{fonte}" '
                 f'fill="{CINZA}">{mes_curto(periodos[i])}</text>')
    p.append("</svg>")
    return "".join(p)


def linha(valores, periodos, rotulo: str, cor: str = "#B45309", largura: int = 800,
          altura: int = 270, sufixo: str = "", area: bool = True, zero: bool = False) -> Markup:
    """Linha com grade, último ponto marcado e rótulos de mês no eixo x."""
    pares = [(i, v) for i, v in enumerate(valores) if v is not None]
    if len(pares) < 2:
        return Markup('<p class="nota">Série curta demais para o gráfico.</p>')
    n = len(periodos)
    larga = _svg(pares, periodos, rotulo, cor, largura, altura, sufixo, area, zero,
                 fonte=12 if largura >= 700 else 13, classe="larga", n_ticks=4, passo_x=6 if n <= 24 else 12)
    cel = _svg(pares, periodos, rotulo, cor, 380, 240, sufixo, area, zero,
               fonte=13, classe="cel", n_ticks=3, passo_x=12 if n <= 24 else 24)
    return Markup(larga + cel)

"""Gráficos em SVG gerados no servidor: abrem rápido, funcionam sem JavaScript
e o Google consegue ler os rótulos.

Cada gráfico sai em duas versões (larga e celular), para que as letras
continuem legíveis na tela pequena; o CSS mostra uma ou outra.
"""
import math
from html import escape

from markupsafe import Markup

from robo.util import fmt, mes_curto

TINTA, CINZA, GRADE, EIXO = "#14161A", "#5B616B", "#EEF0F2", "#CDD1D6"


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
    passo = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    return 0 if passo >= 1 else 1 if passo >= 0.1 else 2


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
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in pares)

    p = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico {classe}" role="img" aria-label="{escape(rotulo)}">']
    for t in ticks:
        traco = EIXO if t == y0 else GRADE
        p.append(f'<line x1="{esq:.0f}" y1="{y(t):.1f}" x2="{dir_}" y2="{y(t):.1f}" stroke="{traco}" stroke-width="1"/>')
        p.append(f'<text x="{esq - 8:.0f}" y="{y(t) + fonte * 0.35:.1f}" text-anchor="end" font-size="{fonte}" '
                 f'fill="{CINZA}">{fmt(t, casas)}{sufixo}</text>')
    if area:
        p.append(f'<polygon points="{x(pares[0][0]):.1f},{base} {pts} {x(pares[-1][0]):.1f},{base}" '
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

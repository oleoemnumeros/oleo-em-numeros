/* Família em Números: botão "Compartilhar" em gráficos e tabelas, com envio por WhatsApp.
 *
 * O mesmo arquivo vale para os quatro portais (Óleo, Comex, Voo e Economia): ele reconhece gráficos (SVG),
 * barras horizontais (.hbars) e tabelas pela estrutura da página. Não coloque nada específico de um site aqui.
 *
 * O que cada botão faz:
 *   - Enviar imagem: monta um PNG (título, gráfico ou tabela, fonte e endereço do site) e abre a folha de
 *     compartilhar do celular (WhatsApp aparece na lista). No computador, copia a imagem e abre o WhatsApp Web.
 *   - Enviar o link: abre o WhatsApp com o título e o endereço direto daquele gráfico (#c-nome-do-grafico).
 *   - Enviar como texto (só tabelas): as primeiras linhas em fonte monoespaçada, mais o link.
 *   - Copiar o link e Baixar a imagem.
 * Sem JavaScript nada muda na página. Para uma tabela ou gráfico ficar de fora: data-sem-compartilhar.
 */
(function () {
  "use strict";
  if (window.famCompartilhar) return;

  var ESC = 2, LARG = 560, PAD = 24, MAX_LINHAS = 14, MAX_COLUNAS = 6;
  var PILHA = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
  var NS = "http://www.w3.org/2000/svg";
  var PORTAIS = {
    "oleoemnumeros.com.br": ["Óleo em Números", "#F59E0B"],
    "comexemnumeros.com.br": ["Comex em Números", "#37C0A5"],
    "vooemnumeros.com.br": ["Voo em Números", "#A58BF0"],
    "economiaemnumeros.com.br": ["Economia em Números", "#7EA3FF"]
  };
  var host = location.hostname.replace(/^www\./, "");
  var metaNome = document.querySelector('meta[property="og:site_name"]');
  var portal = PORTAIS[host] || [(metaNome && metaNome.getAttribute("content")) || host, "#6B7280"];
  var NOME = portal[0], COR_SITE = portal[1];
  var EXCLUIR = "header, nav, footer, button, a, .fam-sheet-fundo, .fam-comp, details.dados, .leaflet-container, [data-sem-compartilhar]";

  // ---------- utilidades ----------
  function limpo(t) { return String(t == null ? "" : t).replace(/\s+/g, " ").trim(); }
  function slug(t) {
    return String(t).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "grafico";
  }
  function rgba(s) {
    var m = /rgba?\(([^)]+)\)/.exec(s || "");
    if (!m) return null;
    var p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    if (p.length < 3 || p.some(isNaN)) return null;
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  function css(c, a) { return "rgba(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," + Math.round(c[2]) + "," + (a == null ? c[3] : a) + ")"; }
  function fundoDe(el) {
    for (var e = el; e && e.nodeType === 1; e = e.parentElement) {
      var c = rgba(getComputedStyle(e).backgroundColor);
      if (c && c[3] > 0.95) return c;
    }
    return [255, 255, 255, 1];
  }
  function conta(acao, a) {
    try { if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: "compartilhar/" + acao + "/" + (a ? a.id : ""), title: "Compartilhar", event: true }); } catch (e) { /* sem contador: segue */ }
  }
  function famGlobal() { return getComputedStyle(document.body).fontFamily + ", " + PILHA; }

  // ---------- onde fica o título, a legenda e a fonte ----------
  function cartaoDe(el) { return el.closest(".card, .cartao, section, article") || document.querySelector("main") || document.body; }

  function tituloEl(cartao, ref) {
    var hs = cartao.querySelectorAll("h1, h2, h3, h4"), r = null, i;
    for (i = 0; i < hs.length; i++) {
      if (hs[i].closest(".fam-sheet-fundo")) continue;
      if (hs[i].compareDocumentPosition(ref) & 4) r = hs[i];   // 4: ref vem depois do título
    }
    if (!r) for (i = 0; i < hs.length; i++) { if (!hs[i].closest(".fam-sheet-fundo")) { r = hs[i]; break; } }
    return r;
  }

  function fonteDe(cartao) {
    var re = /^Fontes?:/i, els = cartao.querySelectorAll(".src, .fonte, p, .small, .nota, small"), i, t;
    for (i = 0; i < els.length; i++) { t = limpo(els[i].textContent); if (re.test(t) && t.length < 400) return t; }
    var rod = document.querySelector("footer"), m = rod && /Fontes?:[^.]{3,140}\./.exec(limpo(rod.textContent));
    return m ? m[0] : "";
  }

  function legendaDe(cartao, ref) {
    var els = cartao.querySelectorAll(".leg, .legend, .legenda, .fam-gr-leg"), el = null, i;
    for (i = 0; i < els.length; i++) {
      if (els[i].closest(".fam-sheet-fundo")) continue;
      if (els[i].compareDocumentPosition(ref) & 4) el = els[i];
    }
    if (!el) for (i = 0; i < els.length; i++) { if (!els[i].closest(".fam-sheet-fundo")) { el = els[i]; break; } }
    var itens = [], sub = "";
    if (!el) return { itens: itens, sub: sub };
    if (!el.children.length) return { itens: itens, sub: limpo(el.textContent) };   // legenda em texto corrido (unidade, período)
    Array.prototype.forEach.call(el.children, function (c) {
      var t = limpo(c.textContent);
      if (!t) return;
      var sw = c.querySelector("i, .sw, .dot") || c, cs = getComputedStyle(sw);
      var cor = rgba(cs.backgroundColor);
      if (!cor || cor[3] === 0) cor = rgba(cs.color);
      itens.push({ t: t, cor: cor || [120, 120, 120, 1] });
    });
    return { itens: itens, sub: sub };
  }

  function tituloTexto(el, h) {
    if (h) return limpo(h.textContent);
    var h1 = document.querySelector("h1");
    return limpo((el.getAttribute && el.getAttribute("aria-label")) || (h1 && h1.textContent) || document.title.split("|")[0]) || NOME;
  }

  function infoDe(a) {
    var ref = a.el, cartao = cartaoDe(ref), h = tituloEl(cartao, ref);
    var titulo = tituloTexto(ref, h);
    var painel = ref.closest("[data-p]"), grupo = painel && painel.closest("[data-tabs]");
    var aba = grupo && grupo.querySelector('button[data-t="' + painel.getAttribute("data-p") + '"]');
    if (aba && limpo(aba.textContent)) titulo += " · " + limpo(aba.textContent);   // abas do mesmo cartão (maior valor, altas, quedas)
    var h1 = document.querySelector("h1"), contexto = "";
    if (h1 && h !== h1 && titulo !== limpo(h1.textContent) && titulo.length < 24) contexto = limpo(h1.textContent).slice(0, 90);   // título curto ("Mês a mês") ganha o assunto da página
    var leg = legendaDe(cartao, ref), base = h || ref;
    var bg = fundoDe(cartao), tinta = rgba(getComputedStyle(base).color) || [20, 22, 26, 1];
    if (tinta[3] < 0.5) tinta = [20, 22, 26, 1];
    return { titulo: titulo, contexto: contexto, sub: leg.sub, legenda: leg.itens, fonte: fonteDe(cartao), bg: bg, tinta: tinta, cartao: cartao };
  }

  // ---------- texto quebrado em linhas ----------
  function corta(ctx, t, larg) {
    t = limpo(t);
    if (ctx.measureText(t).width <= larg) return t;
    while (t.length > 1 && ctx.measureText(t + "…").width > larg) t = t.slice(0, -1);
    return t.replace(/[\s,;:.\-–]+$/, "") + "…";
  }
  function quebra(ctx, txt, larg, max) {
    var ps = limpo(txt).split(" "), linhas = [], l = "", i, t;
    for (i = 0; i < ps.length; i++) {
      t = l ? l + " " + ps[i] : ps[i];
      if (!l || ctx.measureText(t).width <= larg) l = t; else { linhas.push(l); l = ps[i]; }
    }
    if (l) linhas.push(l);
    if (linhas.length > max) { linhas = linhas.slice(0, max); linhas[max - 1] = corta(ctx, linhas[max - 1] + " …", larg); }
    return linhas;
  }

  // ---------- leitura dos alvos ----------
  function textoCelula(c) {
    var k = c.cloneNode(true);
    function tira(raiz, sel) { Array.prototype.forEach.call(raiz.querySelectorAll(sel), function (e) { e.parentNode.removeChild(e); }); }
    tira(k, ".s, .sr, .meta, [data-csv-ignorar]");
    var sem = k.cloneNode(true);
    tira(sem, ".small");
    if (limpo(sem.textContent)) k = sem;   // texto miúdo ao lado do principal (nomes por extenso das rotas) fica de fora da imagem
    Array.prototype.forEach.call(k.querySelectorAll("*"), function (e) { e.insertAdjacentText("afterend", " "); });
    return limpo(k.textContent).replace(/▲\s*/g, "+").replace(/▼\s*/g, "-");
  }
  function lerTabela(t) {
    var cab = [], dir = [], linhas = [], nCol, cabTr = t.tHead && t.tHead.rows.length ? t.tHead.rows[t.tHead.rows.length - 1] : null;
    var corpo = t.tBodies.length ? Array.prototype.slice.call(t.tBodies[0].rows) : [];
    if (!cabTr && corpo.length) cabTr = corpo.shift();
    if (!cabTr) return { cab: [], dir: [], linhas: [], total: 0 };
    nCol = Math.min(cabTr.cells.length, MAX_COLUNAS);
    for (var j = 0; j < nCol; j++) {
      var th = cabTr.cells[j];
      cab.push(textoCelula(th));
      var alvo = (corpo[0] && corpo[0].cells[j]) || th, al = getComputedStyle(alvo).textAlign;
      dir.push(alvo.classList.contains("r") || th.classList.contains("r") || al === "right" || al === "end");
    }
    corpo.forEach(function (tr) {
      var cs = [], vazio = true;
      for (var j = 0; j < nCol; j++) { var v = tr.cells[j] ? textoCelula(tr.cells[j]) : ""; if (v) vazio = false; cs.push(v); }
      if (!vazio) linhas.push(cs);
    });
    return { cab: cab, dir: dir, linhas: linhas, total: linhas.length };
  }
  function lerBarras(el) {
    var out = [];
    Array.prototype.forEach.call(el.querySelectorAll(".hbar"), function (b) {
      var t = b.querySelector(".t"), v = b.querySelector(".v"), tr = b.querySelector(".track");
      if (!t || !tr) return;
      var barra = tr.querySelector("span:not(.zero)"), larg = barra ? parseFloat(barra.style.width) : 0, esq = barra ? parseFloat(barra.style.left) : 0;
      out.push({ rot: textoCelula(t), val: v ? limpo(v.textContent) : "", larg: isNaN(larg) ? 0 : larg, esq: isNaN(esq) ? 0 : esq,
                 cor: barra ? (rgba(getComputedStyle(barra).backgroundColor) || [120, 120, 120, 1]) : [120, 120, 120, 1] });
    });
    return out;
  }
  function svgAtual(a) {
    var el = a.el;
    if (el.matches && el.matches("svg")) return el;
    // gráfico interativo da Família (.fam-gr) ou do Óleo (.gr, com uma variante .gr-v por período; vale a que está à vista)
    return el.querySelector(".fam-gr-area svg") || el.querySelector(".gr-v:not([hidden]) svg.larga") || el.querySelector(".gr-v:not([hidden]) svg") ||
           el.querySelector("svg.larga") || el.querySelector("svg");
  }

  // ---------- SVG -> imagem ----------
  var PROPS = ["fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray", "stroke-linecap", "stroke-linejoin",
               "opacity", "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing", "visibility", "paint-order"];
  function svgImagem(svg, w) {
    return new Promise(function (ok, erro) {
      var vb = svg.viewBox && svg.viewBox.baseVal, vw = vb && vb.width, vh = vb && vb.height;
      if (!vw || !vh) { var r = svg.getBoundingClientRect(); vw = r.width; vh = r.height; }
      if (!vw || !vh) { erro(new Error("gráfico sem tamanho")); return; }
      var h = Math.round(w * vh / vw), clone = svg.cloneNode(true);
      var orig = [svg].concat(Array.prototype.slice.call(svg.querySelectorAll("*")));
      var copia = [clone].concat(Array.prototype.slice.call(clone.querySelectorAll("*")));
      var tirar = [];
      orig.forEach(function (o, i) {
        var c = copia[i], cs = getComputedStyle(o);
        if (i > 0 && cs.display === "none") { tirar.push(c); return; }
        var st = "";
        PROPS.forEach(function (p) { var v = cs.getPropertyValue(p); if (v) st += p + ":" + v + ";"; });
        c.setAttribute("style", st);
      });
      tirar.forEach(function (c) { if (c.parentNode) c.parentNode.removeChild(c); });
      clone.setAttribute("xmlns", NS);
      clone.setAttribute("width", String(w * ESC));
      clone.setAttribute("height", String(h * ESC));
      if (!clone.getAttribute("viewBox")) clone.setAttribute("viewBox", "0 0 " + vw + " " + vh);
      var xml = new XMLSerializer().serializeToString(clone), img = new Image();
      img.onload = function () { ok({ img: img, h: h }); };
      img.onerror = function () { erro(new Error("o gráfico não pôde virar imagem")); };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
    });
  }

  // ---------- conteúdo desenhado no canvas: cada um devolve {h, desenhar(ctx, x, y)} ----------
  function conteudoSvg(a, w) {
    var svg = svgAtual(a);
    if (!svg) return Promise.reject(new Error("sem gráfico"));
    return svgImagem(svg, w).then(function (r) {
      return { h: r.h, desenhar: function (ctx, x, y) { ctx.drawImage(r.img, x, y, w, r.h); } };
    });
  }

  function conteudoTabela(a, w, info, fam) {
    var d = lerTabela(a.el), ctx = document.createElement("canvas").getContext("2d"), n = d.cab.length, j, i;
    var linhas = d.linhas.slice(0, MAX_LINHAS), resto = d.total - linhas.length, F = 14, FC = 12;
    var pad = 12, pesos = [];
    function medir(corpoF) {
      ctx.font = "400 " + corpoF + "px " + fam;
      for (j = 0; j < n; j++) {
        var m = 0;
        for (i = 0; i < linhas.length; i++) m = Math.max(m, ctx.measureText(linhas[i][j]).width);
        ctx.font = "700 " + (corpoF - 2) + "px " + fam;
        m = Math.max(m, Math.min(ctx.measureText(d.cab[j]).width, 64));   // título longo quebra em duas linhas, não alarga a coluna
        ctx.font = "400 " + corpoF + "px " + fam;
        pesos[j] = Math.ceil(m) + pad;
      }
    }
    function ajustar() {
      var soma = 0, k;
      for (k = 0; k < n; k++) soma += pesos[k];
      var larg = pesos.slice();
      if (soma <= w) { for (k = 0; k < n; k++) larg[k] = pesos[k] * w / soma; return larg; }   // sobra espaço: reparte na proporção do texto
      var guarda = 0;
      while (soma > w + 0.5 && guarda++ < 2000) {   // falta espaço: a coluna mais larga cede primeiro
        var mx = 0;
        for (k = 1; k < n; k++) if (larg[k] > larg[mx]) mx = k;
        if (larg[mx] <= 64) break;
        larg[mx] -= 2; soma -= 2;
      }
      return larg;
    }
    var larg;
    [[14, 12], [13, 11], [12, 10]].some(function (t) {   // letra menor só se a primeira coluna (o nome) ainda ficaria cortada
      F = t[0]; FC = t[1]; medir(F); larg = ajustar();
      return larg[0] >= pesos[0] - 2;
    });
    ctx.font = "700 " + FC + "px " + fam;
    var cabL = d.cab.map(function (t, k) { return quebra(ctx, t, larg[k] - pad, 2); });
    var duas = cabL.some(function (l) { return l.length > 1; });
    var hc = duas ? 46 : 28, hl = 30, nota = resto > 0 ? 26 : 0;
    var h = hc + linhas.length * hl + nota;
    var tinta = info.tinta;
    return Promise.resolve({
      h: h,
      desenhar: function (c, x, y) {
        c.textBaseline = "middle";
        var xs = [], acc = x;
        for (j = 0; j < n; j++) { xs[j] = acc; acc += larg[j]; }
        function cel(t, jj, yy, fonte) {
          c.font = fonte;
          var tx = corta(c, t, larg[jj] - pad);
          if (d.dir[jj] && jj > 0) { c.textAlign = "right"; c.fillText(tx, xs[jj] + larg[jj] - pad / 2, yy); }
          else { c.textAlign = "left"; c.fillText(tx, xs[jj] + (jj === 0 ? 0 : pad / 2), yy); }
        }
        c.fillStyle = css(tinta, 0.7);
        for (j = 0; j < n; j++) {
          var ls = cabL[j], y0 = y + hc / 2 - (ls.length - 1) * 8;
          for (var q = 0; q < ls.length; q++) cel(ls[q], j, y0 + q * 16, "700 " + FC + "px " + fam);
        }
        c.fillStyle = css(tinta, 0.45); c.fillRect(x, y + hc - 1.5, w, 1.5);
        for (i = 0; i < linhas.length; i++) {
          var yy = y + hc + i * hl;
          c.fillStyle = tinta ? css(tinta, 1) : "#000";
          for (j = 0; j < n; j++) cel(linhas[i][j], j, yy + hl / 2, (j === 0 ? "600 " : "400 ") + F + "px " + fam);
          if (i < linhas.length - 1 || resto > 0) { c.fillStyle = css(tinta, 0.14); c.fillRect(x, yy + hl - 1, w, 1); }
        }
        if (resto > 0) {
          c.fillStyle = css(tinta, 0.7); c.textAlign = "left"; c.font = "italic 400 " + FC + "px " + fam;
          c.fillText("+ " + resto + (resto === 1 ? " linha" : " linhas") + ". Veja a tabela completa no site.", x, y + hc + linhas.length * hl + nota / 2 + 2);
        }
        c.textAlign = "left"; c.textBaseline = "alphabetic";
      }
    });
  }

  function conteudoBarras(a, w, info, fam) {
    var itens = lerBarras(a.el).slice(0, 14), hl = 44, tinta = info.tinta;
    return Promise.resolve({
      h: itens.length * hl,
      desenhar: function (c, x, y) {
        itens.forEach(function (it, i) {
          var yy = y + i * hl;
          c.textBaseline = "alphabetic"; c.font = "400 14px " + fam; c.fillStyle = css(tinta, 1); c.textAlign = "right";
          c.fillText(it.val, x + w, yy + 16);
          c.textAlign = "left"; c.fillText(corta(c, it.rot, w - c.measureText(it.val).width - 14), x, yy + 16);
          c.fillStyle = css(tinta, 0.1); c.fillRect(x, yy + 24, w, 8);
          c.fillStyle = css(it.cor, 1);
          var bx = x + w * Math.max(0, it.esq) / 100, bw = Math.max(2, w * it.larg / 100);
          c.fillRect(bx, yy + 24, Math.min(bw, x + w - bx), 8);
        });
        c.textAlign = "left";
      }
    });
  }

  // ---------- a imagem inteira ----------
  function gerarImagem(a) {
    var info = infoDe(a), fam = famGlobal(), inner = LARG - 2 * PAD;
    var prontoFonte = document.fonts && document.fonts.load
      ? Promise.all([document.fonts.load("400 14px " + fam), document.fonts.load("700 14px " + fam)]).catch(function () { })
      : Promise.resolve();
    return prontoFonte.then(function () {
      return a.tipo === "svg" ? conteudoSvg(a, inner) : a.tipo === "tabela" ? conteudoTabela(a, inner, info, fam) : conteudoBarras(a, inner, info, fam);
    }).then(function (cont) {
      var m = document.createElement("canvas").getContext("2d");
      m.font = "700 22px " + fam; var tl = quebra(m, info.titulo, inner, 3);
      m.font = "400 14px " + fam; var cl = info.contexto ? quebra(m, info.contexto, inner, 2) : [], sl = info.sub ? quebra(m, info.sub, inner, 2) : [];
      m.font = "400 12px " + fam;
      var fl = info.fonte ? quebra(m, info.fonte, inner, 9) : [];
      if (fl.length > 3) fl = quebra(m, info.fonte.split(/\s*Último dado/)[0], inner, 3);   // fonte longa: fica a fonte, sai o "último dado"
      var leg = [], lx = 0, lin = 0;   // legenda: itens em linhas que quebram
      info.legenda.forEach(function (it) {
        var w = m.measureText(it.t).width + 26;
        if (lx && lx + w > inner) { lin++; lx = 0; }
        leg.push({ it: it, x: lx, l: lin }); lx += w;
      });
      var nLeg = info.legenda.length ? lin + 1 : 0;
      var topo = 6 + 22, y = topo + tl.length * 28 + cl.length * 20 + sl.length * 20 + (nLeg ? nLeg * 22 + 4 : 0) + 12;
      var altCont = cont.h, yRodape = y + altCont + 16;
      var H = yRodape + 1 + 14 + fl.length * 17 + (fl.length ? 10 : 0) + 22 + 20;
      var cv = document.createElement("canvas");
      cv.width = LARG * ESC; cv.height = Math.round(H * ESC);
      var c = cv.getContext("2d");
      c.scale(ESC, ESC);
      c.fillStyle = css(info.bg, 1); c.fillRect(0, 0, LARG, H);
      c.fillStyle = COR_SITE; c.fillRect(0, 0, LARG, 6);
      c.textBaseline = "alphabetic"; c.textAlign = "left";
      var yy = topo + 20;
      c.font = "700 22px " + fam; c.fillStyle = css(info.tinta, 1);
      tl.forEach(function (t) { c.fillText(t, PAD, yy); yy += 28; });
      yy -= 6;
      c.font = "400 14px " + fam; c.fillStyle = css(info.tinta, 0.7);
      cl.concat(sl).forEach(function (t) { c.fillText(t, PAD, yy + 10); yy += 20; });
      if (nLeg) {
        c.font = "400 13px " + fam;
        leg.forEach(function (g) {
          var ly = yy + 8 + g.l * 22;
          c.fillStyle = css(g.it.cor, 1); c.fillRect(PAD + g.x, ly - 8, 12, 12);
          c.fillStyle = css(info.tinta, 0.8); c.fillText(g.it.t, PAD + g.x + 18, ly + 3);
        });
      }
      cont.desenhar(c, PAD, y);
      var fy = yRodape;
      c.fillStyle = css(info.tinta, 0.2); c.fillRect(PAD, fy, inner, 1);
      fy += 14;
      c.font = "400 12px " + fam; c.fillStyle = css(info.tinta, 0.7);
      fl.forEach(function (t) { c.fillText(t, PAD, fy + 11); fy += 17; });
      if (fl.length) fy += 10;
      c.fillStyle = COR_SITE; c.fillRect(PAD, fy + 4, 10, 10);
      c.font = "700 14px " + fam; c.fillStyle = css(info.tinta, 1); c.fillText(NOME, PAD + 18, fy + 14);
      c.font = "400 13px " + fam; c.fillStyle = css(info.tinta, 0.7); c.textAlign = "right"; c.fillText(host, LARG - PAD, fy + 14);
      c.textAlign = "left";
      return new Promise(function (ok, erro) {
        cv.toBlob(function (b) { b ? ok(b) : erro(new Error("sem imagem")); }, "image/png");
      });
    });
  }

  // ---------- texto para o WhatsApp ----------
  function linkDe(a) { return location.origin + location.pathname + "#" + a.id; }
  function textoLink(a, info) {
    return "*" + info.titulo + "*" + (info.contexto ? "\n" + info.contexto : "") + "\n" + linkDe(a);
  }
  function textoTabela(a, info) {
    var d = lerTabela(a.el), linhas = d.linhas.slice(0, 10), resto = d.total - linhas.length, n = d.cab.length, cols = [0], j, i;
    var larg = {}, MAX = 38, tot;   // 38 caracteres: o que cabe numa linha de texto monoespaçado no celular
    for (j = 0; j < n; j++) {
      var m = Math.min(d.cab[j].length, 8);
      for (i = 0; i < linhas.length; i++) m = Math.max(m, linhas[i][j].length);
      larg[j] = Math.min(m, 30);
    }
    tot = larg[0];
    for (j = 1; j < n && cols.length < 4; j++) {
      if (tot + 1 + larg[j] > MAX && cols.length >= 2) break;
      cols.push(j); tot += 1 + larg[j];
    }
    var guarda = 0;
    while (tot > MAX && guarda++ < 200) {   // passou do limite: a coluna mais larga cede
      var mx = cols[0];
      cols.forEach(function (k) { if (larg[k] > larg[mx]) mx = k; });
      if (larg[mx] <= 6) break;
      larg[mx]--; tot--;
    }
    function ajusta(t, w, dir) { t = t.length > w ? t.slice(0, Math.max(1, w - 1)) + "…" : t; var p = new Array(w - t.length + 1).join(" "); return dir ? p + t : t + p; }
    function linha(cs) { return cols.map(function (k, p) { return ajusta(cs[k], larg[k], p > 0 && d.dir[k]); }).join(" "); }
    var corpo = [linha(d.cab)].concat(linhas.map(linha)).join("\n");
    var fonte = info.fonte ? "\n_" + info.fonte.replace(/\s*Atualizado em.*$/, "") + "_" : "";
    return "*" + info.titulo + "*\n```" + corpo + "```" + (resto > 0 ? "\n+ " + resto + " linhas no site" : "") + fonte + "\n" + linkDe(a);
  }
  function abrirWhatsApp(texto) { window.open("https://wa.me/?text=" + encodeURIComponent(texto), "_blank", "noopener"); }

  // ---------- âncoras (link direto para cada gráfico) ----------
  var alvos = [];
  function atribuirId(a) {
    var cartao = cartaoDe(a.el), h = tituloEl(cartao, a.el), t = tituloTexto(a.el, h);
    if (h && h.id && /^c-/.test(h.id) && a.el.closest("[data-p]")) { a.id = h.id; a.dono = h; return; }   // painéis de abas apontam para o cartão
    var base = "c-" + slug(t), id = base, n = 1, dono = h && !h.hasAttribute("data-fam-ancora") ? h : a.el;
    if (dono.id && /^c-/.test(dono.id)) { id = dono.id; }
    else {
      while (document.getElementById(id) && document.getElementById(id) !== dono) { n++; id = base + "-" + n; }
      if (!dono.id) dono.id = id; else id = dono.id;
    }
    dono.setAttribute("data-fam-ancora", "1");
    dono.classList.add("fam-ancora");
    a.id = id; a.dono = dono;
  }
  function irParaHash() {
    var id = decodeURIComponent((location.hash || "").slice(1));
    if (!/^c-/.test(id)) return;
    var e = document.getElementById(id);
    if (!e) return;
    e.scrollIntoView({ block: "start" });
    e.classList.remove("fam-pisca"); void e.offsetWidth; e.classList.add("fam-pisca");
  }

  // ---------- folha de compartilhar ----------
  var ICONES = {
    compartilhar: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="3.5" r="2"/><circle cx="4" cy="8" r="2"/><circle cx="12" cy="12.5" r="2"/><path d="M5.8 7 10.2 4.6M5.8 9l4.4 2.4"/></g></svg>',
    balao: '<svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true" focusable="false"><path d="M2 3.5C2 2.7 2.7 2 3.5 2h9c.8 0 1.5.7 1.5 1.5v6c0 .8-.7 1.5-1.5 1.5H8l-3 3v-3H3.5C2.7 11 2 10.3 2 9.5z" fill="currentColor"/></svg>'
  };
  var folha = null, estado = null, abridor = null;

  function criarFolha() {
    var f = document.createElement("div");
    f.className = "fam-sheet-fundo"; f.hidden = true;
    f.innerHTML = '<div class="fam-sheet" role="dialog" aria-modal="true" aria-labelledby="fam-sheet-t">' +
      '<div class="fam-sheet-topo"><strong id="fam-sheet-t">Compartilhar</strong><button type="button" class="fam-sheet-x" aria-label="Fechar">×</button></div>' +
      '<p class="fam-sheet-nome"></p>' +
      '<div class="fam-sheet-prev"><span class="fam-sheet-carr">Preparando a imagem…</span><img alt="Prévia da imagem que será enviada" hidden></div>' +
      '<div class="fam-sheet-acoes">' +
      '<button type="button" class="zap" data-a="img" disabled>' + ICONES.balao + '<span>Enviar imagem no WhatsApp</span></button>' +
      '<button type="button" class="zap" data-a="link">' + ICONES.balao + '<span>Enviar o link no WhatsApp</span></button>' +
      '<button type="button" class="zap" data-a="txt" hidden>' + ICONES.balao + '<span>Enviar como texto no WhatsApp</span></button>' +
      '<button type="button" data-a="copiar">Copiar o link</button>' +
      '<button type="button" data-a="baixar" disabled>Baixar a imagem</button>' +
      '</div><p class="fam-sheet-msg" role="status" aria-live="polite"></p></div>';
    document.body.appendChild(f);
    f.addEventListener("click", function (ev) {
      if (ev.target === f || ev.target.closest(".fam-sheet-x")) { fechar(); return; }
      var b = ev.target.closest("button[data-a]");
      if (b && !b.disabled) acao(b.getAttribute("data-a"));
    });
    f.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { ev.stopPropagation(); fechar(); return; }
      if (ev.key !== "Tab") return;
      var bs = Array.prototype.filter.call(f.querySelectorAll("button"), function (b) { return !b.disabled && !b.hidden; });
      if (!bs.length) return;
      var p = bs[0], u = bs[bs.length - 1];
      if (ev.shiftKey && document.activeElement === p) { ev.preventDefault(); u.focus(); }
      else if (!ev.shiftKey && document.activeElement === u) { ev.preventDefault(); p.focus(); }
    });
    return f;
  }
  function msg(t) { if (folha) folha.querySelector(".fam-sheet-msg").textContent = t || ""; }
  function botao(k) { return folha.querySelector('[data-a="' + k + '"]'); }

  function abrir(a, opener) {
    if (!folha) folha = criarFolha();
    abridor = opener || null;
    var info = infoDe(a);
    estado = { a: a, info: info, blob: null, file: null, url: null };
    folha.style.setProperty("--fs-bg", css(info.bg, 1));
    folha.style.setProperty("--fs-ink", css(info.tinta, 1));
    folha.querySelector(".fam-sheet-nome").textContent = info.titulo;
    var img = folha.querySelector(".fam-sheet-prev img"), carr = folha.querySelector(".fam-sheet-carr");
    img.hidden = true; img.removeAttribute("src"); carr.hidden = false; carr.textContent = "Preparando a imagem…";
    botao("img").disabled = true; botao("baixar").disabled = true;
    botao("txt").hidden = a.tipo !== "tabela";
    msg("");
    folha.hidden = false;
    document.documentElement.classList.add("fam-sheet-aberta");
    var alvoFoco = botao("link"); if (alvoFoco) alvoFoco.focus();
    var meu = estado;
    gerarImagem(a).then(function (blob) {
      if (estado !== meu) return;
      meu.blob = blob;
      try { meu.file = new File([blob], slug(info.titulo) + "-" + slug(NOME) + ".png", { type: "image/png" }); } catch (e) { meu.file = null; }
      meu.url = URL.createObjectURL(blob);
      img.src = meu.url; img.hidden = false; carr.hidden = true;
      botao("img").disabled = false; botao("baixar").disabled = false;
    }, function () {
      if (estado !== meu) return;
      carr.textContent = "Não foi possível montar a imagem neste aparelho. O link e o texto funcionam.";
    });
  }
  function fechar() {
    if (!folha || folha.hidden) return;
    folha.hidden = true;
    document.documentElement.classList.remove("fam-sheet-aberta");
    if (estado && estado.url) { try { URL.revokeObjectURL(estado.url); } catch (e) { } }
    estado = null;
    if (abridor && abridor.focus) abridor.focus();
  }

  function baixar() {
    if (!estado || !estado.blob) return false;
    var u = estado.url || URL.createObjectURL(estado.blob), l = document.createElement("a");
    l.href = u; l.download = (estado.file && estado.file.name) || "grafico.png";
    document.body.appendChild(l); l.click(); document.body.removeChild(l);
    return true;
  }
  function copiarTexto(t) {
    return new Promise(function (ok, erro) {
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(t).then(ok, erro); return; }
      var ta = document.createElement("textarea");
      ta.value = t; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? ok() : erro(); } catch (e) { erro(e); }
      document.body.removeChild(ta);
    });
  }
  function toqueNaTela() { return window.matchMedia && window.matchMedia("(pointer: coarse)").matches; }

  function acao(k) {
    if (!estado) return;
    var a = estado.a, info = estado.info;
    if (k === "link") { conta("link", a); abrirWhatsApp(textoLink(a, info)); return; }
    if (k === "txt") { conta("texto", a); abrirWhatsApp(textoTabela(a, info)); return; }
    if (k === "copiar") {
      conta("copiar", a);
      copiarTexto(linkDe(a)).then(function () { msg("Link copiado."); }, function () { msg("Não consegui copiar. Endereço: " + linkDe(a)); });
      return;
    }
    if (k === "baixar") { conta("baixar", a); if (baixar()) msg("Imagem salva no aparelho."); return; }
    if (k !== "img" || !estado.blob) return;
    conta("imagem", a);
    var arq = estado.file, texto = textoLink(a, info);
    if (arq && navigator.canShare && navigator.share && navigator.canShare({ files: [arq] })) {
      navigator.share({ files: [arq], title: info.titulo, text: texto }).then(function () { msg(""); }, function (e) {
        if (e && e.name === "AbortError") return;
        msg("O compartilhamento direto falhou. Use \"Baixar a imagem\" e anexe no WhatsApp.");
      });
      return;
    }
    // sem compartilhamento de arquivos (computador, alguns navegadores): copia a imagem, abre o WhatsApp e avisa
    var copiou = null;
    if (!toqueNaTela() && navigator.clipboard && window.ClipboardItem) {
      try { copiou = navigator.clipboard.write([new window.ClipboardItem({ "image/png": estado.blob })]); } catch (e) { copiou = null; }
    }
    abrirWhatsApp(texto);
    if (copiou) copiou.then(function () { msg("Imagem copiada. No WhatsApp, cole com Ctrl+V (ou ⌘V) na conversa."); }, function () { baixar(); msg("Imagem salva no aparelho. Anexe-a na conversa do WhatsApp."); });
    else { baixar(); msg("Imagem salva no aparelho. Anexe-a na conversa do WhatsApp."); }
  }

  // ---------- botões na página ----------
  function criarBotao(a, titulo) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "fam-comp-btn";
    b.innerHTML = ICONES.compartilhar + "<span>Compartilhar</span>";
    b.setAttribute("aria-label", "Compartilhar " + (a.tipo === "tabela" ? "a tabela" : "o gráfico") + " " + titulo + " no WhatsApp");
    b.addEventListener("click", function () { abrir(a, b); });
    return b;
  }
  function registrar(tipo, el, ancora) {
    el.setAttribute("data-fam-comp", "1");
    var a = { tipo: tipo, el: el, ancora: ancora };
    atribuirId(a);
    var h = tituloEl(cartaoDe(el), el), titulo = h ? limpo(h.textContent) : "";
    var b = criarBotao(a, titulo);
    var antes = ancora.previousElementSibling;
    if (tipo === "tabela" && antes && antes.classList && antes.classList.contains("fam-ferr")) antes.appendChild(b);   // junto do botão "Baixar tabela (CSV)"
    else {
      var barra = document.createElement("div");
      barra.className = "fam-comp";
      barra.appendChild(b);
      ancora.parentNode.insertBefore(barra, ancora.nextSibling);
    }
    alvos.push(a);
  }
  function viewBoxLargura(s) {
    var vb = s.viewBox && s.viewBox.baseVal;
    if (vb && vb.width) return vb.width;
    return parseFloat(s.getAttribute("width")) || 0;
  }
  // elemento que embrulha só o gráfico (o botão entra depois dele); .chart que é o cartão inteiro não serve
  function contDe(s) {
    var c = s.closest(".fam-gr, .gr[data-d]") || s.closest(".chart:not(.card):not(.cartao)");
    if (c) return c;
    c = s.parentElement && s.parentElement.closest("[role='img']");
    return c && !c.matches(".card, .cartao, section, article") ? c : s;
  }
  function iniciar(contexto) {
    var raiz = contexto || document;
    // gráficos SVG (e os interativos, que ficam dentro de .fam-gr)
    Array.prototype.forEach.call(raiz.querySelectorAll("svg"), function (s) {
      if (s.closest(EXCLUIR) || s.closest("[data-fam-comp]") || s.getAttribute("aria-hidden") === "true") return;
      if (s.classList.contains("cel") && s.parentNode.querySelector("svg.larga")) return;   // Óleo: versão para celular do mesmo gráfico
      if (!s.getAttribute("role") && !s.closest(".chart, .fam-gr, [role='img']")) return;
      if (viewBoxLargura(s) < 240) return;
      var cont = contDe(s);
      if (cont.closest("[data-fam-comp]") || cont.hasAttribute("data-fam-comp")) return;
      var ancora = cont;
      if (cont === s && s.classList.contains("larga") && s.nextElementSibling && s.nextElementSibling.classList.contains("cel")) ancora = s.nextElementSibling;
      registrar("svg", cont, ancora);
    });
    // barras horizontais feitas de HTML
    Array.prototype.forEach.call(raiz.querySelectorAll(".hbars"), function (el) {
      if (el.closest(EXCLUIR) || el.hasAttribute("data-fam-comp") || el.querySelectorAll(".hbar").length < 2) return;
      registrar("barras", el, el);
    });
    // tabelas
    Array.prototype.forEach.call(raiz.querySelectorAll("table"), function (t) {
      if (t.closest(EXCLUIR) || t.closest(".fam-gr") || t.hasAttribute("data-fam-comp") || t.hasAttribute("data-sem-export")) return;
      if (!t.tBodies.length || t.tBodies[0].rows.length < 3) return;
      var primeira = t.tBodies[0].rows[0];
      if (!primeira || primeira.cells.length < 2) return;
      var env = t.closest(".scroll, .tabela, .tabela-wrap, .tw");
      if (env && env.querySelectorAll("table").length > 1) env = null;
      registrar("tabela", t, env || t);
    });
  }

  // ---------- estilo ----------
  function estilo() {
    var s = document.createElement("style");
    s.textContent =
      ".fam-comp{display:flex;justify-content:flex-end;margin:10px 0 4px}" +
      ".fam-ferr{flex-wrap:wrap;gap:8px}" +
      ".fam-ferr .fam-btn,.fam-comp-btn{white-space:nowrap}" +
      ".fam-comp-btn{font:inherit;font-size:14px;font-weight:600;line-height:1;display:inline-flex;align-items:center;gap:8px;min-height:40px;padding:0 14px;border-radius:8px;border:1.5px solid var(--fam-cor,rgba(128,128,128,.6));background:transparent;color:var(--fam-texto,inherit);cursor:pointer}" +
      ".fam-comp-btn:hover,.fam-comp-btn:focus-visible{background:var(--fam-cor,rgba(128,128,128,.16));color:var(--fam-sobre,inherit)}" +
      ".fam-comp-btn svg,.fam-sheet svg{flex:none;display:inline-block;width:16px!important;height:16px!important;max-width:none!important;margin:0!important}" +
      ".fam-sheet-acoes button svg{width:18px!important;height:18px!important}" +
      ".fam-comp-btn,.fam-sheet button{text-transform:none;letter-spacing:normal;box-shadow:none;width:auto}" +
      ".fam-sheet-acoes button{width:100%}" +
      ".fam-ancora{scroll-margin-top:var(--fam-topo,84px)}" +
      ".fam-pisca{animation:fam-pisca 2.6s ease-out 1}" +
      "@keyframes fam-pisca{0%,40%{outline:3px solid #f59e0b;outline-offset:6px}100%{outline:3px solid transparent;outline-offset:6px}}" +
      "@media (prefers-reduced-motion:reduce){.fam-pisca{animation:none;outline:3px solid #f59e0b;outline-offset:6px}}" +
      ".fam-sheet-aberta{overflow:hidden}" +
      ".fam-sheet-fundo{position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,.55);display:flex;align-items:flex-end;justify-content:center}" +
      ".fam-sheet-fundo[hidden]{display:none}" +
      ".fam-sheet{box-sizing:border-box;width:100%;max-width:520px;max-height:94vh;overflow:auto;background:var(--fs-bg,#fff);color:var(--fs-ink,#14161a);border-radius:16px 16px 0 0;padding:14px 16px calc(16px + env(safe-area-inset-bottom));font:15px/1.4 system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;box-shadow:0 -6px 30px rgba(0,0,0,.3)}" +
      "@media (min-width:600px){.fam-sheet-fundo{align-items:center}.fam-sheet{border-radius:16px}}" +
      ".fam-sheet-topo{display:flex;justify-content:space-between;align-items:center;font-size:17px}" +
      ".fam-sheet-x{font:inherit;font-size:26px;line-height:1;width:44px;height:44px;margin:-8px -10px -8px 0;border:0;background:transparent;color:inherit;cursor:pointer;border-radius:8px}" +
      ".fam-sheet-nome{margin:2px 0 10px;font-size:14px;opacity:.75}" +
      ".fam-sheet-prev{min-height:96px;display:flex;align-items:center;justify-content:center;border:1px solid rgba(128,128,128,.35);border-radius:10px;overflow:hidden;background:rgba(128,128,128,.08);padding:0}" +
      ".fam-sheet-prev img{display:block;max-width:100%;max-height:34vh;object-fit:contain}" +
      ".fam-sheet-prev img[hidden],.fam-sheet-carr[hidden]{display:none}" +
      ".fam-sheet-carr{padding:16px;font-size:14px;opacity:.75;text-align:center}" +
      ".fam-sheet-acoes{display:flex;flex-direction:column;gap:8px;margin-top:12px}" +
      ".fam-sheet-acoes button{font:inherit;font-size:15px;font-weight:600;min-height:48px;padding:0 14px;border-radius:10px;border:1.5px solid rgba(128,128,128,.55);background:transparent;color:inherit;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px}" +
      ".fam-sheet-acoes button[hidden]{display:none}" +
      ".fam-sheet-acoes button.zap{background:#0f7a47;border-color:#0f7a47;color:#fff}" +
      ".fam-sheet-acoes button[disabled]{opacity:.45;cursor:default}" +
      ".fam-sheet-acoes button:focus-visible,.fam-sheet-x:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}" +
      ".fam-sheet-msg{min-height:20px;margin:10px 0 0;font-size:14px}" +
      "@media print{.fam-comp,.fam-comp-btn,.fam-sheet-fundo{display:none!important}}";
    document.head.appendChild(s);
  }

  // cabeçalho fixo (sticky) cobriria o título do gráfico ao abrir um link direto: mede a altura e deixa a margem certa
  function medirTopo() {
    var h = document.querySelector("header"), cs = h && getComputedStyle(h);
    var px = cs && (cs.position === "sticky" || cs.position === "fixed") ? h.offsetHeight + 12 : 12;
    document.documentElement.style.setProperty("--fam-topo", px + "px");
  }
  function pronto() {
    estilo();
    medirTopo();
    window.addEventListener("resize", medirTopo);
    iniciar();
    irParaHash();
    window.addEventListener("load", function () { iniciar(); irParaHash(); });
    var main = document.querySelector("main") || document.body;
    if (main && window.MutationObserver) {
      var espera = 0;
      new MutationObserver(function () { clearTimeout(espera); espera = setTimeout(function () { iniciar(); }, 300); }).observe(main, { childList: true, subtree: true });
    }
  }
  window.famCompartilhar = { iniciar: iniciar, gerarImagem: gerarImagem, alvos: function () { return alvos; }, textoTabela: textoTabela, textoLink: textoLink, infoDe: infoDe };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", pronto); else pronto();
})();

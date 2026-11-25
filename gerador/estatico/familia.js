/* Família em Números: recursos comuns a todos os portais.
   Hoje: botão visível "Baixar tabela (CSV)" em toda tabela de dados, botão "Copiar texto" dos textos prontos
   e, ao lado de "Copiar texto do X", o botão "Publicar no X", que abre o X com o texto e o link já preenchidos.
   Instagram: monta a legenda (#post-ig) a partir do texto do LinkedIn e liga o botão "Compartilhar no Instagram".
   O mesmo arquivo vale para os quatro portais; não coloque nada específico de um site aqui. */
(function () {
  "use strict";
  var ICONE = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M8 1.5v8m0 0L4.8 6.3M8 9.5l3.2-3.2M2.5 11.5v1.2c0 .5.4.8.8.8h9.4c.5 0 .8-.3.8-.8v-1.2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function limpo(t) { return String(t).replace(/\s+/g, " ").trim(); }

  // texto da célula sem os subtítulos pequenos (classes s, meta, sr) que só enfeitam a tela
  function textoCelula(c) {
    var k = c.cloneNode(true);
    Array.prototype.forEach.call(k.querySelectorAll(".s, .sr, .meta, [data-csv-ignorar]"), function (e) { e.parentNode.removeChild(e); });
    Array.prototype.forEach.call(k.querySelectorAll("*"), function (e) { e.insertAdjacentText("afterend", " "); });   // evita "TomateAlimentação": separa blocos vizinhos
    return limpo(k.textContent).replace(/▲\s*/g, "+").replace(/▼\s*/g, "-");   // setas de variação viram sinal
  }
  function celulaCsv(v) { return /[;"\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }

  // CSV para abrir direto no Excel em português: ponto e vírgula, UTF-8 com BOM
  function csvDe(tabela) {
    var linhas = Array.prototype.map.call(tabela.rows, function (tr) {
      var cab = tr.parentNode && tr.parentNode.tagName === "THEAD";
      return Array.prototype.map.call(tr.cells, function (c) {
        var v = textoCelula(c);
        return celulaCsv(cab && !v ? "#" : v);   // coluna de posição sem título ganha "#"
      }).join(";");
    });
    return "﻿" + linhas.join("\r\n") + "\r\n";
  }

  function slug(t) {
    return String(t).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "tabela";
  }

  function titulo(tabela) {
    var bloco = tabela.closest(".card, .cartao, section, article") || document.body;
    var h = bloco.querySelector("h2, h3");
    return limpo(h ? h.textContent : document.title);
  }

  function baixar(tabela) {
    var nome = slug(titulo(tabela)) + "-" + slug(location.hostname.replace(/^www\./, "").split(".")[0]) + ".csv";
    var blob = new Blob([csvDe(tabela)], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); document.body.removeChild(a); }, 1500);
    try { if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: "exportar/" + nome, title: "Exportar tabela", event: true }); } catch (e) { /* sem contador: segue */ }
  }

  function elegivel(t) {
    if (t.getAttribute("data-fam") || t.hasAttribute("data-sem-export")) return false;
    if (t.closest("details.dados, .fam-ferr, footer, nav")) return false;   // gráficos com CSV próprio, rodapé e menus
    return t.tBodies.length > 0 && t.tBodies[0].rows.length >= 2;
  }

  function iniciar(contexto) {
    Array.prototype.forEach.call((contexto || document).querySelectorAll("main table, .conteudo table"), function (t) {
      if (!elegivel(t)) return;
      t.setAttribute("data-fam", "1");
      var envoltorio = t.closest(".scroll, .tabela, .tabela-wrap, .tw") || t;
      var barra = document.createElement("div");
      barra.className = "fam-ferr";
      var b = document.createElement("button");
      b.type = "button";
      b.className = "fam-btn";
      b.innerHTML = ICONE + "<span>Baixar tabela (CSV)</span>";
      b.setAttribute("aria-label", "Baixar a tabela " + titulo(t) + " em CSV, para abrir no Excel");
      b.addEventListener("click", function () { baixar(t); });
      barra.appendChild(b);
      envoltorio.parentNode.insertBefore(barra, envoltorio);
    });
  }
  window.familiaIniciarTabelas = iniciar;

  // botão "Copiar texto": <button data-copiar="id-do-textarea"> copia o conteúdo e avisa em [data-copiar-msg]
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-copiar]");
    if (!b) return;
    var alvo = document.getElementById(b.getAttribute("data-copiar")), msg = b.parentNode.querySelector("[data-copiar-msg]");
    if (!alvo) return;
    function aviso(t) { if (msg) msg.textContent = t; }
    try {
      navigator.clipboard.writeText(alvo.value).then(function () { aviso("Texto copiado."); }, function () { alvo.select(); aviso("Selecione o texto e copie."); });
    } catch (e) { alvo.select(); aviso("Selecione o texto e copie."); }
  });

  // botão "Publicar no X": vai ao lado de <button data-copiar="post-x"> e abre o X com o texto do <textarea id="post-x">.
  // No celular, abre o aplicativo do X se ele estiver instalado. O LinkedIn não aceita texto preenchido pelo link,
  // por isso lá continua só o "Copiar texto".
  function botoesX() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-copiar="post-x"]'), function (b) {
      var alvo = document.getElementById("post-x");
      if (!alvo || !limpo(alvo.value) || b.parentNode.querySelector("[data-publicar-x]")) return;
      var a = document.createElement("a");
      a.className = "fam-btn";
      a.setAttribute("data-publicar-x", "");
      a.href = "https://x.com/intent/tweet?text=" + encodeURIComponent(alvo.value.trim());
      a.target = "_blank";
      a.rel = "noopener";
      a.style.textDecoration = "none";
      a.innerHTML = "<span>Publicar no X</span>";
      a.addEventListener("click", function () {
        try { if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: "publicar-x", title: document.title, event: true }); } catch (e) { /* contador é opcional */ }
      });
      b.insertAdjacentElement("afterend", a);
      b.insertAdjacentText("afterend", " ");
    });
  }

  // Instagram: link na legenda não é clicável, então o endereço vira só o site, com "link na bio" no fim
  function legendaInstagram() {
    var ig = document.getElementById("post-ig");
    if (!ig || limpo(ig.value)) return;
    var base = document.getElementById("post") || document.getElementById("post-x");
    if (!base) return;
    var t = base.value.replace(/https?:\/\/(www\.)?([^\/\s]+)\S*/g, "$2").replace(/[ \t]+\n/g, "\n").trim();
    ig.value = t + "\n\nLink na bio.";
  }

  // "Compartilhar no Instagram": no celular, abre a tela de compartilhar do sistema com a imagem (o Instagram aparece
  // na lista) e copia a legenda antes, porque o Instagram não aceita texto vindo de fora. Sem suporte, o botão fica escondido.
  function compartilharInstagram() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-compartilhar-ig]"), function (b) {
      if (!navigator.share || !navigator.canShare || !window.fetch || !window.File) return;
      var arquivo = null;
      fetch(b.getAttribute("data-compartilhar-ig")).then(function (r) { return r.ok ? r.blob() : null; }).then(function (bl) {
        if (!bl) return;
        arquivo = new File([bl], b.getAttribute("data-nome") || "imagem.png", { type: "image/png" });
        try { if (navigator.canShare({ files: [arquivo] })) b.hidden = false; } catch (e) { /* sem suporte a arquivos */ }
      }).catch(function () {});
      b.addEventListener("click", function () {
        if (!arquivo) return;
        var ig = document.getElementById("post-ig"), msg = b.parentNode.querySelector("[data-copiar-msg]");
        try { if (ig && navigator.clipboard) navigator.clipboard.writeText(ig.value).then(function () { if (msg) msg.textContent = "Legenda copiada: cole no Instagram."; }, function () {}); } catch (e) { /* segue sem copiar */ }
        navigator.share({ files: [arquivo] }).catch(function () {});
        try { if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: "compartilhar-instagram", title: document.title, event: true }); } catch (e) { /* contador é opcional */ }
      });
    });
  }

  function pronto() {
    iniciar();
    botoesX();
    legendaInstagram();
    compartilharInstagram();
    // tabelas que a página monta depois (buscas, abas): uma checagem leve quando o conteúdo muda
    var main = document.querySelector("main");
    if (main && window.MutationObserver) {
      var espera = 0;
      new MutationObserver(function () {
        clearTimeout(espera);
        espera = setTimeout(function () { iniciar(); }, 250);
      }).observe(main, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", pronto); else pronto();
})();
/* menu: no celular, rola a lista até o item da página atual */
(function(){var a=document.querySelector('nav a[aria-current="page"]');if(!a)return;var n=a.parentElement;if(n&&n.scrollWidth>n.clientWidth){n.scrollLeft=Math.max(0,a.offsetLeft-n.clientWidth/2+a.offsetWidth/2);}})();

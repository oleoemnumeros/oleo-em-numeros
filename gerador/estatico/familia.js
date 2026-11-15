/* Família em Números: recursos comuns a todos os portais.
   Hoje: botão visível "Baixar tabela (CSV)" em toda tabela de dados.
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

  function pronto() {
    iniciar();
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

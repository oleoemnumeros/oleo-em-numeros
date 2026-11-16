/* Família em Números — busca no cabeçalho, igual nos quatro portais.
   A página carrega este arquivo com data-indice="/busca-geral.json" (lista de [título, endereço, tipo],
   da página mais procurada para a menos) e, se os endereços forem relativos, data-raiz="../".
   O índice só é baixado quando a pessoa abre a busca. Atalho: tecla "/". */
(function () {
  "use strict";
  var eu = document.currentScript || document.querySelector("script[data-indice]");
  if (!eu || !eu.getAttribute("data-indice")) return;
  var INDICE = eu.getAttribute("data-indice"), RAIZ = eu.getAttribute("data-raiz") || "";
  var DICA = eu.getAttribute("data-dica") || "Digite o que procura";
  var MAX = 12, lista = null, norm = null, carregando = false, fundo, campo, res, aviso, ativo = -1, quemAbriu;

  function limpa(s) {
    return String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }
  function conta(nome) {
    try { if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: "busca/" + nome, title: "Busca", event: true }); } catch (e) {}
  }

  function estilo() {
    var cab = document.querySelector("header"), cs = cab ? getComputedStyle(cab) : null;
    var bg = cs && cs.backgroundColor && cs.backgroundColor !== "rgba(0, 0, 0, 0)" ? cs.backgroundColor : getComputedStyle(document.body).backgroundColor;
    var tinta = getComputedStyle(document.body).color;
    var a = document.querySelector("main a"), realce = a ? getComputedStyle(a).color : tinta;
    var st = document.createElement("style");
    st.textContent =
      ".fam-busca-btn{display:inline-flex;align-items:center;gap:6px;margin:-5px 0 -5px auto;flex:none;padding:7px 12px;border-radius:999px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);background:transparent;color:inherit;font:inherit;font-size:14px;font-weight:500;cursor:pointer;line-height:1.2}" +
      ".fam-busca-btn:hover{border-color:currentColor}" +
      ".fam-busca-btn svg{width:16px!important;height:16px!important;flex:none;display:block}" +
      ".fam-busca-btn kbd{font:inherit;font-size:11px;opacity:.6;border:1px solid color-mix(in srgb,currentColor 25%,transparent);border-radius:4px;padding:0 5px}" +
      ".fam-busca-btn.fim{order:1;margin-left:8px}" +
      "@media (max-width:600px){.fam-busca-btn{padding:8px}.fam-busca-btn kbd,.fam-busca-btn span{display:none}.fam-busca-btn svg{width:18px!important;height:18px!important}}" +
      "@media print{.fam-busca-btn{display:none}}" +
      ".fam-busca-fundo{position:fixed;inset:0;z-index:2147482000;background:rgba(0,0,0,.5);display:flex;justify-content:center;align-items:flex-start;padding:10px}" +
      ".fam-busca-fundo[hidden]{display:none}" +
      ".fam-busca-cx{width:100%;max-width:640px;margin-top:min(8vh,70px);max-height:calc(100dvh - 20px - min(8vh,70px));display:flex;flex-direction:column;border-radius:14px;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.35);background:" + bg + ";color:" + tinta + "}" +
      ".fam-busca-topo{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid color-mix(in srgb,currentColor 14%,transparent)}" +
      ".fam-busca-topo svg{width:18px!important;height:18px!important;flex:none;opacity:.6}" +
      ".fam-busca-topo input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:inherit;font:inherit;font-size:17px;padding:8px 2px}" +
      ".fam-busca-topo button{border:0;background:transparent;color:inherit;font:inherit;font-size:14px;padding:8px;cursor:pointer;opacity:.75}" +
      ".fam-busca-res{list-style:none;margin:0;padding:6px;overflow:auto;-webkit-overflow-scrolling:touch}" +
      ".fam-busca-res a{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:11px 10px;border-radius:8px;color:inherit;text-decoration:none;font-size:15px;line-height:1.3}" +
      ".fam-busca-res a small{flex:none;font-size:12px;opacity:.6;text-transform:lowercase}" +
      ".fam-busca-res a mark{background:transparent;color:" + realce + ";font-weight:700}" +
      ".fam-busca-res a:hover,.fam-busca-res a.at{background:color-mix(in srgb,currentColor 9%,transparent)}" +
      ".fam-busca-aviso{margin:0;padding:14px 16px;font-size:14px;opacity:.75}" +
      ".fam-busca-aviso[hidden]{display:none}";
    document.head.appendChild(st);
  }

  var LUPA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>';

  function montar() {
    var cab = document.querySelector("header");
    if (!cab || document.querySelector(".fam-busca-btn")) return;
    estilo();
    var dentro = cab.firstElementChild && cab.firstElementChild.tagName === "DIV" ? cab.firstElementChild : cab;
    var marca = dentro.querySelector("a");
    var b = document.createElement("button");
    b.type = "button"; b.className = "fam-busca-btn"; b.setAttribute("aria-label", "Buscar no site"); b.setAttribute("aria-haspopup", "dialog");
    b.innerHTML = LUPA + "<span>Buscar</span><kbd>/</kbd>";
    if (marca && marca.parentNode === dentro) dentro.insertBefore(b, marca.nextSibling); else dentro.appendChild(b);
    b.addEventListener("click", abrir);
    // menu na mesma linha da marca: a busca vai para o fim da linha; menu em outra linha: fica ao lado da marca
    var menu = dentro.querySelector("nav");
    function ajustar() {
      b.classList.remove("fim");
      if (!menu || !marca || menu.offsetTop > marca.offsetTop + marca.offsetHeight - 4) return;
      b.classList.add("fim");
      if (b.offsetTop > menu.offsetTop + menu.offsetHeight - 4 || menu.offsetTop > marca.offsetTop + marca.offsetHeight - 4) b.classList.remove("fim");
    }
    ajustar(); window.addEventListener("resize", ajustar);

    fundo = document.createElement("div");
    fundo.className = "fam-busca-fundo"; fundo.hidden = true;
    fundo.innerHTML = '<div class="fam-busca-cx" role="dialog" aria-modal="true" aria-label="Buscar no site">' +
      '<div class="fam-busca-topo">' + LUPA + '<input type="search" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search" aria-label="Buscar no site" aria-controls="fam-busca-res" placeholder="' + esc(DICA) + '"><button type="button">Fechar</button></div>' +
      '<p class="fam-busca-aviso" role="status"></p><ul class="fam-busca-res" id="fam-busca-res"></ul></div>';
    document.body.appendChild(fundo);
    campo = fundo.querySelector("input"); res = fundo.querySelector("ul"); aviso = fundo.querySelector(".fam-busca-aviso");
    fundo.querySelector("button").addEventListener("click", fechar);
    fundo.addEventListener("mousedown", function (e) { if (e.target === fundo) fechar(); });
    campo.addEventListener("input", buscar);
    campo.addEventListener("keydown", teclas);
    res.addEventListener("click", function (e) { if (e.target.closest("a")) conta("ir"); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !fundo.hidden) { fechar(); return; }
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || !fundo.hidden) return;
      var t = e.target, n = t && t.tagName;
      if (n === "INPUT" || n === "TEXTAREA" || n === "SELECT" || (t && t.isContentEditable)) return;
      e.preventDefault(); abrir();
    });
  }

  function abrir() {
    quemAbriu = document.activeElement;
    fundo.hidden = false; document.documentElement.style.overflow = "hidden";
    campo.value = ""; res.innerHTML = ""; ativo = -1; dizer(lista ? "" : "Carregando…");
    campo.focus(); conta("abrir");
    if (lista || carregando) return;
    carregando = true;
    fetch(INDICE).then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (j) {
      lista = j; norm = j.map(function (x) { return limpa(x[0] + " " + (x[3] || "")); });
      carregando = false; buscar();
    }).catch(function () { carregando = false; dizer("Não foi possível carregar a busca. Tente de novo em instantes."); });
  }
  function fechar() {
    fundo.hidden = true; document.documentElement.style.overflow = "";
    if (quemAbriu && quemAbriu.focus) quemAbriu.focus();
  }
  function dizer(t) { aviso.textContent = t; aviso.hidden = !t; }

  function realca(titulo, termos) {
    // marca o trecho que casou, comparando sem acento letra a letra
    var base = titulo.normalize("NFD"), saida = "", i = 0, plano = "", mapa = [];
    for (var k = 0; k < base.length; k++) {
      var ch = base[k];
      if (/[̀-ͯ]/.test(ch)) continue;
      plano += ch.toLowerCase(); mapa.push(k);
    }
    var marcas = [];
    termos.forEach(function (t) { var p = plano.indexOf(t); if (p >= 0) marcas.push([mapa[p], (mapa[p + t.length] !== undefined ? mapa[p + t.length] : base.length)]); });
    marcas.sort(function (a, b) { return a[0] - b[0]; });
    marcas.forEach(function (m) {
      if (m[0] < i) return;
      saida += esc(base.slice(i, m[0])) + "<mark>" + esc(base.slice(m[0], m[1])) + "</mark>"; i = m[1];
    });
    return (saida + esc(base.slice(i))).normalize("NFC");
  }

  function buscar() {
    if (!lista) return;
    var q = limpa(campo.value), termos = q ? q.split(" ") : [];
    res.innerHTML = ""; ativo = -1;
    if (q.length < 2) { dizer(""); return; }
    var achados = [];
    for (var i = 0; i < norm.length; i++) {
      var n = norm[i], ok = true, nota = 0;
      for (var t = 0; t < termos.length; t++) {
        var p = n.indexOf(termos[t]);
        if (p < 0) { ok = false; break; }
        nota += p === 0 ? 3 : (n.charAt(p - 1) === " " ? 2 : 0);
      }
      if (!ok) continue;
      if (n === q) nota += 6; else if (n.indexOf(q) === 0) nota += 3;
      achados.push([nota, i]);
    }
    achados.sort(function (a, b) { return b[0] - a[0] || a[1] - b[1]; });
    if (!achados.length) { dizer("Nada encontrado para “" + campo.value.trim() + "”. Tente outra palavra ou menos letras."); return; }
    dizer(achados.length > MAX ? "Mostrando " + MAX + " de " + achados.length.toLocaleString("pt-BR") + " resultados. Continue digitando para afinar." : "");
    res.innerHTML = achados.slice(0, MAX).map(function (a) {
      var x = lista[a[1]];
      return '<li><a href="' + esc(RAIZ + String(x[1]).replace(RAIZ ? /^\// : /^$/, "")) + '"><span>' + realca(String(x[0]), termos) + "</span>" + (x[2] ? "<small>" + esc(x[2]) + "</small>" : "") + "</a></li>";
    }).join("");
  }

  function teclas(e) {
    var links = res.querySelectorAll("a");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!links.length) return;
      e.preventDefault();
      if (ativo >= 0 && links[ativo]) links[ativo].classList.remove("at");
      ativo = (ativo + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
      links[ativo].classList.add("at"); links[ativo].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      var alvo = links[ativo >= 0 ? ativo : 0];
      if (alvo) { e.preventDefault(); conta("ir"); location.href = alvo.href; }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", montar); else montar();
})();

"""Auditoria de navegação de um site estático: links/âncoras quebrados, páginas sem h1, títulos repetidos,
peso; e, no navegador (celular 390px), erros de JS, recursos que falham e rolagem horizontal."""
import sys, os, re, json, random, collections, threading, functools, http.server, socketserver
from urllib.parse import urlparse, unquote
raiz = os.path.abspath(sys.argv[1]); nome = sys.argv[2]; amostra = int(sys.argv[3]) if len(sys.argv) > 3 else 40
paginas = {}
for dp, _, fs in os.walk(raiz):
    for f in fs:
        if f.endswith(".html"):
            p = os.path.join(dp, f); rel = "/" + os.path.relpath(p, raiz).replace(os.sep, "/")
            paginas[rel] = p
def alvo(rel):
    rel = unquote(rel)
    c = os.path.join(raiz, rel.lstrip("/"))
    if rel.endswith("/"): c = os.path.join(c, "index.html")
    if os.path.isfile(c): return c
    if os.path.isfile(os.path.join(c, "index.html")): return os.path.join(c, "index.html")
    if os.path.isfile(c + ".html"): return c + ".html"
    return None
ids_cache = {}
def ids(p):
    if p not in ids_cache:
        ids_cache[p] = set(re.findall(r'\bid="([^"]+)"', open(p, encoding="utf-8", errors="ignore").read()))
    return ids_cache[p]
quebrados = collections.Counter(); ancoras = collections.Counter(); origem = {}
sem_h1 = []; titulos = collections.defaultdict(list); pesados = []; multi_h1 = []
for rel, p in paginas.items():
    t = open(p, encoding="utf-8", errors="ignore").read()
    if rel.endswith("404.html"): continue
    n_h1 = len(re.findall(r"<h1[\s>]", t))
    if n_h1 == 0: sem_h1.append(rel)
    elif n_h1 > 1: multi_h1.append(rel)
    m = re.search(r"<title>(.*?)</title>", t, re.S)
    if m: titulos[m.group(1).strip()].append(rel)
    if len(t) > 400_000: pesados.append((rel, len(t)//1024))
    base = rel.rsplit("/", 1)[0] + "/"
    for h in re.findall(r'href="([^"]+)"', t):
        if h.startswith(("http", "mailto:", "tel:", "javascript:", "data:", "//")): continue
        u = urlparse(h)
        caminho = u.path
        if not caminho:
            if u.fragment and u.fragment not in ids(p): ancoras[(rel, "#" + u.fragment)] += 1
            continue
        if not caminho.startswith("/"): caminho = os.path.normpath(base + caminho) + ("/" if caminho.endswith("/") else "")
        a = alvo(caminho)
        if not a and not re.search(r"\.(css|js|png|svg|ico|xml|txt|json|csv|xlsx|webmanifest|jpg|webp|pdf|zip)$", caminho):
            quebrados[caminho] += 1; origem.setdefault(caminho, rel)
        elif a and u.fragment and a.endswith(".html") and u.fragment not in ids(a):
            ancoras[(caminho, "#" + u.fragment)] += 1
print(f"== {nome}: {len(paginas)} páginas")
print("links quebrados (destinos distintos):", len(quebrados))
for k, v in quebrados.most_common(12): print("  ", k, v, "ex.:", origem[k])
print("âncoras inexistentes:", len(ancoras))
for k, v in list(ancoras.most_common(8)): print("  ", k, v)
print("sem h1:", len(sem_h1), sem_h1[:5]); print("mais de um h1:", len(multi_h1), multi_h1[:3])
dup = {k: v for k, v in titulos.items() if len(v) > 1}
print("títulos repetidos:", len(dup), [(k[:60], len(v)) for k, v in list(dup.items())[:4]])
print("páginas > 400 KB:", len(pesados), sorted(pesados, key=lambda x: -x[1])[:5])
# navegador
class _H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
Handler = functools.partial(_H, directory=raiz)
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler); porta = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
grupos = collections.defaultdict(list)
for rel in paginas:
    if rel.endswith("404.html"): continue
    grupos[rel.strip("/").split("/")[0] if rel.count("/") > 1 else "_raiz"].append(rel)
random.seed(1); sel = []
for g, l in sorted(grupos.items()):
    l = sorted(l); sel += [l[0]] + random.sample(l[1:], min(2, len(l) - 1))
extra = [x for x in sys.argv[4:]]
sel = list(dict.fromkeys(extra + sel))[:amostra]
from playwright.sync_api import sync_playwright
prob = []
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True, has_touch=True)
    ctx.route(re.compile(r"^https?://(?!127\.0\.0\.1).*"), lambda r: r.abort())
    for rel in sel:
        pg = ctx.new_page(); errs = []; falhas = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
        pg.on("console", lambda m: errs.append("console: " + m.text[:160]) if m.type == "error" and "net::ERR_FAILED" not in m.text and "ERR_BLOCKED" not in m.text else None)
        pg.on("requestfailed", lambda r: falhas.append(r.url) if "127.0.0.1" in r.url else None)
        pg.on("response", lambda r: falhas.append(f"{r.status} {r.url}") if r.status >= 400 and "127.0.0.1" in r.url else None)
        url = rel[:-10] if rel.endswith("index.html") else rel
        try:
            pg.goto(f"http://127.0.0.1:{porta}{url}", wait_until="load", timeout=30000); pg.wait_for_timeout(500)
            info = pg.evaluate("""() => { const W = innerWidth; const larg = [];
              for (const e of document.querySelectorAll('body *')) { const r = e.getBoundingClientRect();
                if (r.right > W + 2 && r.width > 0 && getComputedStyle(e).position !== 'fixed') {
                  let p = e.parentElement, cortado = false;
                  while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { cortado = true; break } p = p.parentElement }
                  if (!cortado) larg.push(e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '') + ' ' + Math.round(r.right)) } }
              const peq = [...document.querySelectorAll('a,button,select,input')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.height < 24 && getComputedStyle(e).display !== 'inline' }).length;
              return {sw: document.scrollingElement.scrollWidth, larg: larg.slice(0, 4), h: document.body.scrollHeight, peq} }""")
        except Exception as e:
            errs.append("carregar: " + str(e)[:120]); info = {"sw": 0, "larg": [], "h": 0, "peq": 0}
        if errs or falhas or info["sw"] > 392 or info["larg"]:
            prob.append((rel, errs[:3], falhas[:3], info["sw"], info["larg"]))
        pg.close()
    b.close()
print(f"navegador: {len(sel)} páginas testadas em 390px; com problema: {len(prob)}")
for p in prob[:15]: print("  ", p)
srv.shutdown()

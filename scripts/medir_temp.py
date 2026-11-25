import sys, re, threading, functools, http.server, socketserver, os
raiz=os.path.abspath(sys.argv[1]); paths=sys.argv[3:]; shots=sys.argv[2]
class _H(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*a): pass
srv=socketserver.ThreadingTCPServer(("127.0.0.1",0),functools.partial(_H,directory=raiz)); porta=srv.server_address[1]
threading.Thread(target=srv.serve_forever,daemon=True).start()
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome",args=["--no-sandbox"])
    ctx=b.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True)
    ctx.route(re.compile(r"^https?://(?!127\.0\.0\.1).*"),lambda r:r.abort())
    for p in paths:
        pg=ctx.new_page(); pg.goto(f"http://127.0.0.1:{porta}{p}",wait_until="load"); pg.wait_for_timeout(400)
        i=pg.evaluate("""()=>({h:document.body.scrollHeight, secs:[...document.querySelectorAll('main > section, main > div, main > .grid2, main > details')].map(s=>{const h=s.querySelector('h1,h2');return (h?h.textContent.trim().slice(0,38):s.className.slice(0,20))+':'+Math.round(s.getBoundingClientRect().height)}), nav:[...document.querySelectorAll('header nav a, nav a')].length})""")
        print(p, "altura", i["h"], "px =", round(i["h"]/844,1), "telas | links no menu:", i["nav"]); print("   ", " | ".join(i["secs"]))
        if shots!="-": pg.screenshot(path=f"{shots}{p.strip('/').replace('/','_') or 'home'}.png", full_page=True)
        pg.close()
    b.close()

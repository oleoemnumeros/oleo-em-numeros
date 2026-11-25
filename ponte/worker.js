// Ponte do Óleo em Números para o gov.br (Cloudflare Worker).
// O gov.br recusa conexões dos servidores do GitHub; o robô pede o arquivo aqui e o Worker busca no gov.br.
// Só atende quem manda o token certo (segredo TOKEN do Worker) e só busca em destinos da lista.
const PERMITIDOS = ["www.gov.br", "ftp.ibge.gov.br", "portal.inmet.gov.br"];

export default {
  async fetch(pedido, env) {
    if (pedido.headers.get("x-token") !== env.TOKEN) return new Response("proibido", { status: 403 });
    const alvo = new URL(pedido.url).searchParams.get("url") || "";
    let host = "";
    try { host = new URL(alvo).hostname; } catch (e) { return new Response("url inválida", { status: 400 }); }
    if (!PERMITIDOS.includes(host)) return new Response("destino não permitido", { status: 403 });
    const r = await fetch(alvo, {
      redirect: "follow",
      headers: { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36 OleoEmNumeros" },
    });
    return new Response(r.body, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") || "application/octet-stream" },
    });
  },
};

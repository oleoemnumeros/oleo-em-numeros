"""Configurações do robô de dados do Óleo em Números."""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
BRUTOS = DADOS / "brutos"            # arquivos baixados da ANP, sem alteração
PROCESSADOS = DADOS / "processados"  # base normalizada e agregações
MANUAIS = DADOS / "manuais"          # ajustes feitos à mão (ex.: operador por campo)

# Páginas da ANP de onde o robô lê os links dos arquivos. Os nomes dos arquivos
# mudam de um ano para outro, então o robô lê os links em vez de montar URLs.
PAGINAS_ANP = [
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/producao-de-petroleo-e-gas-natural-por-poco",
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/fase-de-desenvolvimento-e-producao",
]

# Só baixa links cujo endereço combine com um destes padrões (produção por poço).
PADROES_LINKS = [
    r"arquivos-producao-de-petroleo-e-gas-natural-por-poco/.+\.(zip|csv)$",
    r"arquivos-producao-de-petroleo-e-gas-natural-nacional/(pm|pt)/.+\.(zip|csv)$",
]

# O site da ANP recusa requisições sem um user-agent de navegador.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 OleoEmNumeros/0.1"
)

ANO_INICIAL = 2018          # primeiro ano carregado na base
M3_PARA_BBL = 6.289811      # 1 m³ = 6,289811 barris

# Limites da conferência automática: acima disso, a publicação trava.
LIMITE_VARIACAO_BRASIL = 0.15   # 15% de variação mensal no petróleo do Brasil
LIMITE_VARIACAO_POCOS = 0.20    # 20% de variação no número de poços ativos

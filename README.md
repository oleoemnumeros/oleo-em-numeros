# Óleo em Números

Portal gratuito com os dados públicos de produção de petróleo e gás do Brasil, organizados por
operadora, campo e bacia. Um robô baixa os arquivos da ANP, confere os números, gera o site
estático e publica tudo sozinho, três vezes por mês.

## Como funciona

```
ANP (dados abertos)
   │  robo/baixar.py    lê os links das páginas da ANP e baixa só o que é novo
   ▼
dados/brutos/           arquivos originais, sem alteração
   │  robo/ler.py       junta os dois formatos da ANP numa base única, poço a poço
   │  robo/agregar.py   calcula Brasil, bacias, operadoras, campos e rankings
   │  robo/conferir.py  trava a publicação se algo sair da curva
   ▼
dados/processados/      base normalizada, agregados.json e conferencia.md
   │  gerador/gerar.py  monta as páginas HTML com gráficos em SVG
   ▼
publico/                site pronto: é isso que vai para a internet
```

### Os dois formatos da ANP

| Formato | Período | Unidades | Operador | Observação |
| --- | --- | --- | --- | --- |
| Produção por poço (ZIP mensal) | até 2023 | bbl/dia e mil m³/dia | sim | Arquivos Terra, Mar e PreSal. O PreSal repete parte do Mar e é ignorado. |
| Fase de desenvolvimento e produção (CSV) | 2019 em diante | m³ e mil m³ no mês | pode faltar | Convertido para bbl/d dividindo pelos dias do mês. Operador vem do último operador conhecido do campo. |

Quando os dois formatos cobrem o mesmo mês, fica o formato por poço.
Se aparecer um campo sem operador, preencha `dados/manuais/operador_por_campo.csv`.

## Rodar no computador

Precisa de Python 3.11 ou mais novo.

```bash
pip install -r requirements.txt

# Com dados fictícios, sem internet
python -m testes.gerar_amostras
python -m robo.executar --brutos testes/amostras
python -m gerador.gerar --teste
# abra publico/index.html no navegador

# Com os dados reais da ANP
python -m robo.executar
python -m gerador.gerar
```

Testes: `python -m pytest -q`

## Publicar (GitHub Pages, gratuito)

1. Crie um repositório no GitHub (pode ser privado só se a conta tiver GitHub Pro; público funciona no plano gratuito).
2. Envie esta pasta para o repositório.
3. Em **Settings › Pages**, escolha **Source: GitHub Actions**.
4. Em **Settings › Secrets and variables › Actions › Variables**, crie:
   - `SITE_URL` = `https://oleoemnumeros.com.br`
   - `SITE_CONTATO` = e-mail do projeto (aparece na página de metodologia)
5. Em **Actions › Atualização mensal**, clique em **Run workflow** para a primeira execução.
6. Para usar o domínio: em **Settings › Pages › Custom domain**, informe `oleoemnumeros.com.br`
   e crie no Registro.br os registros DNS que o GitHub indicar.

Depois disso, o robô roda sozinho nos dias 5, 15 e 25 de cada mês.

## Rotina mensal (cerca de 15 minutos)

1. Abra a última execução em **Actions** e leia o resumo da conferência.
2. Confira 3 números do site contra o Painel Dinâmico da ANP.
3. Se a execução falhou, o site continua com a versão anterior. O resumo diz o motivo.

## Pendências conhecidas

- **Validar com os arquivos reais da ANP.** O leitor foi escrito a partir dos metadados oficiais
  (formato por poço) e da descrição pública do formato mensal, e testado com arquivos fictícios.
  Na primeira execução real, ajustes nos nomes de coluna podem ser necessários.
- **Licença de uso dos dados da ANP:** confirmar os termos antes do lançamento público.
- **Royalties, Raio-X, Radar e as demais páginas** entram nos meses seguintes do plano.

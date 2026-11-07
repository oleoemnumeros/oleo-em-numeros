"""Executa o ciclo mensal: baixar, ler, agregar e conferir.

Uso:
    python -m robo.executar                 # baixa da ANP e processa
    python -m robo.executar --sem-baixar    # usa o que já está em dados/brutos
    python -m robo.executar --brutos testes/amostras   # roda com outra pasta
"""
import argparse
import json
import sys
from pathlib import Path

from . import config
from .agregar import agregar
from .conferir import conferir, relatorio
from .ler import consolidar


def _leve(ag: dict) -> dict:
    """Versão do JSON sem repetir os campos dentro de cada operadora."""
    copia = dict(ag)
    copia["operadores"] = [{**o, "campos": [c["slug"] for c in o["campos"]]} for o in ag["operadores"]]
    copia["rankings"] = {k: {lado: [c["slug"] for c in lista] for lado, lista in v.items()}
                         for k, v in ag["rankings"].items()}
    return copia


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sem-baixar", action="store_true")
    p.add_argument("--brutos", type=Path, default=config.BRUTOS)
    p.add_argument("--saida", type=Path, default=config.PROCESSADOS)
    a = p.parse_args(argv)

    if not a.sem_baixar and a.brutos == config.BRUTOS:
        from .baixar import baixar_tudo
        r = baixar_tudo(a.brutos)
        print(f"Baixados: {len(r['novos'])} novos, {len(r['sem_mudanca'])} sem mudança.")

    base = consolidar(a.brutos)
    ag = agregar(base)
    ok, criticos, avisos = conferir(base, ag)

    a.saida.mkdir(parents=True, exist_ok=True)
    base.to_csv(a.saida / "base_pocos.csv.gz", index=False, compression="gzip")
    (a.saida / "agregados.json").write_text(json.dumps(_leve(ag), ensure_ascii=False, default=str), "utf-8")
    texto = relatorio(ag, criticos, avisos)
    (a.saida / "conferencia.md").write_text(texto, "utf-8")
    print(texto)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

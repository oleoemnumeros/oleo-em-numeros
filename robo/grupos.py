"""Grupos econômicos: junta as empresas que a ANP registra separadamente
(3R Potiguar, 3R Bahia, Enauta...) no grupo que as controla (Brava Energia).

A tabela fica em dados/manuais/grupo_economico.csv, com duas colunas:
operador (nome como a ANP escreve; aceita * no fim, como '3R *') e grupo.
"""
import fnmatch
from pathlib import Path

import pandas as pd

from . import config
from .util import sem_acento

ARQUIVO = config.MANUAIS / "grupo_economico.csv"


def _chave(texto: str) -> str:
    return sem_acento(str(texto)).casefold().strip()


def carregar(caminho: Path = ARQUIVO) -> list[tuple[str, str]]:
    if not caminho.exists():
        return []
    t = pd.read_csv(caminho, dtype=str, comment="#").dropna()
    return [(_chave(o), g.strip()) for o, g in zip(t["operador"], t["grupo"])]


def grupo_de(operador: str, regras: list[tuple[str, str]]) -> str:
    """Primeira regra que casar; sem regra, o grupo é a própria empresa."""
    k = _chave(operador)
    for padrao, grupo in regras:
        if fnmatch.fnmatchcase(k, padrao):
            return grupo
    return operador

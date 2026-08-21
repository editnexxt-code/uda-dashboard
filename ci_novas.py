"""Quantas partidas a ultima coleta salvou. Usado pelo GitHub Actions para
decidir se vale gravar o cache do banco (48 MB) e publicar o site.

Imprime um inteiro e nada mais. Nunca levanta excecao: se o banco nao existe
ou a coleta caiu antes de gravar a marca, imprime 0 -- e quem chama trata isso
como "nada novo". Falhar aqui pararia a publicacao por um motivo bobo.
"""

from __future__ import annotations

import os
import sqlite3

DB = os.path.join("data", "uda.sqlite3")


def novas() -> int:
    if not os.path.exists(DB):
        return 0
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='last_fetch_saved'"
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0
    if not row:
        return 0
    try:
        return max(0, int(str(row[0]).strip()))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    print(novas())

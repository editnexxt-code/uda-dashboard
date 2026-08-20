"""Onde cada um morre -- o mapa de calor tirado da timeline.

Cada CHAMPION_KILL da timeline vem com a coordenada exata no mapa. Juntando as
mortes de uma pessoa ao longo de centenas de partidas aparece o padrao que
ninguem admite: quem morre sempre no mesmo mato, quem so morre invadindo, e quem
morre dentro da propria base.

So Summoner's Rift entra. Howling Abyss (ARAM) usa outra escala de coordenada e
misturar os dois viraria borrao -- por isso o recorte e por game_mode CLASSIC, e
nao pela fila escolhida.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from .kpi import _r, _safe_div

MAPA_MAX = 14870          # extensao do Rift nos dois eixos
GRADE = 16                # 16x16 = 256 celulas; abaixo disso vira mancha unica
MIN_MORTES = 25           # menos que isso nao forma padrao, forma acaso
MIN_PARTIDAS = 5


def _celula(x: int, y: int) -> int:
    """(x, y) do jogo -> indice 0..255, ja com o eixo Y invertido.

    No jogo Y cresce para CIMA; em SVG cresce para BAIXO. A inversao mora aqui
    para o template nao precisar saber disso.
    """
    col = min(GRADE - 1, max(0, int(x / MAPA_MAX * GRADE)))
    lin = min(GRADE - 1, max(0, int(y / MAPA_MAX * GRADE)))
    return (GRADE - 1 - lin) * GRADE + col


def construir(conn: sqlite3.Connection, players: dict,
              min_partidas: int = MIN_PARTIDAS) -> dict:
    linhas = conn.execute("""
        SELECT t.puuid, t.mortes_json, t.abates_json, t.primeira_morte,
               t.ouro10, t.ouro15, t.cs10, p.team_id
          FROM timeline_stats t
          JOIN matches m ON m.match_id = t.match_id
          JOIN participants p ON p.match_id = t.match_id AND p.puuid = t.puuid
         WHERE m.game_mode = 'CLASSIC' AND p.tracked = 1
           AND m.game_duration >= 300
    """).fetchall()
    if not linhas:
        return {"fichas": [], "grade": GRADE, "partidasComTimeline": 0}

    acc: dict[str, dict] = defaultdict(lambda: {
        "mortes": defaultdict(int), "abates": defaultdict(int),
        "n": 0, "totalMortes": 0, "totalAbates": 0, "foraDeCasa": 0,
        "primeiras": [], "ouro10": 0.0, "ouro15": 0.0, "cs10": 0.0,
    })

    for r in linhas:
        puuid = r["puuid"]
        if puuid not in players:
            continue
        a = acc[puuid]
        a["n"] += 1
        a["ouro10"] += r["ouro10"] or 0
        a["ouro15"] += r["ouro15"] or 0
        a["cs10"] += r["cs10"] or 0
        if r["primeira_morte"]:
            a["primeiras"].append(r["primeira_morte"] / 60000.0)

        # A base azul (time 100) fica no canto de baixo-esquerda; a vermelha em
        # cima-direita. A diagonal x+y = MAPA_MAX separa os dois lados, entao da
        # para dizer se a morte foi em casa ou em territorio inimigo.
        azul = (r["team_id"] or 100) == 100
        for x, y, _m in json.loads(r["mortes_json"] or "[]"):
            a["mortes"][_celula(x, y)] += 1
            a["totalMortes"] += 1
            longe = (x + y) > MAPA_MAX if azul else (x + y) < MAPA_MAX
            if longe:
                a["foraDeCasa"] += 1
        for x, y, _m in json.loads(r["abates_json"] or "[]"):
            a["abates"][_celula(x, y)] += 1
            a["totalAbates"] += 1

    fichas = []
    grupo_mortes: dict[int, int] = defaultdict(int)
    for puuid, a in acc.items():
        if a["n"] < min_partidas:
            continue
        for c, n in a["mortes"].items():
            grupo_mortes[c] += n
        pri = sorted(a["primeiras"])
        mediana = pri[len(pri) // 2] if pri else 0.0
        fichas.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "partidas": a["n"],
            "mortes": a["totalMortes"],
            "abates": a["totalAbates"],
            # celulas viajam como [indice, contagem] -- so as ocupadas
            "gradeMortes": sorted(a["mortes"].items()),
            "gradeAbates": sorted(a["abates"].items()),
            "temGrade": a["totalMortes"] >= MIN_MORTES,
            "foraDeCasa": _r(_safe_div(a["foraDeCasa"], a["totalMortes"]) * 100, 1),
            "primeiraMorte": _r(mediana, 1),
            "ouro10": int(round(_safe_div(a["ouro10"], a["n"]))),
            "ouro15": int(round(_safe_div(a["ouro15"], a["n"]))),
            "cs10": _r(_safe_div(a["cs10"], a["n"]), 1),
        })

    fichas.sort(key=lambda x: -x["foraDeCasa"])
    return {
        "fichas": fichas,
        "grade": GRADE,
        "gradeGrupo": sorted(grupo_mortes.items()),
        "minMortes": MIN_MORTES,
        "partidasComTimeline": len({(r["puuid"]) for r in linhas}) and len(linhas),
    }

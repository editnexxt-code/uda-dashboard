"""Series temporais para a aba Evolucao.

Tudo sai da tabela participants, que ja guarda game_creation em cada linha.
Nenhuma dessas metricas custa uma requisicao a mais na Riot.
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from typing import Any

from .kpi import (
    WEIGHT_PROFILES,
    _apply_scores,
    _accumulate,
    _blank,
    _finalize,
    _r,
    _safe_div,
    _score_metrics,
)

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _cronologico(linhas: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """_load_rows entrega do mais novo para o mais velho; series precisam do inverso."""
    return sorted(linhas, key=lambda r: r["game_creation"])


# --------------------------------------------------------------- media movel

def media_movel(rows_by_player: dict[str, list[sqlite3.Row]],
                players: dict[str, dict], janela: int = 10) -> list[dict]:
    """Forma recente: media das ultimas <janela> partidas, partida a partida."""
    saida = []
    for puuid, linhas in rows_by_player.items():
        if puuid not in players or len(linhas) < janela:
            continue
        linhas = _cronologico(linhas)
        pontos = []
        for i in range(janela - 1, len(linhas)):
            fatia = linhas[i - janela + 1:i + 1]
            vitorias = sum(r["win"] for r in fatia)
            k = sum(r["kills"] for r in fatia)
            d = sum(r["deaths"] for r in fatia)
            a = sum(r["assists"] for r in fatia)
            pontos.append({
                "i": i + 1,
                "data": linhas[i]["game_creation"],
                "winrate": _r(vitorias / janela * 100, 1),
                "kda": _r(_safe_div(k + a, max(d, 1))),
            })
        saida.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "janela": janela,
            "pontos": pontos,
        })
    saida.sort(key=lambda s: -s["pontos"][-1]["kda"] if s["pontos"] else 0)
    return saida


# -------------------------------------------------------------- score mensal

def score_mensal(rows_by_player: dict[str, list[sqlite3.Row]],
                 players: dict[str, dict], min_partidas: int = 5) -> list[dict]:
    """UDA Score recalculado dentro de cada mes, com a mesma logica do ranking geral."""
    por_mes: dict[str, dict[str, list[sqlite3.Row]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for puuid, linhas in rows_by_player.items():
        if puuid not in players:
            continue
        for r in linhas:
            mes = time.strftime("%Y-%m", time.localtime(r["game_creation"] / 1000))
            por_mes[mes][puuid].append(r)

    saida = []
    for mes in sorted(por_mes):
        entradas = []
        for puuid, linhas in por_mes[mes].items():
            if len(linhas) < min_partidas:
                continue
            acc = _blank()
            for r in linhas:
                _accumulate(acc, r)
            stats = _finalize(acc)
            entradas.append({
                "puuid": puuid,
                "gameName": players[puuid]["gameName"],
                "games": stats["games"],
                "metrics": _score_metrics(stats, "default"),
            })
        if len(entradas) < 2:
            continue
        _apply_scores(entradas, "default")
        entradas.sort(key=lambda e: -e["score"])
        for i, e in enumerate(entradas, 1):
            e["rank"] = i
            e.pop("metrics", None)
            e.pop("radar", None)
        saida.append({"mes": mes, "entradas": entradas})
    return saida


# ----------------------------------------------------------- saldo acumulado

def saldo_acumulado(rows_by_player: dict[str, list[sqlite3.Row]],
                    players: dict[str, dict], min_partidas: int = 10) -> list[dict]:
    """Curva de vitorias menos derrotas. Acima de zero e lucro."""
    saida = []
    for puuid, linhas in rows_by_player.items():
        if puuid not in players or len(linhas) < min_partidas:
            continue
        saldo = 0
        pontos = []
        for i, r in enumerate(_cronologico(linhas), 1):
            saldo += 1 if r["win"] else -1
            pontos.append({"jogo_n": i, "data": r["game_creation"], "saldo": saldo})
        saida.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "final": saldo,
            "pontos": pontos,
        })
    saida.sort(key=lambda s: -s["final"])
    return saida


# ---------------------------------------------------------- mapa de atividade

def mapa_atividade(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Partidas por dia da semana (0=segunda) x hora do dia, no fuso local."""
    celulas = [[0] * 24 for _ in range(7)]
    vistas: set[str] = set()
    for r in rows:
        if r["match_id"] in vistas:
            continue
        vistas.add(r["match_id"])
        t = time.localtime(r["game_creation"] / 1000)
        celulas[t.tm_wday][t.tm_hour] += 1

    pico = {"dia": 0, "hora": 0, "n": 0}
    for d in range(7):
        for hh in range(24):
            if celulas[d][hh] > pico["n"]:
                pico = {"dia": d, "hora": hh, "n": celulas[d][hh]}
    return {
        "celulas": celulas,
        "total": len(vistas),
        "pico": pico if pico["n"] else None,
        "diaLabel": DIAS[pico["dia"]] if pico["n"] else "",
    }


# ------------------------------------------------------------------ sequencias

def sequencias(rows_by_player: dict[str, list[sqlite3.Row]],
               players: dict[str, dict]) -> list[dict]:
    """Maior sequencia de vitorias, de derrotas, e a sequencia em andamento."""
    saida = []
    for puuid, linhas in rows_by_player.items():
        if puuid not in players or not linhas:
            continue
        cron = _cronologico(linhas)
        maior_v = maior_d = corrida = 0
        for r in cron:
            if r["win"]:
                corrida = corrida + 1 if corrida > 0 else 1
                maior_v = max(maior_v, corrida)
            else:
                corrida = corrida - 1 if corrida < 0 else -1
                maior_d = max(maior_d, -corrida)
        saida.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "maiorVitorias": maior_v,
            "maiorDerrotas": maior_d,
            "atual": corrida,      # positivo = vitorias, negativo = derrotas
            "games": len(cron),
        })
    saida.sort(key=lambda s: (-s["atual"], -s["maiorVitorias"]))
    return saida


# ------------------------------------------------------------------ montagem

def construir(rows: list[sqlite3.Row], rows_by_player: dict[str, list[sqlite3.Row]],
              players: dict[str, dict], min_games: int) -> dict[str, Any]:
    """Bloco de evolucao de um grupo (uma aba de fila)."""
    return {
        "mediaMovel": media_movel(rows_by_player, players),
        "scoreMensal": score_mensal(rows_by_player, players, min_games),
        "saldo": saldo_acumulado(rows_by_player, players),
        "atividade": mapa_atividade(rows),
        "sequencias": sequencias(rows_by_player, players),
    }

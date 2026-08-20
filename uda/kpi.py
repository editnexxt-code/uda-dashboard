"""Calculo dos KPIs. Le o SQLite e devolve o payload JSON do dashboard."""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from statistics import mean, pstdev
from typing import Any

from . import inhouse as _inhouse

# ---------------------------------------------------------------- definicoes

# Classificar por queueId nao funciona: a Riot cria IDs novos sem avisar e
# static.developer.riotgames.com/docs/lol/queues.json fica desatualizado
# (1740 e 1750 sao Arena e nao constam la). gameMode vem dentro da propria
# partida e e estavel, entao e ele quem manda.

# Modos com estrutura de time incompativel com 5v5. Em Arena (CHERRY) a API
# agrupa todo mundo em 2 teamIds, entao "abates do time" e "% do dano" somariam
# 9 jogadores e destruiriam participacao e dano de quem joga.
BROKEN_MODES = {"CHERRY", "STRAWBERRY", "TUTORIAL", "TUTORIAL_MODULE_1",
                "TUTORIAL_MODULE_2", "TUTORIAL_MODULE_3", "PRACTICETOOL"}
BOT_QUEUES = {830, 840, 850, 870, 880, 890, 1090, 1100, 1110, 1120}
TUTORIAL_QUEUES = {2000, 2010, 2020}

# Modos 5v5 na Fenda com regras normais.
CLASSIC_MODES = {"CLASSIC", "SWIFTPLAY"}
RANKED_QUEUES = {420, 440}

# PERSONALIZADAS. 3200/3220 sao ARAM personalizada e 3270 e o modo Kiwi. Elas
# TEM de ser recortadas por queueId e nao por gameMode: o cliente devolve
# gameMode="ARAM" nas 11 primeiras e "KIWI" nas 6 outras, entao sem esta lista
# elas vazariam para a fila "ARAM" e para "Outros modos" sem ninguem perceber.
# 3140 (PRACTICETOOL) ja cai em BROKEN_MODES pelo game_mode; nao mexer.
INHOUSE_QUEUES = _inhouse.INHOUSE_QUEUES


def _excluded(queue_id: int, mode: str) -> bool:
    return (mode or "").upper() in BROKEN_MODES or queue_id in (
        BOT_QUEUES | TUTORIAL_QUEUES
    )


def _grupo_normal(queue_id: int, mode: str) -> bool:
    return (mode or "").upper() in CLASSIC_MODES and queue_id not in RANKED_QUEUES


def _grupo_outros(queue_id: int, mode: str) -> bool:
    m = (mode or "").upper()
    return (m not in CLASSIC_MODES and m != "ARAM"
            and queue_id not in INHOUSE_QUEUES)


# key, rotulo, teste(queue_id, game_mode), so-partidas-em-equipe
# "Todas" EXCLUI as personalizadas de proposito: numa inhouse alguem da UDA
# sempre ganha e alguem sempre perde, entao juntar as duas coisas puxaria o
# aproveitamento do grupo para 50% por construcao (medido: 48,6% nas 17) e
# uniqueMatches contaria como "UDA contra o servidor" partidas que sao UDA
# contra UDA. A fila propria e o lugar delas.
GROUPS: list[dict] = [
    {"key": "all", "label": "Todas",
     "test": lambda q, m: q not in INHOUSE_QUEUES, "squad": False},
    {"key": "solo", "label": "Ranked Solo",
     "test": lambda q, m: q == 420, "squad": False},
    {"key": "flex", "label": "Ranked Flex",
     "test": lambda q, m: q == 440, "squad": False},
    {"key": "normal", "label": "Normais", "test": _grupo_normal, "squad": False},
    {"key": "aram", "label": "ARAM",
     "test": lambda q, m: (m or "").upper() == "ARAM"
     and q not in INHOUSE_QUEUES, "squad": False},
    {"key": "inhouse", "label": "Personalizadas",
     "test": lambda q, m: q in INHOUSE_QUEUES, "squad": False},
    {"key": "outros", "label": "Outros modos",
     "test": _grupo_outros, "squad": False},
    {"key": "squad", "label": "Em equipe", "test": None, "squad": True},
]

QUEUE_NAMES = {
    400: "Normal Draft", 420: "Ranked Solo", 430: "Normal Blind", 440: "Ranked Flex",
    450: "ARAM", 480: "Swiftplay", 490: "Rapida",
    700: "Clash", 710: "Clash", 720: "ARAM Clash",
    900: "ARURF", 1020: "Um pra Todos", 1400: "Livro de Feiticos",
    1700: "Arena", 1710: "Arena", 1740: "Arena", 1750: "Arena", 1900: "URF",
    3140: "Ferramenta de treino",
    3200: "ARAM personalizada", 3220: "ARAM personalizada", 3270: "Modo Kiwi",
}

TIER_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
              "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISION_ORDER = ["IV", "III", "II", "I"]

# Pesos do UDA Score. Somam 1.0 dentro de cada perfil.
WEIGHT_PROFILES = {
    "default": {
        "winrate": 0.28, "kda": 0.18, "kp": 0.12, "dmg_min": 0.14,
        "cs_min": 0.10, "vision_min": 0.08, "survival": 0.10,
    },
    "aram": {
        "winrate": 0.30, "kda": 0.20, "kp": 0.10, "dmg_min": 0.25, "survival": 0.15,
    },
    # Personalizadas nao reusam o perfil "aram" cru. La vitoria pesa 0,30, e aqui
    # ela depende em parte de COM QUEM o sorteio te colocou: os dois times sao da
    # UDA, entao metade do grupo perde toda partida por construcao. Vitoria desce,
    # dano sobe -- dano e o que continua sendo so seu.
    "inhouse": {
        "winrate": 0.22, "kda": 0.22, "kp": 0.16, "dmg_min": 0.28, "survival": 0.12,
    },
}

METRIC_LABELS = {
    "winrate": "Vitórias", "kda": "KDA", "kp": "Participação",
    "dmg_min": "Dano/min", "cs_min": "CS/min", "vision_min": "Visão/min",
    "survival": "Sobrevivência",
}


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _r(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


# ------------------------------------------------------------------- leitura

def _load_rows(conn: sqlite3.Connection, window_days: int) -> list[sqlite3.Row]:
    since = 0
    if window_days > 0:
        since = int((time.time() - window_days * 86400) * 1000)
    linhas = list(conn.execute(
        """
        SELECT p.*, m.game_mode,
               (SELECT SUM(q.damage_champions) FROM participants q
                 WHERE q.match_id = p.match_id AND q.team_id = p.team_id) AS team_damage,
               (SELECT SUM(q.gold) FROM participants q
                 WHERE q.match_id = p.match_id AND q.team_id = p.team_id) AS team_gold
        FROM participants p
        JOIN matches m ON m.match_id = p.match_id
        WHERE p.tracked = 1
          AND p.game_creation >= ?
          AND p.game_duration >= 300
          AND p.early_surrender = 0
        ORDER BY p.game_creation DESC
        """,
        (since,),
    ))
    return [r for r in linhas if not _excluded(r["queue_id"], r["game_mode"])]


def _load_players(conn: sqlite3.Connection) -> dict[str, dict]:
    players: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM players"):
        players[row["puuid"]] = {
            "puuid": row["puuid"],
            "gameName": row["game_name"],
            "tagLine": row["tag_line"],
            "riotId": f"{row['game_name']}#{row['tag_line']}",
            "icon": row["profile_icon_id"] or 29,
            "level": row["summoner_level"] or 0,
            "ranks": {},
        }
    for row in conn.execute("SELECT * FROM ranks"):
        player = players.get(row["puuid"])
        if not player:
            continue
        queue = "solo" if row["queue_type"] == "RANKED_SOLO_5x5" else (
            "flex" if row["queue_type"] == "RANKED_FLEX_SR" else row["queue_type"]
        )
        wins, losses = row["wins"] or 0, row["losses"] or 0
        player["ranks"][queue] = {
            "tier": row["tier"] or "UNRANKED",
            "division": row["division"] or "",
            "lp": row["lp"] or 0,
            "wins": wins,
            "losses": losses,
            "winrate": _r(_safe_div(wins, wins + losses) * 100, 1),
            "hotStreak": bool(row["hot_streak"]),
            "weight": _rank_weight(row["tier"], row["division"], row["lp"]),
        }
    return players


def _rank_weight(tier: str | None, division: str | None, lp: int | None) -> int:
    if not tier or tier.upper() not in TIER_ORDER:
        return -1
    base = TIER_ORDER.index(tier.upper()) * 400
    if tier.upper() in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return base + int(lp or 0)
    div = DIVISION_ORDER.index(division.upper()) if division and division.upper() in DIVISION_ORDER else 0
    return base + div * 100 + int(lp or 0)


# ------------------------------------------------------------- agregacao base

def _blank() -> dict[str, Any]:
    return {
        "games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0,
        "cs": 0, "gold": 0, "dmg": 0, "dmg_taken": 0, "dmg_obj": 0,
        "heal_shield": 0, "vision": 0, "wards": 0, "control_wards": 0,
        "seconds": 0, "team_kills": 0, "team_damage": 0, "first_bloods": 0,
        "doubles": 0, "triples": 0, "quadras": 0, "pentas": 0,
        "turrets": 0, "surrenders": 0,
    }


# Coluna que pode ser NULL de verdade. As personalizadas vem do cliente do LoL,
# que nao tem visao nenhuma no Abismo (visionScore medio 0,0, wardsPlaced zerado
# em 100% das linhas): gravar 0 seria afirmar "mediu zero", NULL e "nao existe
# medida". A soma nao pode quebrar por causa disso.
def _n(valor) -> int:
    return valor or 0


def _accumulate(acc: dict[str, Any], row: sqlite3.Row) -> None:
    acc["games"] += 1
    acc["wins"] += _n(row["win"])
    acc["kills"] += _n(row["kills"])
    acc["deaths"] += _n(row["deaths"])
    acc["assists"] += _n(row["assists"])
    acc["cs"] += _n(row["cs"])
    acc["gold"] += _n(row["gold"])
    acc["dmg"] += _n(row["damage_champions"])
    acc["dmg_taken"] += _n(row["damage_taken"])
    acc["dmg_obj"] += _n(row["damage_objectives"])
    acc["heal_shield"] += _n(row["heal_shield"])
    acc["vision"] += _n(row["vision_score"])
    acc["wards"] += _n(row["wards_placed"])
    acc["control_wards"] += _n(row["control_wards"])
    acc["seconds"] += _n(row["game_duration"])
    acc["team_kills"] += _n(row["team_kills"])
    acc["team_damage"] += _n(row["team_damage"])
    acc["first_bloods"] += _n(row["first_blood"]) + _n(row["first_blood_assist"])
    acc["doubles"] += _n(row["double_kills"])
    acc["triples"] += _n(row["triple_kills"])
    acc["quadras"] += _n(row["quadra_kills"])
    acc["pentas"] += _n(row["penta_kills"])
    acc["turrets"] += _n(row["turret_takedowns"])
    acc["surrenders"] += _n(row["surrender"])


def _finalize(acc: dict[str, Any]) -> dict[str, Any]:
    games = acc["games"]
    minutes = _safe_div(acc["seconds"], 60.0)
    deaths = acc["deaths"]
    return {
        "games": games,
        "wins": acc["wins"],
        "losses": games - acc["wins"],
        "winrate": _r(_safe_div(acc["wins"], games) * 100, 1),
        "kills": _r(_safe_div(acc["kills"], games)),
        "deaths": _r(_safe_div(deaths, games)),
        "assists": _r(_safe_div(acc["assists"], games)),
        "kda": _r(_safe_div(acc["kills"] + acc["assists"], max(deaths, 1))),
        "kp": _r(_safe_div(acc["kills"] + acc["assists"], acc["team_kills"]) * 100, 1),
        "cs_min": _r(_safe_div(acc["cs"], minutes)),
        "cs_game": _r(_safe_div(acc["cs"], games), 1),
        "gold_min": _r(_safe_div(acc["gold"], minutes), 0),
        "dmg_min": _r(_safe_div(acc["dmg"], minutes), 0),
        "dmg_game": _r(_safe_div(acc["dmg"], games), 0),
        "dmg_share": _r(_safe_div(acc["dmg"], acc["team_damage"]) * 100, 1),
        "dmg_taken_min": _r(_safe_div(acc["dmg_taken"], minutes), 0),
        "dmg_obj_game": _r(_safe_div(acc["dmg_obj"], games), 0),
        "heal_shield_game": _r(_safe_div(acc["heal_shield"], games), 0),
        "vision_min": _r(_safe_div(acc["vision"], minutes)),
        "vision_game": _r(_safe_div(acc["vision"], games), 1),
        "wards_game": _r(_safe_div(acc["wards"], games), 1),
        "control_wards_game": _r(_safe_div(acc["control_wards"], games), 1),
        "deaths_10min": _r(_safe_div(deaths, minutes) * 10),
        "survival": _r(_safe_div(minutes, max(deaths, 1)), 1),
        "avg_minutes": _r(_safe_div(minutes, games), 1),
        "first_blood_rate": _r(_safe_div(acc["first_bloods"], games) * 100, 1),
        "turrets_game": _r(_safe_div(acc["turrets"], games), 1),
        "surrender_rate": _r(_safe_div(acc["surrenders"], games) * 100, 1),
        "multikills": {
            "double": acc["doubles"], "triple": acc["triples"],
            "quadra": acc["quadras"], "penta": acc["pentas"],
        },
        "total_deaths": deaths,
        "total_kills": acc["kills"],
        "total_minutes": _r(minutes, 0),
    }


# ------------------------------------------------------------------ UDA Score

def _score_metrics(stats: dict[str, Any], profile: str) -> dict[str, float]:
    weights = WEIGHT_PROFILES[profile]
    raw = {
        "winrate": stats["winrate"],
        "kda": stats["kda"],
        "kp": stats["kp"],
        "dmg_min": stats["dmg_min"],
        "cs_min": stats["cs_min"],
        "vision_min": stats["vision_min"],
        # Sobrevivencia: minutos vividos por morte. Maior = melhor.
        "survival": stats["survival"],
    }
    return {k: raw[k] for k in weights}


def _apply_scores(entries: list[dict], profile: str) -> None:
    """Z-score dentro do grupo -> UDA Score 1..99 + detalhamento por metrica."""
    if not entries:
        return
    weights = WEIGHT_PROFILES[profile]
    metrics = list(weights)
    values = {m: [e["metrics"][m] for e in entries] for m in metrics}
    stats = {}
    for m in metrics:
        vals = values[m]
        mu = mean(vals)
        sigma = pstdev(vals) if len(vals) > 1 else 0.0
        stats[m] = (mu, sigma)

    for entry in entries:
        total = 0.0
        breakdown = {}
        for m in metrics:
            mu, sigma = stats[m]
            z = (entry["metrics"][m] - mu) / sigma if sigma > 1e-9 else 0.0
            z = max(-2.6, min(2.6, z))
            total += weights[m] * z
            # 0..100 relativo ao grupo, para o radar.
            breakdown[m] = round(max(2, min(100, 50 + 19 * z)))
        entry["score"] = round(max(1, min(99, 50 + 15.5 * total)), 1)
        entry["radar"] = breakdown


# -------------------------------------------------------------------- extras

def _game_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "matchId": row["match_id"],
        "champion": row["champion_name"],
        "championId": row["champion_id"],
        "k": row["kills"], "d": row["deaths"], "a": row["assists"],
        "kda": _r(_safe_div(row["kills"] + row["assists"], max(row["deaths"], 1))),
        "win": bool(row["win"]),
        "queue": QUEUE_NAMES.get(row["queue_id"], f"Fila {row['queue_id']}"),
        "minutes": round(row["game_duration"] / 60),
        "date": row["game_creation"],
        "dmg": row["damage_champions"],
        "cs": row["cs"],
    }


def _impact(row: sqlite3.Row) -> float:
    return row["kills"] * 2 + row["assists"] - row["deaths"] * 2.5 + (6 if row["win"] else 0)


# _build_duos() vivia aqui e enchia data[fila].duos com ate 41 duplas por fila,
# em 7 filas (~14 KB de JSON em todo build). A tela nunca leu essa chave: quem
# desenha dupla e a matriz de sinergia (squadExtra.matrix, _synergy_matrix), que
# cobre os mesmos pares com mais contexto. Removido em vez de mantido morto.


def _squad_index(rows: list[sqlite3.Row]) -> dict[tuple[str, int], int]:
    """(partida, time) -> quantos membros rastreados estavam nesse time."""
    contagem: dict[tuple[str, int], int] = defaultdict(int)
    for row in rows:
        contagem[(row["match_id"], row["team_id"])] += 1
    return contagem


def _is_squad(row: sqlite3.Row, index: dict[tuple[str, int], int]) -> bool:
    return index.get((row["match_id"], row["team_id"]), 0) >= 2


def _compare_together_alone(rows_by_player: dict[str, list[sqlite3.Row]],
                            players: dict[str, dict],
                            index: dict[tuple[str, int], int],
                            min_games: int) -> list[dict]:
    """Por jogador: desempenho COM a UDA no time versus SEM ninguem da UDA."""
    saida = []
    for puuid, prows in rows_by_player.items():
        if puuid not in players:
            continue
        juntos = [r for r in prows if _is_squad(r, index)]
        sozinhos = [r for r in prows if not _is_squad(r, index)]

        def resumo(subset):
            acc = _blank()
            for r in subset:
                _accumulate(acc, r)
            return _finalize(acc)

        j, s = resumo(juntos), resumo(sozinhos)
        # So compara quando os dois lados tem amostra suficiente.
        comparavel = j["games"] >= min_games and s["games"] >= min_games

        saida.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "together": j,
            "alone": s,
            "comparable": comparavel,
            "delta": {
                "winrate": _r(j["winrate"] - s["winrate"], 1),
                "kda": _r(j["kda"] - s["kda"]),
                "kp": _r(j["kp"] - s["kp"], 1),
                "deaths": _r(j["deaths"] - s["deaths"]),
                "dmg_min": _r(j["dmg_min"] - s["dmg_min"], 0),
                "vision_min": _r(j["vision_min"] - s["vision_min"]),
            } if comparavel else None,
        })

    saida.sort(key=lambda d: (
        not d["comparable"],
        -(d["delta"]["winrate"] if d["delta"] else -999),
    ))
    return saida


def _synergy_matrix(rows: list[sqlite3.Row], players: dict[str, dict],
                    order: list[str], min_pair: int = 3) -> dict:
    """Matriz jogador x jogador com aproveitamento quando estao no mesmo time."""
    por_time: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        por_time[(row["match_id"], row["team_id"])].append(row)

    pares: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0}
    )
    for grupo in por_time.values():
        if len(grupo) < 2:
            continue
        for i in range(len(grupo)):
            for j in range(i + 1, len(grupo)):
                a, b = sorted((grupo[i]["puuid"], grupo[j]["puuid"]))
                pares[(a, b)]["games"] += 1
                pares[(a, b)]["wins"] += grupo[i]["win"]

    celulas = {}
    for (a, b), d in pares.items():
        if d["games"] < min_pair:
            continue
        valor = {
            "games": d["games"], "wins": d["wins"],
            "losses": d["games"] - d["wins"],
            "winrate": _r(_safe_div(d["wins"], d["games"]) * 100, 1),
        }
        celulas[f"{a}|{b}"] = valor
        celulas[f"{b}|{a}"] = valor

    return {
        "order": [p for p in order if p in players],
        "names": {p: players[p]["gameName"] for p in order if p in players},
        "icons": {p: players[p]["icon"] for p in order if p in players},
        "cells": celulas,
        "minPair": min_pair,
    }


def _lineups(rows: list[sqlite3.Row], players: dict[str, dict],
             min_games: int = 2, min_size: int = 3) -> list[dict]:
    """Formacoes de 3 ou mais.

    Duplas ficam de fora de proposito: a matriz de sinergia ja mostra os 44 pares
    de uma vez e com mais contexto. Listar dupla aqui tambem produzia dois numeros
    diferentes para a mesma dupla na mesma pagina -- a matriz conta todo jogo em
    que os dois estavam no time; aqui so contava quando eram exatamente eles dois.
    """
    por_time: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        por_time[(row["match_id"], row["team_id"])].append(row)

    grupos: dict[frozenset, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
    for membros in por_time.values():
        if len(membros) < min_size:
            continue
        chave = frozenset(m["puuid"] for m in membros)
        grupos[chave]["games"] += 1
        grupos[chave]["wins"] += membros[0]["win"]

    ROTULOS = {3: "Trio", 4: "Quarteto", 5: "Time completo"}
    saida = []
    for chave, d in grupos.items():
        if d["games"] < min_games or not all(p in players for p in chave):
            continue
        nomes = sorted(players[p]["gameName"] for p in chave)
        saida.append({
            "size": len(chave),
            "label": ROTULOS.get(len(chave), f"{len(chave)} jogadores"),
            "members": nomes,
            "icons": [players[p]["icon"] for p in sorted(chave)],
            "games": d["games"], "wins": d["wins"],
            "losses": d["games"] - d["wins"],
            "winrate": _r(_safe_div(d["wins"], d["games"]) * 100, 1),
        })
    # Corte por tamanho, nao global: ordenar so por tamanho faria os trios
    # empurrarem todas as duplas para fora da lista.
    saida.sort(key=lambda d: (-d["games"], -d["winrate"]))
    por_tamanho: dict[int, list[dict]] = defaultdict(list)
    for item in saida:
        por_tamanho[item["size"]].append(item)

    resultado: list[dict] = []
    for tamanho in sorted(por_tamanho, reverse=True):
        resultado.extend(por_tamanho[tamanho][:8])
    return resultado


def _internal_clashes(rows: list[sqlite3.Row], players: dict[str, dict]) -> list[dict]:
    """Quando dois membros da UDA caem em times OPOSTOS, quem leva a melhor."""
    por_partida: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        por_partida[row["match_id"]].append(row)

    duelos: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"games": 0, "a_wins": 0}
    )
    for grupo in por_partida.values():
        for i in range(len(grupo)):
            for j in range(i + 1, len(grupo)):
                if grupo[i]["team_id"] == grupo[j]["team_id"]:
                    continue
                a, b = grupo[i], grupo[j]
                if a["puuid"] > b["puuid"]:
                    a, b = b, a
                chave = (a["puuid"], b["puuid"])
                duelos[chave]["games"] += 1
                duelos[chave]["a_wins"] += a["win"]

    saida = []
    for (a, b), d in duelos.items():
        if a not in players or b not in players:
            continue
        saida.append({
            "a": players[a]["gameName"], "b": players[b]["gameName"],
            "iconA": players[a]["icon"], "iconB": players[b]["icon"],
            "games": d["games"], "aWins": d["a_wins"], "bWins": d["games"] - d["a_wins"],
        })
    saida.sort(key=lambda d: -d["games"])
    return saida[:12]


ROTA_PT = {"TOP": "Topo", "JUNGLE": "Selva", "MIDDLE": "Meio",
           "BOTTOM": "Atirador", "UTILITY": "Suporte"}


def _recent_matches(rows: list[sqlite3.Row], players: dict[str, dict],
                    limite: int = 24) -> list[dict]:
    """Feed das ultimas partidas do grupo, com todos os membros de cada uma."""
    por_partida: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        por_partida[row["match_id"]].append(row)

    recentes = sorted(por_partida.values(),
                      key=lambda g: -g[0]["game_creation"])[:limite]
    saida = []
    for membros in recentes:
        base = membros[0]
        saida.append({
            "matchId": base["match_id"],
            "date": base["game_creation"],
            "minutes": round(base["game_duration"] / 60),
            "queue": QUEUE_NAMES.get(base["queue_id"], f"Fila {base['queue_id']}"),
            # varios membros podem cair em times opostos, entao a vitoria e por jogador
            "players": sorted((
                {
                    "gameName": players[m["puuid"]]["gameName"],
                    "icon": players[m["puuid"]]["icon"],
                    "champion": m["champion_name"],
                    "championId": m["champion_id"],
                    "k": m["kills"], "d": m["deaths"], "a": m["assists"],
                    "kda": _r(_safe_div(m["kills"] + m["assists"], max(m["deaths"], 1))),
                    "cs": m["cs"], "dmg": m["damage_champions"],
                    "role": ROTA_PT.get(m["position"], ""),
                    "win": bool(m["win"]),
                }
                for m in membros if m["puuid"] in players
            ), key=lambda p: -p["kda"]),
        })
    return saida


def _role_split(rows: list[sqlite3.Row]) -> list[dict]:
    """Quanto o grupo joga de cada rota."""
    contagem: dict[str, dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0})
    for row in rows:
        rota = ROTA_PT.get(row["position"])
        if not rota:
            continue
        contagem[rota]["games"] += 1
        contagem[rota]["wins"] += row["win"]
    total = sum(d["games"] for d in contagem.values())
    return sorted((
        {"role": r, "games": d["games"], "wins": d["wins"],
         "winrate": _r(_safe_div(d["wins"], d["games"]) * 100, 1),
         "share": _r(_safe_div(d["games"], total) * 100, 1)}
        for r, d in contagem.items()
    ), key=lambda d: -d["games"])


def _queue_split(rows: list[sqlite3.Row]) -> list[dict]:
    """Distribuicao de partidas por fila, com aproveitamento de cada uma."""
    contagem: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "matches": set()}
    )
    for row in rows:
        item = contagem[row["queue_id"]]
        item["games"] += 1
        item["wins"] += row["win"]
        item["matches"].add(row["match_id"])
    total = sum(len(d["matches"]) for d in contagem.values())
    return sorted((
        {"queue": QUEUE_NAMES.get(q, f"Fila {q}"), "queueId": q,
         "matches": len(d["matches"]),
         "winrate": _r(_safe_div(d["wins"], d["games"]) * 100, 1),
         "share": _r(_safe_div(len(d["matches"]), total) * 100, 1)}
        for q, d in contagem.items()
    ), key=lambda d: -d["matches"])


def _weekly_trend(rows: list[sqlite3.Row]) -> list[dict]:
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0})
    for row in rows:
        ts = row["game_creation"] / 1000
        week = time.strftime("%Y-W%W", time.localtime(ts))
        buckets[week]["games"] += 1
        buckets[week]["wins"] += row["win"]
    out = [
        {"week": w, "games": d["games"], "wins": d["wins"],
         "winrate": _r(_safe_div(d["wins"], d["games"]) * 100, 1)}
        for w, d in sorted(buckets.items())
    ]
    return out[-16:]


def _champion_table(rows: list[sqlite3.Row], min_games: int = 3) -> list[dict]:
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "k": 0, "d": 0, "a": 0, "id": 0, "players": set()}
    )
    for row in rows:
        champ = row["champion_name"] or "?"
        item = agg[champ]
        item["games"] += 1
        item["wins"] += row["win"]
        item["k"] += row["kills"]
        item["d"] += row["deaths"]
        item["a"] += row["assists"]
        item["id"] = row["champion_id"]
        item["players"].add(row["puuid"])

    out = []
    for champ, item in agg.items():
        if item["games"] < min_games:
            continue
        out.append({
            "champion": champ, "championId": item["id"], "games": item["games"],
            "wins": item["wins"], "losses": item["games"] - item["wins"],
            "winrate": _r(_safe_div(item["wins"], item["games"]) * 100, 1),
            "kda": _r(_safe_div(item["k"] + item["a"], max(item["d"], 1))),
            "pilots": len(item["players"]),
        })
    out.sort(key=lambda d: -d["games"])
    return out[:30]


# ------------------------------------------------------- sala dos recordes

# Recorde de taxa so vale em jogo longo: 5 desvios/min num jogo de 6 minutos
# nao e recorde, e ruido de amostra rendida cedo.
JOGO_LONGO = 1200


def _dur(seg: int):
    return lambda r: r["game_duration"] >= seg


def _por_min(campo: str):
    return lambda r: _safe_div(r[campo] or 0, r["game_duration"] / 60.0)


# key, titulo, grupo, unidade, legenda, calculo, filtro, casas, maior-e-melhor.
# Ordem importa: as tres reliquias (penta, intocavel, roubo sem smite) vem
# primeiro para o front poder destacar o topo da lista de gloria.
REC_PARTIDA: list[tuple] = [
    ("penta", "O Pentakill", "gloria", "pentakill",
     "Cinco caixões de uma vez só. O troféu mais raro desta sala.",
     lambda r: r["penta_kills"] or 0, None, 0, True),
    ("semMorte", "O Intocável", "gloria", "abates e assistências",
     "Partida inteira sem ver a tela cinza. Ninguém conseguiu encostar.",
     lambda r: (r["kills"] or 0) + (r["assists"] or 0),
     lambda r: r["deaths"] == 0 and r["game_duration"] >= JOGO_LONGO, 0, True),
    ("rouboSemSmite", "Roubo Sem Smite", "gloria", "objetivo roubado",
     "Roubou o objetivo do time inimigo sem nem gastar smite. Puro deboche.",
     lambda r: r["epic_steals_no_smite"] or 0, None, 0, True),
    ("sequencia", "Sequência Imparável", "gloria", "abates seguidos",
     "Abate atrás de abate sem morrer uma vez. Fila de espera no respawn inimigo.",
     lambda r: r["largest_spree"] or 0, None, 0, True),
    ("soloKills", "Rei do 1v1", "gloria", "solo kills",
     "Abate que não dá pra dividir crédito com ninguém. O mais honesto que existe.",
     lambda r: r["solo_kills"] or 0, None, 0, True),
    ("critico", "Crítico Devastador", "gloria", "de dano num golpe só",
     "Um único ataque que apagou a barra de vida inteira do coitado.",
     lambda r: r["largest_crit"] or 0, None, 0, True),
    ("maquinaDano", "Máquina de Dano", "gloria", "de dano por minuto",
     "O time inimigo não teve um minuto de sossego a partida toda.",
     lambda r: r["dpm"] or 0, _dur(JOGO_LONGO), 0, True),
    ("onipresente", "Onipresente", "gloria", "% dos abates do time",
     "Apareceu em praticamente todo abate, num jogo em que o time matou muito.",
     lambda r: (r["kp"] or 0) * 100,
     lambda r: (r["team_kills"] or 0) >= 15 and r["game_duration"] >= JOGO_LONGO,
     0, True),
    ("vidaLonga", "Vida Longa", "gloria", "minutos seguidos vivo",
     "Ficou vivo tanto tempo que quase esqueceu o caminho de volta pra base.",
     lambda r: _safe_div(r["longest_alive"] or 0, 60.0), None, 1, True),
    ("contraTodos", "Contra Todos", "gloria", "abates em desvantagem",
     "Abates feitos em menor número. O 1v2 que sempre vira clipe no grupo.",
     lambda r: r["outnumbered_kills"] or 0, None, 0, True),
    ("matrix", "Modo Matrix", "gloria", "desvios por minuto",
     "Desviou de skillshot atrás de skillshot. Parece que joga com delay negativo.",
     _por_min("skillshots_dodged"), _dur(JOGO_LONGO), 1, True),
    ("miraCirurgica", "Mira Cirúrgica", "gloria", "habilidades acertadas",
     "O outro lado da moeda do Matrix: acertou quase tudo que jogou.",
     lambda r: r["skillshots_hit"] or 0, None, 0, True),
    ("muralha", "A Muralha", "gloria", "de dano absorvido",
     "Segurou uma montanha de dano na cara e nem piscou.",
     lambda r: r["self_mitigated"] or 0, None, 0, True),
    ("anjoGuarda", "Anjo da Guarda", "gloria", "de cura nos aliados",
     "Curou o time a noite inteira e provavelmente não ouviu nem um obrigado.",
     lambda r: r["heals_teammates"] or 0, None, 0, True),

    ("maisMortes", "Doador de Ouro Supremo", "vergonha", "mortes numa partida",
     "Distribuiu ouro pro time inimigo feito tio generoso em festa de Natal.",
     lambda r: r["deaths"] or 0, None, 0, True),
    ("telaCinza", "Tempo de Tela Cinza", "vergonha", "minutos morto",
     "Passou um bom pedaço da partida decorando o cronômetro de renascimento.",
     lambda r: _safe_div(r["time_dead"] or 0, 60.0), None, 1, True),
    ("meioJogoMorto", "Meio Jogo Morto", "vergonha", "% da partida morto",
     "Não jogou a partida, assistiu. Porcentagem dói mais que número absoluto.",
     lambda r: _safe_div(r["time_dead"] or 0, r["time_played"] or r["game_duration"]) * 100,
     _dur(JOGO_LONGO), 1, True),
    ("deficitFarm", "Déficit de Farm", "vergonha", "CS atrás do oponente",
     "O oponente de rota farmou uma cidade inteira a mais. Sem exagero.",
     lambda r: r["cs_diff"] if r["cs_diff"] is not None else 0, None, 0, False),
    ("atrasNoPlacar", "Atrás no Placar", "vergonha", "abates de desvantagem",
     "Chegou a ficar tão atrás no placar que dava pra ver o buraco da base.",
     lambda r: r["max_kill_deficit"] or 0, None, 0, True),
    ("ouroNoBolso", "Ouro no Bolso", "vergonha", "de ouro sem gastar",
     "Acabou o jogo com a carteira estufada. Item não se compra sozinho.",
     lambda r: (r["gold"] or 0) - (r["gold_spent"] or 0), None, 0, True),
    ("carona", "O Carona", "vergonha", "de saldo negativo na vitória",
     "Ganhou a partida sendo carregado no colo e ainda saiu comemorando.",
     lambda r: (r["deaths"] or 0) - (r["kills"] or 0) - (r["assists"] or 0),
     lambda r: r["win"] == 1 and r["game_duration"] >= JOGO_LONGO, 0, True),
    ("cadeMid", "Cadê o Mid?", "vergonha", "pings de interrogação",
     "Passou o jogo perguntando onde estava o inimigo em vez de olhar o mapa.",
     lambda r: r["ping_mia"] or 0, None, 0, True),
    ("sacoPancada", "Saco de Pancada", "vergonha", "x mais dano levado que causado",
     "Apanhou a partida inteira e devolveu quase nada. Razão, não total: senão"
     " só tanque apareceria aqui.",
     lambda r: _safe_div(r["damage_taken"] or 0, max(r["damage_champions"] or 0, 1)),
     _dur(JOGO_LONGO), 1, True),

    ("spamPing", "Sinfonia de Pings", "curiosidade", "pings numa partida",
     "Comunicação é importante. Isso aqui já é outro departamento.",
     lambda r: r["pings_total"] or 0, None, 0, True),
    ("dedoNervoso", "Dedo Nervoso", "curiosidade", "habilidades por minuto",
     "Não existiu tempo de recarga que segurasse esse teclado.",
     _por_min("spell_casts"), _dur(JOGO_LONGO), 1, True),
    # "acionamentos" e nao "flashes" de proposito: summoner1Casts conta tecla
    # apertada, nao flash executado. Com o cooldown minimo nem daria 10 numa
    # partida de 39 min, e o maior valor do banco e 19 -- seria recorde mentiroso.
    ("flashPanico", "Dedo no Flash", "curiosidade", "acionamentos do flash",
     "Martelou a tecla do flash tantas vezes que parte foi só ansiedade.",
     lambda r: r["flash_casts"] or 0, None, 0, True),
    ("partidaEterna", "Partida Eterna", "curiosidade", "minutos de partida",
     "O jogo que ninguém aguentava mais e ninguém tinha coragem de render.",
     lambda r: _safe_div(r["game_duration"] or 0, 60.0), None, 0, True),
    ("colecionador", "Colecionador", "curiosidade", "itens comprados",
     "Passou mais tempo na loja do que na rota. Cada volta, uma ideia nova.",
     lambda r: r["items_purchased"] or 0, None, 0, True),
]

GRUPO_ORDEM = {"gloria": 0, "vergonha": 1, "curiosidade": 2}


def _rec_base(key: str, titulo: str, grupo: str, unidade: str,
              legenda: str) -> dict[str, Any]:
    """Vitrine vazia. Recorde sem ocorrencia sai com valor nulo de proposito:
    'ainda ninguem' conta mais historia do que o cartao sumir da grade."""
    return {
        "key": key, "titulo": titulo, "grupo": grupo, "valor": None,
        "unidade": unidade, "legenda": legenda, "player": None, "icon": None,
        "champion": None, "championId": None, "matchId": None, "queue": None,
        "minutes": None, "k": None, "d": None, "a": None, "quando": None,
    }


def _rec(key, titulo, grupo, unidade, legenda, row, valor, casas,
         players) -> dict[str, Any]:
    base = _rec_base(key, titulo, grupo, unidade, legenda)
    # Zero nunca e recorde: significa que o feito nao aconteceu nesta fila.
    if row is None or not valor:
        return base
    player = players[row["puuid"]]
    base.update({
        "valor": _r(valor, casas) if casas else int(round(valor)),
        "player": player["gameName"], "icon": player["icon"],
        "champion": row["champion_name"], "championId": row["champion_id"],
        "matchId": row["match_id"],
        "queue": QUEUE_NAMES.get(row["queue_id"], f"Fila {row['queue_id']}"),
        "minutes": round(row["game_duration"] / 60),
        "k": row["kills"], "d": row["deaths"], "a": row["assists"],
        "quando": row["game_creation"],
    })
    return base


def _melhor(rows: list[sqlite3.Row], calc, filtro, maior: bool):
    alvo, valor = None, None
    for row in rows:
        if filtro is not None and not filtro(row):
            continue
        v = calc(row)
        if v is None:
            continue
        if valor is None or (v > valor if maior else v < valor):
            alvo, valor = row, v
    return alvo, valor


def _streak(rows: list[sqlite3.Row], vitoria: bool):
    """Maior sequencia de vitorias (ou derrotas) de um jogador so, em ordem
    cronologica. Devolve a partida que fechou a sequencia, pra dar contexto."""
    por_jogador: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        por_jogador[row["puuid"]].append(row)
    alvo, melhor = None, 0
    for lista in por_jogador.values():
        n = 0
        for row in sorted(lista, key=lambda r: r["game_creation"]):
            if bool(row["win"]) is vitoria:
                n += 1
                if n > melhor:
                    alvo, melhor = row, n
            else:
                n = 0
    return alvo, melhor


def _maratona(rows: list[sqlite3.Row]):
    """Mais partidas do mesmo jogador no mesmo dia. Dia local, nao UTC."""
    dias: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        dia = time.strftime("%d/%m/%Y", time.localtime(row["game_creation"] / 1000))
        dias[(row["puuid"], dia)].append(row)
    if not dias:
        return None, 0, ""
    (_, dia), lista = max(dias.items(), key=lambda kv: len(kv[1]))
    return max(lista, key=lambda r: r["game_creation"]), len(lista), dia


# Os 13 recordes que o cliente do LoL consegue provar. Os outros 15 dependem do
# bloco challenges{} do Match-V5, que o cliente simplesmente nao tem: em vez de
# 15 vitrines eternamente vazias na fila das personalizadas, a lista encolhe.
REC_LCU_OK = {"penta", "semMorte", "sequencia", "critico", "maquinaDano",
              "onipresente", "vidaLonga", "muralha", "maisMortes",
              "ouroNoBolso", "carona", "sacoPancada", "partidaEterna"}


def _build_records(rows: list[sqlite3.Row], players: dict[str, dict],
                   permitidos: set[str] | None = None) -> list[dict]:
    """A Sala dos Recordes de uma fila. Lista fixa: todo recorde sempre sai,
    com valor nulo quando ninguem cumpriu o feito naquela fila.

    `permitidos` corta a lista quando a FONTE do dado nao suporta o recorde --
    e diferente de "ninguem conseguiu ainda", que continua saindo com valor nulo.
    """
    validos = [r for r in rows if r["puuid"] in players]
    specs = REC_PARTIDA if permitidos is None else [
        r for r in REC_PARTIDA if r[0] in permitidos]
    saida = [
        _rec(key, titulo, grupo, unidade, legenda,
             *_melhor(validos, calc, filtro, maior), casas, players)
        for key, titulo, grupo, unidade, legenda, calc, filtro, casas, maior
        in specs
    ]

    row, n = _streak(validos, True)
    saida.append(_rec(
        "invicto", "O Invicto", "gloria", "vitórias seguidas",
        "Sequência de vitórias sem tropeço. A partida mostrada é a que fechou a conta.",
        row, n, 0, players))
    row, n = _streak(validos, False)
    saida.append(_rec(
        "espiral", "Espiral da Morte", "vergonha", "derrotas seguidas",
        "Sequência de derrotas seguidas. Devia ter desligado o computador antes.",
        row, n, 0, players))
    row, n, dia = _maratona(validos)
    saida.append(_rec(
        "maratonista", "O Maratonista", "curiosidade", "partidas num dia só",
        f"Tudo isso em {dia}. O sol nasceu, andou pelo céu e se pôs sem ser visto."
        if dia else "Muitas partidas num dia só.",
        row, n, 0, players))

    saida.sort(key=lambda d: GRUPO_ORDEM.get(d["grupo"], 9))
    return saida


AWARDS_GOOD = [
    ("carregador", "O Carregador", "Maior dano por minuto", "dmg_min", True, "{v:.0f}", "dmg/min"),
    ("assassino", "Máquina de Abate", "Mais abates por partida", "kills", True, "{v:.1f}", "abates/jogo"),
    ("imortal", "O Imortal", "Menos mortes a cada 10 min", "deaths_10min", False, "{v:.2f}", "mortes/10min"),
    ("farmador", "O Farmador", "Maior CS por minuto", "cs_min", True, "{v:.1f}", "cs/min"),
    ("sentinela", "A Sentinela", "Maior visão por minuto", "vision_min", True, "{v:.2f}", "visão/min"),
    ("presenca", "Onipresente", "Maior participação em abates", "kp", True, "{v:.0f}%", "de participação"),
]

AWARDS_BAD = [
    ("doador", "Doador de Ouro", "Mais mortes por partida", "deaths", True, "{v:.1f}", "mortes/jogo"),
    ("cego", "O Cego", "Menor visão por minuto", "vision_min", False, "{v:.2f}", "visão/min"),
    ("fantasma", "O Fantasma", "Menor participação em abates", "kp", False, "{v:.0f}%", "de participação"),
    ("turista", "O Turista", "Menor CS por minuto", "cs_min", False, "{v:.1f}", "cs/min"),
    ("azarado", "O Azarado", "Pior aproveitamento", "winrate", False, "{v:.0f}%", "de vitórias"),
    ("figurante", "O Figurante", "Menor dano por minuto", "dmg_min", False, "{v:.0f}", "dmg/min"),
]


# Metricas que o cliente do LoL nao mede. No Abismo visionScore fica em 0,0 e
# CS e minion passivo, nao farm: premiar "A Sentinela" ou "O Farmador" ali seria
# entregar um trofeu por um numero que ninguem produziu.
LCU_SEM = {"cs_min", "vision_min"}


def _pick_awards(entries: list[dict], specs: list[tuple]) -> list[dict]:
    """Cada premio carrega tambem o contexto: media do grupo, distancia do
    vencedor para ela, e quem ficou logo atras. Sem isso o premio e so um nome."""
    out = []
    for key, title, desc, metric, want_max, fmt, unit in specs:
        pool = [e for e in entries if e["stats"]["games"] > 0]
        if not pool:
            continue
        ordenado = sorted(pool, key=lambda e: e["stats"][metric], reverse=want_max)
        best = ordenado[0]
        valor = best["stats"][metric]
        media = mean([e["stats"][metric] for e in pool])

        # Positivo = quanto o vencedor e melhor que a media, no sentido da metrica.
        if media:
            bruto = (valor - media) / abs(media) * 100
            vantagem = bruto if want_max else -bruto
        else:
            vantagem = 0.0

        # O campeao mais jogado do vencedor vira a ARTE do quadro no hall. Vai
        # pronto no payload para o front nao ter que caçar o jogador de novo.
        main = (best.get("champions") or [None])[0]

        out.append({
            "key": key, "title": title, "desc": desc,
            "player": best["gameName"], "icon": best["icon"],
            "championId": main["championId"] if main else None,
            "champion": main["champion"] if main else None,
            "value": fmt.format(v=valor), "unit": unit,
            "raw": _r(valor, 2),
            "avg": fmt.format(v=media),
            "edge": _r(vantagem, 0),
            # posicao do vencedor na barra, relativa ao intervalo do grupo
            "fill": _r(_safe_div(
                abs(valor - min(e["stats"][metric] for e in pool)),
                abs(max(e["stats"][metric] for e in pool)
                    - min(e["stats"][metric] for e in pool)) or 1,
            ) * 100, 0),
            "runnersUp": [
                {"player": e["gameName"], "icon": e["icon"],
                 "value": fmt.format(v=e["stats"][metric])}
                for e in ordenado[1:3]
            ],
        })
    return out


# ------------------------------------------------------------------ principal

def build_payload(conn: sqlite3.Connection, window_days: int,
                  min_games: int, champ_index: dict[int, dict[str, str]],
                  ddragon_ver: str, platform: str = "br1",
                  demo: bool = False) -> dict[str, Any]:
    players = _load_players(conn)
    all_rows = _load_rows(conn, window_days)
    rows_by_player: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in all_rows:
        if row["puuid"] in players:
            rows_by_player[row["puuid"]].append(row)

    squad_index = _squad_index(all_rows)
    groups_payload = {}
    evolucao_payload: dict[str, Any] = {}
    zoeira_payload: dict[str, Any] = {}
    vexames_payload: dict[str, Any] = {}
    arsenal_payload: dict[str, Any] = {}
    rota_payload: dict[str, Any] = {}
    from . import arsenal as _arsenal, rota as _rota, vexames as _vexames, zoeira as _zoeira

    def _keep(row, teste, squad_only) -> bool:
        if teste is not None and not teste(row["queue_id"], row["game_mode"]):
            return False
        if squad_only and not _is_squad(row, squad_index):
            return False
        return True

    for group in GROUPS:
        gkey, glabel = group["key"], group["label"]
        teste, squad_only = group["test"], group["squad"]
        profile = ("inhouse" if gkey == "inhouse"
                   else "aram" if gkey in ("aram", "outros") else "default")
        group_rows = [r for r in all_rows if _keep(r, teste, squad_only)]
        entries: list[dict] = []

        for puuid, player in players.items():
            prows = [r for r in rows_by_player.get(puuid, [])
                     if _keep(r, teste, squad_only)]
            if not prows:
                continue

            acc = _blank()
            champs: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"games": 0, "wins": 0, "k": 0, "d": 0, "a": 0, "id": 0}
            )
            positions: dict[str, int] = defaultdict(int)
            for row in prows:
                _accumulate(acc, row)
                champ = champs[row["champion_name"] or "?"]
                champ["games"] += 1
                champ["wins"] += row["win"]
                champ["k"] += row["kills"]
                champ["d"] += row["deaths"]
                champ["a"] += row["assists"]
                champ["id"] = row["champion_id"]
                if row["position"]:
                    positions[row["position"]] += 1

            stats = _finalize(acc)
            top_champs = sorted(
                (
                    {
                        "champion": name, "championId": c["id"], "games": c["games"],
                        "wins": c["wins"], "losses": c["games"] - c["wins"],
                        "winrate": _r(_safe_div(c["wins"], c["games"]) * 100, 1),
                        "kda": _r(_safe_div(c["k"] + c["a"], max(c["d"], 1))),
                    }
                    for name, c in champs.items()
                ),
                key=lambda d: (-d["games"], -d["winrate"]),
            )[:6]

            ranked_games = sorted(prows, key=_impact)
            entries.append({
                **{k: player[k] for k in
                   ("puuid", "gameName", "tagLine", "riotId", "icon", "level", "ranks")},
                "stats": stats,
                "metrics": _score_metrics(stats, profile),
                "champions": top_champs,
                "roles": sorted(
                    ({"role": p, "games": n} for p, n in positions.items()),
                    key=lambda d: -d["games"],
                )[:3],
                "form": [_game_record(r) for r in prows[:12]],
                "bestGame": _game_record(ranked_games[-1]) if ranked_games else None,
                "worstGame": _game_record(ranked_games[0]) if ranked_games else None,
                "eligible": stats["games"] >= min_games,
            })

        ranked = [e for e in entries if e["eligible"]]
        _apply_scores(ranked, profile)
        for entry in entries:
            entry.setdefault("score", None)
            entry.setdefault("radar", {})
        ranked.sort(key=lambda e: -e["score"])
        for i, entry in enumerate(ranked, 1):
            entry["rank"] = i
        for entry in entries:
            entry.setdefault("rank", None)
        entries.sort(
            key=lambda e: (e["rank"] is None, e["rank"] or 0, -e["stats"]["games"])
        )

        total_games = len({r["match_id"] for r in group_rows})
        total_acc = _blank()
        for row in group_rows:
            _accumulate(total_acc, row)
        team = _finalize(total_acc)
        team["uniqueMatches"] = total_games

        # Fila alimentada pelo CLIENTE do LoL, e nao pela API: sem challenges{},
        # sem visao, sem rota e sem farm de rota. A tela usa esta marca para nao
        # desenhar coluna de zero nem prometer recorde que a fonte nao tem.
        lcu = gkey == "inhouse"

        groups_payload[gkey] = {
            "label": glabel,
            "lcu": lcu,
            "players": entries,
            "podium": [e["puuid"] for e in ranked[:3]],
            "shame": [e["puuid"] for e in ranked[-3:]][::-1],
            "team": team,
            "awardsGood": _pick_awards(
                ranked, [a for a in AWARDS_GOOD if not (lcu and a[3] in LCU_SEM)]),
            "awardsBad": _pick_awards(
                ranked, [a for a in AWARDS_BAD if not (lcu and a[3] in LCU_SEM)]),
            "trend": _weekly_trend(group_rows),
            "champions": _champion_table(group_rows),
            "records": _build_records(group_rows, players,
                                      REC_LCU_OK if lcu else None),
            "recent": _recent_matches(group_rows, players),
            "roles": _role_split(group_rows),
            "queues": _queue_split(group_rows),
        }

        # As metricas que SO existem quando os dois times sao da UDA: a tabela de
        # confrontos, algoz e fregues, a dupla-e-divorcio, a balanca, o espelho e
        # a chamada. Nenhuma delas cabe nas paginas atuais, que sao todas listas
        # de jogador -- estas sao matriciais, jogador contra jogador.
        if gkey == "inhouse":
            extra = _inhouse.construir(conn, players, window_days)
            if extra:
                groups_payload[gkey]["inhouseExtra"] = extra

        # Linhas do grupo agrupadas por jogador: servem para evolucao, trofeus,
        # vexames e arsenal. Montadas uma vez so, aqui.
        rows_grupo: dict[str, list] = defaultdict(list)
        for r in group_rows:
            if r["puuid"] in players:
                rows_grupo[r["puuid"]].append(r)

        # Series temporais: so para as abas que a tela realmente desenha.
        # "inhouse" fica de fora de proposito: sao 17 partidas em 4 noites, e uma
        # media movel de 10 partidas ou uma corrida MENSAL nao tem o que dizer.
        if gkey in ("all", "solo", "flex", "squad"):
            from . import evolucao as _ev
            evolucao_payload[gkey] = _ev.construir(
                group_rows, rows_grupo, players, min_games
            )

        # Trofeus, vexames e arsenal por FILA. Medido antes de fazer: as seis
        # filas juntas custam +0,7 MB, porque so "Todas", Solo e Flex tem volume
        # -- as outras sao pequenas e quase nao pesam. Vale o preco: zoar
        # "quem mais morre no ARAM" e outra piada, diferente de "quem mais morre".
        if not squad_only:
            # Trofeus por JANELA. So o podio viaja nas janelas curtas, senao o
            # payload triplicaria: medido em 1,7 MB a mais com ranking inteiro.
            zoeira_payload[gkey] = {"tudo": _zoeira.construir(rows_grupo, players)}
            for nome, dias in (("90", 90), ("30", 30)):
                corte = int((time.time() - dias * 86400) * 1000)
                curto = defaultdict(list)
                for puuid, ls in rows_grupo.items():
                    recentes = [r for r in ls if r["game_creation"] >= corte]
                    if recentes:
                        curto[puuid] = recentes
                zoeira_payload[gkey][nome] = _zoeira.construir(
                    curto, players, ranking_completo=False)
            vexames_payload[gkey] = _vexames.construir(
                conn, rows_grupo, players, verbose=False)
            arsenal_payload[gkey] = _arsenal.construir(
                group_rows, players, ddragon_ver, verbose=False)
            rota_payload[gkey] = _rota.construir(group_rows, players, verbose=False)

        if squad_only:
            ordem = [e["puuid"] for e in entries]
            partidas_em_equipe = len({r["match_id"] for r in group_rows})
            total_partidas = len({r["match_id"] for r in all_rows})
            groups_payload[gkey]["squadExtra"] = {
                "comparison": _compare_together_alone(
                    rows_by_player, players, squad_index, min_games
                ),
                "matrix": _synergy_matrix(group_rows, players, ordem),
                "lineups": _lineups(group_rows, players),
                "clashes": _internal_clashes(all_rows, players),
                "coopRate": _r(
                    _safe_div(partidas_em_equipe, total_partidas) * 100, 1
                ),
                "squadMatches": partidas_em_equipe,
                "totalMatches": total_partidas,
            }

    last_fetch = conn.execute(
        "SELECT value FROM meta WHERE key='last_fetch'"
    ).fetchone()

    payload = {
        "title": "UNIAO DOS AFUNDADOS",
        "platform": platform,
        "demo": demo,
        "generatedAt": int(time.time() * 1000),
        "lastFetch": int(last_fetch[0]) * 1000 if last_fetch else None,
        "windowDays": window_days,
        "minGames": min_games,
        "ddragon": ddragon_ver,
        "championIndex": {str(k): v["key"] for k, v in champ_index.items()},
        "metricLabels": METRIC_LABELS,
        "groups": [{"key": g["key"], "label": g["label"], "squad": g["squad"]}
                   for g in GROUPS],
        "data": groups_payload,
        "evolution": evolucao_payload,
    }

    # Depois do payload montado, porque a ficha so vai para os campeoes que
    # aparecem nele. Importado aqui, como evolucao, para nao arrastar requests
    # em quem so quer os KPIs.
    from . import assets as _assets
    payload["champDetails"] = _assets.detalhes(payload, ddragon_ver)

    # Blocos que nao dependem de fila: sao a carreira inteira do elenco.
    # Importados aqui, como evolucao, para nao arrastar requests em quem so
    # quer os KPIs.
    from . import mural as _mural
    payload["zoeira"] = zoeira_payload
    payload["arsenal"] = arsenal_payload
    payload["rota"] = rota_payload
    payload["mural"] = _mural.construir(rows_by_player, players, min_games,
                                        champ_index)
    payload["titulos"] = _mural.resumo_titulos(payload["mural"])

    # Placares por ultimo: so entram as partidas que o payload ja referencia.
    payload["vexames"] = vexames_payload

    # Placares por ultimo: so entram as partidas que o payload ja referencia,
    # e os vexames acabaram de acrescentar um monte delas.
    from . import partidas as _partidas
    payload["placares"] = _partidas.construir(
        conn, _partidas.coletar_ids(payload), players, QUEUE_NAMES)
    return payload

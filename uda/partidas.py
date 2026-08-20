"""Placar completo das partidas citadas no painel.

Quando o cartao do jogador mostra "pior partida: 2/20/11", a pergunta seguinte e
sempre a mesma: contra quem? com quem? Este modulo monta o placar dos dez, dos
dois times, para as partidas que a tela referencia.

Duas fontes, porque o banco tem duas origens:
  - Partidas da Riot: `matches.raw` traz riotIdGameName/riotIdTagline dos dez e
    os objetivos por time. E a fonte boa.
  - Personalizadas: nao tem raw (vieram do cliente, nao da API). Ali o placar sai
    da tabela participants, e os nomes de `inhouse_accounts`. Quem nao e da UDA
    aparece com puuid "lcu:..." e vira "Desconhecido" -- e o que existe.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from typing import Any

CHAVES_ID = ("matchId",)


def coletar_ids(node: Any, saida: set[str] | None = None) -> set[str]:
    """Varre o payload atras de toda partida que a tela pode querer abrir."""
    if saida is None:
        saida = set()
    if isinstance(node, dict):
        for chave, valor in node.items():
            if chave in CHAVES_ID and isinstance(valor, str) and valor:
                saida.add(valor)
            else:
                coletar_ids(valor, saida)
    elif isinstance(node, list):
        for item in node:
            coletar_ids(item, saida)
    return saida


def _obj(teams: list, team_id: int) -> dict:
    for t in teams or []:
        if t.get("teamId") == team_id:
            o = t.get("objectives") or {}
            return {
                "torres": (o.get("tower") or {}).get("kills", 0),
                "dragoes": (o.get("dragon") or {}).get("kills", 0),
                "baroes": (o.get("baron") or {}).get("kills", 0),
                "arautos": (o.get("riftHerald") or {}).get("kills", 0),
                "inibidores": (o.get("inhibitor") or {}).get("kills", 0),
                "abates": (o.get("champion") or {}).get("kills", 0),
            }
    return {}


def _do_raw(bruto: bytes, tracked: set[str]) -> dict | None:
    try:
        match = json.loads(zlib.decompress(bruto))
    except (zlib.error, ValueError, TypeError):
        return None
    info = match.get("info") or {}
    parts = info.get("participants") or []
    if not parts:
        return None

    duracao = int(info.get("gameDuration") or 0)
    if duracao > 100_000:
        duracao //= 1000

    times: dict[int, dict] = {}
    for p in parts:
        tid = int(p.get("teamId") or 0)
        alvo = times.setdefault(tid, {"id": tid, "win": bool(p.get("win")),
                                      "jogadores": []})
        nome = (p.get("riotIdGameName") or p.get("summonerName") or "").strip()
        tag = (p.get("riotIdTagline") or "").strip()
        alvo["jogadores"].append({
            "puuid": p.get("puuid", ""),
            "nome": nome or "Desconhecido",
            "tag": tag,
            "uda": p.get("puuid") in tracked,
            "championId": int(p.get("championId") or 0),
            "champion": p.get("championName", ""),
            "rota": p.get("teamPosition") or p.get("individualPosition") or "",
            "nivel": int(p.get("champLevel") or 0),
            "k": int(p.get("kills") or 0),
            "d": int(p.get("deaths") or 0),
            "a": int(p.get("assists") or 0),
            "cs": int(p.get("totalMinionsKilled") or 0)
            + int(p.get("neutralMinionsKilled") or 0),
            "ouro": int(p.get("goldEarned") or 0),
            "dano": int(p.get("totalDamageDealtToChampions") or 0),
            "danoSofrido": int(p.get("totalDamageTaken") or 0),
            "visao": int(p.get("visionScore") or 0),
            "wards": int(p.get("wardsPlaced") or 0),
            "wardsMortas": int(p.get("wardsKilled") or 0),
            "sentinelasControle": int(p.get("visionWardsBoughtInGame") or 0),
            "itens": [int(p.get(f"item{i}") or 0) for i in range(7)],
            "feiticos": [int(p.get("summoner1Id") or 0),
                         int(p.get("summoner2Id") or 0)],
            "keystone": _keystone(p),
        })

    for tid, dados in times.items():
        dados["_minutos"] = duracao / 60.0
        dados.update(_obj(info.get("teams"), tid))
        dados["abatesTime"] = sum(j["k"] for j in dados["jogadores"])
        dados["ouroTime"] = sum(j["ouro"] for j in dados["jogadores"])
        dados["danoTime"] = sum(j["dano"] for j in dados["jogadores"])
        dados["jogadores"].sort(key=lambda j: _ordem_rota(j["rota"]))
    _derivados(times)

    return {
        "duracao": duracao,
        "criacao": int(info.get("gameCreation") or 0),
        "modo": info.get("gameMode", ""),
        "queueId": int(info.get("queueId") or 0),
        "times": [times[k] for k in sorted(times)],
    }


def _keystone(p: dict) -> int:
    """A runa principal fica no primeiro slot do primeiro estilo do perks."""
    try:
        return int(p["perks"]["styles"][0]["selections"][0]["perk"])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _derivados(times: dict) -> None:
    """Participacao, CS/min e os selos MVP/ACE, como no placar do cliente.

    MVP e o maior impacto do time vencedor; ACE, do perdedor -- convencao do
    proprio jogo. O impacto pesa abate e assistencia contra morte e soma dano,
    para nao coroar quem so tomou conta do placar de assistencia.
    """
    for dados in times.values():
        abates = max(sum(j["k"] for j in dados["jogadores"]), 1)
        dano_max = max((j["dano"] for j in dados["jogadores"]), default=1) or 1
        for j in dados["jogadores"]:
            minutos = max(dados.get("_minutos", 0), 1)
            j["kp"] = round((j["k"] + j["a"]) / abates * 100)
            j["csMin"] = round(j["cs"] / minutos, 1)
            j["kda"] = round((j["k"] + j["a"]) / max(j["d"], 1), 2)
            j["impacto"] = round(
                (j["k"] * 3 + j["a"] * 1.2 - j["d"] * 2.5)
                + (j["dano"] / dano_max) * 6, 2)
        melhor = max(dados["jogadores"], key=lambda x: x["impacto"], default=None)
        if melhor:
            melhor["selo"] = "MVP" if dados["win"] else "ACE"
        # Limpeza: o puuid sozinho custava ~340 KB no payload (78 chars x 10 x 438)
        # e a tela nunca precisa dele -- o que importa e o booleano `uda`.
        # `impacto` e `_minutos` sao andaimes do calculo acima.
        dados.pop("_minutos", None)
        for j in dados["jogadores"]:
            j.pop("puuid", None)
            j.pop("impacto", None)


ROTAS_ORDEM = {"TOP": 0, "JUNGLE": 1, "MIDDLE": 2, "BOTTOM": 3, "UTILITY": 4}


def _ordem_rota(rota: str) -> int:
    return ROTAS_ORDEM.get((rota or "").upper(), 9)


def _do_banco(conn: sqlite3.Connection, match_id: str, nomes: dict[str, dict],
              tracked: set[str]) -> dict | None:
    """Fallback das personalizadas: monta o placar da tabela participants."""
    linhas = list(conn.execute(
        "SELECT * FROM participants WHERE match_id=?", (match_id,)))
    if not linhas:
        return None
    cabec = conn.execute(
        "SELECT queue_id, game_creation, game_duration, game_mode FROM matches "
        "WHERE match_id=?", (match_id,)).fetchone()

    times: dict[int, dict] = {}
    for row in linhas:
        tid = int(row["team_id"] or 0)
        alvo = times.setdefault(tid, {"id": tid, "win": bool(row["win"]),
                                      "jogadores": []})
        ficha = nomes.get(row["puuid"]) or {}
        try:
            itens = json.loads(row["items"]) if row["items"] else []
        except (ValueError, TypeError):
            itens = []
        alvo["jogadores"].append({
            "puuid": row["puuid"],
            "nome": ficha.get("gameName", "Desconhecido"),
            "tag": ficha.get("tagLine", ""),
            "uda": row["puuid"] in tracked,
            "championId": row["champion_id"], "champion": row["champion_name"],
            "rota": row["position"] or "", "nivel": row["champ_level"] or 0,
            "k": row["kills"], "d": row["deaths"], "a": row["assists"],
            "cs": row["cs"], "ouro": row["gold"],
            "dano": row["damage_champions"], "danoSofrido": row["damage_taken"],
            "visao": row["vision_score"],
            "wards": row["wards_placed"] or 0,
            "wardsMortas": row["wards_killed"] or 0,
            "sentinelasControle": row["control_wards"] or 0,
            "itens": itens,
            "feiticos": [row["spell1_id"] or 0, row["spell2_id"] or 0],
            "keystone": row["keystone"] or 0,
        })

    for dados in times.values():
        dados["_minutos"] = (cabec["game_duration"] if cabec else 0) / 60.0
        dados["abatesTime"] = sum(j["k"] for j in dados["jogadores"])
        dados["ouroTime"] = sum(j["ouro"] for j in dados["jogadores"])
        dados["danoTime"] = sum(j["dano"] for j in dados["jogadores"])
        dados["jogadores"].sort(key=lambda j: _ordem_rota(j["rota"]))
    _derivados(times)

    return {
        "duracao": cabec["game_duration"] if cabec else 0,
        "criacao": cabec["game_creation"] if cabec else 0,
        "modo": cabec["game_mode"] if cabec else "",
        "queueId": cabec["queue_id"] if cabec else 0,
        "times": [times[k] for k in sorted(times)],
    }


def construir(conn: sqlite3.Connection, match_ids: set[str],
              players: dict[str, dict], queue_names: dict[int, str],
              verbose: bool = True) -> dict[str, dict]:
    if not match_ids:
        return {}

    tracked = set(players)
    # Nomes para o fallback: o roster atual mais o cadastro das personalizadas.
    nomes = {p: {"gameName": d["gameName"], "tagLine": d["tagLine"]}
             for p, d in players.items()}
    try:
        for row in conn.execute(
                "SELECT puuid, game_name, tag_line FROM inhouse_accounts"):
            nomes.setdefault(row["puuid"], {"gameName": row["game_name"],
                                            "tagLine": row["tag_line"]})
    except sqlite3.Error:
        pass

    saida: dict[str, dict] = {}
    sem_raw = 0
    for match_id in match_ids:
        linha = conn.execute("SELECT raw FROM matches WHERE match_id=?",
                             (match_id,)).fetchone()
        placar = None
        if linha and linha["raw"]:
            placar = _do_raw(linha["raw"], tracked)
        if placar is None:
            placar = _do_banco(conn, match_id, nomes, tracked)
            if placar:
                sem_raw += 1
        if placar:
            placar["matchId"] = match_id
            placar["queue"] = queue_names.get(placar["queueId"],
                                              f"Fila {placar['queueId']}")
            saida[match_id] = placar

    if verbose:
        extra = f" ({sem_raw} montadas sem raw)" if sem_raw else ""
        print(f"  placares: {len(saida)} partidas detalhadas{extra}")
    return saida

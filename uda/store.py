"""Persistencia em SQLite. Partida baixada uma vez fica no banco para sempre."""

from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    puuid            TEXT PRIMARY KEY,
    game_name        TEXT NOT NULL,
    tag_line         TEXT NOT NULL,
    profile_icon_id  INTEGER,
    summoner_level   INTEGER,
    updated_at       INTEGER
);

CREATE TABLE IF NOT EXISTS ranks (
    puuid       TEXT NOT NULL,
    queue_type  TEXT NOT NULL,
    tier        TEXT,
    division    TEXT,
    lp          INTEGER,
    wins        INTEGER,
    losses      INTEGER,
    hot_streak  INTEGER,
    updated_at  INTEGER,
    PRIMARY KEY (puuid, queue_type)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,
    queue_id      INTEGER,
    game_creation INTEGER,
    game_duration INTEGER,
    game_version  TEXT,
    game_mode     TEXT,
    raw           BLOB
);

CREATE TABLE IF NOT EXISTS participants (
    match_id            TEXT NOT NULL,
    puuid               TEXT NOT NULL,
    queue_id            INTEGER,
    game_creation       INTEGER,
    game_duration       INTEGER,
    team_id             INTEGER,
    win                 INTEGER,
    champion_id         INTEGER,
    champion_name       TEXT,
    position            TEXT,
    kills               INTEGER,
    deaths              INTEGER,
    assists             INTEGER,
    cs                  INTEGER,
    gold                INTEGER,
    damage_champions    INTEGER,
    damage_taken        INTEGER,
    damage_objectives   INTEGER,
    heal_shield         INTEGER,
    vision_score        INTEGER,
    wards_placed        INTEGER,
    wards_killed        INTEGER,
    control_wards       INTEGER,
    first_blood         INTEGER,
    first_blood_assist  INTEGER,
    double_kills        INTEGER,
    triple_kills        INTEGER,
    quadra_kills        INTEGER,
    penta_kills         INTEGER,
    turret_takedowns    INTEGER,
    team_kills          INTEGER,
    team_deaths         INTEGER,
    early_surrender     INTEGER,
    surrender           INTEGER,
    tracked             INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, puuid)
);

CREATE INDEX IF NOT EXISTS idx_part_puuid  ON participants (puuid, game_creation);
CREATE INDEX IF NOT EXISTS idx_part_track  ON participants (tracked, queue_id);
CREATE INDEX IF NOT EXISTS idx_match_time  ON matches (game_creation);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Fila de download. Os ids sao descobertos por jogador, nao em ordem cronologica,
-- entao uma interrupcao no meio deixaria buracos que a marca d'agua (MAX de
-- game_creation) esconderia para sempre. Aqui todo id descoberto fica registrado
-- ate ser baixado, e o run seguinte retoma exatamente de onde parou.
CREATE TABLE IF NOT EXISTS match_queue (
    match_id      TEXT PRIMARY KEY,
    discovered_at INTEGER,
    done          INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_queue_todo ON match_queue (done);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------------- writes

def upsert_player(conn: sqlite3.Connection, puuid: str, game_name: str, tag_line: str,
                  icon: int | None, level: int | None, ts: int) -> None:
    conn.execute(
        """INSERT INTO players (puuid, game_name, tag_line, profile_icon_id,
                                summoner_level, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(puuid) DO UPDATE SET
               game_name=excluded.game_name,
               tag_line=excluded.tag_line,
               profile_icon_id=COALESCE(excluded.profile_icon_id, players.profile_icon_id),
               summoner_level=COALESCE(excluded.summoner_level, players.summoner_level),
               updated_at=excluded.updated_at""",
        (puuid, game_name, tag_line, icon, level, ts),
    )


def replace_ranks(conn: sqlite3.Connection, puuid: str, entries: Iterable[dict],
                  ts: int) -> None:
    conn.execute("DELETE FROM ranks WHERE puuid=?", (puuid,))
    for e in entries:
        conn.execute(
            """INSERT OR REPLACE INTO ranks
               (puuid, queue_type, tier, division, lp, wins, losses, hot_streak, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                puuid,
                e.get("queueType", "?"),
                e.get("tier"),
                e.get("rank"),
                e.get("leaguePoints", 0),
                e.get("wins", 0),
                e.get("losses", 0),
                1 if e.get("hotStreak") else 0,
                ts,
            ),
        )


def known_match_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT match_id FROM matches")}


def last_match_time(conn: sqlite3.Connection, puuid: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(game_creation) FROM participants WHERE puuid=?", (puuid,)
    ).fetchone()
    return row[0] if row and row[0] else None


def save_match(conn: sqlite3.Connection, match: dict, tracked: set[str]) -> bool:
    """Grava a partida e todos os 10 participantes. Retorna False se invalida."""
    info = match.get("info") or {}
    meta = match.get("metadata") or {}
    match_id = meta.get("matchId")
    parts = info.get("participants") or []
    if not match_id or not parts:
        return False

    duration = int(info.get("gameDuration") or 0)
    # Partidas antigas reportavam duracao em ms.
    if duration > 100_000:
        duration //= 1000

    conn.execute(
        """INSERT OR REPLACE INTO matches
           (match_id, queue_id, game_creation, game_duration, game_version, game_mode, raw)
           VALUES (?,?,?,?,?,?,?)""",
        (
            match_id,
            int(info.get("queueId") or 0),
            int(info.get("gameCreation") or 0),
            duration,
            info.get("gameVersion", ""),
            info.get("gameMode", ""),
            zlib.compress(json.dumps(match, separators=(",", ":")).encode("utf-8"), 6),
        ),
    )

    team_kills: dict[int, int] = {}
    team_deaths: dict[int, int] = {}
    for p in parts:
        tid = int(p.get("teamId") or 0)
        team_kills[tid] = team_kills.get(tid, 0) + int(p.get("kills") or 0)
        team_deaths[tid] = team_deaths.get(tid, 0) + int(p.get("deaths") or 0)

    for p in parts:
        puuid = p.get("puuid") or ""
        tid = int(p.get("teamId") or 0)
        ch = p.get("challenges") or {}
        row = {
            "match_id": match_id,
            "puuid": puuid,
            "queue_id": int(info.get("queueId") or 0),
            "game_creation": int(info.get("gameCreation") or 0),
            "game_duration": duration,
            "team_id": tid,
            "win": 1 if p.get("win") else 0,
            "champion_id": int(p.get("championId") or 0),
            "champion_name": p.get("championName", ""),
            "position": p.get("teamPosition") or p.get("individualPosition") or "",
            "kills": int(p.get("kills") or 0),
            "deaths": int(p.get("deaths") or 0),
            "assists": int(p.get("assists") or 0),
            "cs": int(p.get("totalMinionsKilled") or 0)
            + int(p.get("neutralMinionsKilled") or 0),
            "gold": int(p.get("goldEarned") or 0),
            "damage_champions": int(p.get("totalDamageDealtToChampions") or 0),
            "damage_taken": int(p.get("totalDamageTaken") or 0),
            "damage_objectives": int(p.get("damageDealtToObjectives") or 0),
            "heal_shield": int(p.get("totalHealsOnTeammates") or 0)
            + int(p.get("totalDamageShieldedOnTeammates") or 0),
            "vision_score": int(p.get("visionScore") or 0),
            "wards_placed": int(p.get("wardsPlaced") or 0),
            "wards_killed": int(p.get("wardsKilled") or 0),
            "control_wards": int(p.get("visionWardsBoughtInGame") or 0),
            "first_blood": 1 if p.get("firstBloodKill") else 0,
            "first_blood_assist": 1 if p.get("firstBloodAssist") else 0,
            "double_kills": int(p.get("doubleKills") or 0),
            "triple_kills": int(p.get("tripleKills") or 0),
            "quadra_kills": int(p.get("quadraKills") or 0),
            "penta_kills": int(p.get("pentaKills") or 0),
            "turret_takedowns": int(
                p.get("turretTakedowns") or ch.get("turretTakedowns") or 0
            ),
            "team_kills": team_kills.get(tid, 0),
            "team_deaths": team_deaths.get(tid, 0),
            "early_surrender": 1 if p.get("gameEndedInEarlySurrender") else 0,
            "surrender": 1 if p.get("gameEndedInSurrender") else 0,
            "tracked": 1 if puuid in tracked else 0,
        }
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        conn.execute(
            f"INSERT OR REPLACE INTO participants ({cols}) VALUES ({marks})", row
        )
    return True


def enqueue_matches(conn: sqlite3.Connection, match_ids: Iterable[str],
                    ts: int) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO match_queue (match_id, discovered_at, done) VALUES (?,?,0)",
        [(m, ts) for m in match_ids],
    )
    # Quem ja esta em matches nao precisa ser baixado de novo.
    conn.execute(
        "UPDATE match_queue SET done=1 "
        "WHERE done=0 AND match_id IN (SELECT match_id FROM matches)"
    )
    conn.commit()


def pending_matches(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT match_id FROM match_queue WHERE done=0 ORDER BY discovered_at"
    )]


def mark_done(conn: sqlite3.Connection, match_id: str) -> None:
    conn.execute("UPDATE match_queue SET done=1 WHERE match_id=?", (match_id,))


def refresh_tracked(conn: sqlite3.Connection) -> int:
    """Recalcula o flag `tracked` a partir da tabela players.

    O flag era congelado no momento do download. Isso quebrava em tres casos reais:
    membro novo entrava e todo o historico anterior dele (ja no banco, vindo das
    partidas dos outros) continuava invisivel; uma falha temporaria ao resolver um
    Riot ID marcava as partidas daquele run com tracked=0 para sempre; e ex-membro
    continuava contando. Derivar da tabela players corrige os tres de uma vez.
    """
    cur = conn.execute(
        """UPDATE participants
              SET tracked = CASE WHEN puuid IN (SELECT puuid FROM players)
                                 THEN 1 ELSE 0 END
            WHERE tracked != CASE WHEN puuid IN (SELECT puuid FROM players)
                                  THEN 1 ELSE 0 END"""
    )
    conn.commit()
    return cur.rowcount


def prune_players(conn: sqlite3.Connection,
                  roster: set[tuple[str, str]]) -> list[str]:
    """Tira do banco quem nao esta mais em players.json.

    Sem isso, um Riot ID digitado errado que resolveu uma vez fica no dashboard
    para sempre, mesmo depois de corrigido no players.json.
    """
    removidos = []
    for row in conn.execute("SELECT puuid, game_name, tag_line FROM players"):
        chave = (row["game_name"].lower(), row["tag_line"].lower())
        if chave in roster:
            continue
        removidos.append(f"{row['game_name']}#{row['tag_line']}")
        conn.execute("DELETE FROM players WHERE puuid=?", (row["puuid"],))
        conn.execute("DELETE FROM ranks WHERE puuid=?", (row["puuid"],))
    if removidos:
        conn.commit()
    return removidos


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

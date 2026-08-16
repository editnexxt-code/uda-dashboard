"""Persistencia em SQLite. Partida baixada uma vez fica no banco para sempre."""

from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path
from typing import Iterable

# Versao do schema. Subir este numero dispara o backfill uma unica vez; depois a
# marca fica em meta e o run seguinte nao varre as 1200+ partidas de novo.
SCHEMA_VERSION = 2

# Colunas destravadas do JSON que ja esta no banco (custo zero de API). A MESMA
# lista alimenta o CREATE TABLE e o ALTER TABLE da migracao: banco novo e banco
# velho nunca podem divergir campo a campo.
NOVAS_PARTICIPANTS: list[tuple[str, str]] = [
    # tempo e sobrevivencia
    ("time_dead", "INTEGER DEFAULT 0"),
    ("time_played", "INTEGER DEFAULT 0"),
    ("longest_alive", "INTEGER DEFAULT 0"),
    ("champ_level", "INTEGER DEFAULT 0"),
    # economia
    ("gold_spent", "INTEGER DEFAULT 0"),
    ("bounty_gold", "INTEGER DEFAULT 0"),
    ("items_purchased", "INTEGER DEFAULT 0"),
    ("consumables", "INTEGER DEFAULT 0"),
    # dano
    ("self_mitigated", "INTEGER DEFAULT 0"),
    ("damage_buildings", "INTEGER DEFAULT 0"),
    ("damage_epic", "INTEGER DEFAULT 0"),
    ("dmg_physical", "INTEGER DEFAULT 0"),
    ("dmg_magic", "INTEGER DEFAULT 0"),
    ("dmg_true", "INTEGER DEFAULT 0"),
    ("dpm", "REAL DEFAULT 0"),
    ("gpm", "REAL DEFAULT 0"),
    ("vspm", "REAL DEFAULT 0"),
    ("dmg_share", "REAL DEFAULT 0"),
    ("dmg_taken_share", "REAL DEFAULT 0"),
    ("kp", "REAL DEFAULT 0"),
    # suporte: heal e escudo separados, que hoje somem dentro de heal_shield
    ("heals_teammates", "INTEGER DEFAULT 0"),
    ("shields_teammates", "INTEGER DEFAULT 0"),
    ("save_ally", "INTEGER DEFAULT 0"),
    ("control_wards_placed", "INTEGER DEFAULT 0"),
    # combate
    ("largest_spree", "INTEGER DEFAULT 0"),
    ("largest_crit", "INTEGER DEFAULT 0"),
    ("cc_time", "INTEGER DEFAULT 0"),
    ("cc_dealt", "INTEGER DEFAULT 0"),
    ("solo_kills", "INTEGER DEFAULT 0"),
    ("outnumbered_kills", "INTEGER DEFAULT 0"),
    ("immobilizations", "INTEGER DEFAULT 0"),
    ("skillshots_hit", "INTEGER DEFAULT 0"),
    ("skillshots_dodged", "INTEGER DEFAULT 0"),
    ("ability_uses", "INTEGER DEFAULT 0"),
    ("flawless_aces", "INTEGER DEFAULT 0"),
    ("aces_15", "INTEGER DEFAULT 0"),
    ("multikill_spell", "INTEGER DEFAULT 0"),
    ("multikill_flash", "INTEGER DEFAULT 0"),
    ("survived_low_hp", "INTEGER DEFAULT 0"),
    ("took_large_dmg", "INTEGER DEFAULT 0"),
    # objetivos
    ("epic_steals", "INTEGER DEFAULT 0"),
    ("epic_steals_no_smite", "INTEGER DEFAULT 0"),
    ("dragon_td", "INTEGER DEFAULT 0"),
    ("baron_td", "INTEGER DEFAULT 0"),
    ("herald_td", "INTEGER DEFAULT 0"),
    ("void_kills", "INTEGER DEFAULT 0"),
    ("perfect_souls", "INTEGER DEFAULT 0"),
    ("enemy_jungle_kills", "INTEGER DEFAULT 0"),
    ("buffs_stolen", "INTEGER DEFAULT 0"),
    ("turret_plates", "INTEGER DEFAULT 0"),
    ("turrets_lost", "INTEGER DEFAULT 0"),
    ("inhibs_lost", "INTEGER DEFAULT 0"),
    ("had_open_nexus", "INTEGER DEFAULT 0"),
    ("first_turret_time", "REAL DEFAULT 0"),
    ("quick_first_turret", "INTEGER DEFAULT 0"),
    ("team_baron", "INTEGER DEFAULT 0"),
    ("team_dragon", "INTEGER DEFAULT 0"),
    ("team_herald", "INTEGER DEFAULT 0"),
    # duelo de rota
    ("cs_adv", "INTEGER DEFAULT 0"),
    ("cs_diff", "INTEGER DEFAULT 0"),
    ("lvl_lead", "INTEGER DEFAULT 0"),
    ("vision_adv", "REAL DEFAULT 0"),
    ("lane_cs10", "INTEGER DEFAULT 0"),
    ("max_kill_deficit", "INTEGER DEFAULT 0"),
    ("opponent_champ_id", "INTEGER DEFAULT 0"),
    ("opponent_champ", "TEXT"),
    # build
    ("items", "TEXT"),
    ("spell1_id", "INTEGER DEFAULT 0"),
    ("spell2_id", "INTEGER DEFAULT 0"),
    ("spell4_casts", "INTEGER DEFAULT 0"),
    ("spell_casts", "INTEGER DEFAULT 0"),
    ("flash_casts", "INTEGER DEFAULT 0"),
    ("keystone", "INTEGER DEFAULT 0"),
    ("perk_primary", "INTEGER DEFAULT 0"),
    ("perk_secondary", "INTEGER DEFAULT 0"),
    ("perks_json", "TEXT"),
    # comportamento
    ("pings_total", "INTEGER DEFAULT 0"),
    ("ping_mia", "INTEGER DEFAULT 0"),
    ("was_afk", "INTEGER DEFAULT 0"),
    # raridades que viram badge
    ("danced_herald", "INTEGER DEFAULT 0"),
    ("snowballs_hit", "INTEGER DEFAULT 0"),
    ("poro_explosions", "INTEGER DEFAULT 0"),
    ("fist_bumps", "INTEGER DEFAULT 0"),
    ("unseen_recalls", "INTEGER DEFAULT 0"),
    ("blast_cone", "INTEGER DEFAULT 0"),
    ("quick_cleanse", "INTEGER DEFAULT 0"),
    ("assist_streak_12", "INTEGER DEFAULT 0"),
    # arena
    ("placement", "INTEGER DEFAULT 0"),
    ("augments", "TEXT"),
]

NOVAS_MATCHES: list[tuple[str, str]] = [
    ("bans_json", "TEXT"),
    ("objectives_json", "TEXT"),
    ("patch", "TEXT"),
]


def _ddl(cols: list[tuple[str, str]]) -> str:
    return "".join(f"    {nome:<20} {tipo},\n" for nome, tipo in cols)


SCHEMA = f"""
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
    raw           BLOB,
{_ddl(NOVAS_MATCHES).rstrip(",\n")}
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
{_ddl(NOVAS_PARTICIPANTS)}    PRIMARY KEY (match_id, puuid)
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
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Alinha um banco antigo ao SCHEMA atual. Idempotente.

    O CREATE TABLE IF NOT EXISTS nao toca em tabela que ja existe, entao sem isto
    todo banco anterior a v2 abriria sem as colunas novas e quebraria no primeiro
    INSERT. Aqui as faltantes entram uma a uma, e rodar de novo nao faz nada.
    """
    novas: list[str] = []
    for tabela, cols in (("participants", NOVAS_PARTICIPANTS),
                         ("matches", NOVAS_MATCHES)):
        tem = {r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")}
        if not tem:  # tabela ainda nao criada; o SCHEMA cuida dela
            continue
        for nome, tipo in cols:
            if nome in tem:
                continue
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
            novas.append(f"{tabela}.{nome}")
    if novas:
        conn.commit()
    return novas


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


# Pings que a Riot realmente preenche. basicPings, dangerPings, holdPings e
# visionClearedPings vem zerados em 100% do banco: somar so daria falsa precisao.
PING_FIELDS = (
    "allInPings", "assistMePings", "commandPings", "enemyMissingPings",
    "enemyVisionPings", "getBackPings", "needVisionPings", "onMyWayPings",
    "pushPings", "retreatPings",
)


def _i(valor) -> int:
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def _f(valor) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def extrair(match: dict, tracked: set[str]) -> tuple[dict, list[dict]]:
    """Traduz o JSON cru da Riot em (linha de matches, linhas de participants).

    Extracao unica de proposito: save_match e backfill chamam esta mesma funcao,
    senao partida baixada hoje e partida repopulada do raw acabariam com valores
    diferentes na mesma coluna.
    """
    info = match.get("info") or {}
    meta = match.get("metadata") or {}
    match_id = meta.get("matchId")
    parts = info.get("participants") or []
    if not match_id or not parts:
        return {}, []

    duration = _i(info.get("gameDuration"))
    # Partidas antigas reportavam duracao em ms.
    if duration > 100_000:
        duration //= 1000
    queue_id = _i(info.get("queueId"))
    creation = _i(info.get("gameCreation"))
    versao = info.get("gameVersion", "") or ""
    times = info.get("teams") or []

    m_row = {
        "match_id": match_id,
        "queue_id": queue_id,
        "game_creation": creation,
        "game_duration": duration,
        "game_version": versao,
        "game_mode": info.get("gameMode", ""),
        "bans_json": json.dumps(
            [[_i(b.get("championId")) for b in (t.get("bans") or [])] for t in times],
            separators=(",", ":"),
        ),
        "objectives_json": json.dumps(
            {str(_i(t.get("teamId"))): (t.get("objectives") or {}) for t in times},
            separators=(",", ":"),
        ),
        # So os dois primeiros numeros: e o eixo de patch e a versao do ddragon.
        "patch": ".".join(versao.split(".")[:2]),
    }

    team_kills: dict[int, int] = {}
    team_deaths: dict[int, int] = {}
    for p in parts:
        tid = _i(p.get("teamId"))
        team_kills[tid] = team_kills.get(tid, 0) + _i(p.get("kills"))
        team_deaths[tid] = team_deaths.get(tid, 0) + _i(p.get("deaths"))

    obj: dict[int, dict] = {_i(t.get("teamId")): (t.get("objectives") or {})
                            for t in times}

    # Indice de rota para achar o oponente direto. maxCsAdvantageOnLaneOpponent
    # nunca vem negativo, entao deficit de CS e nemesis de rota so existem se a
    # gente cruzar os dois participantes na mao.
    por_pos: dict[str, list[dict]] = {}
    for p in parts:
        pos = (p.get("teamPosition") or "").upper()
        if pos and pos != "INVALID":
            por_pos.setdefault(pos, []).append(p)

    linhas = []
    for p in parts:
        puuid = p.get("puuid") or ""
        tid = _i(p.get("teamId"))
        ch = p.get("challenges") or {}
        cs = _i(p.get("totalMinionsKilled")) + _i(p.get("neutralMinionsKilled"))

        pos = (p.get("teamPosition") or "").upper()
        opp = next((q for q in por_pos.get(pos, []) if _i(q.get("teamId")) != tid), None)
        if opp:
            opp_cs = _i(opp.get("totalMinionsKilled")) + _i(opp.get("neutralMinionsKilled"))
            cs_diff = cs - opp_cs
        else:
            cs_diff = 0

        # Flash tem id 4; qual dos dois slots ele ocupa varia por jogador.
        flash = 0
        for slot in (1, 2):
            if _i(p.get(f"summoner{slot}Id")) == 4:
                flash += _i(p.get(f"summoner{slot}Casts"))

        estilos = ((p.get("perks") or {}).get("styles") or [])
        primario = estilos[0] if estilos else {}
        secundario = estilos[1] if len(estilos) > 1 else {}
        selecoes = primario.get("selections") or []

        augs = [_i(p.get(f"playerAugment{i}")) for i in range(1, 7)]
        objs = obj.get(tid) or {}

        row = {
            "match_id": match_id,
            "puuid": puuid,
            "queue_id": queue_id,
            "game_creation": creation,
            "game_duration": duration,
            "team_id": tid,
            "win": 1 if p.get("win") else 0,
            "champion_id": _i(p.get("championId")),
            "champion_name": p.get("championName", ""),
            "position": p.get("teamPosition") or p.get("individualPosition") or "",
            "kills": _i(p.get("kills")),
            "deaths": _i(p.get("deaths")),
            "assists": _i(p.get("assists")),
            "cs": cs,
            "gold": _i(p.get("goldEarned")),
            "damage_champions": _i(p.get("totalDamageDealtToChampions")),
            "damage_taken": _i(p.get("totalDamageTaken")),
            "damage_objectives": _i(p.get("damageDealtToObjectives")),
            "heal_shield": _i(p.get("totalHealsOnTeammates"))
            + _i(p.get("totalDamageShieldedOnTeammates")),
            "vision_score": _i(p.get("visionScore")),
            "wards_placed": _i(p.get("wardsPlaced")),
            "wards_killed": _i(p.get("wardsKilled")),
            "control_wards": _i(p.get("visionWardsBoughtInGame")),
            "first_blood": 1 if p.get("firstBloodKill") else 0,
            "first_blood_assist": 1 if p.get("firstBloodAssist") else 0,
            "double_kills": _i(p.get("doubleKills")),
            "triple_kills": _i(p.get("tripleKills")),
            "quadra_kills": _i(p.get("quadraKills")),
            "penta_kills": _i(p.get("pentaKills")),
            "turret_takedowns": _i(p.get("turretTakedowns") or ch.get("turretTakedowns")),
            "team_kills": team_kills.get(tid, 0),
            "team_deaths": team_deaths.get(tid, 0),
            "early_surrender": 1 if p.get("gameEndedInEarlySurrender") else 0,
            "surrender": 1 if p.get("gameEndedInSurrender") else 0,
            "tracked": 1 if puuid in tracked else 0,
            # --- destravadas na v2 ---
            "time_dead": _i(p.get("totalTimeSpentDead")),
            "time_played": _i(p.get("timePlayed")),
            "longest_alive": _i(p.get("longestTimeSpentLiving")),
            "champ_level": _i(p.get("champLevel")),
            "gold_spent": _i(p.get("goldSpent")),
            "bounty_gold": _i(ch.get("bountyGold")),
            "items_purchased": _i(p.get("itemsPurchased")),
            "consumables": _i(p.get("consumablesPurchased")),
            "self_mitigated": _i(p.get("damageSelfMitigated")),
            "damage_buildings": _i(p.get("damageDealtToBuildings")),
            "damage_epic": _i(p.get("damageDealtToEpicMonsters")),
            "dmg_physical": _i(p.get("physicalDamageDealtToChampions")),
            "dmg_magic": _i(p.get("magicDamageDealtToChampions")),
            "dmg_true": _i(p.get("trueDamageDealtToChampions")),
            "dpm": _f(ch.get("damagePerMinute")),
            "gpm": _f(ch.get("goldPerMinute")),
            "vspm": _f(ch.get("visionScorePerMinute")),
            "dmg_share": _f(ch.get("teamDamagePercentage")),
            "dmg_taken_share": _f(ch.get("damageTakenOnTeamPercentage")),
            "kp": _f(ch.get("killParticipation")),
            "heals_teammates": _i(p.get("totalHealsOnTeammates")),
            "shields_teammates": _i(p.get("totalDamageShieldedOnTeammates")),
            "save_ally": _i(ch.get("saveAllyFromDeath")),
            "control_wards_placed": _i(p.get("detectorWardsPlaced")),
            "largest_spree": _i(p.get("largestKillingSpree")),
            "largest_crit": _i(p.get("largestCriticalStrike")),
            "cc_time": _i(p.get("timeCCingOthers")),
            "cc_dealt": _i(p.get("totalTimeCCDealt")),
            "solo_kills": _i(ch.get("soloKills")),
            "outnumbered_kills": _i(ch.get("outnumberedKills")),
            "immobilizations": _i(ch.get("enemyChampionImmobilizations")),
            "skillshots_hit": _i(ch.get("skillshotsHit")),
            "skillshots_dodged": _i(ch.get("skillshotsDodged")),
            "ability_uses": _i(ch.get("abilityUses")),
            "flawless_aces": _i(ch.get("flawlessAces")),
            "aces_15": _i(ch.get("acesBefore15Minutes")),
            "multikill_spell": _i(ch.get("multiKillOneSpell")),
            "multikill_flash": _i(ch.get("multikillsAfterAggressiveFlash")),
            "survived_low_hp": _i(ch.get("survivedSingleDigitHpCount")),
            "took_large_dmg": _i(ch.get("tookLargeDamageSurvived")),
            "epic_steals": _i(ch.get("epicMonsterSteals") or p.get("objectivesStolen")),
            "epic_steals_no_smite": _i(ch.get("epicMonsterStolenWithoutSmite")),
            "dragon_td": _i(ch.get("dragonTakedowns")),
            "baron_td": _i(ch.get("baronTakedowns")),
            "herald_td": _i(ch.get("riftHeraldTakedowns")),
            "void_kills": _i(ch.get("voidMonsterKill")),
            "perfect_souls": _i(ch.get("perfectDragonSoulsTaken")),
            "enemy_jungle_kills": _i(ch.get("enemyJungleMonsterKills")),
            "buffs_stolen": _i(ch.get("buffsStolen")),
            "turret_plates": _i(ch.get("turretPlatesTaken")),
            "turrets_lost": _i(p.get("turretsLost")),
            "inhibs_lost": _i(p.get("inhibitorsLost")),
            "had_open_nexus": _i(ch.get("hadOpenNexus")),
            "first_turret_time": _f(ch.get("firstTurretKilledTime")),
            "quick_first_turret": _i(ch.get("quickFirstTurret")),
            "team_baron": _i((objs.get("baron") or {}).get("kills")),
            "team_dragon": _i((objs.get("dragon") or {}).get("kills")),
            "team_herald": _i((objs.get("riftHerald") or {}).get("kills")),
            "cs_adv": _i(ch.get("maxCsAdvantageOnLaneOpponent")),
            "cs_diff": cs_diff,
            "lvl_lead": _i(ch.get("maxLevelLeadLaneOpponent")),
            "vision_adv": _f(ch.get("visionScoreAdvantageLaneOpponent")),
            "lane_cs10": _i(ch.get("laneMinionsFirst10Minutes")),
            "max_kill_deficit": _i(ch.get("maxKillDeficit")),
            "opponent_champ_id": _i(opp.get("championId")) if opp else 0,
            "opponent_champ": (opp.get("championName", "") if opp else ""),
            "items": json.dumps([_i(p.get(f"item{i}")) for i in range(7)],
                                separators=(",", ":")),
            "spell1_id": _i(p.get("summoner1Id")),
            "spell2_id": _i(p.get("summoner2Id")),
            "spell4_casts": _i(p.get("spell4Casts")),
            "spell_casts": sum(_i(p.get(f"spell{i}Casts")) for i in range(1, 5)),
            "flash_casts": flash,
            "keystone": _i((selecoes[0] if selecoes else {}).get("perk")),
            "perk_primary": _i(primario.get("style")),
            "perk_secondary": _i(secundario.get("style")),
            "perks_json": json.dumps(p.get("perks") or {}, separators=(",", ":")),
            "pings_total": sum(_i(p.get(k)) for k in PING_FIELDS),
            "ping_mia": _i(p.get("enemyMissingPings")),
            "was_afk": 1 if p.get("wasAfk") else 0,
            "danced_herald": _i(ch.get("dancedWithRiftHerald")),
            "snowballs_hit": _i(ch.get("snowballsHit")),
            "poro_explosions": _i(ch.get("poroExplosions")),
            "fist_bumps": _i(ch.get("fistBumpParticipation")),
            "unseen_recalls": _i(ch.get("unseenRecalls")),
            "blast_cone": _i(ch.get("blastConeOppositeOpponentCount")),
            "quick_cleanse": _i(ch.get("quickCleanse")),
            "assist_streak_12": _i(ch.get("12AssistStreakCount")),
            "placement": _i(p.get("placement") or p.get("subteamPlacement")),
            "augments": json.dumps(augs, separators=(",", ":")) if any(augs) else None,
        }
        linhas.append(row)
    return m_row, linhas


def _gravar_participante(conn: sqlite3.Connection, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT OR REPLACE INTO participants ({cols}) VALUES ({marks})", row)


def save_match(conn: sqlite3.Connection, match: dict, tracked: set[str]) -> bool:
    """Grava a partida e todos os 10 participantes. Retorna False se invalida."""
    m_row, linhas = extrair(match, tracked)
    if not linhas:
        return False

    m_row["raw"] = zlib.compress(
        json.dumps(match, separators=(",", ":")).encode("utf-8"), 6
    )
    cols = ", ".join(m_row)
    marks = ", ".join(f":{c}" for c in m_row)
    conn.execute(f"INSERT OR REPLACE INTO matches ({cols}) VALUES ({marks})", m_row)

    for row in linhas:
        _gravar_participante(conn, row)
    return True


def backfill(conn: sqlite3.Connection, lote: int = 200) -> int:
    """Repopula as colunas novas das partidas que ja estavam no banco.

    matches.raw guarda o JSON completo comprimido, entao da para reextrair tudo
    sem gastar uma unica chamada de API. Sem isto as 1200+ partidas antigas
    ficariam com as colunas da v2 zeradas para sempre e os recordes novos
    nasceriam vazios. matches.patch NULL e a marca de 'ainda nao processada',
    e schema_version em meta evita reabrir a varredura a cada run.
    """
    if get_meta(conn, "schema_version") == str(SCHEMA_VERSION):
        return 0

    pendentes = [r[0] for r in conn.execute(
        "SELECT match_id FROM matches WHERE patch IS NULL"
    )]
    total = len(pendentes)
    if not total:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        conn.commit()
        return 0

    # O flag tracked e derivado do roster atual, igual ao refresh_tracked.
    tracked = {r[0] for r in conn.execute("SELECT puuid FROM players")}
    print(f"\nMigracao: repopulando {total} partidas a partir do JSON local "
          "(sem API)...")

    feitas = 0
    for i, match_id in enumerate(pendentes, 1):
        linha = conn.execute(
            "SELECT raw FROM matches WHERE match_id=?", (match_id,)
        ).fetchone()
        bruto = linha["raw"] if linha else None
        if not bruto:
            # Sem raw nao da para reextrair; marca para nao tentar de novo.
            conn.execute("UPDATE matches SET patch='' WHERE match_id=?", (match_id,))
            continue
        try:
            match = json.loads(zlib.decompress(bruto).decode("utf-8"))
        except (zlib.error, ValueError, UnicodeDecodeError):
            conn.execute("UPDATE matches SET patch='' WHERE match_id=?", (match_id,))
            continue

        m_row, linhas = extrair(match, tracked)
        if not linhas:
            conn.execute("UPDATE matches SET patch='' WHERE match_id=?", (match_id,))
            continue

        # raw fica de fora do UPDATE: ja esta gravado e recomprimir seria so custo.
        campos = [c for c in m_row if c != "match_id"]
        conn.execute(
            "UPDATE matches SET " + ", ".join(f"{c}=:{c}" for c in campos)
            + " WHERE match_id=:match_id", m_row,
        )
        for row in linhas:
            _gravar_participante(conn, row)
        feitas += 1

        if i % lote == 0:
            conn.commit()
            print(f"  {i}/{total} partidas")

    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()
    print(f"  concluido: {feitas} partidas repopuladas")
    return feitas


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

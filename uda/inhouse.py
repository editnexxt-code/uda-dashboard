"""PARTIDAS PERSONALIZADAS (inhouse): conversao do formato do cliente e metricas.

Este modulo e a ponte entre duas coisas que nao se parecem:

  personalizadas/bruto/{gameId}.json   o detalhe que o cliente do LoL devolve,
                                       no esquema ANTIGO da Riot (stats{} plano,
                                       participantIdentities{} a parte)
  data/uda.sqlite3                     o banco do dashboard, no esquema do
                                       Match-V5 (124 colunas em participants)

Roda no BUILD, nunca na coleta. O coletor (personalizadas.py) e local, precisa do
cliente aberto e so escreve arquivo; aqui a gente le esse arquivo e grava no
banco -- inclusive dentro do GitHub Actions, que nunca vera cliente nenhum.

Duas armadilhas do formato do cliente ficam resolvidas aqui, de uma vez:

  1. participantIdentities[].player.puuid tem 36 caracteres. E um UUID interno e
     ESTAVEL por conta, mas nao e o PUUID de 78 que o resto do banco usa: casar
     por ele juntaria contas diferentes. O que e real no cliente e gameName +
     tagLine, entao a identidade se resolve por Riot ID -- pelo cache que o
     coletor gravou, ou pela propria tabela players, que ja tem o PUUID de quem
     esta no roster. So quem nao aparece em lugar nenhum (convidado de fora)
     fica com uma chave local derivada daquele UUID de 36.

  2. o cliente nao tem o bloco challenges{} do Match-V5. Metade dos recordes
     depende dele. Nao ha o que inventar: as colunas ficam no valor neutro e o
     kpi.py filtra a Sala dos Recordes por fila em vez de expor 15 vitrines
     eternamente vazias.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

# 3200 e 3220 = ARAM personalizada, 3270 = modo Kiwi. 3140 e a Ferramenta de
# Treino: e treino solo, nao partida do grupo, e fica de fora em toda etapa.
INHOUSE_QUEUES = {3200, 3220, 3270}
FILA_TREINO = 3140

# Pisos. Nenhum deles e estetico: cada um marca onde a amostra deixa de dizer
# alguma coisa. Duelo com 3 jogos e moeda jogada tres vezes.
MIN_DUR = 300         # remake: 3272219465 durou 110s
MIN_DUEL = 4          # confrontos diretos contra a MESMA pessoa
MIN_PAIR = 6          # jogos no mesmo time para a dupla contar
MIN_BASE = 5          # ARAM publica para o espelho ter base
MIN_IH = 5            # personalizadas para o espelho ter o outro lado
GAP_NOITE = 4 * 3600 * 1000   # intervalo que separa duas noites de jogo

QUEUE_LABEL = {3200: "ARAM personalizada", 3220: "ARAM personalizada",
               3270: "Modo Kiwi", FILA_TREINO: "Ferramenta de treino"}

# Fila da ARAM publica. E a UNICA base honesta para o Espelho: mesmo mapa, mesmo
# modo, mesma duracao. Comparar com a Fenda seria comparar CS/min de duas coisas
# diferentes.
FILA_BASE = 450


# ------------------------------------------------------------------ utilidades

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


def _div(a: float, b: float, padrao: float = 0.0) -> float:
    return a / b if b else padrao


def _r(valor: float, casas: int = 2) -> float:
    return round(float(valor), casas)


def chave_conta(game_name: str, tag_line: str) -> str:
    """Identidade de uma pessoa dentro das personalizadas.

    Riot ID em minusculas, e nao PUUID, porque 5 das 16 pessoas em campo nao
    estao no players.json -- convidados nao tem PUUID resolvido e mesmo assim
    precisam aparecer na tabela de confrontos.
    """
    return f"{(game_name or '').strip()}#{(tag_line or '').strip()}".lower()


# ---------------------------------------------------------------- filtro LCU

def aceitar_resumo(resumo: dict) -> bool:
    """Vale a pena baixar o detalhe deste jogo?

    O resumo do historico ja traz gameType e queueId, entao filtrar aqui evita
    baixar o detalhe de toda ranqueada da semana so para descartar depois.
    """
    if _i(resumo.get("queueId")) == FILA_TREINO:
        return False
    if _i(resumo.get("queueId")) in INHOUSE_QUEUES:
        return True
    return (resumo.get("gameType") or "").upper() == "CUSTOM_GAME"


def aceitar(jogo: dict) -> tuple[bool, str]:
    """O detalhe completo e uma personalizada do grupo? (ok, motivo da recusa)"""
    queue = _i(jogo.get("queueId"))
    if queue == FILA_TREINO:
        return False, "ferramenta de treino"
    tipo = (jogo.get("gameType") or "").upper()
    if queue not in INHOUSE_QUEUES and tipo != "CUSTOM_GAME":
        return False, "fila publica"
    if len(jogo.get("participants") or []) < 4:
        return False, "gente de menos"
    return True, ""


# -------------------------------------------------------------- cache de conta

def caminho_cache(pasta: Path) -> Path:
    return Path(pasta) / "contas.json"


def carregar_cache(caminho: Path) -> dict:
    """{contas: {chave: {gameName, tagLine, puuid, icon}}, sem_conta: {...}}"""
    vazio = {"contas": {}, "sem_conta": {}}
    caminho = Path(caminho)
    if not caminho.exists():
        return vazio
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return vazio
    if not isinstance(dados, dict):
        return vazio
    dados.setdefault("contas", {})
    dados.setdefault("sem_conta", {})
    return dados


def salvar_cache(cache: dict, caminho: Path) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def jogos_do_acervo(dir_bruto: Path) -> list[dict]:
    """Le personalizadas/bruto/*.json, ja descartando o que nao vale."""
    dir_bruto = Path(dir_bruto)
    if not dir_bruto.exists():
        return []
    jogos = []
    for arq in sorted(dir_bruto.glob("*.json")):
        try:
            jogo = json.loads(arq.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            continue
        if not isinstance(jogo, dict) or not jogo.get("gameId"):
            continue
        ok, _motivo = aceitar(jogo)
        if ok:
            jogos.append(jogo)
    return sorted(jogos, key=lambda j: _i(j.get("gameCreation")))


def contas_do_acervo(dir_bruto: Path) -> dict[str, dict]:
    """Todo Riot ID que ja apareceu numa personalizada guardada."""
    contas: dict[str, dict] = {}
    for jogo in jogos_do_acervo(dir_bruto):
        for ident in jogo.get("participantIdentities") or []:
            p = ident.get("player") or {}
            nome, tag = p.get("gameName") or "", p.get("tagLine") or ""
            if not nome or not tag:
                continue
            contas[chave_conta(nome, tag)] = {
                "gameName": nome, "tagLine": tag,
                "icon": _i(p.get("profileIcon")),
                "local": p.get("puuid") or "",
            }
    return contas


# ------------------------------------------------------------------- ingestao

def _identidades(jogo: dict) -> dict[int, dict]:
    saida: dict[int, dict] = {}
    for ident in jogo.get("participantIdentities") or []:
        p = ident.get("player") or {}
        saida[_i(ident.get("participantId"))] = {
            "gameName": p.get("gameName") or "",
            "tagLine": p.get("tagLine") or "",
            "icon": _i(p.get("profileIcon")),
            "local": p.get("puuid") or "",
        }
    return saida


def _puuid_local(ident: dict) -> str:
    """Chave de ultimo recurso para quem nao tem PUUID de verdade.

    O UUID de 36 do cliente e estavel por conta (o mesmo valor reaparece em todas
    as partidas da mesma pessoa), entao serve de chave primaria. O prefixo garante
    que ele nunca vai ser confundido com um PUUID real de 78 caracteres.
    """
    base = ident.get("local") or chave_conta(ident["gameName"], ident["tagLine"])
    return f"lcu:{base}"


def _mapear_participante(jogo: dict, part: dict, ident: dict, puuid: str,
                         tracked: bool, times: dict, champ_index: dict) -> dict:
    """Esquema antigo do cliente -> as colunas de participants."""
    s = part.get("stats") or {}
    tid = _i(part.get("teamId"))
    dur = _i(jogo.get("gameDuration"))
    minutos = _div(dur, 60.0)
    t = times.get(tid) or {}

    cs = _i(s.get("totalMinionsKilled")) + _i(s.get("neutralMinionsKilled"))
    dano = _i(s.get("totalDamageDealtToChampions"))
    sofrido = _i(s.get("totalDamageTaken"))
    champ_id = _i(part.get("championId"))
    entrada = (champ_index or {}).get(champ_id) or {}

    perks = {f"perk{n}": _i(s.get(f"perk{n}")) for n in range(6)}
    perks["primary"] = _i(s.get("perkPrimaryStyle"))
    perks["sub"] = _i(s.get("perkSubStyle"))

    return {
        "match_id": match_id_de(jogo),
        "puuid": puuid,
        "queue_id": _i(jogo.get("queueId")),
        "game_creation": _i(jogo.get("gameCreation")),
        "game_duration": dur,
        "team_id": tid,
        "win": 1 if s.get("win") else 0,
        "champion_id": champ_id,
        "champion_name": entrada.get("key", ""),
        # position fica NULL de proposito: timeline.lane devolve 74 NONE, 34 TOP e
        # 19 MIDDLE num mapa de UMA rota so. Gravar isso faria _role_split inventar
        # uma distribuicao de rotas que nao existe no Abismo.
        "position": None,
        "kills": _i(s.get("kills")),
        "deaths": _i(s.get("deaths")),
        "assists": _i(s.get("assists")),
        "cs": cs,
        "gold": _i(s.get("goldEarned")),
        "damage_champions": dano,
        "damage_taken": sofrido,
        "damage_objectives": _i(s.get("damageDealtToObjectives")),
        # ATENCAO: no cliente totalHeal soma cura PROPRIA e cura em aliados. Nao e
        # o totalHealsOnTeammates do V5. Por isso heals_teammates fica NULL: nulo
        # e "nao da para saber", zero seria uma afirmacao falsa.
        "heal_shield": _i(s.get("totalHeal")),
        "heals_teammates": None,
        "shields_teammates": None,
        # visao existe no dado e vale 0 em 100% das linhas (media 0,0; wardsPlaced
        # zerado nas 134). Gravar 0 encheria a tabela de uma coluna de zeros com
        # cara de medida; NULL deixa a tela esconder.
        "vision_score": None,
        "wards_placed": None,
        "wards_killed": None,
        "control_wards": None,
        "first_blood": 1 if s.get("firstBloodKill") else 0,
        "first_blood_assist": 1 if s.get("firstBloodAssist") else 0,
        "double_kills": _i(s.get("doubleKills")),
        "triple_kills": _i(s.get("tripleKills")),
        "quadra_kills": _i(s.get("quadraKills")),
        "penta_kills": _i(s.get("pentaKills")),
        "turret_takedowns": _i(s.get("turretKills")),
        "team_kills": _i(t.get("kills")),
        "team_deaths": _i(t.get("deaths")),
        "early_surrender": 1 if s.get("gameEndedInEarlySurrender") else 0,
        "surrender": 1 if s.get("gameEndedInSurrender") else 0,
        "tracked": 1 if tracked else 0,
        # derivadas, exatamente como o backfill faz para as partidas da API
        "dpm": _r(_div(dano, minutos), 1),
        "gpm": _r(_div(_i(s.get("goldEarned")), minutos), 1),
        "kp": _r(_div(_i(s.get("kills")) + _i(s.get("assists")), _i(t.get("kills"))), 4),
        "dmg_share": _r(_div(dano, _i(t.get("damage"))), 4),
        "dmg_taken_share": _r(_div(sofrido, _i(t.get("taken"))), 4),
        "champ_level": _i(s.get("champLevel")),
        "gold_spent": _i(s.get("goldSpent")),
        "self_mitigated": _i(s.get("damageSelfMitigated")),
        "damage_buildings": _i(s.get("damageDealtToTurrets")),
        "dmg_physical": _i(s.get("physicalDamageDealtToChampions")),
        "dmg_magic": _i(s.get("magicDamageDealtToChampions")),
        "dmg_true": _i(s.get("trueDamageDealtToChampions")),
        "largest_spree": _i(s.get("largestKillingSpree")),
        "largest_crit": _i(s.get("largestCriticalStrike")),
        "longest_alive": _i(s.get("longestTimeSpentLiving")),
        "cc_time": _i(s.get("timeCCingOthers")),
        "cc_dealt": _i(s.get("totalTimeCrowdControlDealt")),
        "time_played": dur,
        "items": json.dumps([_i(s.get(f"item{n}")) for n in range(7)],
                            separators=(",", ":")),
        "spell1_id": _i(part.get("spell1Id")),
        "spell2_id": _i(part.get("spell2Id")),
        "keystone": _i(s.get("perk0")),
        "perk_primary": _i(s.get("perkPrimaryStyle")),
        "perk_secondary": _i(s.get("perkSubStyle")),
        "perks_json": json.dumps(perks, separators=(",", ":")),
        "placement": _i(s.get("subteamPlacement")),
    }


def match_id_de(jogo: dict) -> str:
    """BR1_3271642284, no mesmo formato do Match-V5.

    A numeracao do cliente e a MESMA sequencia dos ids da API (uma personalizada
    3271642284 convive com uma publica 3213181783), entao nao ha colisao possivel.
    O id entra em match_queue ja com done=1 para o fetch.py nunca tentar baixar
    pela API uma partida que a API nao conhece.
    """
    plat = (jogo.get("platformId") or "BR1").upper()
    return f"{plat}_{_i(jogo.get('gameId'))}"


def _garantir_tabela(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS inhouse_accounts (
            chave     TEXT PRIMARY KEY,
            game_name TEXT,
            tag_line  TEXT,
            puuid     TEXT,
            icon      INTEGER,
            tracked   INTEGER DEFAULT 0,
            visto     INTEGER
        )""")


def importar(conn: sqlite3.Connection, pasta: str | Path = "personalizadas",
             champ_index: dict[int, dict[str, str]] | None = None,
             verbose: bool = True) -> dict[str, int]:
    """Le personalizadas/ e faz upsert em matches/participants. Idempotente.

    A fonte e a pasta bruto/, que VAI para o git -- e nao um consolidado a parte,
    que seria o mesmo conteudo duplicado dentro do mesmo repositorio. O cache de
    Riot ID -> PUUID vem de personalizadas/contas.json, gravado pelo coletor.
    """
    pasta = Path(pasta)
    dir_bruto = pasta / "bruto"
    jogos = jogos_do_acervo(dir_bruto)
    resumo = {"partidas": 0, "participantes": 0, "remakes": 0, "contas": 0}
    if not jogos:
        return resumo

    _garantir_tabela(conn)
    cache = carregar_cache(caminho_cache(pasta))
    por_riot_id = {
        k: v.get("puuid") for k, v in (cache.get("contas") or {}).items()
        if v.get("puuid")
    }
    # A tabela players ja tem o PUUID real de quem esta no roster: usar isso
    # resolve o grupo inteiro sem uma unica chamada de API, e sem depender de o
    # coletor ter rodado com a chave no .env.
    do_banco: dict[str, str] = {}
    puuids_roster: set[str] = set()
    for row in conn.execute("SELECT puuid, game_name, tag_line FROM players"):
        do_banco[chave_conta(row["game_name"], row["tag_line"])] = row["puuid"]
        puuids_roster.add(row["puuid"])

    agora = int(time.time())
    contas_vistas: dict[str, dict] = {}

    for jogo in jogos:
        dur = _i(jogo.get("gameDuration"))
        if dur < MIN_DUR:
            resumo["remakes"] += 1
            continue

        idents = _identidades(jogo)
        parts = jogo.get("participants") or []
        if not parts:
            continue

        # totais por time: sem eles nao existe participacao em abates nem fatia
        # do dano, e as duas continuam validas aqui (sao por time, e cada time
        # e um time de verdade).
        times: dict[int, dict[str, int]] = defaultdict(
            lambda: {"kills": 0, "deaths": 0, "damage": 0, "taken": 0, "gold": 0}
        )
        for part in parts:
            s = part.get("stats") or {}
            t = times[_i(part.get("teamId"))]
            t["kills"] += _i(s.get("kills"))
            t["deaths"] += _i(s.get("deaths"))
            t["damage"] += _i(s.get("totalDamageDealtToChampions"))
            t["taken"] += _i(s.get("totalDamageTaken"))
            t["gold"] += _i(s.get("goldEarned"))

        match_id = match_id_de(jogo)
        versao = jogo.get("gameVersion") or ""
        conn.execute(
            """INSERT OR REPLACE INTO matches
               (match_id, queue_id, game_creation, game_duration, game_version,
                game_mode, raw, bans_json, objectives_json, patch)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                match_id, _i(jogo.get("queueId")), _i(jogo.get("gameCreation")),
                dur, versao, jogo.get("gameMode") or "",
                # raw fica NULL: o JSON original mora em personalizadas/bruto/ e
                # esta no git. Guardar copia comprimida aqui so daria duas fontes
                # da verdade -- e o backfill do store.py tentaria reextrair essa
                # copia com o extrator do Match-V5, que zeraria a linha inteira.
                None,
                "[[],[]]",
                json.dumps({str(_i(t.get("teamId"))): {
                    "torres": _i(t.get("towerKills")),
                    "inibidores": _i(t.get("inhibitorKills")),
                } for t in (jogo.get("teams") or [])}, separators=(",", ":")),
                # patch NAO pode ficar NULL: e a marca de "ainda nao processada"
                # que faz store.backfill() varrer a partida -- e ele reextrairia
                # esta linha com o extrator do V5.
                ".".join(versao.split(".")[:2]) or "0",
            ),
        )

        for part in parts:
            ident = idents.get(_i(part.get("participantId"))) or {
                "gameName": "", "tagLine": "", "icon": 0, "local": ""}
            chave = chave_conta(ident["gameName"], ident["tagLine"])
            puuid = por_riot_id.get(chave) or do_banco.get(chave) or _puuid_local(ident)
            tracked = puuid in puuids_roster
            if chave and chave != "#":
                contas_vistas[chave] = {
                    "chave": chave, "game_name": ident["gameName"],
                    "tag_line": ident["tagLine"], "puuid": puuid,
                    "icon": ident["icon"], "tracked": 1 if tracked else 0,
                    "visto": agora,
                }
            linha = _mapear_participante(
                jogo, part, ident, puuid, tracked, times, champ_index or {})
            cols = ", ".join(linha)
            marks = ", ".join(f":{c}" for c in linha)
            conn.execute(
                f"INSERT OR REPLACE INTO participants ({cols}) VALUES ({marks})",
                linha)
            resumo["participantes"] += 1

        conn.execute(
            "INSERT OR REPLACE INTO match_queue (match_id, discovered_at, done) "
            "VALUES (?,?,1)", (match_id, agora))
        resumo["partidas"] += 1

    for conta in contas_vistas.values():
        conn.execute(
            """INSERT OR REPLACE INTO inhouse_accounts
               (chave, game_name, tag_line, puuid, icon, tracked, visto)
               VALUES (:chave,:game_name,:tag_line,:puuid,:icon,:tracked,:visto)""",
            conta)
    resumo["contas"] = len(contas_vistas)
    # A contagem de remakes vive em meta porque a partida descartada nao entra no
    # banco: sem isso a tela nao teria como dizer "1 remake fora da conta", e
    # numero que some sem explicacao vira desconfianca.
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('inhouse_remakes',?)",
                 (str(resumo["remakes"]),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('inhouse_import',?)",
                 (str(agora),))
    conn.commit()

    if verbose and resumo["partidas"]:
        print(f"  personalizadas: {resumo['partidas']} partidas, "
              f"{resumo['participantes']} participacoes, "
              f"{resumo['contas']} contas"
              + (f" ({resumo['remakes']} remake descartado)"
                 if resumo["remakes"] else ""))
    return resumo


# ------------------------------------------------------------------- metricas

def _noites(criacoes: list[int]) -> list[list[int]]:
    """Agrupa timestamps em noites. Corte: mais de 4h sem partida = outra noite."""
    blocos: list[list[int]] = []
    for ts in sorted(criacoes):
        if blocos and ts - blocos[-1][-1] <= GAP_NOITE:
            blocos[-1].append(ts)
        else:
            blocos.append([ts])
    return blocos


def _rot_noite(ts: int) -> str:
    return time.strftime("%d/%m", time.localtime(ts / 1000))


def _pessoa(chave: str, nomes: dict[str, dict]) -> dict:
    d = nomes.get(chave) or {}
    return {
        "chave": chave,
        "nome": d.get("nome") or chave,
        "icon": d.get("icon") or 29,
        "convidado": bool(d.get("convidado")),
    }


def _resumo_lado(linhas: list[dict]) -> dict:
    """Os mesmos nomes de campo que _finalize() usa no kpi.py.

    Nao e coincidencia: o Espelho reaproveita o desenho dos halteres, e dois
    dicionarios com o mesmo significado e nomes diferentes seriam a receita para
    a tela ler `deaths` de um lado e `mortes` do outro.
    """
    jogos = len(linhas)
    vitorias = sum(l["win"] for l in linhas)
    mortes = sum(l["deaths"] for l in linhas)
    minutos = _div(sum(l["dur"] for l in linhas), 60.0)
    return {
        "games": jogos,
        "wins": vitorias,
        "losses": jogos - vitorias,
        "winrate": _r(_div(vitorias, jogos) * 100, 1),
        "kda": _r(_div(sum(l["kills"] + l["assists"] for l in linhas), max(mortes, 1))),
        "dmg_min": _r(_div(sum(l["dmg"] for l in linhas), minutos), 0),
        "kp": _r(_div(sum(l["kills"] + l["assists"] for l in linhas),
                      sum(l["team_kills"] for l in linhas)) * 100, 1),
        "deaths": _r(_div(mortes, jogos)),
    }


def construir(conn: sqlite3.Connection, players: dict[str, dict],
              window_days: int) -> dict[str, Any] | None:
    """O bloco inhouseExtra: tudo que SO existe porque a partida e UDA x UDA.

    Le o banco direto, e nao as linhas ja filtradas do kpi.py, por um motivo: la
    so entra quem tem tracked=1, e 5 das 16 pessoas em campo sao convidadas. Uma
    tabela de confrontos com buracos no lugar de cinco pessoas nao e uma tabela.
    """
    _garantir_tabela(conn)
    since = int((time.time() - window_days * 86400) * 1000) if window_days > 0 else 0
    marcas = ",".join(str(q) for q in sorted(INHOUSE_QUEUES))

    cru = list(conn.execute(
        f"""SELECT p.match_id, p.puuid, p.queue_id, p.game_creation, p.game_duration,
                   p.team_id, p.win, p.kills, p.deaths, p.assists, p.gold,
                   p.damage_champions, p.team_kills, p.champion_id, p.champion_name,
                   p.tracked, m.game_mode,
                   a.game_name, a.tag_line, a.icon
              FROM participants p
              JOIN matches m ON m.match_id = p.match_id
         LEFT JOIN inhouse_accounts a ON a.puuid = p.puuid
             WHERE p.queue_id IN ({marcas})
               AND p.game_creation >= ?
               AND p.game_duration >= ?
          ORDER BY p.game_creation""",
        (since, MIN_DUR),
    ))
    if not cru:
        return None

    # -------------------------------------------------- identidade e linhas
    nomes: dict[str, dict] = {}
    linhas: list[dict] = []
    for r in cru:
        jogador = players.get(r["puuid"])
        if jogador:
            nome, tag, icon = jogador["gameName"], jogador["tagLine"], jogador["icon"]
            convidado = False
        else:
            nome = r["game_name"] or "?"
            tag = r["tag_line"] or ""
            icon = r["icon"] or 29
            convidado = True
        chave = chave_conta(nome, tag)
        nomes[chave] = {"nome": nome, "tag": tag, "icon": icon,
                        "convidado": convidado, "puuid": r["puuid"]}
        linhas.append({
            "match": r["match_id"], "chave": chave, "time": r["team_id"],
            "win": 1 if r["win"] else 0, "kills": r["kills"], "deaths": r["deaths"],
            "assists": r["assists"], "gold": r["gold"] or 0,
            "dmg": r["damage_champions"] or 0, "team_kills": r["team_kills"] or 0,
            "dur": r["game_duration"], "quando": r["game_creation"],
            "fila": r["queue_id"], "modo": r["game_mode"],
            "champ": r["champion_name"] or "", "champId": r["champion_id"] or 0,
        })

    por_partida: dict[str, list[dict]] = defaultdict(list)
    for l in linhas:
        por_partida[l["match"]].append(l)
    partidas = sorted(por_partida.values(), key=lambda g: g[0]["quando"])

    # ------------------------------------------------------------- a tabela
    duelos_par: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    juntos_par: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for grupo in partidas:
        for i in range(len(grupo)):
            for j in range(i + 1, len(grupo)):
                a, b = grupo[i], grupo[j]
                if a["chave"] == b["chave"]:
                    continue
                if a["time"] == b["time"]:
                    par = tuple(sorted((a["chave"], b["chave"])))
                    juntos_par[par][0] += 1
                    juntos_par[par][1] += a["win"]
                else:
                    # sempre na ordem (vencedor, perdedor) do proprio jogo
                    ganhou, perdeu = (a, b) if a["win"] else (b, a)
                    par = tuple(sorted((a["chave"], b["chave"])))
                    duelos_par[par][0] += 1
                    if ganhou["chave"] == par[0]:
                        duelos_par[par][1] += 1

    celulas: dict[str, dict] = {}
    for (x, y), (jogos, x_wins) in duelos_par.items():
        celulas[f"{x}|{y}"] = {"v": x_wins, "d": jogos - x_wins, "jogos": jogos}
        celulas[f"{y}|{x}"] = {"v": jogos - x_wins, "d": x_wins, "jogos": jogos}

    # ordem da tabela: quem mais jogou primeiro. E a leitura que o grupo faz --
    # a linha de cima e de quem esta sempre presente.
    jogos_por_chave: dict[str, int] = defaultdict(int)
    for l in linhas:
        jogos_por_chave[l["chave"]] += 1
    ordem = sorted(jogos_por_chave, key=lambda k: (-jogos_por_chave[k], k))

    tabela = {
        "jogadores": [{**_pessoa(k, nomes), "jogos": jogos_por_chave[k]}
                      for k in ordem],
        "celulas": celulas,
        "minDuel": MIN_DUEL,
    }

    # ------------------------------------------------------- algoz e fregues
    nemesis = []
    for k in ordem:
        algoz = fregues = None
        for outro in ordem:
            if outro == k:
                continue
            cel = celulas.get(f"{k}|{outro}")
            if not cel or cel["jogos"] < MIN_DUEL:
                continue
            if algoz is None or cel["d"] > algoz["d"] or (
                    cel["d"] == algoz["d"] and cel["v"] < algoz["v"]):
                algoz = {**_pessoa(outro, nomes), **cel}
            if fregues is None or cel["v"] > fregues["v"] or (
                    cel["v"] == fregues["v"] and cel["d"] < fregues["d"]):
                fregues = {**_pessoa(outro, nomes), **cel}
        vitorias = sum(1 for l in linhas if l["chave"] == k and l["win"])
        nemesis.append({
            **_pessoa(k, nomes),
            "jogos": jogos_por_chave[k],
            "vitorias": vitorias,
            "derrotas": jogos_por_chave[k] - vitorias,
            "winrate": _r(_div(vitorias, jogos_por_chave[k]) * 100, 1),
            "algoz": algoz if (algoz and algoz["d"] > algoz["v"]) else None,
            "fregues": fregues if (fregues and fregues["v"] > fregues["d"]) else None,
        })
    nemesis.sort(key=lambda d: (-d["jogos"], -d["winrate"]))

    # ----------------------------------------------- a dupla e o divorcio
    divorcio = []
    for (x, y), (jogos, x_wins) in juntos_par.items():
        if jogos < MIN_PAIR:
            continue
        contra = celulas.get(f"{x}|{y}") or {"v": 0, "d": 0, "jogos": 0}
        divorcio.append({
            "a": _pessoa(x, nomes), "b": _pessoa(y, nomes),
            "juntos": {"jogos": jogos, "v": x_wins, "d": jogos - x_wins,
                       "winrate": _r(_div(x_wins, jogos) * 100, 1)},
            "separados": {"jogos": contra["jogos"], "aV": contra["v"],
                          "bV": contra["d"]},
        })
    divorcio.sort(key=lambda d: (-d["juntos"]["jogos"],
                                 -abs(d["juntos"]["winrate"] - 50)))

    # ------------------------------------------------------------- a balanca
    bal_partidas = []
    for grupo in partidas:
        times: dict[int, dict[str, int]] = defaultdict(
            lambda: {"gold": 0, "kills": 0, "dmg": 0, "win": 0, "gente": []})
        for l in grupo:
            t = times[l["time"]]
            t["gold"] += l["gold"]
            t["kills"] += l["kills"]
            t["dmg"] += l["dmg"]
            t["win"] = l["win"]
            t["gente"].append(l)
        if len(times) != 2:
            continue
        (ta, tb) = list(times.values())
        vence, perde = (ta, tb) if ta["win"] else (tb, ta)
        soma = vence["gold"] + perde["gold"]
        diff = _div(vence["gold"] - perde["gold"], _div(soma, 2.0)) * 100
        base = grupo[0]
        bal_partidas.append({
            "matchId": base["match"],
            "quando": base["quando"],
            "minutos": round(base["dur"] / 60),
            "modo": QUEUE_LABEL.get(base["fila"], base["modo"] or "?"),
            "diff": _r(diff, 1),
            "apertada": abs(diff) < 5,
            "virada": vence["kills"] < perde["kills"],
            "ouroAtras": vence["gold"] < perde["gold"],
            "danoAtras": vence["dmg"] < perde["dmg"],
            "abates": [vence["kills"], perde["kills"]],
            "ouro": [vence["gold"], perde["gold"]],
            "vencedores": [_pessoa(l["chave"], nomes)["nome"]
                           for l in sorted(vence["gente"], key=lambda l: -l["kills"])],
            "perdedores": [_pessoa(l["chave"], nomes)["nome"]
                           for l in sorted(perde["gente"], key=lambda l: -l["kills"])],
        })
    bal_partidas.sort(key=lambda d: -d["quando"])
    diffs = sorted(abs(p["diff"]) for p in bal_partidas)
    mediana = 0.0
    if diffs:
        meio = len(diffs) // 2
        mediana = diffs[meio] if len(diffs) % 2 else (diffs[meio - 1] + diffs[meio]) / 2
    balanca = {
        "partidas": bal_partidas,
        "mediana": _r(mediana, 1),
        "apertadas": sum(1 for p in bal_partidas if p["apertada"]),
        "viradas": sum(1 for p in bal_partidas if p["virada"]),
        "placarMente": sum(1 for p in bal_partidas
                           if p["virada"] or p["ouroAtras"] or p["danoAtras"]),
        "faixa": 5,
    }

    # -------------------------------------------------------------- o espelho
    # A base do espelho ignora a janela de analise de proposito. Ela nao e um
    # periodo, e um retrato de como a pessoa joga ARAM quando o adversario e o
    # servidor -- e dentro de 90 dias so UM jogador do grupo chega a 5 ARAM
    # publicas. Cortar pela janela apagaria a comparacao inteira para manter uma
    # coerencia que aqui nao significa nada.
    base_cru = list(conn.execute(
        """SELECT p.puuid, p.win, p.kills, p.deaths, p.assists, p.team_kills,
                  p.damage_champions, p.game_duration
             FROM participants p
            WHERE p.queue_id = ?
              AND p.tracked = 1
              AND p.game_duration >= ?
              AND p.early_surrender = 0""",
        (FILA_BASE, MIN_DUR),
    ))
    base_por_puuid: dict[str, list[dict]] = defaultdict(list)
    for r in base_cru:
        base_por_puuid[r["puuid"]].append({
            "win": 1 if r["win"] else 0, "kills": r["kills"], "deaths": r["deaths"],
            "assists": r["assists"], "team_kills": r["team_kills"] or 0,
            "dmg": r["damage_champions"] or 0, "dur": r["game_duration"],
        })
    ih_por_puuid: dict[str, list[dict]] = defaultdict(list)
    for l in linhas:
        puuid = (nomes.get(l["chave"]) or {}).get("puuid")
        if puuid in players:
            ih_por_puuid[puuid].append(l)

    espelho_jog = []
    for puuid, ih in ih_por_puuid.items():
        base = base_por_puuid.get(puuid) or []
        if len(base) < MIN_BASE or len(ih) < MIN_IH:
            continue
        espelho_jog.append({
            "nome": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "puuid": puuid,
            "base": _resumo_lado(base),
            "inhouse": _resumo_lado(ih),
        })
    espelho = {
        "jogadores": sorted(espelho_jog, key=lambda d: -d["inhouse"]["games"]),
        "minBase": MIN_BASE, "minIh": MIN_IH,
        "metricas": [
            {"k": "kda", "t": "KDA", "u": "", "d": 2, "dir": 1},
            {"k": "dmg_min", "t": "Dano/min", "u": "", "d": 0, "dir": 1},
            {"k": "kp", "t": "Particip.", "u": "%", "d": 1, "dir": 1},
            {"k": "deaths", "t": "Mortes", "u": "", "d": 2, "dir": -1},
        ],
    }

    # -------------------------------------------------------------- a chamada
    blocos = _noites([g[0]["quando"] for g in partidas])
    indice_noite: dict[int, int] = {}
    for n, bloco in enumerate(blocos):
        for ts in bloco:
            indice_noite[ts] = n
    noites = [{"rotulo": _rot_noite(b[0]), "partidas": len(b), "quando": b[0]}
              for b in blocos]

    presenca: dict[str, list[int]] = {k: [0] * len(blocos) for k in ordem}
    for grupo in partidas:
        n = indice_noite.get(grupo[0]["quando"], 0)
        for chave in {l["chave"] for l in grupo}:
            presenca[chave][n] += 1

    nunca = []
    presentes = {(nomes[k] or {}).get("puuid") for k in ordem}
    for puuid, jogador in players.items():
        if puuid not in presentes:
            nunca.append({"nome": jogador["gameName"], "icon": jogador["icon"]})
    nunca.sort(key=lambda d: d["nome"].lower())

    chamada = {
        "noites": noites,
        "pessoas": [{**_pessoa(k, nomes), "total": jogos_por_chave[k],
                     "porNoite": presenca[k]} for k in ordem],
        "nunca": nunca,
    }

    # ------------------------------------------------- as placas de duelo
    duelos = []
    for grupo in sorted(partidas, key=lambda g: -g[0]["quando"])[:24]:
        times: dict[int, list[dict]] = defaultdict(list)
        for l in grupo:
            times[l["time"]].append(l)
        duelos.append({
            "matchId": grupo[0]["match"],
            "quando": grupo[0]["quando"],
            "minutos": round(grupo[0]["dur"] / 60),
            "modo": QUEUE_LABEL.get(grupo[0]["fila"], grupo[0]["modo"] or "?"),
            "times": [
                {
                    "teamId": tid,
                    "venceu": bool(membros[0]["win"]),
                    "abates": sum(m["kills"] for m in membros),
                    "ouro": sum(m["gold"] for m in membros),
                    "dano": sum(m["dmg"] for m in membros),
                    "jogadores": [
                        {**_pessoa(m["chave"], nomes), "champion": m["champ"],
                         "championId": m["champId"], "k": m["kills"],
                         "d": m["deaths"], "a": m["assists"], "dmg": m["dmg"]}
                        for m in sorted(membros, key=lambda m: -m["kills"])
                    ],
                }
                for tid, membros in sorted(times.items(),
                                           key=lambda kv: -kv[1][0]["win"])
            ],
        })

    # ------------------------------------------------------------- cabecalho
    filas: dict[int, set] = defaultdict(set)
    for l in linhas:
        filas[l["fila"]].add(l["match"])
    total_partidas = len(partidas)
    modos_agr: dict[str, set] = defaultdict(set)
    for fila, ms in filas.items():
        modos_agr[QUEUE_LABEL.get(fila, f"Fila {fila}")] |= ms
    modos = sorted(
        ({"modo": nome, "partidas": len(ms),
          "share": _r(_div(len(ms), total_partidas) * 100, 1)}
         for nome, ms in modos_agr.items()),
        key=lambda d: -d["partidas"])

    remakes = 0
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='inhouse_remakes'").fetchone()
        remakes = int(row[0]) if row else 0
    except (sqlite3.Error, ValueError):
        remakes = 0

    return {
        "partidas": total_partidas,
        "noites": len(blocos),
        "jogadores": len(ordem),
        "convidados": sum(1 for k in ordem if nomes[k]["convidado"]),
        "minutos": round(sum(g[0]["dur"] for g in partidas) / 60),
        "descartadas": {"remake": remakes},
        "modos": modos,
        "tabela": tabela,
        "nemesis": nemesis,
        "divorcio": divorcio,
        "balanca": balanca,
        "espelho": espelho,
        "chamada": chamada,
        "duelos": duelos,
        "pisos": {"duelo": MIN_DUEL, "dupla": MIN_PAIR,
                  "base": MIN_BASE, "inhouse": MIN_IH},
    }

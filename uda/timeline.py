"""Timeline da Match-V5: o minuto a minuto que o resumo da partida nao conta.

O endpoint /matches/{id}/timeline devolve um quadro por minuto com ouro, XP,
CS e POSICAO de cada um dos dez, mais a lista completa de eventos -- cada abate
com a coordenada no mapa, cada item na ordem em que foi comprado, cada ponto de
habilidade na ordem em que foi gasto.

E o dado mais caro do projeto: 963 KB crus por partida, 82 KB comprimidos. Mil e
setecentas partidas dariam 139 MB, que atravessariam o cache do GitHub Actions a
cada duas horas. Por isso este modulo NAO guarda a timeline: extrai o que vira
tela e joga o resto fora. O extrato fica em ~400 bytes por participante da UDA.

A contrapartida e assumida: campo novo daqui exige rebuscar na API, ao contrario
de matches.raw. Em troca o banco continua cabendo no cache.
"""

from __future__ import annotations

import json
import sqlite3

# Coordenadas do Rift vao de ~0 a ~14870 nos dois eixos. Howling Abyss (ARAM) usa
# outra escala, entao mapa de calor so pode misturar partidas do MESMO mapa.
RIFT_MAX = 14870
MARCOS = (10, 15, 20)          # minutos em que a curva e fotografada
MAX_ITENS = 10                 # ordem de compra: o suficiente para ver a build
MAX_SKILLS = 9                 # 9 pontos ja revelam qual habilidade foi maximizada
CONSUMIVEIS = {2003, 2031, 2033, 2055, 2138, 2139, 2140, 3363, 3364, 3340, 3330}


def _min(ts) -> int:
    return int((ts or 0) // 60000)


def extrair(timeline: dict, alvos: set[str]) -> list[dict]:
    """Timeline crua -> uma linha compacta por participante da UDA.

    `alvos` sao os puuids que interessam. Os outros nove jogadores so entram
    como autores de abate sobre os nossos, nunca como linha propria.
    """
    meta = timeline.get("metadata") or {}
    info = timeline.get("info") or {}
    puuids = meta.get("participants") or []
    if not puuids:
        return []
    match_id = meta.get("matchId") or ""

    # participantId e 1-based e seguem a ordem de metadata.participants.
    nossos = {i + 1: p for i, p in enumerate(puuids) if p in alvos}
    if not nossos:
        return []

    dados = {pid: {"mortes": [], "abates": [], "itens": [], "skills": [],
                   "wards": 0, "primeira_morte": None, "primeiro_abate": None,
                   "marcos": {}, "assist": 0}
             for pid in nossos}

    for frame in info.get("frames") or []:
        # --- fotos da curva nos marcos
        minuto = _min(frame.get("timestamp"))
        if minuto in MARCOS:
            pf = frame.get("participantFrames") or {}
            for pid in nossos:
                q = pf.get(str(pid)) or {}
                if q:
                    dados[pid]["marcos"][minuto] = {
                        "ouro": int(q.get("totalGold") or 0),
                        "xp": int(q.get("xp") or 0),
                        "cs": int(q.get("minionsKilled") or 0)
                             + int(q.get("jungleMinionsKilled") or 0),
                    }
        # --- eventos
        for ev in frame.get("events") or []:
            tipo = ev.get("type")
            ts = ev.get("timestamp") or 0
            if tipo == "CHAMPION_KILL":
                pos = ev.get("position") or {}
                xy = [int(pos.get("x", 0)), int(pos.get("y", 0)), _min(ts)]
                vit, mat = ev.get("victimId"), ev.get("killerId")
                if vit in dados:
                    dados[vit]["mortes"].append(xy)
                    if dados[vit]["primeira_morte"] is None:
                        dados[vit]["primeira_morte"] = ts
                if mat in dados:
                    dados[mat]["abates"].append(xy)
                    if dados[mat]["primeiro_abate"] is None:
                        dados[mat]["primeiro_abate"] = ts
                for a in ev.get("assistingParticipantIds") or []:
                    if a in dados:
                        dados[a]["assist"] += 1
            elif tipo == "ITEM_PURCHASED":
                pid, item = ev.get("participantId"), int(ev.get("itemId") or 0)
                # Consumivel e poro-snack nao contam build: sao ruido de recall.
                if pid in dados and item and item not in CONSUMIVEIS \
                        and len(dados[pid]["itens"]) < MAX_ITENS:
                    dados[pid]["itens"].append(item)
            elif tipo == "SKILL_LEVEL_UP":
                pid, slot = ev.get("participantId"), int(ev.get("skillSlot") or 0)
                if pid in dados and slot and len(dados[pid]["skills"]) < MAX_SKILLS:
                    dados[pid]["skills"].append(slot)
            elif tipo == "WARD_PLACED":
                pid = ev.get("creatorId")
                if pid in dados:
                    dados[pid]["wards"] += 1

    saida = []
    for pid, puuid in nossos.items():
        d = dados[pid]
        linha = {"match_id": match_id, "puuid": puuid,
                 "primeira_morte": d["primeira_morte"],
                 "primeiro_abate": d["primeiro_abate"],
                 "wards_tl": d["wards"], "assists_tl": d["assist"],
                 "mortes_json": json.dumps(d["mortes"], separators=(",", ":")),
                 "abates_json": json.dumps(d["abates"], separators=(",", ":")),
                 "itens_json": json.dumps(d["itens"], separators=(",", ":")),
                 "skills_json": json.dumps(d["skills"], separators=(",", ":"))}
        for m in MARCOS:
            v = d["marcos"].get(m) or {}
            linha[f"ouro{m}"] = v.get("ouro", 0)
            linha[f"xp{m}"] = v.get("xp", 0)
            linha[f"cs{m}"] = v.get("cs", 0)
        saida.append(linha)
    return saida


def gravar(conn: sqlite3.Connection, linhas: list[dict]) -> None:
    if not linhas:
        return
    campos = list(linhas[0].keys())
    sql = (f"INSERT OR REPLACE INTO timeline_stats ({', '.join(campos)}) "
           f"VALUES ({', '.join(':' + c for c in campos)})")
    conn.executemany(sql, linhas)


# ------------------------------------------------------------------ coleta

def pendentes(conn: sqlite3.Connection, limite: int) -> list[str]:
    """Partidas sem timeline, das mais novas para as mais velhas.

    Da recente para a antiga de proposito: se a coleta nunca terminar, o que
    existir e o periodo que as pessoas lembram de ter jogado.
    """
    return [r[0] for r in conn.execute(
        "SELECT match_id FROM matches WHERE COALESCE(tl_done, 0) = 0 "
        "ORDER BY game_creation DESC LIMIT ?", (limite,))]


def coletar(client, conn: sqlite3.Connection, tracked: set[str],
            limite: int = 250, verbose: bool = True) -> int:
    """Busca timelines pendentes, com TETO por execucao.

    Sao 1700 partidas e uma chamada cada: sem teto, a primeira execucao gastaria
    ~35 min de cota so aqui e ainda tomaria a janela do resto. Com teto a coleta
    converge em algumas rodadas e nenhuma execucao isolada estoura.
    """
    ids = pendentes(conn, limite)
    if not ids:
        return 0
    restam = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE COALESCE(tl_done, 0) = 0").fetchone()[0]
    if verbose:
        print(f"\n[timeline] {len(ids)} de {restam} pendentes nesta rodada "
              f"(~{len(ids) * 1.25 / 60:.0f} min)")

    feitas = 0
    for i, mid in enumerate(ids, 1):
        try:
            tl = client._get(f"{client.routing}.api.riotgames.com",
                             f"/lol/match/v5/matches/{mid}/timeline",
                             allow_404=True)
        except Exception as exc:                       # rede, 5xx apos retries
            if verbose:
                print(f"  ! {mid}: {type(exc).__name__} -- fica pendente")
            continue
        if not tl:
            # 404: partida antiga demais. Marca 2 para nao tentar de novo nunca.
            conn.execute("UPDATE matches SET tl_done=2 WHERE match_id=?", (mid,))
            continue
        linhas = extrair(tl, tracked)
        gravar(conn, linhas)
        conn.execute("UPDATE matches SET tl_done=1 WHERE match_id=?", (mid,))
        feitas += 1
        if i % 25 == 0:
            conn.commit()
            if verbose:
                print(f"  {i}/{len(ids)}  ({feitas} com dado da UDA)")
    conn.commit()
    if verbose:
        print(f"  timeline: {feitas} partidas extraidas")
    return feitas

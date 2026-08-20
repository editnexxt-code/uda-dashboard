"""As piores partidas de cada um, com o porque explicado.

O painel inteiro existe para zoar, e zoacao sem prova nao tem graca. Aqui cada
vexame vem com os motivos concretos: quantas mortes, quanto tempo morto, quanto
de CS a menos que o oponente de rota, quanto do time ele participou. Tudo medido,
nada inventado -- a piada e melhor quando o numero esta do lado.

De proposito NAO existe lista de partidas boas. O site nao e curriculo.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .kpi import QUEUE_NAMES, _r, _safe_div

PIORES_POR_JOGADOR = 12
JOGO_VALIDO = 600          # abaixo de 10 min nao da tempo de ser ruim de verdade


def _n(valor) -> float:
    return float(valor) if valor is not None else 0.0


# ------------------------------------------------------------------- motivos
# Cada motivo: (chave, peso, teste(ctx) -> bool, frase(ctx) -> str)
# ctx traz a linha, a media do proprio jogador e o contexto da partida.

def _motivos(ctx) -> list[dict]:
    r, med, par = ctx["row"], ctx["media"], ctx["partida"]
    minutos = max(_n(r["game_duration"]) / 60.0, 1)
    mortes = _n(r["deaths"])
    saida = []

    def add(chave, peso, texto, valor=None):
        saida.append({"chave": chave, "peso": peso, "texto": texto, "valor": valor})

    # --- morte
    if mortes >= 10:
        add("mortes", 3, f"Morreu {int(mortes)} vezes numa partida de "
                         f"{int(minutos)} minutos. Uma a cada "
                         f"{_pt(minutos / mortes)} min.", int(mortes))
    elif mortes >= max(6, med["deaths"] * 1.6):
        add("mortes", 2, f"{int(mortes)} mortes, contra as "
                         f"{_pt(med['deaths'])} que ele costuma dar de presente.",
            int(mortes))

    if par["piorKda"] and mortes >= 5:
        add("piorDaPartida", 3, "Foi o pior KDA dos dez jogadores. Dos DEZ.")

    # --- tempo morto
    morto = _n(r["time_dead"])
    jogado = _n(r["time_played"]) or _n(r["game_duration"])
    if jogado and morto / jogado >= 0.22:
        add("telaCinza", 2,
            f"Ficou {int(morto / 60)} minutos morto — "
            f"{round(morto / jogado * 100)}% da partida assistindo.",
            f"{round(morto / jogado * 100)}%")

    # --- participacao
    kp = _n(r["kp"]) * 100
    if kp and kp <= 35 and _n(r["team_kills"]) >= 12:
        add("ausente", 2, f"Participou de {round(kp)}% dos abates. "
                          "O time brigou sozinho.", f"{round(kp)}%")

    # --- dano
    dpm = _n(r["dpm"])
    if dpm and dpm <= med["dpm"] * 0.6:
        add("semDano", 2, f"{int(dpm)} de dano por minuto, contra os "
                          f"{int(med['dpm'])} de sempre. Estava lá de corpo presente.",
            int(dpm))
    if par["menorDano"] and minutos >= 20:
        add("menorDano", 2, "Deu o menor dano da partida inteira.")

    # --- farm
    cs_diff = r["cs_diff"] if r["cs_diff"] is not None else 0
    if cs_diff <= -40:
        add("farm", 2, f"Levou {abs(int(cs_diff))} de CS a menos que o oponente "
                       "de rota. Uma vila inteira.", int(cs_diff))

    # --- carteira
    sobrou = _n(r["gold"]) - _n(r["gold_spent"])
    if sobrou >= 2000:
        add("carteira", 1, f"Acabou com {int(sobrou)} de ouro no bolso. "
                           "Item não se compra sozinho.", int(sobrou))

    # --- apanhou
    dado, levado = _n(r["damage_champions"]), _n(r["damage_taken"])
    if dado and levado / max(dado, 1) >= 2.5 and minutos >= 20:
        add("apanhou", 1, f"Levou {_pt(levado / max(dado, 1))}x mais dano do que "
                          "causou. Serviu de saco de pancada.",
            f"{_pt(levado / max(dado, 1))}x")

    # --- vergonhas categoricas
    if r["was_afk"]:
        add("afk", 3, "Simplesmente sumiu no meio da partida.")
    if r["had_open_nexus"]:
        add("nexus", 2, "Terminou com o nexus escancarado.")
    if _n(r["max_kill_deficit"]) >= 15:
        add("deficit", 1, f"Chegou a estar {int(_n(r['max_kill_deficit']))} abates "
                          "atrás no placar.", int(_n(r["max_kill_deficit"])))
    if _n(r["ping_mia"]) >= 8:
        add("ping", 1, f"Deu {int(_n(r['ping_mia']))} pings de interrogação. "
                       "A culpa, claro, era dos outros.", int(_n(r["ping_mia"])))
    if r["surrender"] and not r["win"]:
        add("rendeu", 1, "Terminou em rendição. Nem deu o troco.")

    saida.sort(key=lambda m: -m["peso"])
    return saida


def _pt(valor, casas: int = 1) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


# --------------------------------------------------------------- veredito

# O veredito sai do MOTIVO dominante, nao so da nota. Calibrado pela distribuicao
# real do banco (mediana 82, p90 101, maximo 132): cortes em 90 deixavam quase
# todo mundo com a mesma frase, que e o oposto de zoar.
VEREDITOS = {
    "mortes": [(115, "Isso não foi partida, foi um serviço de entrega de ouro."),
               (95, "O time inimigo passou a partida agradecendo."),
               (0, "Morreu tanto que decorou o caminho de volta.")],
    "piorDaPartida": [(100, "O pior dos dez, e com folga. Um feito."),
                      (0, "Dos dez jogadores em campo, foi o pior. Dos dez.")],
    "semDano": [(0, "Estava lá. Fisicamente, pelo menos.")],
    "ausente": [(0, "O time jogou de quatro a partida inteira.")],
    "telaCinza": [(0, "Assistiu mais partida do que jogou.")],
    "farm": [(0, "O oponente de rota farmou por dois.")],
    "afk": [(0, "Sumiu. Sem explicação, sem aviso, sem volta.")],
    "apanhou": [(0, "Serviu de boneco de treino com barra de vida.")],
    "carteira": [(0, "Morreu rico. De ouro no bolso, não de item.")],
    "ping": [(0, "Passou a partida achando culpado. Nunca no espelho.")],
    "rendeu": [(0, "Nem deu o troco. Rendeu e foi dormir.")],
    "nexus": [(0, "Nexus aberto. Dá pra ser pior? Dificilmente.")],
    "deficit": [(0, "Ficou tão atrás no placar que dava pra ver o buraco.")],
}
VEREDITO_PADRAO = [(100, "Um vexame com hora marcada."),
                   (80, "Daqueles jogos que a gente finge que não aconteceu."),
                   (0, "Dia ruim. Acontece — só que ficou registrado.")]


def _veredito(nota: float, motivos: list[dict]) -> str:
    escala = VEREDITOS.get(motivos[0]["chave"]) if motivos else None
    for corte, frase in (escala or VEREDITO_PADRAO):
        if nota >= corte:
            return frase
    return VEREDITO_PADRAO[-1][1]


# ------------------------------------------------------------------- calculo

def _nota_vergonha(ctx) -> float:
    """Quanto mais alto, pior foi. Composto para nao premiar so quem morre muito."""
    r, med = ctx["row"], ctx["media"]
    minutos = max(_n(r["game_duration"]) / 60.0, 1)
    mortes = _n(r["deaths"])
    kda = _safe_div(_n(r["kills"]) + _n(r["assists"]), max(mortes, 1))
    jogado = _n(r["time_played"]) or _n(r["game_duration"])

    nota = 0.0
    nota += min(mortes / max(med["deaths"], 1), 3.0) * 16      # morte relativa
    nota += min(mortes, 20) * 1.4                              # morte absoluta
    nota += max(0.0, 3.0 - kda) * 7                            # KDA baixo
    nota += _safe_div(_n(r["time_dead"]), jogado) * 40         # tela cinza
    nota += max(0.0, 1 - _safe_div(_n(r["dpm"]), max(med["dpm"], 1))) * 14
    nota += max(0.0, 0.5 - _n(r["kp"])) * 20                   # sumiu do mapa
    if ctx["partida"]["piorKda"]:
        nota += 12
    if ctx["partida"]["menorDano"]:
        nota += 6
    if r["was_afk"]:
        nota += 25
    if minutos < 18:
        nota *= 0.75            # partida curta perdoa um pouco
    return round(nota, 1)


def _contexto_partida(conn: sqlite3.Connection,
                      rows: list[sqlite3.Row]) -> dict[str, dict]:
    """Como o jogador se saiu perante os outros nove da MESMA partida."""
    ids = {r["match_id"] for r in rows}
    if not ids:
        return {}
    ctx: dict[str, dict] = {}
    marcadores = ",".join("?" * len(ids))
    todos: dict[str, list] = defaultdict(list)
    for linha in conn.execute(
            f"SELECT match_id, puuid, kills, deaths, assists, damage_champions "
            f"FROM participants WHERE match_id IN ({marcadores})", tuple(ids)):
        todos[linha["match_id"]].append(linha)

    for match_id, participantes in todos.items():
        pior_kda, menor_dano = None, None
        melhor = 1e9
        for p in participantes:
            k = _safe_div(_n(p["kills"]) + _n(p["assists"]), max(_n(p["deaths"]), 1))
            if k < melhor:
                melhor, pior_kda = k, p["puuid"]
        menor = min(participantes, key=lambda p: _n(p["damage_champions"]))
        menor_dano = menor["puuid"]
        ctx[match_id] = {"piorKdaPuuid": pior_kda, "menorDanoPuuid": menor_dano,
                         "n": len(participantes)}
    return ctx


def construir(conn: sqlite3.Connection, rows_by_player, players,
              limite: int = PIORES_POR_JOGADOR, verbose: bool = True) -> dict:
    saida = []
    todas = [r for linhas in rows_by_player.values() for r in linhas]
    ctx_partidas = _contexto_partida(conn, todas)

    for puuid, linhas in rows_by_player.items():
        if puuid not in players:
            continue
        validas = [r for r in linhas if _n(r["game_duration"]) >= JOGO_VALIDO]
        if len(validas) < 5:
            continue

        n = len(validas)
        media = {
            "deaths": sum(_n(r["deaths"]) for r in validas) / n,
            "dpm": sum(_n(r["dpm"]) for r in validas) / n,
            "kda": sum(_safe_div(_n(r["kills"]) + _n(r["assists"]),
                                 max(_n(r["deaths"]), 1)) for r in validas) / n,
        }

        avaliadas = []
        for r in validas:
            c = ctx_partidas.get(r["match_id"], {})
            ctx = {
                "row": r, "media": media,
                "partida": {
                    "piorKda": c.get("piorKdaPuuid") == puuid,
                    "menorDano": c.get("menorDanoPuuid") == puuid,
                },
            }
            nota = _nota_vergonha(ctx)
            avaliadas.append((nota, ctx, r))

        avaliadas.sort(key=lambda x: -x[0])
        piores = []
        for nota, ctx, r in avaliadas[:limite]:
            motivos = _motivos(ctx)
            if not motivos:
                continue
            piores.append({
                "matchId": r["match_id"],
                "date": r["game_creation"],
                "queue": QUEUE_NAMES.get(r["queue_id"], f"Fila {r['queue_id']}"),
                "minutes": round(_n(r["game_duration"]) / 60),
                "champion": r["champion_name"], "championId": r["champion_id"],
                "k": r["kills"], "d": r["deaths"], "a": r["assists"],
                "kda": _r(_safe_div(_n(r["kills"]) + _n(r["assists"]),
                                    max(_n(r["deaths"]), 1))),
                "win": bool(r["win"]),
                "nota": nota, "veredito": _veredito(nota, motivos),
                "motivos": motivos[:5],
                "piorDaPartida": ctx["partida"]["piorKda"],
            })

        if not piores:
            continue
        saida.append({
            "puuid": puuid, "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "analisadas": n,
            "piorNota": piores[0]["nota"],
            "mediaMortes": _r(media["deaths"]),
            "piores": piores,
        })

    saida.sort(key=lambda j: -j["piorNota"])
    if verbose:
        total = sum(len(j["piores"]) for j in saida)
        print(f"  vexames: {total} partidas vergonhosas de {len(saida)} jogadores")
    return {"jogadores": saida, "limite": limite}

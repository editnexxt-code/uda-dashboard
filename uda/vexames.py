"""As piores partidas de cada um, com o porque explicado.

O painel inteiro existe para zoar, e zoacao sem prova nao tem graca. Aqui cada
vexame vem com os motivos concretos: quantas mortes, quanto tempo morto, quanto
de CS a menos que o oponente de rota, quanto do time ele participou. Tudo medido,
nada inventado -- a piada e melhor quando o numero esta do lado.

De proposito NAO existe lista de partidas boas. O site nao e curriculo.
"""

from __future__ import annotations

import zlib

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
        add("mortes", 3, f"Morreu {int(mortes)} vezes em {int(minutos)} minutos. "
                         f"Uma a cada {_pt(minutos / mortes)} min — dava pra marcar no relógio.", int(mortes))
    elif mortes >= max(6, med["deaths"] * 1.6):
        add("mortes", 2, f"{int(mortes)} mortes. A média dele é {_pt(med['deaths'])} — "
                         f"hoje resolveu caprichar.",
            int(mortes))

    if par["piorKda"] and mortes >= 5:
        add("piorDaPartida", 3, "Pior KDA dos dez. Não do time: da partida inteira.")

    # --- tempo morto
    morto = _n(r["time_dead"])
    jogado = _n(r["time_played"]) or _n(r["game_duration"])
    if jogado and morto / jogado >= 0.22:
        add("telaCinza", 2,
            f"Passou {int(morto / 60)} minutos morto — "
            f"{round(morto / jogado * 100)}% do jogo encarando a tela cinza.",
            f"{round(morto / jogado * 100)}%")

    # --- participacao
    kp = _n(r["kp"]) * 100
    if kp and kp <= 35 and _n(r["team_kills"]) >= 12:
        add("ausente", 2, f"Participou de {round(kp)}% dos abates. "
                          "O time brigou sozinho e nem sentiu falta.", f"{round(kp)}%")

    # --- dano
    dpm = _n(r["dpm"])
    if dpm and dpm <= med["dpm"] * 0.6:
        add("semDano", 2, f"{int(dpm)} de dano por minuto, contra os {int(med['dpm'])} de sempre. "
                          "Presença confirmada; dano, não.",
            int(dpm))
    if par["menorDano"] and minutos >= 20:
        add("menorDano", 2, "Menor dano da partida. Dos dez, contando o suporte.")

    # --- farm
    cs_diff = r["cs_diff"] if r["cs_diff"] is not None else 0
    if cs_diff <= -40:
        add("farm", 2, f"Levou {abs(int(cs_diff))} de CS a menos que o oponente de rota. "
                       "Uma vila inteira de minion.", int(cs_diff))

    # --- carteira
    sobrou = _n(r["gold"]) - _n(r["gold_spent"])
    if sobrou >= 2000:
        add("carteira", 1, f"Terminou com {int(sobrou)} de ouro no bolso. "
                           "Item não se compra sozinho, e ele descobriu tarde.", int(sobrou))

    # --- apanhou
    dado, levado = _n(r["damage_champions"]), _n(r["damage_taken"])
    if dado and levado / max(dado, 1) >= 2.5 and minutos >= 20:
        add("apanhou", 1, f"Levou {_pt(levado / max(dado, 1))}x mais dano do que causou. "
                          "Boneco de treino oficial da partida.",
            f"{_pt(levado / max(dado, 1))}x")

    # --- vergonhas categoricas
    if r["was_afk"]:
        add("afk", 3, "Simplesmente sumiu no meio da partida. O time ficou de quatro.")
    if r["had_open_nexus"]:
        add("nexus", 2, "Terminou com o nexus escancarado. Nem a decência de perder rápido.")
    if _n(r["max_kill_deficit"]) >= 15:
        add("deficit", 1, f"Chegou a estar {int(_n(r['max_kill_deficit']))} abates atrás. "
                          "Isso não é placar, é dívida.", int(_n(r["max_kill_deficit"])))
    if _n(r["ping_mia"]) >= 8:
        add("ping", 1, f"Deu {int(_n(r['ping_mia']))} pings de interrogação. "
                       "A culpa, óbvio, era dos outros nove.", int(_n(r["ping_mia"])))
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
    # Faixa real das notas: 29 a 132, mediana 82, p90 100. Os cortes seguem a
    # distribuicao, nao numeros redondos escolhidos no olho.
    "mortes": [
        (120, ["Isso não foi partida, foi entrega em domicílio.",
               "O inimigo não precisou caçar: bastou esperar na fila do caixão.",
               "Serviço de entrega de ouro, com pontualidade britânica."]),
        (105, ["O outro time terminou o jogo agradecendo nominalmente.",
               "Alimentou tanto que devia constar na build do inimigo.",
               "Não foi derrota, foi doação em espécie."]),
        (90, ["Morreu tanto que a fonte já reconhece ele pelo nome.",
              "Passou mais tempo escolhendo respawn do que jogando.",
              "O caminho da base virou rota decorada."]),
        (75, ["Morreu além da conta, e a conta já era generosa.",
              "Cada briga tinha ele no chão antes do fim.",
              "Entregou de graça o que devia custar caro."]),
        (0, ["Morreu mais do que devia, e devia pouco.",
             "Não foi catástrofe, mas foi vexame.",
             "Dia de dar mais do que receber."]),
    ],
    "piorDaPartida": [
        (100, ["O pior dos dez. Não por pouco: com folga e com sobra.",
               "Dos dez em campo, o último. E olha que tinha concorrência."]),
        (0, ["Dos dez jogadores em campo, foi o pior. Dos DEZ.",
             "Último lugar entre dez. Nem o suporte inimigo ficou atrás."]),
    ],
    "semDano": [(0, ["Estava lá. Fisicamente. O dano ficou em casa.",
                     "Presença confirmada, contribuição não.",
                     "Bateu no minion e chamou de participação."])],
    "ausente": [(0, ["O time jogou de quatro o jogo inteiro e ninguém sentiu falta.",
                     "Sumiu do mapa sem sumir da partida.",
                     "Estava em outra briga. Sempre na outra."])],
    "telaCinza": [(0, ["Assistiu mais partida do que jogou. Faltou a pipoca.",
                       "Passou o jogo no cinza, esperando a próxima chance de morrer."])],
    "farm": [(0, ["O oponente de rota farmou por dois. Um deles era ele.",
                  "Deixou uma vila inteira de minion passar batido."])],
    "afk": [(0, ["Sumiu. Sem explicação, sem aviso, sem vergonha.",
                 "A internet levou a culpa, como sempre."])],
    "apanhou": [(0, ["Serviu de saco de pancada com barra de vida e nome em cima.",
                     "O outro time testou build nele e aprovou."])],
    "carteira": [(0, ["Morreu rico e morreu burro. O ouro foi enterrado junto.",
                      "Juntou a partida inteira pra não comprar nada."])],
    "ping": [(0, ["Passou a partida procurando culpado. Nunca no lugar certo.",
                  "Digitou mais interrogação do que deu dano."])],
    "rendeu": [(0, ["Rendeu e foi dormir. Nem o troco deu.",
                    "Votou sim antes de tentar não."])],
    "nexus": [(0, ["Nexus escancarado. Dá pra ser pior? Tecnicamente, não.",
                   "Perdeu com a porta aberta e a luz acesa."])],
    "deficit": [(0, ["Ficou tão atrás no placar que o buraco aparecia do espaço.",
                     "O placar virou dívida, e ninguém pagou."])],
    "menorDano": [(0, ["Menor dano da partida. Podia ter ficado na fonte, dava na mesma.",
                       "Dos dez, o que menos incomodou o inimigo."])],
}
VEREDITO_PADRAO = [
    (100, ["Vexame com hora marcada e dez testemunhas."]),
    (80, ["Daqueles jogos que a gente finge que nunca aconteceu. Só que ficou gravado."]),
    (0, ["Dia ruim. Acontece. Pena que tem print."]),
]


def _veredito(nota: float, motivos: list[dict], semente: str = "") -> str:
    """Frase do degrau certo, variante sorteada pelo ID da partida.

    O sorteio TEM que ser estavel entre execucoes: com hash() a mesma partida
    mudaria de frase a cada geracao, porque o Python embaralha o hash de string
    por processo. crc32 devolve sempre o mesmo numero para a mesma entrada.
    """
    escala = VEREDITOS.get(motivos[0]["chave"]) if motivos else None
    for corte, frases in (escala or VEREDITO_PADRAO):
        if nota >= corte:
            if not frases:
                break
            i = zlib.crc32(semente.encode("utf-8")) % len(frases) if semente else 0
            return frases[i]
    return VEREDITO_PADRAO[-1][1][0]


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
                "nota": nota,
                "veredito": _veredito(nota, motivos, r["match_id"]),
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

"""O elevador: quem subiu, quem desceu e quem ficou parado.

Aqui mora uma limitacao que vale explicar, porque ela decide o formato da aba.

A Riot NAO publica historico de elo. O endpoint de League-V4 devolve so a
posicao de agora, e a tabela `ranks` deste projeto sempre foi um retrato do
instante -- cada execucao apagava a anterior. Ou seja: no dia em que esta aba
nasceu nao existia, em lugar nenhum, o dado "onde fulano estava semana passada".

Duas respostas, e as duas entram:

1. O SALDO RANQUEADO, que existe desde sempre. Vitoria menos derrota nas filas
   420 e 440, contado das partidas que ja estao no banco. Nao e PDL, mas e
   exatamente o que MOVE o PDL: quem fecha o mes em -16 desceu, e quem fecha em
   +19 subiu. Isso da para dizer hoje, com numero medido.

2. O MOVIMENTO REAL DE PDL, que passa a existir a partir de agora. O
   `replace_ranks` carimba uma foto por dia em `rank_history`, entao em alguns
   dias esta aba mostra a subida e a queda de verdade, em divisao e PDL.

Enquanto o historico nao tem dois dias, a secao de PDL diz que esta anotando em
vez de inventar uma estimativa. Fabricar trajetoria a partir de "supondo 20 PDL
por vitoria" seria dar cara de medicao para um chute.
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict

from .kpi import _r, _safe_div

FILAS_RANQUEADAS = (420, 440)          # solo/duo e flex
FILA_NOME = {420: "Solo/Duo", 440: "Flex"}
QUEUE_TYPE = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}

# Minimo de partidas ranqueadas para entrar em qualquer um dos tres podios.
# Sem isso, "quem mais se manteve" seria ganho por quem nao jogou: zero partida
# da saldo zero, que e o saldo mais estavel possivel e o menos merecido.
MIN_RANQUEADAS = 20

ORDEM_TIER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
              "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
TIER_PT = {"IRON": "Ferro", "BRONZE": "Bronze", "SILVER": "Prata", "GOLD": "Ouro",
           "PLATINUM": "Platina", "EMERALD": "Esmeralda", "DIAMOND": "Diamante",
           "MASTER": "Mestre", "GRANDMASTER": "Grão-Mestre",
           "CHALLENGER": "Desafiante"}
ORDEM_DIV = {"IV": 0, "III": 1, "II": 2, "I": 3}


def valor_elo(tier: str | None, division: str | None, lp: int | None) -> int | None:
    """Elo achatado num numero so, para poder subtrair duas posicoes.

    Cada divisao vale 100 e cada tier 400, entao Ouro I 90 PDL fica logo abaixo
    de Platina IV 0 -- a mesma ordem da tela de ranqueada.
    """
    if not tier or tier.upper() not in ORDEM_TIER:
        return None
    t = ORDEM_TIER.index(tier.upper())
    d = ORDEM_DIV.get((division or "I").upper(), 3)
    return t * 400 + d * 100 + int(lp or 0)


def texto_elo(tier: str | None, division: str | None, lp: int | None) -> str:
    if not tier:
        return "sem elo"
    nome = TIER_PT.get(tier.upper(), tier.title())
    # Mestre para cima nao tem divisao.
    if tier.upper() in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return f"{nome} {int(lp or 0)} PDL"
    return f"{nome} {division or ''} {int(lp or 0)} PDL".replace("  ", " ").strip()


def _historico(conn: sqlite3.Connection, players: dict) -> dict:
    """Movimento real de PDL, se ja houver pelo menos dois dias anotados."""
    linhas = list(conn.execute(
        "SELECT puuid, queue_type, dia, tier, division, lp FROM rank_history "
        "ORDER BY dia"))
    dias = sorted({r["dia"] for r in linhas})
    if len(dias) < 2:
        return {"dias": len(dias), "desde": dias[0] if dias else None,
                "movimento": [], "pronto": False}

    por = defaultdict(list)
    for r in linhas:
        if r["puuid"] in players:
            por[(r["puuid"], r["queue_type"])].append(r)

    mov = []
    for (puuid, qt), ls in por.items():
        if len(ls) < 2:
            continue
        ini, fim = ls[0], ls[-1]
        a = valor_elo(ini["tier"], ini["division"], ini["lp"])
        b = valor_elo(fim["tier"], fim["division"], fim["lp"])
        if a is None or b is None:
            continue
        mov.append({
            "puuid": puuid, "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "fila": FILA_NOME.get(QUEUE_TYPE.get(qt, 0), qt),
            "de": texto_elo(ini["tier"], ini["division"], ini["lp"]),
            "para": texto_elo(fim["tier"], fim["division"], fim["lp"]),
            "delta": b - a, "dias": len(ls),
        })
    mov.sort(key=lambda x: -x["delta"])
    return {"dias": len(dias), "desde": dias[0], "movimento": mov, "pronto": True}


def construir(conn: sqlite3.Connection, players: dict) -> dict:
    # --- saldo ranqueado, das partidas que ja estao no banco ----------------
    acc = defaultdict(lambda: defaultdict(int))
    for r in conn.execute(
            "SELECT puuid, queue_id, win FROM participants "
            "WHERE tracked = 1 AND queue_id IN (?, ?)", FILAS_RANQUEADAS):
        if r["puuid"] not in players:
            continue
        a = acc[r["puuid"]]
        a["jogos"] += 1
        a["vitorias"] += 1 if r["win"] else 0

    atuais = {}
    for r in conn.execute("SELECT * FROM ranks"):
        atuais.setdefault(r["puuid"], {})[r["queue_type"]] = r

    fichas = []
    for puuid, a in acc.items():
        jogos, vit = a["jogos"], a["vitorias"]
        der = jogos - vit
        solo = (atuais.get(puuid) or {}).get("RANKED_SOLO_5x5")
        flex = (atuais.get(puuid) or {}).get("RANKED_FLEX_SR")
        fichas.append({
            "puuid": puuid, "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "jogos": jogos, "vitorias": vit, "derrotas": der,
            "saldo": vit - der,
            "winrate": _r(_safe_div(vit, jogos) * 100, 1),
            "eloSolo": texto_elo(solo["tier"], solo["division"], solo["lp"]) if solo else None,
            "eloFlex": texto_elo(flex["tier"], flex["division"], flex["lp"]) if flex else None,
            "valorSolo": valor_elo(solo["tier"], solo["division"], solo["lp"]) if solo else None,
            "elegivel": jogos >= MIN_RANQUEADAS,
        })

    aptos = [f for f in fichas if f["elegivel"]]
    subiu = sorted(aptos, key=lambda f: -f["saldo"])[:3]
    caiu = sorted(aptos, key=lambda f: f["saldo"])[:3]
    # "Se manteve" e o saldo mais perto de ZERO -- jogou bastante e terminou
    # exatamente onde comecou. O piso de partidas ja exclui quem so ficou
    # parado por nao ter jogado.
    parado = sorted(aptos, key=lambda f: (abs(f["saldo"]), -f["jogos"]))[:3]

    fichas.sort(key=lambda f: -f["saldo"])
    return {
        "fichas": fichas,
        "subiu": subiu, "caiu": caiu, "parado": parado,
        "minJogos": MIN_RANQUEADAS,
        "historico": _historico(conn, players),
        "geradoEm": time.strftime("%Y-%m-%d"),
    }

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

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

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
    """Movimento real de PDL, ponto a ponto.

    Cada linha de rank_history e uma MUDANCA de posicao, nao uma leitura
    periodica -- o `replace_ranks` descarta a leitura identica a anterior. Entao
    a serie ja vem sem pontos repetidos, e o intervalo entre dois pontos e o
    tempo que levou para o PDL mexer.

    `wins`/`losses` vem da League-V4 e sao os totais da temporada, entao a
    diferenca entre dois pontos diz exatamente quantas partidas ranqueadas
    aconteceram naquele trecho -- inclusive as que o painel nao baixou.
    """
    linhas = list(conn.execute(
        "SELECT puuid, queue_type, tier, division, lp, wins, losses, updated_at "
        "  FROM rank_history ORDER BY updated_at"))
    if not linhas:
        return {"pontos": 0, "desde": None, "movimento": [], "series": [],
                "pronto": False}

    por = defaultdict(list)
    for r in linhas:
        if r["puuid"] in players:
            por[(r["puuid"], r["queue_type"])].append(r)

    mov, series = [], []
    for (puuid, qt), ls in por.items():
        if len(ls) < 2:
            continue
        pontos = []
        for r in ls:
            v = valor_elo(r["tier"], r["division"], r["lp"])
            if v is None:
                continue
            pontos.append({"t": int(r["updated_at"]) * 1000, "v": v,
                           "txt": texto_elo(r["tier"], r["division"], r["lp"]),
                           "j": int((r["wins"] or 0) + (r["losses"] or 0))})
        if len(pontos) < 2:
            continue
        ini, fim = pontos[0], pontos[-1]
        ficha = {
            "puuid": puuid, "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "fila": FILA_NOME.get(QUEUE_TYPE.get(qt, 0), qt),
            "de": ini["txt"], "para": fim["txt"],
            "delta": fim["v"] - ini["v"],
            "partidas": max(0, fim["j"] - ini["j"]),
            "pontos": len(pontos),
        }
        mov.append(ficha)
        series.append({**ficha, "serie": pontos})
    mov.sort(key=lambda x: -x["delta"])
    return {"pontos": len(linhas), "desde": int(linhas[0]["updated_at"]) * 1000,
            "movimento": mov, "series": series, "pronto": bool(mov)}


def _pt_para_tier(txt: str) -> tuple[str | None, str | None]:
    """'PLATINA II' -> ('PLATINUM', 'II'). Aceita o nome em portugues."""
    if not txt:
        return None, None
    partes = txt.strip().upper().replace("-", " ").split()
    inverso = {v.upper().replace("Ã", "A").replace("Ç", "C").replace("-", " "): k
               for k, v in TIER_PT.items()}
    inverso["GRAO MESTRE"] = "GRANDMASTER"
    tier = div = None
    for i in range(len(partes), 0, -1):
        chave = " ".join(partes[:i])
        if chave in inverso:
            tier = inverso[chave]
            resto = partes[i:]
            div = resto[0] if resto and resto[0] in ORDEM_DIV else None
            break
    return tier, div


def _informado(players: dict) -> list[dict]:
    """Marcos digitados a mao em elos.json.

    Existe porque a Riot nao devolve temporada antiga: o Match-V5 so guarda
    partida recente e a League-V4 so responde o agora. A queda que o grupo
    lembra e real, mas nao esta em nenhuma API -- entao ou o grupo digita, ou
    o painel finge que nao aconteceu. Fica marcado como INFORMADO na tela, para
    ninguem confundir com o que foi medido.
    """
    caminho = Path(__file__).resolve().parent.parent / "elos.json"
    if not caminho.exists():
        return []
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    por_riot = {}
    for puuid, info in players.items():
        nome = info.get("gameName", "")
        tag = info.get("tagLine") or ""
        por_riot[f"{nome}#{tag}".lower()] = (puuid, info)
        por_riot[nome.lower()] = (puuid, info)

    saida = []
    for riot_id, marcos in (bruto.get("jogadores") or {}).items():
        alvo = por_riot.get(riot_id.lower()) or por_riot.get(riot_id.split("#")[0].lower())
        if not alvo:
            continue
        puuid, info = alvo
        for m in marcos or []:
            t_pico, d_pico = _pt_para_tier(m.get("pico", ""))
            t_fim, d_fim = _pt_para_tier(m.get("fim", ""))
            v_pico = valor_elo(t_pico, d_pico, 0)
            v_fim = valor_elo(t_fim, d_fim, 0)
            if v_pico is None:
                continue
            # `de` e `para` sao dicionarios com a chave "tier" de proposito: o
            # coletor de assets varre o payload atras dessa chave exata para
            # decidir quais emblemas embutir. Sem isso, um marco que cita MESTRE
            # -- faixa que ninguem do elenco tem hoje -- pediria a imagem na
            # internet e o painel deixaria de funcionar offline.
            queda = (v_fim - v_pico) if v_fim is not None else None
            saida.append({
                "puuid": puuid, "gameName": info["gameName"], "icon": info["icon"],
                "temporada": m.get("temporada", ""),
                "de": {"tier": (t_pico or "").lower(),
                       "txt": texto_elo(t_pico, d_pico, 0).replace(" 0 PDL", "")},
                "para": ({"tier": (t_fim or "").lower(),
                          "txt": texto_elo(t_fim, d_fim, 0).replace(" 0 PDL", "")}
                         if v_fim is not None else None),
                "queda": queda,
                # Cada divisao vale 100 na escala achatada, entao a conta vira a
                # unidade que todo mundo usa para medir tombo: divisoes.
                "divisoes": abs(queda) // 100 if queda is not None else None,
                "nota": m.get("nota", ""),
            })
    # A maior queda primeiro: e o que a aba existe para mostrar.
    saida.sort(key=lambda x: (x["queda"] if x["queda"] is not None else 0))
    return saida


def _curva(conn: sqlite3.Connection, players: dict) -> list[dict]:
    """Saldo ranqueado acumulado ao longo do tempo, medido das partidas.

    Nao e PDL, e nao alcanca temporada antiga -- o Match-V5 nao guarda tanto.
    Mas onde alcanca, mostra o FORMATO do tombo com data: luizin1v9 desce de 0 a
    -21 ao longo de dez meses, e isso e medido, nao lembrado.
    """
    por = defaultdict(list)
    for r in conn.execute(
            "SELECT puuid, game_creation, win FROM participants "
            " WHERE tracked = 1 AND queue_id IN (?, ?) ORDER BY game_creation",
            FILAS_RANQUEADAS):
        if r["puuid"] in players:
            por[r["puuid"]].append(r)

    saida = []
    for puuid, linhas in por.items():
        if len(linhas) < MIN_RANQUEADAS:
            continue
        acum = 0
        pontos, pico, vale = [], 0, 0
        for r in linhas:
            acum += 1 if r["win"] else -1
            pico = max(pico, acum)
            vale = min(vale, acum)
            pontos.append({"t": int(r["game_creation"]), "v": acum})
        # Um ponto por partida deixaria a serie com centenas de itens por
        # pessoa. A curva e lida pelo formato, entao 60 pontos bastam.
        passo = max(1, len(pontos) // 60)
        magros = pontos[::passo]
        if magros[-1] is not pontos[-1]:
            magros.append(pontos[-1])
        saida.append({
            "puuid": puuid, "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "serie": magros, "pico": pico, "vale": vale,
            "fim": acum, "jogos": len(linhas),
            # A queda que interessa: do melhor momento ate onde parou.
            "doPico": acum - pico,
        })
    saida.sort(key=lambda x: x["doPico"])
    return saida


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
            # O emblema da faixa ja vem embutido no HTML; a tela so precisa do
            # nome do tier para escolher qual.
            "tierSolo": (solo["tier"] or "").lower() if solo else "unranked",
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
        "informado": _informado(players),
        "curva": _curva(conn, players),
        "minJogos": MIN_RANQUEADAS,
        "historico": _historico(conn, players),
        "geradoEm": time.strftime("%Y-%m-%d"),
    }

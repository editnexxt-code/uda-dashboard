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
from datetime import datetime
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


def passos(tier: str | None, division: str | None) -> int | None:
    """Posicao na escada real, contando degrau por degrau.

    valor_elo() serve para ORDENAR, mas mente na hora de contar: ela da 400 a
    cada tier e 100 a cada divisao, entao trata Mestre como se existissem
    divisoes acima de Diamante I. Medido: Mestre -> Esmeralda I dava "8
    divisoes" quando a escada real tem 5 degraus (Mestre, D-I, D-II, D-III,
    D-IV, E-I). Aqui cada tier abaixo de Mestre vale 4 degraus e Mestre para
    cima vale 1 cada, que e como a fila de fato funciona.
    """
    if not tier or tier.upper() not in ORDEM_TIER:
        return None
    i = ORDEM_TIER.index(tier.upper())
    if i >= ORDEM_TIER.index("MASTER"):
        return ORDEM_TIER.index("MASTER") * 4 + (i - ORDEM_TIER.index("MASTER"))
    return i * 4 + ORDEM_DIV.get((division or "I").upper(), 3)


def _dias_entre(a: str, b: str) -> int | None:
    """Intervalo em dias entre duas datas AAAA-MM-DD."""
    try:
        d1 = datetime.strptime(a, "%Y-%m-%d")
        d2 = datetime.strptime(b, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return abs((d2 - d1).days)


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


def _recordes(players: dict) -> dict:
    """Os dois recordes do grupo -- maior queda e maior ascensao -- de elos.json.

    Existe porque a Riot nao devolve historico de elo: a League-V4 so responde o
    agora e o Match-V5 so serve partida recente. Blitz e OP.GG conseguem porque
    vem gravando ha anos; aqui e memoria do grupo, e a tela marca como INFORMADO.

    Cada categoria tem um `atual` e um `historico`. Quem bate a marca ocupa o
    `atual` e o antigo desce para o quadro de honra -- e por isso o formato ja
    nasce com lista, em vez de um campo unico que precisaria ser reescrito.
    """
    caminho = Path(__file__).resolve().parent.parent / "elos.json"
    if not caminho.exists():
        return {}
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}

    por_riot = {}
    for puuid, info in players.items():
        nome, tag = info.get("gameName", ""), info.get("tagLine") or ""
        por_riot[f"{nome}#{tag}".lower()] = (puuid, info)
        por_riot[nome.lower()] = (puuid, info)

    def ficha(m: dict) -> dict | None:
        alvo = (por_riot.get(str(m.get("jogador", "")).lower())
                or por_riot.get(str(m.get("jogador", "")).split("#")[0].lower()))
        if not alvo:
            return None
        puuid, info = alvo
        t_de, d_de = _pt_para_tier(m.get("de", ""))
        t_para, d_para = _pt_para_tier(m.get("para", ""))
        if not t_de or not t_para:
            return None
        p_de, p_para = passos(t_de, d_de), passos(t_para, d_para)
        # O numero de degraus pode vir escrito, mas se as duas pontas existem a
        # conta manda: assim uma correcao de faixa nunca deixa o texto mentindo.
        degraus = (abs(p_de - p_para) if (p_de is not None and p_para is not None)
                   else m.get("degraus"))
        pdl = m.get("dePdl")
        return {
            "puuid": puuid, "gameName": info["gameName"], "icon": info["icon"],
            "quando": m.get("quando", ""), "fonte": m.get("fonte", ""),
            "nota": m.get("nota", ""),
            "de": {"tier": t_de.lower(),
                   "txt": texto_elo(t_de, d_de, 0).replace(" 0 PDL", ""),
                   "pdl": int(pdl) if isinstance(pdl, (int, float)) else None,
                   "em": m.get("deEm") or None},
            "para": {"tier": t_para.lower(),
                     "txt": texto_elo(t_para, d_para, 0).replace(" 0 PDL", ""),
                     "em": m.get("paraEm") or None},
            "degraus": degraus,
            "partidas": m.get("partidas"),
            "dias": _dias_entre(m.get("deEm", ""), m.get("paraEm", "")),
        }

    saida = {}
    for chave in ("queda", "ascensao"):
        bloco = (bruto.get("recordes") or {}).get(chave) or {}
        atual = ficha(bloco.get("atual") or {})
        if not atual:
            continue
        saida[chave] = {
            "atual": atual,
            "historico": [f for f in (ficha(h) for h in (bloco.get("historico") or []))
                          if f],
        }
    return saida


def _banimentos(players: dict) -> dict:
    """Podio de quem passou mais tempo suspenso.

    A Riot NAO expoe punicao de conta em endpoint nenhum. Verificado antes de
    escrever isto: `PlayerBehavior` na partida so carrega estado de combate
    ({"PlayerBehavior_IsHeroInCombat": 0} em 2.000 participacoes), e o unico
    desafio cujo texto casa com "ban" e o de Bandopolis.

    Medir por AUSENCIA tambem nao funciona olhando para tras: os buracos do
    historico sao falha de coleta, nao sumico. O Match-V5 entrega so as ~100
    ultimas partidas, entao o banco tem 874 partidas em agosto e 3 em dezembro
    de 2024 -- e "sumiu 430 dias" seria ler o limite da API como se fosse
    comportamento de gente.

    Sobra a memoria do grupo, e ela fica marcada como tal.
    """
    caminho = Path(__file__).resolve().parent.parent / "elos.json"
    if not caminho.exists():
        return {"podio": [], "todos": [], "total": 0}
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"podio": [], "todos": [], "total": 0}

    por_riot = {}
    for puuid, info in players.items():
        nome, tag = info.get("gameName", ""), info.get("tagLine") or ""
        por_riot[f"{nome}#{tag}".lower()] = (puuid, info)
        por_riot[nome.lower()] = (puuid, info)

    soma: dict[str, dict] = {}
    todos = []
    for b in bruto.get("banimentos") or []:
        # A entrada de exemplo que acompanha o arquivo nao pode virar dado.
        if b.get("_exemplo"):
            continue
        alvo = (por_riot.get(str(b.get("jogador", "")).lower())
                or por_riot.get(str(b.get("jogador", "")).split("#")[0].lower()))
        if not alvo:
            continue
        puuid, info = alvo
        dias = b.get("dias")
        if not isinstance(dias, (int, float)) or dias <= 0:
            continue
        item = {"puuid": puuid, "gameName": info["gameName"], "icon": info["icon"],
                "dias": int(dias), "quando": b.get("quando", ""),
                "tipo": b.get("tipo", ""), "motivo": b.get("motivo", "")}
        todos.append(item)
        acc = soma.setdefault(puuid, {"puuid": puuid, "gameName": info["gameName"],
                                      "icon": info["icon"], "dias": 0, "vezes": 0})
        acc["dias"] += int(dias)
        acc["vezes"] += 1

    podio = sorted(soma.values(), key=lambda x: (-x["dias"], -x["vezes"]))
    todos.sort(key=lambda x: -x["dias"])
    return {"podio": podio, "todos": todos,
            "total": sum(x["dias"] for x in soma.values())}


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
        "recordes": _recordes(players),
        "banimentos": _banimentos(players),
        "curva": _curva(conn, players),
        "minJogos": MIN_RANQUEADAS,
        "historico": _historico(conn, players),
        "geradoEm": time.strftime("%Y-%m-%d"),
    }

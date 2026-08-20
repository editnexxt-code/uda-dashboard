"""Trofeus de carreira: as metricas de zoacao somadas ao longo de TODAS as partidas.

Diferente dos recordes do kpi.py, que premiam uma unica partida gloriosa ou
desastrosa, aqui o que conta e a constancia. Morrer 15 vezes numa noite e azar;
morrer 8 por partida ao longo de 200 jogos e personalidade.

Cada trofeu carrega o ranking inteiro, nao so o campeao -- metade da graca esta
em ver onde VOCE caiu na lista.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .kpi import _r, _safe_div

# Minimo de partidas para entrar num trofeu. Sem isso quem jogou 3 vezes lidera
# tudo por acidente estatistico, e zoacao baseada em acidente nao tem graca.
MIN_PARTIDAS = 10

# Campos somados direto da tabela participants.
SOMAS = [
    "kills", "deaths", "assists", "game_duration", "time_dead", "time_played",
    "gold", "gold_spent", "bounty_gold", "damage_champions", "damage_taken",
    "pings_total", "ping_mia", "solo_kills", "outnumbered_kills",
    "skillshots_dodged", "skillshots_hit", "epic_steals", "epic_steals_no_smite",
    "turret_plates", "flash_casts", "items_purchased", "consumables",
    "control_wards_placed", "vision_score", "cc_time", "self_mitigated",
    "heals_teammates", "surrender", "had_open_nexus", "was_afk",
    "danced_herald", "fist_bumps", "poro_explosions", "snowballs_hit",
    "unseen_recalls", "blast_cone", "quick_cleanse", "largest_spree",
    "penta_kills", "quadra_kills", "triple_kills", "double_kills",
    "multikill_flash", "survived_low_hp", "took_large_dmg", "flawless_aces",
    "buffs_stolen", "inhibs_lost", "turrets_lost", "first_blood",
    # Estavam gravadas no banco desde o backfill e nunca viraram metrica:
    "cs_adv", "lvl_lead", "vision_adv", "lane_cs10", "immobilizations",
    "save_ally", "enemy_jungle_kills", "damage_epic", "ability_uses",
    "dragon_td", "baron_td", "herald_td", "void_kills", "aces_15",
    "spell4_casts", "perfect_souls",
]


def _n(valor) -> float:
    return float(valor) if valor is not None else 0.0


def _agregar(rows_by_player, players) -> dict[str, dict[str, float]]:
    saida: dict[str, dict[str, float]] = {}
    for puuid, linhas in rows_by_player.items():
        if puuid not in players or not linhas:
            continue
        acc: dict[str, float] = defaultdict(float)
        acc["partidas"] = len(linhas)
        acc["vitorias"] = sum(1 for r in linhas if r["win"])
        for row in linhas:
            for campo in SOMAS:
                try:
                    acc[campo] += _n(row[campo])
                except (IndexError, KeyError):
                    pass
        acc["minutos"] = acc["game_duration"] / 60.0

        # CS aos 10 e vantagem de visao dependem da ROTA: jungler e suporte tem
        # pouco farm de rota por definicao, e medi-los junto apontaria o jungler
        # como "quem menos farma" -- o que nao e vexame, e a funcao dele.
        # Aqui a conta corre so sobre topo, meio e atirador.
        lane = [r for r in linhas if (r["position"] or "").upper()
                in ("TOP", "MIDDLE", "BOTTOM")]
        acc["lane_partidas"] = len(lane)
        acc["lane_cs10_lane"] = sum(_n(r["lane_cs10"]) for r in lane)
        acc["vision_adv_lane"] = sum(_n(r["vision_adv"]) for r in lane)
        saida[puuid] = dict(acc)
    return saida


# key, titulo, grupo, legenda, calculo(acc), unidade, casas, maior_e_melhor
TROFEUS: list[tuple] = [
    # --------------------------------------------------------------- gloria
    ("rei1v1", "Rei do 1v1", "gloria",
     "Abates sem dividir credito com ninguem. O mais honesto que existe.",
     lambda a: _safe_div(a["solo_kills"], a["partidas"]), "solo kills por partida", 2, True),
    ("contraTodos", "Contra Todos", "gloria",
     "Abates feitos em menor numero. O 1v2 que sempre vira clipe no grupo.",
     lambda a: a["outnumbered_kills"], "abates em desvantagem", 0, True),
    ("ladrao", "Ladrao de Objetivo", "gloria",
     "Barao e dragao roubados do time inimigo. Puro deboche.",
     lambda a: a["epic_steals"], "objetivos roubados", 0, True),
    ("matrix", "Modo Matrix", "gloria",
     "Skillshots desviados por minuto. Parece que joga com delay negativo.",
     lambda a: _safe_div(a["skillshots_dodged"], a["minutos"]), "desvios por minuto", 2, True),
    ("demolidor", "Demolidor", "gloria",
     "Placas de torre derrubadas. Alguem tinha que lembrar do objetivo.",
     lambda a: a["turret_plates"], "placas derrubadas", 0, True),
    ("imortal", "O Imortal", "gloria",
     "Minutos vividos por morte. Sabe a hora de voltar pra base.",
     lambda a: _safe_div(a["minutos"], max(a["deaths"], 1)), "minutos por morte", 1, True),
    ("cacador", "Caca-Recompensas", "gloria",
     "Ouro de recompensa embolsado. Sempre acha o alimentado do outro time.",
     lambda a: a["bounty_gold"], "de ouro em recompensas", 0, True),
    ("multikill", "Colecionador de Caixoes", "gloria",
     "Multikills somados, com peso maior para os mais raros.",
     lambda a: a["double_kills"] + a["triple_kills"] * 3
     + a["quadra_kills"] * 9 + a["penta_kills"] * 27,
     "pontos de multikill", 0, True),
    ("muralha", "A Muralha", "gloria",
     "Dano absorvido por partida. Segurou a porrada pra galera passar.",
     lambda a: _safe_div(a["self_mitigated"], a["partidas"]),
     "de dano absorvido por jogo", 0, True),

    # ------------------------------------------------------------- vergonha
    ("caixaoAmbulante", "O Caixao Ambulante", "vergonha",
     "Mortes por partida. Nao e azar de uma noite, e o padrao de vida.",
     lambda a: _safe_div(a["deaths"], a["partidas"]), "mortes por partida", 2, True),
    ("telaCinza", "Assinante da Tela Cinza", "vergonha",
     "Tempo total olhando o cronometro de renascimento. Da pra ver uma serie.",
     lambda a: a["time_dead"] / 3600.0, "horas morto", 1, True),
    ("meioJogo", "Metade Fantasma", "vergonha",
     "Fatia da partida passada morto. Nao jogou, assistiu.",
     lambda a: _safe_div(a["time_dead"], a["time_played"] or a["game_duration"]) * 100,
     "% do tempo morto", 1, True),
    ("cadeMid", "Cade o Mid?", "vergonha",
     "Pings de interrogacao por partida. Olhar o mapa custava menos.",
     lambda a: _safe_div(a["ping_mia"], a["partidas"]), "pings ? por partida", 1, True),
    ("bancoCentral", "Banco Central", "vergonha",
     "Ouro que morreu no bolso. Item nao se compra sozinho.",
     lambda a: _safe_div(a["gold"] - a["gold_spent"], a["partidas"]),
     "de ouro parado por jogo", 0, True),
    ("sacoPancada", "Saco de Pancada", "vergonha",
     "Dano levado dividido pelo dano dado. Apanhar tambem e uma escolha.",
     lambda a: _safe_div(a["damage_taken"], max(a["damage_champions"], 1)),
     "x mais apanha do que bate", 2, True),
    ("rendeFacil", "Dedo no WW", "vergonha",
     "Fatia das partidas terminadas em rendicao. Resiliencia zero.",
     lambda a: _safe_div(a["surrender"], a["partidas"]) * 100, "% de rendicoes", 0, True),
    ("nexusAberto", "Nexus Escancarado", "vergonha",
     "Partidas que chegaram a ter o nexus aberto. Vergonha estrutural.",
     lambda a: a["had_open_nexus"], "vezes com nexus aberto", 0, True),
    ("cego", "Jogando de Olhos Fechados", "vergonha",
     "Sentinelas de controle por partida. Visao e opcional pra alguns.",
     lambda a: _safe_div(a["control_wards_placed"], a["partidas"]),
     "sentinelas de controle por jogo", 2, False),
    ("torresPerdidas", "Guardiao Ausente", "vergonha",
     "Torres perdidas por partida. A estrutura caiu e ninguem viu.",
     lambda a: _safe_div(a["turrets_lost"], a["partidas"]),
     "torres perdidas por jogo", 1, True),

    # ---------------------------------------------------------- curiosidade
    ("sinfonia", "Sinfonia de Pings", "curiosidade",
     "Pings por partida. Comunicacao e importante; isso ja e outro departamento.",
     lambda a: _safe_div(a["pings_total"], a["partidas"]), "pings por partida", 1, True),
    ("dedoFlash", "Dedo no Flash", "curiosidade",
     "Acionamentos do flash por partida. Boa parte foi so ansiedade.",
     lambda a: _safe_div(a["flash_casts"], a["partidas"]), "acionamentos por jogo", 1, True),
    ("colecionador", "Rato de Loja", "curiosidade",
     "Itens comprados por partida. Cada volta na base, uma ideia nova.",
     lambda a: _safe_div(a["items_purchased"], a["partidas"]), "itens por partida", 1, True),
    ("pocao", "Dependente de Pocao", "curiosidade",
     "Consumiveis por partida. Metade do ouro virou bebida.",
     lambda a: _safe_div(a["consumables"], a["partidas"]), "consumiveis por jogo", 1, True),
    ("dancarino", "Dancou com o Arauto", "curiosidade",
     "Parou no meio da partida pra dancar em cima do Arauto. Prioridades.",
     lambda a: a["danced_herald"], "dancas", 0, True),
    ("amigao", "Amigao do Rift", "curiosidade",
     "Bateu punho com o aliado. O gesto mais sincero do jogo.",
     lambda a: a["fist_bumps"], "toques de punho", 0, True),
    ("poro", "Terror dos Poros", "curiosidade",
     "Explodiu poro no ARAM. Nao julgamos, so registramos.",
     lambda a: a["poro_explosions"], "poros explodidos", 0, True),
    ("neve", "Mira de Bola de Neve", "curiosidade",
     "Acertos de bola de neve no ARAM. Precisao que some na ranqueada.",
     lambda a: a["snowballs_hit"], "bolas acertadas", 0, True),
    ("fantasma", "Recall Fantasma", "curiosidade",
     "Voltou pra base sem ninguem ver. Furtividade de quem fez besteira.",
     lambda a: a["unseen_recalls"], "recalls invisiveis", 0, True),
    ("vento", "Voando de Vento", "curiosidade",
     "Usou o Cone de Explosao pra atravessar o mapa. Estilo acima de tudo.",
     lambda a: a["blast_cone"], "voos de cone", 0, True),
    ("afk", "O Desaparecido", "curiosidade",
     "Partidas em que simplesmente sumiu. A internet leva a culpa.",
     lambda a: a["was_afk"], "partidas AFK", 0, True),
    # ---------------------------- vindos das colunas que estavam paradas
    ("donoDaRota", "Dono da Rota", "gloria",
     "Maior vantagem de CS ja aberta sobre o oponente de rota, por partida.",
     lambda a: _safe_div(a["cs_adv"], a["partidas"]), "de CS de vantagem", 0, True),
    ("prendedor", "O Prendedor", "gloria",
     "Inimigos imobilizados por minuto. Se voce parou, voce ja era.",
     lambda a: _safe_div(a["immobilizations"], a["minutos"]),
     "imobilizacoes por minuto", 2, True),
    ("salvaVidas", "Salva-Vidas", "gloria",
     "Aliados arrancados da morte no ultimo instante. O verdadeiro suporte.",
     lambda a: a["save_ally"], "aliados salvos", 0, True),
    ("invasor", "O Invasor", "gloria",
     "Monstros roubados da selva inimiga. Educacao zero, lucro alto.",
     lambda a: a["enemy_jungle_kills"], "campos invadidos", 0, True),
    ("cacaObjetivo", "Caca-Objetivo", "gloria",
     "Participacao em dragao, barao e arauto somada. Quem lembra do mapa.",
     lambda a: a["dragon_td"] + a["baron_td"] + a["herald_td"],
     "objetivos disputados", 0, True),
    ("acePrecoce", "Ace Antes dos 15", "gloria",
     "Times inimigos apagados inteiros antes dos 15 minutos. Sem piedade.",
     lambda a: a["aces_15"], "aces precoces", 0, True),
    ("cacadorMonstro", "Cacador de Monstro", "gloria",
     "Dano nos objetivos neutros por partida. O barao nao se mata sozinho.",
     lambda a: _safe_div(a["damage_epic"], a["partidas"]),
     "de dano em objetivos", 0, True),

    ("farmDoDez", "Farm dos Dez Minutos", "vergonha",
     "CS aos dez minutos, contando so partidas de rota. Jungler e suporte ficam"
     " de fora: farmar pouco la e a funcao, nao vexame.",
     lambda a: _safe_div(a["lane_cs10_lane"], a["lane_partidas"]),
     "de CS aos 10 min", 0, False),
    ("cegoDaRota", "Cego de Rota", "vergonha",
     "Vantagem de visao sobre o oponente de rota, so em partidas de rota."
     " Negativo quer dizer que ele te via primeiro.",
     lambda a: _safe_div(a["vision_adv_lane"], a["lane_partidas"]),
     "de vantagem de visao", 2, False),

    ("dedoNervoso", "Dedo Nervoso", "curiosidade",
     "Habilidades usadas por minuto. O teclado que nao descansa.",
     lambda a: _safe_div(a["ability_uses"], a["minutos"]),
     "habilidades por minuto", 1, True),
    ("botaoExtra", "Dedo no Quarto Botao", "curiosidade",
     "Acionamentos do feitico de item. Metade foi ansiedade, como sempre.",
     lambda a: _safe_div(a["spell4_casts"], a["partidas"]),
     "acionamentos por jogo", 1, True),

    ("milagre", "Sobreviveu ao Impossivel", "curiosidade",
     "Ficou com um fio de vida e escapou. Sorte tambem e habilidade.",
     lambda a: a["survived_low_hp"], "escapadas milagrosas", 0, True),
]

GRUPO_ORDEM = {"gloria": 0, "vergonha": 1, "curiosidade": 2}
GRUPO_LABEL = {"gloria": "Gloria", "vergonha": "Vergonha", "curiosidade": "Curiosidade"}




# Qual coluna de `participants` prova cada trofeu, partida a partida. Sem isto o
# trofeu diz "9,06 mortes por partida" e nao da para clicar e ver ONDE isso
# aconteceu -- que e metade da zoacao.
# None = agregado sem equivalente por partida (razoes, tempo total).
COLUNA_POR_TROFEU = {
    "rei1v1": "solo_kills",
    "contraTodos": "outnumbered_kills",
    "ladrao": "epic_steals",
    "demolidor": "turret_plates",
    "cacador": "bounty_gold",
    "muralha": "self_mitigated",
    "caixaoAmbulante": "deaths",
    "telaCinza": "time_dead",
    "cadeMid": "ping_mia",
    "torresPerdidas": "turrets_lost",
    "sinfonia": "pings_total",
    "dedoFlash": "flash_casts",
    "colecionador": "items_purchased",
    "pocao": "consumables",
    "dancarino": "danced_herald",
    "amigao": "fist_bumps",
    "poro": "poro_explosions",
    "neve": "snowballs_hit",
    "fantasma": "unseen_recalls",
    "vento": "blast_cone",
    "afk": "was_afk",
    "milagre": "survived_low_hp",
    "nexusAberto": "had_open_nexus",
    "matrix": "skillshots_dodged",
    "donoDaRota": "cs_adv",
    "prendedor": "immobilizations",
    "salvaVidas": "save_ally",
    "invasor": "enemy_jungle_kills",
    "acePrecoce": "aces_15",
    "cacadorMonstro": "damage_epic",
    "farmDoDez": "lane_cs10",
    "dedoNervoso": "ability_uses",
    "botaoExtra": "spell4_casts",
}

# Como escrever o valor daquela partida na tela.
FORMATO = {
    "cs_adv": lambda v: f"+{int(v)} de CS",
    "damage_epic": lambda v: f"{int(v)} em objetivos",
    "lane_cs10": lambda v: f"{int(v)} CS aos 10",
    "immobilizations": lambda v: f"{int(v)} presos",
    "telaCinza": lambda v: f"{int(v // 60)} min morto",
    "time_dead": lambda v: f"{int(v // 60)} min morto",
    "bounty_gold": lambda v: f"{int(v)} de recompensa",
    "self_mitigated": lambda v: f"{int(v)} absorvido",
}


def _evidencia(linhas, coluna: str, quantas: int = 3) -> list[dict]:
    """As partidas que mais empurraram aquele numero para cima."""
    if not coluna:
        return []
    marcadas = []
    for r in linhas:
        try:
            valor = _n(r[coluna])
        except (IndexError, KeyError):
            continue
        if valor <= 0:
            continue
        marcadas.append((valor, r))
    marcadas.sort(key=lambda x: -x[0])
    fmt = FORMATO.get(coluna, lambda v: f"{int(v)}")
    saida = []
    for valor, r in marcadas[:quantas]:
        saida.append({
            "matchId": r["match_id"],
            "champion": r["champion_name"], "championId": r["champion_id"],
            "k": r["kills"], "d": r["deaths"], "a": r["assists"],
            "minutes": round(_n(r["game_duration"]) / 60),
            "date": r["game_creation"],
            "valor": fmt(valor),
        })
    return saida


def construir(rows_by_player, players, min_partidas: int = MIN_PARTIDAS) -> list[dict]:
    """Um trofeu por metrica, cada um com o ranking completo do elenco."""
    agregados = _agregar(rows_by_player, players)
    elegiveis = {p: a for p, a in agregados.items() if a["partidas"] >= min_partidas}
    if not elegiveis:
        return []

    # Trofeu de rota so aceita quem tem amostra de rota.
    SO_LANE = {"farmDoDez", "cegoDaRota"}
    MIN_LANE = 8

    saida = []
    for key, titulo, grupo, legenda, calc, unidade, casas, maior in TROFEUS:
        linhas = []
        for puuid, acc in elegiveis.items():
            if key in SO_LANE and acc.get("lane_partidas", 0) < MIN_LANE:
                continue
            try:
                valor = float(calc(acc))
            except (KeyError, TypeError, ZeroDivisionError):
                continue
            linhas.append({
                "puuid": puuid,
                "gameName": players[puuid]["gameName"],
                "icon": players[puuid]["icon"],
                "valor": _r(valor, casas) if casas else int(round(valor)),
                "_bruto": valor,
                "partidas": int(acc["partidas"]),
            })
        if not linhas:
            continue
        linhas.sort(key=lambda x: -x["_bruto"] if maior else x["_bruto"])
        # Trofeu em que ninguem pontuou nao vira cartao vazio: some da grade.
        if maior and not any(x["_bruto"] > 0 for x in linhas):
            continue
        for i, item in enumerate(linhas, 1):
            item["pos"] = i
            item.pop("_bruto", None)
        media = sum(x["valor"] for x in linhas) / len(linhas)
        # So o podio ganha evidencia: sao 31 trofeus x 14 pessoas, e anexar as
        # partidas de todo mundo multiplicaria o payload sem ninguem clicar.
        coluna = COLUNA_POR_TROFEU.get(key)
        if coluna:
            for item in linhas[:3]:
                item["evidencia"] = _evidencia(
                    rows_by_player.get(item["puuid"], []), coluna)

        saida.append({
            "key": key, "titulo": titulo, "grupo": grupo,
            "grupoLabel": GRUPO_LABEL[grupo], "legenda": legenda,
            "unidade": unidade, "casas": casas, "maiorMelhor": maior,
            "campeao": linhas[0], "ranking": linhas,
            "media": _r(media, casas) if casas else int(round(media)),
            "minPartidas": min_partidas,
            "temEvidencia": bool(coluna),
        })
    saida.sort(key=lambda t: (GRUPO_ORDEM[t["grupo"]], t["key"]))
    return saida

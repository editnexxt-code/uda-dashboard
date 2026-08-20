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
    "multikill_spell", "assist_streak_12", "first_turret_time", "quick_first_turret",
    # v3 -- destravadas do JSON bruto nesta migracao:
    "exec_torre_10", "scuttle_kills", "kills_own_turret", "kills_enemy_turret",
    "kills_alcove", "kills_fountain", "quick_solo_kills", "team_early_ff",
    "perfect_game", "taken_physical", "taken_magic", "taken_true",
    "spell1_casts", "spell2_casts", "spell3_casts", "pick_with_ally",
    "immob_kill_ally", "kill_hidden_ally", "full_team_td", "ward_takedowns",
    "wards_guarded", "stealth_wards", "killing_sprees", "solo_turrets_late",
    "survived_immob", "lane_gold_adv", "inhib_td",
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
        # --- morte sem culpado (torre, minion, monstro, execucao) ------------
        # deaths_by_champs so existe em ~88% das partidas. Onde o campo falta ele
        # vem 0, e `deaths - 0` marcaria TODA morte como burra. Por isso a conta
        # roda apenas nas partidas onde o campo esta comprovadamente preenchido
        # (>0), e o denominador acompanha. Erra para menos, nunca para mais.
        comCulpado = [r for r in linhas if _n(r["deaths_by_champs"]) > 0]
        acc["bobas_partidas"] = len(comCulpado)
        acc["mortes_bobas"] = sum(
            max(0.0, _n(r["deaths"]) - _n(r["deaths_by_champs"])) for r in comCulpado)

        # --- selva: cada recorte so vale para quem joga a funcao --------------
        # jungle_cs10 e farm de selva, entao so o jungler entra. E o inverso vale
        # para ally_jungle_cs: no jungler isso e o farm dele, nao roubo -- roubo
        # e quando o LANER come o camp do proprio jungler.
        selva = [r for r in linhas if (r["position"] or "").upper() == "JUNGLE"]
        naoSelva = [r for r in linhas if (r["position"] or "").upper() != "JUNGLE"]
        acc["selva_partidas"] = len(selva)
        acc["jungle_cs10_selva"] = sum(_n(r["jungle_cs10"]) for r in selva)
        acc["scuttle_selva"] = sum(_n(r["scuttle_kills"]) for r in selva)
        acc["naoselva_partidas"] = len(naoSelva)
        acc["camp_roubado_aliado"] = sum(_n(r["ally_jungle_cs"]) for r in naoSelva)

        acc["torre_partidas"] = sum(1 for r in linhas if _n(r["first_turret_time"]) > 0)
        acc["lane_partidas"] = len(lane)
        acc["lane_cs10_lane"] = sum(_n(r["lane_cs10"]) for r in lane)
        acc["vision_adv_lane"] = sum(_n(r["vision_adv"]) for r in lane)
        saida[puuid] = dict(acc)
    return saida


# key, titulo, grupo, legenda, calculo(acc), unidade, casas, maior_e_melhor
TROFEUS: list[tuple] = [
    # --------------------------------------------------------------- gloria
    ("rei1v1", "Mão de Sangue", "gloria",
     "Abate solo, sem dividir com ninguém. Encostou, morreu. O time só chega pra ver o corpo.",
     lambda a: _safe_div(a["solo_kills"], a["partidas"]), "solo kills por partida", 2, True),
    ("contraTodos", "Um Contra o Mundo", "gloria",
     "Abate feito em desvantagem numérica. Enquanto o time recuava chorando, esse ficou e levou os dois.",
     lambda a: a["outnumbered_kills"], "abates em desvantagem", 0, True),
    ("ladrao", "Punga de Objetivo", "gloria",
     "Barão e dragão roubados na cara do inimigo. Quarenta minutos de setup pro lixo por causa de um smite.",
     lambda a: a["epic_steals"], "objetivos roubados", 0, True),
    ("matrix", "Desvia Até de Ping", "gloria",
     "Skillshot desviado por minuto. O inimigo mira, ele já não está mais lá. Dá ódio jogar contra.",
     lambda a: _safe_div(a["skillshots_dodged"], a["minutos"]), "desvios por minuto", 2, True),
    ("demolidor", "Come Torre no Café", "gloria",
     "Placa de torre derrubada. Enquanto vocês brigam no meio do mapa, ele está almoçando a base.",
     lambda a: a["turret_plates"], "placas derrubadas", 0, True),
    ("imortal", "Não Morre Nem de Graça", "gloria",
     "Minutos vivo por morte. O resto do elenco decorou o caminho da fonte; esse aqui nem sabe onde fica.",
     lambda a: _safe_div(a["minutos"], max(a["deaths"], 1)), "minutos por morte", 1, True),
    ("cacador", "Cobrador de Dívida", "gloria",
     "Ouro de recompensa embolsado. Se você está alimentado do outro lado, ele já anotou seu nome.",
     lambda a: a["bounty_gold"], "de ouro em recompensas", 0, True),
    ("multikill", "Enterrador", "gloria",
     "Multikill somado, com peso maior nos raros. Não mata: organiza funeral coletivo.",
     lambda a: a["double_kills"] + a["triple_kills"] * 3
     + a["quadra_kills"] * 9 + a["penta_kills"] * 27,
     "pontos de multikill", 0, True),
    ("muralha", "Segura o Rojão", "gloria",
     "Dano absorvido por partida. Fica na frente enquanto o resto do time descobre onde fica o botão de recuar.",
     lambda a: _safe_div(a["self_mitigated"], a["partidas"]),
     "de dano absorvido por jogo", 0, True),

    # ------------------------------------------------------------- vergonha
    ("caixaoAmbulante", "Doador de Ouro", "vergonha",
     "Morte por partida. O inimigo nem precisa caçar: é só esperar ele aparecer sozinho.",
     lambda a: _safe_div(a["deaths"], a["partidas"]), "mortes por partida", 2, True),
    ("telaCinza", "Mora na Tela Cinza", "vergonha",
     "Tempo total encarando o cronômetro de renascimento. Dava pra ver uma temporada inteira nesse tempo.",
     lambda a: a["time_dead"] / 3600.0, "horas morto", 1, True),
    ("meioJogo", "Comprou Ingresso pro Próprio Jogo", "vergonha",
     "Fatia da partida passada morto. Não jogou: assistiu, e de camarote.",
     lambda a: _safe_div(a["time_dead"], a["time_played"] or a["game_duration"]) * 100,
     "% do tempo morto", 1, True),
    ("cadeMid", "A Culpa É Sempre do Outro", "vergonha",
     "Ping de interrogação por partida. O mapa está ali do lado, mas é mais fácil digitar '?'.",
     lambda a: _safe_div(a["ping_mia"], a["partidas"]), "pings ? por partida", 1, True),
    ("bancoCentral", "Morreu Rico", "vergonha",
     "Ouro que apodreceu no bolso. Juntou a vida inteira pra morrer com a poupança cheia.",
     lambda a: _safe_div(a["gold"] - a["gold_spent"], a["partidas"]),
     "de ouro parado por jogo", 0, True),
    ("sacoPancada", "Boneco de Treino", "vergonha",
     "Dano levado dividido pelo dano dado. O outro time usa ele pra testar build nova.",
     lambda a: _safe_div(a["damage_taken"], max(a["damage_champions"], 1)),
     "x mais apanha do que bate", 2, True),
    ("rendeFacil", "Dedo no /ff", "vergonha",
     "Fatia das partidas terminadas em rendição. Aos 15 já está votando. Resiliência de papel molhado.",
     lambda a: _safe_div(a["surrender"], a["partidas"]) * 100, "% de rendicoes", 0, True),
    ("nexusAberto", "Nexus Escancarado", "vergonha",
     "Partida que chegou a ter o nexus aberto. Não perdeu: foi humilhado com hora marcada.",
     lambda a: a["had_open_nexus"], "vezes com nexus aberto", 0, True),
    ("cego", "Joga de Olho Fechado", "vergonha",
     "Sentinela de controle por partida. Visão é opcional. Morrer no escuro, aparentemente, também.",
     lambda a: _safe_div(a["control_wards_placed"], a["partidas"]),
     "sentinelas de controle por jogo", 2, False),
    ("torresPerdidas", "Guardião de Coisa Nenhuma", "vergonha",
     "Torre perdida por partida. A estrutura caiu e ele nem virou a câmera pra ver.",
     lambda a: _safe_div(a["turrets_lost"], a["partidas"]),
     "torres perdidas por jogo", 1, True),

    # ---------------------------------------------------------- curiosidade
    ("sinfonia", "Sinfonia de Ping", "curiosidade",
     "Ping por partida. O teclado dele grita bem mais alto do que ele joga.",
     lambda a: _safe_div(a["pings_total"], a["partidas"]), "pings por partida", 1, True),
    ("dedoFlash", "Flash de Ansiedade", "curiosidade",
     "Flash acionado por partida. Metade foi pânico puro; boa parte da outra metade foi pra dentro da parede.",
     lambda a: _safe_div(a["flash_casts"], a["partidas"]), "acionamentos por jogo", 1, True),
    ("colecionador", "Rato de Loja", "curiosidade",
     "Item comprado por partida. A cada volta na base, uma build nova. Nenhuma delas funciona.",
     lambda a: _safe_div(a["items_purchased"], a["partidas"]), "itens por partida", 1, True),
    ("pocao", "Vive de Poção", "curiosidade",
     "Consumível por partida. Metade do ouro virou bebida. A outra metade também.",
     lambda a: _safe_div(a["consumables"], a["partidas"]), "consumiveis por jogo", 1, True),
    ("dancarino", "Dançou em Cima do Arauto", "curiosidade",
     "Parou a partida pra dançar. O time apanhando no mapa e ele ensaiando coreografia.",
     lambda a: a["danced_herald"], "dancas", 0, True),
    ("amigao", "Bateu Punho", "curiosidade",
     "Bateu punho com o aliado. O gesto mais sincero de um jogo movido a mentira.",
     lambda a: a["fist_bumps"], "toques de punho", 0, True),
    ("poro", "Terror dos Poro", "curiosidade",
     "Poro explodido no ARAM. Bateu em quem não podia revidar. Que coragem.",
     lambda a: a["poro_explosions"], "poros explodidos", 0, True),
    ("neve", "Mira que Some na Ranqueada", "curiosidade",
     "Bola de neve acertada no ARAM. Precisão cirúrgica justo no modo que não vale nada.",
     lambda a: a["snowballs_hit"], "bolas acertadas", 0, True),
    ("fantasma", "Recall Fantasma", "curiosidade",
     "Voltou pra base sem ninguém ver. Furtividade de quem fez besteira e sumiu antes da cobrança.",
     lambda a: a["unseen_recalls"], "recalls invisiveis", 0, True),
    ("vento", "Voou de Vento", "curiosidade",
     "Atravessou o mapa no Cone de Explosão. Estilo primeiro, resultado depois. Bem depois.",
     lambda a: a["blast_cone"], "voos de cone", 0, True),
    ("afk", "O Desaparecido", "curiosidade",
     "Partida em que simplesmente sumiu. A internet leva a culpa, como sempre.",
     lambda a: a["was_afk"], "partidas AFK", 0, True),
    # ---------------------------- vindos das colunas que estavam paradas
    ("donoDaRota", "Cobra Aluguel na Rota", "gloria",
     "Maior vantagem de CS já aberta sobre o oponente. Não ganhou a rota: fez o cara de refém.",
     lambda a: _safe_div(a["cs_adv"], a["partidas"]), "de CS de vantagem", 0, True),
    ("prendedor", "Se Parou, Já Era", "gloria",
     "Inimigo imobilizado por minuto. Nem precisa matar: ele prende e o time faz o serviço.",
     lambda a: _safe_div(a["immobilizations"], a["minutos"]),
     "imobilizacoes por minuto", 2, True),
    ("salvaVidas", "Tira do Caixão", "gloria",
     "Aliado arrancado da morte no último segundo. Salva gente que sinceramente não merecia.",
     lambda a: a["save_ally"], "aliados salvos", 0, True),
    ("invasor", "Almoça na Casa dos Outros", "gloria",
     "Monstro roubado da selva inimiga. Entra sem bater, come tudo e ainda sai reclamando do tempero.",
     lambda a: a["enemy_jungle_kills"], "campos invadidos", 0, True),
    ("cacaObjetivo", "O Único que Olha o Mapa", "gloria",
     "Participação em dragão, barão e arauto. Enquanto vocês brigam por abate, ele está ganhando o jogo.",
     lambda a: a["dragon_td"] + a["baron_td"] + a["herald_td"],
     "objetivos disputados", 0, True),
    ("acePrecoce", "Fecha o Caixão Antes dos 15", "gloria",
     "Time inimigo apagado inteiro antes dos 15 minutos. Não deu nem tempo do coitado montar o primeiro item.",
     lambda a: a["aces_15"], "aces precoces", 0, True),
    ("cacadorMonstro", "Bate no Barão Sozinho", "gloria",
     "Dano nos objetivos neutros por partida. Barão não cai no grito: alguém tem que bater.",
     lambda a: _safe_div(a["damage_epic"], a["partidas"]),
     "de dano em objetivos", 0, True),

    ("farmDoDez", "Mão de Alface", "vergonha",
     "CS aos dez minutos, contando só partida de rota. Minion passa do lado e ele deixa ir.",
     lambda a: _safe_div(a["lane_cs10_lane"], a["lane_partidas"]),
     "de CS aos 10 min", 0, False),
    ("cegoDaRota", "Cego de Rota", "vergonha",
     "Vantagem de visão sobre o oponente de rota. Joga no escuro e depois pergunta de onde veio.",
     lambda a: _safe_div(a["vision_adv_lane"], a["lane_partidas"]),
     "de vantagem de visao", 2, False),

    ("dedoNervoso", "Dedo Nervoso", "curiosidade",
     "Habilidade usada por minuto. Aperta tudo, acerta pouco, culpa o delay.",
     lambda a: _safe_div(a["ability_uses"], a["minutos"]),
     "habilidades por minuto", 1, True),
    ("botaoExtra", "Dedo no Quarto Botão", "curiosidade",
     "Feitiço de item acionado. Ansiedade, só que com tempo de recarga.",
     lambda a: _safe_div(a["spell4_casts"], a["partidas"]),
     "acionamentos por jogo", 1, True),

    ("comboMortal", "Um Botão, Dois Caixões", "gloria",
     "Multikill feito com uma habilidade só. Apertou uma tecla e limpou a tela.",
     lambda a: a["multikill_spell"], "multikills de um botao", 0, True),
    ("assistencia", "Só Encosta", "gloria",
     "Sequência de 12 assistências seguidas. Nunca dá o último tapa, mas aparece em toda foto.",
     lambda a: a["assist_streak_12"], "sequencias de 12", 0, True),
    ("torreCedo", "Acorda Cedo pra Derrubar Torre", "gloria",
     "Primeira torre derrubada mais cedo, na média. Lembra que o objetivo é o nexus, não o KDA.",
     lambda a: _safe_div(a["first_turret_time"], max(a["torre_partidas"], 1)) / 60.0,
     "minutos ate a primeira torre", 1, False),

    ("milagre", "Escapou com um Fio", "curiosidade",
     "Ficou com um fio de vida e fugiu. Nem a morte quis ficar com ele.",
     lambda a: a["survived_low_hp"], "escapadas milagrosas", 0, True),
    # ------------------------------------------------------- v3: gloria
    ("fonteInimiga", "Matou Dentro da Fonte Inimiga", "gloria",
     "Abate feito na base do inimigo, no chão dele. Não bastava ganhar: tinha que ser dentro de casa.",
     lambda a: a["kills_fountain"], "abates na fonte", 0, True),
    ("farmDaSelva", "Farm da Selva aos 10", "gloria",
     "CS de selva aos dez minutos, só de quem estava na selva. O jungler também tem farm — e agora tem cobrança.",
     lambda a: _safe_div(a["jungle_cs10_selva"], a["selva_partidas"]), "CS de selva aos 10", 1, True),
    ("reiCaranguejo", "Rei do Caranguejo", "gloria",
     "Caranguejo abatido por partida na selva. A briga mais ridícula do mapa, e alguém tinha que vencer.",
     lambda a: _safe_div(a["scuttle_selva"], a["selva_partidas"]), "caranguejos por partida", 2, True),
    ("torreSozinho", "Derruba Torre Sozinho", "gloria",
     "Torre derrubada sozinho no fim de jogo. Enquanto o time discute no chat, ele está trabalhando.",
     lambda a: a["solo_turrets_late"], "torres solo", 0, True),
    ("emboscada", "Emboscada no Mato", "gloria",
     "Abate feito dentro da alcova. Entrou no mato, esperou, e o outro nunca viu de onde veio.",
     lambda a: a["kills_alcove"], "abates na alcova", 0, True),
    ("matadorRapido", "Nem Deu Tempo de Reagir", "gloria",
     "Abate solo fechado em poucos segundos. O inimigo ainda estava pensando no que fazer.",
     lambda a: a["quick_solo_kills"], "abates relâmpago", 0, True),
    ("cacaWard", "Caça-Sentinela", "gloria",
     "Sentinela inimiga destruída por partida. Cega o outro time e depois passeia no escuro deles.",
     lambda a: _safe_div(a["ward_takedowns"], a["partidas"]), "sentinelas destruídas", 2, True),
    ("duroDeMatar", "Escapou de Três Prisões", "gloria",
     "Sobreviveu depois de ser imobilizado três vezes na mesma briga. Levou tudo e ainda saiu andando.",
     lambda a: a["survived_immob"], "fugas impossíveis", 0, True),
    # ----------------------------------------------------- v3: vergonha
    ("morteBurra", "Morreu de Bobeira", "vergonha",
     "Mortes sem nenhum campeão inimigo por perto: torre, minion, monstro ou execução. Ninguém matou. Ele se entregou sozinho.",
     lambda a: a["mortes_bobas"], "mortes sem culpado", 0, True),
    ("execTorre", "Executado pela Torre", "vergonha",
     "Vezes que a torre inimiga o executou antes dos dez minutos. A torre não persegue ninguém: ele foi até lá.",
     lambda a: a["exec_torre_10"], "execuções", 0, True),
    ("ratoDeCamp", "Rato de Camp", "vergonha",
     "Camp do PRÓPRIO jungler comido por partida, contando só quem não era o jungler. Rouba de casa e ainda reclama do gank.",
     lambda a: _safe_div(a["camp_roubado_aliado"], a["naoselva_partidas"]), "camps do aliado por partida", 1, True),
    # -------------------------------------------------- v3: curiosidade
    ("umBotaoSo", "Jogador de Um Botão Só", "curiosidade",
     "Fatia dos usos que foram na habilidade preferida. Tem três teclas, usa uma. As outras são decoração.",
     lambda a: _safe_div(max(a["spell1_casts"], a["spell2_casts"], a["spell3_casts"]),
                         a["spell1_casts"] + a["spell2_casts"] + a["spell3_casts"]) * 100,
     "% no botão favorito", 1, True),
    ("covardeTorre", "Só no Colo da Torre", "curiosidade",
     "Abate feito debaixo da própria torre por partida. Corajoso, desde que a torre esteja olhando.",
     lambda a: _safe_div(a["kills_own_turret"], a["partidas"]), "abates na saia da torre", 2, True),
    ("apanhaDeMago", "Apanha de Mago", "curiosidade",
     "Fatia do dano levado que veio de magia. Comprar resistência mágica continua sendo opcional, pelo visto.",
     lambda a: _safe_div(a["taken_magic"],
                         a["taken_magic"] + a["taken_physical"] + a["taken_true"]) * 100,
     "% do dano levado é mágico", 1, True),
    ("guardaCostas", "Guarda-Costas de Sentinela", "curiosidade",
     "Sentinela defendida de quem tentou destruir. Protege um totem melhor do que protege o carregador.",
     lambda a: a["wards_guarded"], "sentinelas defendidas", 0, True),
    ("nuncaSozinho", "Nunca Mata Sozinho", "curiosidade",
     "Abate fechado junto de um aliado, por partida. Coragem em dupla; sozinho, recua.",
     lambda a: _safe_div(a["pick_with_ally"], a["partidas"]), "abates acompanhados", 2, True),
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
    "comboMortal": "multikill_spell",
    "assistencia": "assist_streak_12",
    "botaoExtra": "spell4_casts",
    # v3 -- so entra aqui quem tem UMA coluna que explica o numero. Metrica de
    # razao (umBotaoSo, apanhaDeMago) fica de fora: nao existe "a partida que
    # causou esse percentual", e apontar uma seria mentira com cara de prova.
    "execTorre": "exec_torre_10",
    "ratoDeCamp": "ally_jungle_cs",
    "fonteInimiga": "kills_fountain",
    "reiCaranguejo": "scuttle_kills",
    "torreSozinho": "solo_turrets_late",
    "emboscada": "kills_alcove",
    "matadorRapido": "quick_solo_kills",
    "cacaWard": "ward_takedowns",
    "duroDeMatar": "survived_immob",
    "covardeTorre": "kills_own_turret",
    "guardaCostas": "wards_guarded",
    "nuncaSozinho": "pick_with_ally",
    "farmDaSelva": "jungle_cs10",
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


def construir(rows_by_player, players, min_partidas: int = MIN_PARTIDAS,
              ranking_completo: bool = True) -> list[dict]:
    """Um trofeu por metrica, cada um com o ranking completo do elenco."""
    agregados = _agregar(rows_by_player, players)
    elegiveis = {p: a for p, a in agregados.items() if a["partidas"] >= min_partidas}
    if not elegiveis:
        return []

    # Trofeu de rota so aceita quem tem amostra de rota.
    SO_LANE = {"farmDoDez", "cegoDaRota"}
    MIN_LANE = 8
    # "Torre Madrugadora" premia media de TEMPO: quem derrubou duas torres cedo
    # por acaso lideraria sobre quem derruba sempre. Exige amostra.
    SO_TORRE = {"torreCedo"}
    MIN_TORRE = 10
    # Metrica de selva so vale para quem jogou selva: cobrar farm de selva de um
    # atirador e o mesmo erro que cobrar CS de rota do jungler.
    SO_SELVA = {"farmDaSelva", "reiCaranguejo"}
    MIN_SELVA = 6
    # "Rato de camp" e o inverso: no jungler, comer camp aliado E o farm dele.
    SO_NAOSELVA = {"ratoDeCamp"}
    MIN_NAOSELVA = 8
    # Morte sem culpado depende de um campo que falta em ~12% das partidas.
    SO_BOBA = {"morteBurra"}
    MIN_BOBA = 8

    saida = []
    for key, titulo, grupo, legenda, calc, unidade, casas, maior in TROFEUS:
        linhas = []
        for puuid, acc in elegiveis.items():
            if key in SO_LANE and acc.get("lane_partidas", 0) < MIN_LANE:
                continue
            if key in SO_TORRE and acc.get("torre_partidas", 0) < MIN_TORRE:
                continue
            if key in SO_SELVA and acc.get("selva_partidas", 0) < MIN_SELVA:
                continue
            if key in SO_NAOSELVA and acc.get("naoselva_partidas", 0) < MIN_NAOSELVA:
                continue
            if key in SO_BOBA and acc.get("bobas_partidas", 0) < MIN_BOBA:
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

        # Nas janelas curtas so o podio viaja: o recorte de periodo serve
        # para responder "quem esta pior AGORA", e essa pergunta acaba no
        # terceiro lugar. Guardar os 18 em cada janela triplicaria o payload.
        ranking = linhas if ranking_completo else linhas[:3]
        saida.append({
            "key": key, "titulo": titulo, "grupo": grupo,
            "grupoLabel": GRUPO_LABEL[grupo], "legenda": legenda,
            "unidade": unidade, "casas": casas, "maiorMelhor": maior,
            "campeao": linhas[0], "ranking": ranking,
            "rankingCompleto": ranking_completo,
            "media": _r(media, casas) if casas else int(round(media)),
            "minPartidas": min_partidas,
            "temEvidencia": bool(coluna),
        })
    saida.sort(key=lambda t: (GRUPO_ORDEM[t["grupo"]], t["key"]))
    return saida

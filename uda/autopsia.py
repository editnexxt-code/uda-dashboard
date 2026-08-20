"""A autopsia: nao QUANTO alguem morre, mas COMO.

O paredao ja responde "quem jogou pior". Aqui a pergunta e outra e mais especifica:
de que tipo de dano voce apanha, onde voce se sente seguro para matar, e qual
botao seu dedo castiga. Sao campos que estavam no JSON bruto desde sempre e
nunca tinham virado tela.

Nada aqui e ranking de qualidade -- e anatomia. Por isso quase tudo vira
percentual dentro da propria pessoa, e nao comparacao com o grupo: "43% do que
voce leva e magico" fala de voce, independente de quantas partidas jogou.
"""

from __future__ import annotations

from collections import defaultdict

from .kpi import _r, _safe_div

MIN_PARTIDAS = 5
# Abaixo disso o percentual por habilidade vira ruido: uma partida de 12 minutos
# com 40 usos nao diz qual botao a pessoa prefere.
MIN_USOS_BOTAO = 200


def _n(valor) -> float:
    return float(valor) if valor is not None else 0.0


def construir(rows_by_player, players, min_partidas: int = MIN_PARTIDAS) -> dict:
    acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for puuid, linhas in rows_by_player.items():
        if puuid not in players or not linhas:
            continue
        a = acc[puuid]
        a["partidas"] = len(linhas)
        for r in linhas:
            for campo in ("deaths", "kills", "taken_physical", "taken_magic",
                          "taken_true", "kills_own_turret", "kills_enemy_turret",
                          "spell1_casts", "spell2_casts", "spell3_casts",
                          "pick_with_ally", "damage_taken"):
                try:
                    a[campo] += _n(r[campo])
                except (IndexError, KeyError):
                    pass
            # Morte sem culpado so conta onde o campo esta comprovadamente
            # preenchido -- ele falta em ~14% das partidas, e ali vem 0, o que
            # marcaria TODA morte como boba.
            try:
                dc = _n(r["deaths_by_champs"])
                if dc > 0:
                    a["bobas_partidas"] += 1
                    a["mortes_bobas"] += max(0.0, _n(r["deaths"]) - dc)
            except (IndexError, KeyError):
                pass

    fichas = []
    for puuid, a in acc.items():
        if a["partidas"] < min_partidas:
            continue
        tk = a["taken_physical"] + a["taken_magic"] + a["taken_true"]
        usos = a["spell1_casts"] + a["spell2_casts"] + a["spell3_casts"]
        abates_torre = a["kills_own_turret"] + a["kills_enemy_turret"]
        botoes = [("Q", a["spell1_casts"]), ("W", a["spell2_casts"]),
                  ("E", a["spell3_casts"])]
        favorito = max(botoes, key=lambda x: x[1]) if usos else ("—", 0)
        fichas.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "partidas": int(a["partidas"]),
            # --- de que dano ele apanha
            "temDano": tk > 0,
            "fisico": _r(_safe_div(a["taken_physical"], tk) * 100, 1),
            "magico": _r(_safe_div(a["taken_magic"], tk) * 100, 1),
            "verdadeiro": _r(_safe_div(a["taken_true"], tk) * 100, 1),
            "levadoPorMin": int(round(a["damage_taken"] / max(a["partidas"], 1))),
            # --- onde ele se sente seguro para matar
            "temTorre": abates_torre >= 5,
            "souTorre": int(a["kills_own_turret"]),
            "torreInimiga": int(a["kills_enemy_turret"]),
            "ousadia": _r(_safe_div(a["kills_enemy_turret"], abates_torre) * 100, 1),
            # --- o dedo
            "temBotao": usos >= MIN_USOS_BOTAO,
            "botao": favorito[0],
            "botaoPct": _r(_safe_div(favorito[1], usos) * 100, 1),
            "botoes": [{"t": k, "pct": _r(_safe_div(v, usos) * 100, 1)}
                       for k, v in botoes],
            # --- morte sem culpado
            "temBobas": a["bobas_partidas"] >= 8,
            "bobas": int(a["mortes_bobas"]),
            "mortes": int(a["deaths"]),
            # --- coragem: fatia dos abates fechada com aliado junto
            "acompanhado": _r(_safe_div(a["pick_with_ally"],
                                        max(a["kills"], 1)) * 100, 1),
        })

    fichas.sort(key=lambda x: -x["magico"])
    return {
        "fichas": fichas,
        "minPartidas": min_partidas,
        "minUsos": MIN_USOS_BOTAO,
    }

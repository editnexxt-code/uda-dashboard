"""Mural do mes: o UDA e o AFUNDADO de cada mes fechado.

Reaproveita o score mensal de evolucao.py -- o mesmo UDA Score do ranking geral,
so que recalculado dentro do mes. Isso importa: eleger o rei do mes pela media
geral premiaria quem ja e bom desde sempre, e o mural deixaria de contar
qualquer historia nova.

O mes corrente aparece marcado como "em disputa", porque ainda pode virar.
"""

from __future__ import annotations

import zlib

import time
from collections import defaultdict

from .evolucao import score_mensal
from .kpi import _accumulate, _blank, _finalize, _r, _safe_div

MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

# Um mes com pouquissimo jogo elege campeao no ruido. Melhor nem abrir o mural.
MIN_PARTIDAS_MES = 12


def _rotulo(mes: str) -> str:
    try:
        ano, num = mes.split("-")
        return f"{MESES_PT[int(num) - 1]} de {ano}"
    except (ValueError, IndexError):
        return mes


# Cada entrada: (campo, maior_e_melhor, formatador, frases).
# A escolha e pela distancia RELATIVA a media do mes, para que metricas de
# escalas diferentes (KDA ~2, dano ~700) possam competir entre si.
DESTAQUE_REI = [
    ("winrate", True, lambda v: f"{v:.0f}%",
     ["ganhou {v} das partidas enquanto o resto do grupo afundava",
      "{v} de vitórias enquanto a média da casa era {m}",
      "carregou o mês com {v} de aproveitamento"]),
    ("kda", True, lambda v: f"{v:.2f}".replace(".", ","),
     ["KDA de {v} contra os {m} da média — jogou outro jogo",
      "fechou o mês com KDA {v}, quase o dobro do resto",
      "KDA {v}. O grupo inteiro ficou em {m}"]),
    ("kills", True, lambda v: f"{v:.1f}".replace(".", ","),
     ["{v} abates por partida, contra {m} do resto do elenco",
      "passou o mês colecionando abate: {v} por jogo"]),
    ("kp", True, lambda v: f"{v:.0f}%",
     ["participou de {v} dos abates do time — não teve briga sem ele",
      "{v} de participação: onde teve confusão, ele estava"]),
    ("dmg_min", True, lambda v: f"{v:.0f}",
     ["{v} de dano por minuto, contra {m} da média do grupo",
      "despejou {v} de dano por minuto e não pediu desculpa"]),
]

DESTAQUE_AFUNDADO = [
    ("deaths", False, lambda v: f"{v:.1f}".replace(".", ","),
     ["morreu {v} vezes por partida, contra {m} do resto do elenco",
      "{v} mortes por jogo. O inimigo agradeceu o mês inteiro",
      "entregou {v} vezes por partida e chamou de azar"]),
    ("winrate", False, lambda v: f"{v:.0f}%",
     ["ganhou só {v} das partidas enquanto a casa fazia {m}",
      "{v} de aproveitamento. Deu para levantar a bandeira branca"]),
    ("kda", False, lambda v: f"{v:.2f}".replace(".", ","),
     ["KDA de {v} contra os {m} da média — ficou para trás sozinho",
      "fechou o mês em KDA {v}. Dá para fazer melhor de olho fechado"]),
    ("kp", False, lambda v: f"{v:.0f}%",
     ["participou de só {v} dos abates: o time brigou sem ele",
      "{v} de participação. Estava logado, ao menos"]),
    ("dmg_min", False, lambda v: f"{v:.0f}",
     ["{v} de dano por minuto, contra {m} da média. Presença simbólica",
      "deu {v} de dano por minuto e ainda reclamou do time"]),
]


def _motivo(stats, media, tabela, semente) -> str:
    """A frase do maior desvio contra a media do mes, variante estavel."""
    melhor = None
    for campo, maior, fmt, frases in tabela:
        v, m = float(stats.get(campo) or 0), float(media.get(campo) or 0)
        if not m:
            continue
        rel = (v - m) / m
        if not maior:
            rel = -rel
        if rel > 0 and (melhor is None or rel > melhor[0]):
            melhor = (rel, campo, fmt, frases, v, m)
    if melhor is None:                      # ninguem se destacou em nada
        return (f"{stats.get('winrate', 0):.0f}% de vitórias em "
                f"{stats.get('games', 0)} partidas")
    _, campo, fmt, frases, v, m = melhor
    i = zlib.crc32(semente.encode("utf-8")) % len(frases)
    return frases[i].format(v=fmt(v), m=fmt(m))


def _stats_do_mes(linhas) -> dict:
    acc = _blank()
    for row in linhas:
        _accumulate(acc, row)
    return _finalize(acc)


def _destaques(por_jogador: dict, players: dict) -> list[dict]:
    """Pequenos titulos do mes. So entra quem de fato pontuou."""
    specs = [
        ("Mais abates", lambda s: s["total_kills"], "abates", 0, True, "gloria"),
        ("Melhor KDA", lambda s: s["kda"], "de KDA", 2, True, "gloria"),
        ("Mais partidas", lambda s: s["games"], "partidas", 0, True, "curiosidade"),
        ("Mais mortes", lambda s: s["total_deaths"], "mortes", 0, True, "vergonha"),
        ("Pior aproveitamento", lambda s: s["winrate"], "% de vitorias", 0, False, "vergonha"),
        ("Mais dano por minuto", lambda s: s["dmg_min"], "dano/min", 0, True, "gloria"),
    ]
    saida = []
    for titulo, calc, unidade, casas, maior, grupo in specs:
        pool = [(p, s) for p, s in por_jogador.items() if s["games"] > 0 and p in players]
        if len(pool) < 2:
            continue
        alvo = (max if maior else min)(pool, key=lambda kv: calc(kv[1]))
        valor = calc(alvo[1])
        if maior and not valor:
            continue
        saida.append({
            "titulo": titulo, "grupo": grupo,
            "player": players[alvo[0]]["gameName"], "icon": players[alvo[0]]["icon"],
            "valor": _r(valor, casas) if casas else int(round(valor)),
            "unidade": unidade,
        })
    return saida


def _campeao_do_mes(linhas, champ_index) -> dict | None:
    cont: dict[int, dict] = defaultdict(lambda: {"n": 0, "v": 0, "nome": ""})
    for row in linhas:
        item = cont[row["champion_id"]]
        item["n"] += 1
        item["v"] += 1 if row["win"] else 0
        item["nome"] = row["champion_name"]
    if not cont:
        return None
    cid, dados = max(cont.items(), key=lambda kv: kv[1]["n"])
    return {
        "championId": cid, "champion": dados["nome"],
        "partidas": dados["n"],
        "winrate": _r(_safe_div(dados["v"], dados["n"]) * 100, 1),
    }


def construir(rows_by_player, players, min_games: int,
              champ_index=None) -> list[dict]:
    meses = score_mensal(rows_by_player, players, min_games)
    if not meses:
        return []

    # Reagrupa as linhas por mes uma vez so, para nao varrer o elenco por mes.
    linhas_mes: dict[str, list] = defaultdict(list)
    por_jog_mes: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for puuid, linhas in rows_by_player.items():
        if puuid not in players:
            continue
        for row in linhas:
            mes = time.strftime("%Y-%m", time.localtime(row["game_creation"] / 1000))
            linhas_mes[mes].append(row)
            por_jog_mes[mes][puuid].append(row)

    atual = time.strftime("%Y-%m")
    saida = []
    for bloco in meses:
        mes = bloco["mes"]
        linhas = linhas_mes.get(mes, [])
        partidas = len({r["match_id"] for r in linhas})
        if partidas < MIN_PARTIDAS_MES or len(bloco["entradas"]) < 2:
            continue

        stats_jog = {p: _stats_do_mes(ls) for p, ls in por_jog_mes[mes].items()}
        ordenado = bloco["entradas"]           # ja vem ordenado por score desc

        def ficha(entrada, motivo_calc):
            s = stats_jog.get(entrada["puuid"], {})
            return {
                "puuid": entrada["puuid"], "gameName": entrada["gameName"],
                "icon": players[entrada["puuid"]]["icon"],
                "score": entrada["score"], "games": entrada["games"],
                "winrate": s.get("winrate", 0), "kda": s.get("kda", 0),
                "kills": s.get("kills", 0), "deaths": s.get("deaths", 0),
                "assists": s.get("assists", 0), "dmg_min": s.get("dmg_min", 0),
                "motivo": motivo_calc(s),
            }

        def _pt(valor, casas=1):
            """Virgula decimal: o motivo e texto pronto, nao passa pelo nf() da tela."""
            return f"{valor:.{casas}f}".replace(".", ",")

        # A media do mes precisa existir ANTES das fichas: e contra ela que o
        # motivo de cada um e escolhido.
        team = _stats_do_mes(linhas)
        rei = ficha(ordenado[0], lambda s: _motivo(
            s, team, DESTAQUE_REI, mes + ordenado[0]["puuid"]))
        afundado = ficha(ordenado[-1], lambda s: _motivo(
            s, team, DESTAQUE_AFUNDADO, mes + ordenado[-1]["puuid"]))
        saida.append({
            "mes": mes, "rotulo": _rotulo(mes), "emDisputa": mes == atual,
            "partidas": partidas, "participacoes": len(linhas),
            "winrate": team["winrate"], "kda": team["kda"],
            "rei": rei, "afundado": afundado,
            "podio": [{"gameName": e["gameName"], "score": e["score"],
                       "icon": players[e["puuid"]]["icon"], "pos": e["rank"]}
                      for e in ordenado[:3]],
            "destaques": _destaques(stats_jog, players),
            "campeao": _campeao_do_mes(linhas, champ_index),
            "concorrentes": len(ordenado),
        })

    saida.sort(key=lambda m: m["mes"], reverse=True)
    return saida


def resumo_titulos(mural: list[dict]) -> list[dict]:
    """Quantas vezes cada um foi rei e quantas foi afundado, no periodo inteiro."""
    cont: dict[str, dict] = {}
    for mes in mural:
        if mes["emDisputa"]:
            continue                      # mes aberto ainda nao vale titulo
        for chave, campo in (("reis", "rei"), ("afundadas", "afundado")):
            p = mes[campo]
            alvo = cont.setdefault(p["puuid"], {
                "gameName": p["gameName"], "icon": p["icon"],
                "reis": 0, "afundadas": 0, "meses": []})
            alvo[chave] += 1
            alvo["meses"].append({"mes": mes["mes"], "rotulo": mes["rotulo"],
                                  "tipo": campo})
    saida = [dict(v, puuid=k) for k, v in cont.items()]
    saida.sort(key=lambda d: (-d["reis"], d["afundadas"]))
    return saida

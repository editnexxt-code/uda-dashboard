"""Mural do mes: o UDA e o AFUNDADO de cada mes fechado.

Reaproveita o score mensal de evolucao.py -- o mesmo UDA Score do ranking geral,
so que recalculado dentro do mes. Isso importa: eleger o rei do mes pela media
geral premiaria quem ja e bom desde sempre, e o mural deixaria de contar
qualquer historia nova.

O mes corrente aparece marcado como "em disputa", porque ainda pode virar.
"""

from __future__ import annotations

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

        rei = ficha(ordenado[0], lambda s: (
            f"{s.get('winrate', 0):.0f}% de vitórias e KDA "
            f"{_pt(s.get('kda', 0), 2)} em {ordenado[0]['games']} partidas"))
        afundado = ficha(ordenado[-1], lambda s: (
            f"{s.get('winrate', 0):.0f}% de vitórias e "
            f"{_pt(s.get('deaths', 0))} mortes por partida"))

        team = _stats_do_mes(linhas)
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

"""O algoz de rota: contra qual campeao cada um apanha na fase de rota.

A Riot entrega o oponente direto de rota em cada partida (opponent_champ_id, do
challenge `laneOpponent`), e junto o saldo de CS contra ele. E o dado mais
especifico de zoacao que existe no banco: nao e "voce joga mal", e "voce nao sabe
jogar contra Yasuo, e aqui estao os quatro duelos que provam".

So conta rota nomeada -- topo, meio e atirador. Selva e suporte nao tem oponente
direto de farm, e medir os dois junto viraria ruido.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .kpi import _r, _safe_div

ROTAS_DE_LANE = ("TOP", "MIDDLE", "BOTTOM")
MIN_DUELOS_GRUPO = 6      # para o campeao entrar no placar do grupo
MIN_DUELOS_PESSOA = 4     # para virar o algoz de alguem
TOPO = 10


def _n(valor) -> float:
    return float(valor) if valor is not None else 0.0


def _ficha(cid: int, nome: str, dados: dict) -> dict:
    n = dados["n"]
    return {
        "championId": cid, "champion": nome, "duelos": n,
        "csDiff": _r(_safe_div(dados["cs"], n), 0),
        "mortes": _r(_safe_div(dados["d"], n), 1),
        "abates": _r(_safe_div(dados["k"], n), 1),
        "winrate": _r(_safe_div(dados["v"], n) * 100, 1),
        "matchIds": dados["ids"][:3],
    }


def _acumular(linhas) -> dict[int, dict]:
    cont: dict[int, dict] = defaultdict(
        lambda: {"n": 0, "cs": 0.0, "d": 0.0, "k": 0.0, "v": 0, "nome": "", "ids": []}
    )
    for r in linhas:
        cid = r["opponent_champ_id"]
        if not cid or (r["position"] or "").upper() not in ROTAS_DE_LANE:
            continue
        item = cont[cid]
        item["n"] += 1
        item["cs"] += _n(r["cs_diff"])
        item["d"] += _n(r["deaths"])
        item["k"] += _n(r["kills"])
        item["v"] += 1 if r["win"] else 0
        item["nome"] = r["opponent_champ"] or ""
        # as piores primeiro: a evidencia deve mostrar o duelo mais humilhante
        item["ids"].append((_n(r["cs_diff"]), r["match_id"]))
    for item in cont.values():
        item["ids"] = [m for _, m in sorted(item["ids"])]
    return cont


def construir(rows, players, verbose: bool = True) -> dict:
    geral = _acumular(rows)
    if not geral:
        return {}

    duros = sorted(
        (_ficha(cid, d["nome"], d) for cid, d in geral.items() if d["n"] >= MIN_DUELOS_GRUPO),
        key=lambda f: f["csDiff"])[:TOPO]
    faceis = sorted(
        (_ficha(cid, d["nome"], d) for cid, d in geral.items() if d["n"] >= MIN_DUELOS_GRUPO),
        key=lambda f: -f["csDiff"])[:TOPO]

    # por jogador
    por_jogador = []
    linhas_por_jog: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["puuid"] in players:
            linhas_por_jog[r["puuid"]].append(r)

    for puuid, linhas in linhas_por_jog.items():
        cont = _acumular(linhas)
        elegiveis = [(cid, d) for cid, d in cont.items() if d["n"] >= MIN_DUELOS_PESSOA]
        if not elegiveis:
            continue
        pior = min(elegiveis, key=lambda kv: _safe_div(kv[1]["cs"], kv[1]["n"]))
        melhor = max(elegiveis, key=lambda kv: _safe_div(kv[1]["cs"], kv[1]["n"]))
        ficha_pior = _ficha(pior[0], pior[1]["nome"], pior[1])
        por_jogador.append({
            "puuid": puuid,
            "gameName": players[puuid]["gameName"],
            "icon": players[puuid]["icon"],
            "duelosTotais": sum(d["n"] for _, d in elegiveis),
            "algoz": ficha_pior,
            # so vira "fregues" se o saldo for de fato positivo
            "fregues": _ficha(melhor[0], melhor[1]["nome"], melhor[1])
            if _safe_div(melhor[1]["cs"], melhor[1]["n"]) > 0 else None,
        })
    por_jogador.sort(key=lambda j: j["algoz"]["csDiff"])

    if verbose and duros:
        print(f"  rota: {len(duros)} algozes do grupo, "
              f"{len(por_jogador)} jogadores com confronto marcado")
    return {
        "duros": duros, "faceis": faceis, "porJogador": por_jogador,
        "minGrupo": MIN_DUELOS_GRUPO, "minPessoa": MIN_DUELOS_PESSOA,
    }

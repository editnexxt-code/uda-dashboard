"""Contra o servidor: a unica aba em que a regua nao e a UDA.

Todo o resto do painel compara voces com voces, porque a Riot nao publica media
agregada do BR. A API de Desafios e a excecao: cada desafio vem com o PERCENTIL
do jogador contra a base inteira. percentil 0,037 = top 3,7% do servidor.

Aqui percentil MENOR e melhor. E nivel NONE nao e "ruim nisso": e "nunca
pontuou", com percentil 100 por definicao. Misturar os dois transformaria "nunca
joguei Teemo" em "sou pessimo de Teemo", entao os dois viram blocos separados.
"""

from __future__ import annotations

import json
import sqlite3

from .desafios import CATEGORIA_PT, NIVEL_PT, ORDEM_NIVEL, SEM_NIVEL
from .kpi import _r

TOPO = 3
# Desafio de valor zero nao diz nada mesmo com nivel atribuido.
MIN_VALOR = 1


def _pct(v) -> float | None:
    return None if v is None else _r(v * 100, 2)


def construir(conn: sqlite3.Connection, players: dict,
              champ_index: dict | None = None) -> dict:
    nomes = {r["challenge_id"]: r for r in
             conn.execute("SELECT * FROM challenge_names")}
    if not nomes:
        return {"fichas": []}

    por_jogador: dict[str, list] = {}
    for r in conn.execute("SELECT * FROM challenges"):
        por_jogador.setdefault(r["puuid"], []).append(r)

    totais = {r["puuid"]: r for r in conn.execute("SELECT * FROM challenge_totals")}
    maestria: dict[str, list] = {}
    for r in conn.execute("SELECT * FROM mastery ORDER BY pontos DESC"):
        maestria.setdefault(r["puuid"], []).append(r)

    def ficha_desafio(r) -> dict:
        n = nomes.get(r["challenge_id"]) or {}
        return {"id": r["challenge_id"],
                "nome": n["nome"] if n else f"#{r['challenge_id']}",
                "desc": (n["descricao"] if n else "") or "",
                "nivel": r["nivel"], "nivelPt": NIVEL_PT.get(r["nivel"], r["nivel"]),
                "pct": _pct(r["percentil"]), "valor": _r(r["valor"] or 0, 0)}

    # --- mediana da UDA por desafio -------------------------------------
    # O percentil da Riot e por (desafio, FAIXA), nao por pessoa: quem esta em
    # Ouro num desafio recebe o mesmo numero de todo mundo em Ouro nele, com
    # valor 357 ou 169. Medido: 5,5 percentis distintos por desafio entre 18
    # pessoas -- ou seja, ele separa faixas, nao individuos. Sem desempate, os
    # MESMOS tres desafios apareciam no cartao de quase todo o elenco.
    # O valor bruto e o que individualiza, e a mediana do grupo torna valores de
    # desafios diferentes comparaveis entre si.
    valores: dict[int, list[float]] = {}
    for linhas in por_jogador.values():
        for r in linhas:
            if r["percentil"] is not None and (r["valor"] or 0) >= MIN_VALOR:
                valores.setdefault(r["challenge_id"], []).append(float(r["valor"]))
    medianas = {}
    for cid, vs in valores.items():
        vs.sort()
        medianas[cid] = vs[len(vs) // 2] or 1.0

    def desvio(r) -> float:
        """Quanto o valor foge da mediana da UDA, em fracao. + acima, - abaixo."""
        med = medianas.get(r["challenge_id"]) or 1.0
        return (float(r["valor"] or 0) - med) / med

    fichas = []
    for puuid, info in players.items():
        linhas = por_jogador.get(puuid) or []
        if not linhas:
            continue
        uteis = [r for r in linhas
                 if r["challenge_id"] and r["percentil"] is not None
                 and (r["valor"] or 0) >= MIN_VALOR
                 and r["challenge_id"] in nomes]
        com_nivel = [r for r in uteis if r["nivel"] != SEM_NIVEL]
        nunca = [r for r in linhas
                 if r["nivel"] == SEM_NIVEL and r["challenge_id"] in nomes]

        # Ordena pela FAIXA, nao pelo percentil. O percentil e propriedade do
        # par (desafio, faixa): "Chegou Nem Perto" devolve 0,397 para as 18
        # pessoas porque as 18 estao em Ouro nele. Ordenar por percentil fazia
        # os mesmos tres desafios aparecerem no cartao do elenco inteiro -- e,
        # pior, no bloco de PONTO FRACO saiam desafios em que a pessoa estava
        # ate 505% ACIMA da mediana da UDA.
        # A faixa varia de verdade: 4,83 faixas distintas por desafio entre 18
        # pessoas. O desvio contra a mediana do grupo desempata.
        def ordem(r):
            return (ORDEM_NIVEL.index(r["nivel"])
                    if r["nivel"] in ORDEM_NIVEL else 0)
        melhores = sorted(com_nivel, key=lambda r: (-ordem(r), -desvio(r)))
        piores = sorted(com_nivel, key=lambda r: (ordem(r), desvio(r)))

        tot = totais.get(puuid)
        cats = []
        if tot and tot["categorias"]:
            try:
                for chave, v in json.loads(tot["categorias"]).items():
                    cats.append({"cat": CATEGORIA_PT.get(chave, chave),
                                 "nivel": v.get("level") or SEM_NIVEL,
                                 "nivelPt": NIVEL_PT.get(v.get("level") or SEM_NIVEL, "-"),
                                 "pontos": v.get("current") or 0,
                                 "pct": _pct(v.get("percentile"))})
            except (ValueError, TypeError, AttributeError):
                pass
        cats.sort(key=lambda c: ORDEM_NIVEL.index(c["nivel"])
                  if c["nivel"] in ORDEM_NIVEL else 0, reverse=True)

        top = maestria.get(puuid) or []

        def ficha(r):
            d = ficha_desafio(r)
            d["vsGrupo"] = _r(desvio(r) * 100, 0)
            return d

        fichas.append({
            "puuid": puuid, "gameName": info["gameName"], "icon": info["icon"],
            "pontos": tot["pontos"] if tot else 0,
            "maximo": tot["maximo"] if tot else 0,
            "nivel": tot["nivel"] if tot else SEM_NIVEL,
            "nivelPt": NIVEL_PT.get(tot["nivel"] if tot else SEM_NIVEL, "-"),
            "pct": _pct(tot["percentil"]) if tot else None,
            "categorias": cats,
            "melhores": [ficha(r) for r in melhores[:TOPO]],
            "piores": [ficha(r) for r in piores[:TOPO]],
            "nuncaPontuou": len(nunca),
            "avaliados": len(com_nivel),
            "maestria": [{"cid": m["champion_id"], "nivel": m["nivel"],
                          "pontos": m["pontos"]} for m in top[:5]],
        })

    # Ordena por PONTOS, nao por percentil: o percentil do total vem bucketizado
    # por faixa -- so tres valores no elenco inteiro -- e empilharia dez pessoas
    # no mesmo lugar, com o desempate saindo da ordem do dicionario.
    fichas.sort(key=lambda f: -f["pontos"])

    return {"fichas": fichas, "topo": TOPO}

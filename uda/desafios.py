"""Desafios e maestria: a primeira regua deste painel que NAO e o proprio grupo.

O README sempre disse que a Riot nao publica estatistica agregada do BR, e por
isso o UDA Score compara voces com voces. Isso continua verdade para KDA, dano e
CS -- mas a API de Desafios entrega, por desafio, o PERCENTIL do jogador contra
toda a base. `percentile: 0.037` quer dizer top 3,7% do servidor.

Ou seja: da para dizer "nisso aqui voce e pior que seis em cada dez jogadores do
BR", com numero da propria Riot. E o unico lugar do painel onde a acusacao vem
de fora do grupo.

Custo: duas chamadas por conta (desafios + maestria) mais uma de configuracao.
Sao ~37 chamadas por execucao, contra as 1700 da timeline. Barato o bastante
para refazer inteiro toda vez, e por isso aqui nao ha marca de progresso.
"""

from __future__ import annotations

import sqlite3
import time

IDIOMA = "pt_BR"
# Nivel NONE quer dizer "nunca pontuou". O percentil vem 100% nesses casos, o que
# NAO e "e ruim nisso" -- e "nunca tentou". Sao coisas diferentes e viram blocos
# diferentes na tela.
SEM_NIVEL = "NONE"
ORDEM_NIVEL = ["NONE", "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
               "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
NIVEL_PT = {"NONE": "—", "IRON": "Ferro", "BRONZE": "Bronze", "SILVER": "Prata",
            "GOLD": "Ouro", "PLATINUM": "Platina", "DIAMOND": "Diamante",
            "MASTER": "Mestre", "GRANDMASTER": "Grão-Mestre",
            "CHALLENGER": "Desafiante"}
CATEGORIA_PT = {"VETERANCY": "Veterania", "COLLECTION": "Coleção",
                "EXPERTISE": "Perícia", "TEAMWORK": "Trabalho em equipe",
                "IMAGINATION": "Imaginação"}


def coletar(client, conn: sqlite3.Connection, puuids: list[str],
            verbose: bool = True) -> int:
    plataforma = f"{client.platform}.api.riotgames.com"
    agora = int(time.time())

    cfg = client._get(plataforma, "/lol/challenges/v1/challenges/config",
                      allow_404=True) or []
    nomes = []
    for item in cfg:
        pt = (item.get("localizedNames") or {}).get(IDIOMA) or {}
        if pt.get("name"):
            nomes.append({"challenge_id": item.get("id"), "nome": pt["name"],
                          "descricao": pt.get("shortDescription", ""),
                          "categoria": item.get("category") or ""})
    if nomes:
        conn.executemany(
            "INSERT OR REPLACE INTO challenge_names "
            "(challenge_id, nome, descricao, categoria) "
            "VALUES (:challenge_id, :nome, :descricao, :categoria)", nomes)

    feitos = 0
    for puuid in puuids:
        dados = client._get(plataforma,
                            f"/lol/challenges/v1/player-data/{puuid}",
                            allow_404=True)
        if dados:
            conn.execute("DELETE FROM challenges WHERE puuid=?", (puuid,))
            linhas = [{"puuid": puuid, "challenge_id": d.get("challengeId"),
                       "nivel": d.get("level") or SEM_NIVEL,
                       "percentil": d.get("percentile"),
                       "valor": d.get("value") or 0}
                      for d in (dados.get("challenges") or [])
                      if d.get("challengeId") is not None]
            if linhas:
                conn.executemany(
                    "INSERT OR REPLACE INTO challenges "
                    "(puuid, challenge_id, nivel, percentil, valor) "
                    "VALUES (:puuid, :challenge_id, :nivel, :percentil, :valor)",
                    linhas)
            tot = dados.get("totalPoints") or {}
            cats = dados.get("categoryPoints") or {}
            conn.execute(
                "INSERT OR REPLACE INTO challenge_totals "
                "(puuid, nivel, pontos, maximo, percentil, categorias, atualizado) "
                "VALUES (?,?,?,?,?,?,?)",
                (puuid, tot.get("level") or SEM_NIVEL, tot.get("current") or 0,
                 tot.get("max") or 0, tot.get("percentile"),
                 __import__("json").dumps(cats, separators=(",", ":")), agora))
            feitos += 1

        # --- maestria
        top = client._get(
            plataforma,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top",
            params={"count": 8}, allow_404=True) or []
        if top:
            conn.execute("DELETE FROM mastery WHERE puuid=?", (puuid,))
            conn.executemany(
                "INSERT OR REPLACE INTO mastery "
                "(puuid, champion_id, nivel, pontos, ultima_vez) VALUES (?,?,?,?,?)",
                [(puuid, m.get("championId"), m.get("championLevel") or 0,
                  m.get("championPoints") or 0, m.get("lastPlayTime") or 0)
                 for m in top])
        conn.commit()

    if verbose:
        print(f"  desafios: {feitos} contas, {len(nomes)} desafios catalogados")
    return feitos

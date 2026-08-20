"""Coleta os dados da Riot e alimenta o SQLite.

Estrategia para gastar poucas requisicoes:
  - partida ja no banco nunca e baixada de novo;
  - varios membros da UDA na mesma partida = 1 download so;
  - runs seguintes pedem apenas ids criados depois da ultima partida conhecida.
"""

from __future__ import annotations

import sqlite3
import time

from .config import Settings
from .riot import RiotClient, RiotError
from . import store


def _now() -> int:
    return int(time.time())


def resolve_players(client: RiotClient, conn: sqlite3.Connection,
                    settings: Settings) -> dict[str, dict]:
    """Riot ID -> PUUID + elo + nivel. Retorna {puuid: info}."""
    resolved: dict[str, dict] = {}
    with_rank = 0
    print(f"\n[1/3] Resolvendo {len(settings.players)} contas (Account-V1)")

    for player in settings.players:
        account = client.account_by_riot_id(player.game_name, player.tag_line)
        if not account or not account.get("puuid"):
            print(f"  x  {player.riot_id:<26} NAO ENCONTRADO (confira o Riot ID)")
            continue

        puuid = account["puuid"]
        game_name = account.get("gameName", player.game_name)
        tag_line = account.get("tagLine", player.tag_line)

        summoner = client.summoner_by_puuid(puuid)
        if summoner is None:
            # Account-V1 acha o Riot ID em qualquer regiao; so Summoner-V4 no br1
            # prova que existe invocador de LoL AQUI.
            print(f"  !! {game_name}#{tag_line}: SEM invocador de LoL em "
                  f"{settings.platform.upper()} -- conta de outro servidor, ou "
                  "de alguem que so joga Valorant/TFT. Rode 'python verificar.py'.")
            summoner = {}
        store.upsert_player(
            conn, puuid, game_name, tag_line,
            summoner.get("profileIconId"), summoner.get("summonerLevel"), _now(),
        )
        entries = client.league_entries(puuid)
        with_rank += 1 if entries else 0
        store.replace_ranks(conn, puuid, entries, _now())
        conn.commit()

        resolved[puuid] = {"game_name": game_name, "tag_line": tag_line}
        level = summoner.get("summonerLevel", "?")
        elo = "sem elo"
        for e in entries:
            if e.get("queueType") == "RANKED_SOLO_5x5":
                elo = f"{e.get('tier','')} {e.get('rank','')} ({e.get('leaguePoints',0)} PDL)"
        print(f"  ok {game_name}#{tag_line:<10} nivel {level:<5} {elo}")

    if not resolved:
        raise RiotError("Nenhuma conta foi resolvida. Verifique os Riot IDs e a chave.")
    if with_rank == 0:
        print(
            "\n  AVISO: nenhum jogador retornou dados de elo (League-V4).\n"
            "  Ou ninguem do grupo esta rankeado nesta temporada, ou o endpoint\n"
            "  /lol/league/v4/entries/by-puuid mudou. A coluna Elo ficara vazia,\n"
            "  mas todo o resto do dashboard continua funcionando normalmente."
        )
    return resolved


def collect_match_ids(client: RiotClient, conn: sqlite3.Connection,
                      resolved: dict[str, dict], settings: Settings) -> list[str]:
    print(f"\n[2/3] Listando partidas (Match-V5 / {settings.routing})")
    known = store.known_match_ids(conn)
    wanted: list[str] = []
    seen: set[str] = set()

    for puuid, info in resolved.items():
        name = f"{info['game_name']}#{info['tag_line']}"
        # "Ja teve carga completa?" NAO pode ser deduzido de existir partida no
        # banco. Quem entra no elenco depois ja tem participacoes gravadas -- elas
        # vieram das partidas dos outros, com tracked=0, e o refresh_tracked as
        # adota assim que a pessoa aparece em players.json. Deduzir dai jogava o
        # membro novo direto no modo incremental e o historico SOLO dele nunca
        # chegava. Por isso a marca e explicita, gravada so quando a carga termina.
        completo = store.get_meta(conn, f"fullload:{puuid}") == "1"
        last = store.last_match_time(conn, puuid) if completo else None
        # startTime da Riot e em segundos; recua 1h para nao perder nada na borda.
        start_time = (last // 1000 - 3600) if last else None
        target = settings.match_count if not completo else 100

        ids: list[str] = []
        start = 0
        while len(ids) < target:
            batch = client.match_ids(
                puuid, count=min(100, target - len(ids)),
                start=start, start_time=start_time,
            )
            if not batch:
                break
            ids.extend(batch)
            start += len(batch)
            if len(batch) < 100:
                break

        novos = [m for m in ids if m not in known and m not in seen]
        seen.update(novos)
        wanted.extend(novos)
        marca = "primeira carga" if not completo else "incremental"
        print(f"  {name:<28} {len(ids):>4} ids ({marca}) -> {len(novos):>4} novos")
        # A marca so entra depois da listagem inteira: se cair no meio, a proxima
        # execucao refaz a carga completa em vez de assumir que ja veio tudo.
        if not completo and ids:
            store.set_meta(conn, f"fullload:{puuid}", "1")

    # Registra tudo na fila ANTES de baixar. Se o processo cair no meio, o run
    # seguinte retoma pelos que ficaram com done=0 em vez de perde-los.
    store.enqueue_matches(conn, wanted, _now())
    pendentes = store.pending_matches(conn)
    atrasados = len(pendentes) - len(wanted)
    if atrasados > 0:
        print(f"  + {atrasados} partidas pendentes de uma coleta interrompida")
    print(f"  total a baixar: {len(pendentes)}")
    return pendentes


def download_matches(client: RiotClient, conn: sqlite3.Connection,
                     match_ids: list[str], tracked: set[str]) -> int:
    if not match_ids:
        print("\n[3/3] Nada novo para baixar. Banco ja esta em dia.")
        return 0

    total = len(match_ids)
    eta_min = total / 95 * 2  # limite de 100 req / 2 min
    print(f"\n[3/3] Baixando {total} partidas (~{eta_min:.0f} min por causa do rate limit)")

    saved = 0
    started = time.monotonic()
    for i, match_id in enumerate(match_ids, 1):
        try:
            match = client.match(match_id)
        except RiotError as exc:
            print(f"  ! {match_id}: {exc}")
            continue
        if match and store.save_match(conn, match, tracked):
            saved += 1
            store.mark_done(conn, match_id)
        elif match is None:
            # 404: partida sumiu do historico da Riot. Nao adianta tentar de novo.
            store.mark_done(conn, match_id)
        if i % 20 == 0 or i == total:
            conn.commit()
            elapsed = time.monotonic() - started
            rate = i / elapsed if elapsed else 0
            restante = (total - i) / rate / 60 if rate else 0
            # flush explicito: sem isso o progresso some quando a saida vai
            # para arquivo (atualizar.bat), e parece que travou.
            print(f"  {i:>5}/{total}  ({saved} salvas)  ~{restante:.0f} min restantes",
                  flush=True)
    conn.commit()
    return saved


def run(settings: Settings, conn: sqlite3.Connection) -> None:
    client = RiotClient(settings.api_key, settings.platform, settings.routing)
    started = time.monotonic()

    roster = {(p.game_name.lower(), p.tag_line.lower()) for p in settings.players}
    saiu = store.prune_players(conn, roster)
    if saiu:
        print(f"\nRemovidos do banco (nao estao mais em players.json): {', '.join(saiu)}")

    resolved = resolve_players(client, conn, settings)
    tracked = set(resolved)
    match_ids = collect_match_ids(client, conn, resolved, settings)
    saved = download_matches(client, conn, match_ids, tracked)

    # Recalcula tracked a partir da tabela players: pega o historico ja gravado
    # de quem entrou no roster depois, e conserta runs em que alguem falhou.
    corrigidos = store.refresh_tracked(conn)
    if corrigidos:
        print(f"  {corrigidos} participacoes reassociadas ao roster atual")

    store.set_meta(conn, "last_fetch", str(_now()))
    store.set_meta(conn, "last_fetch_saved", str(saved))
    conn.commit()

    total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(
        f"\nColeta concluida em {(time.monotonic() - started) / 60:.1f} min | "
        f"{client.calls} chamadas a API | {client.throttled} bloqueios 429 | "
        f"{total_matches} partidas no banco"
    )

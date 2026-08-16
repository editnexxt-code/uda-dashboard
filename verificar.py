"""Auditoria das 12 contas ANTES de gastar a coleta inteira.

Para cada Riot ID responde, com prova:
  - o Riot ID existe?
  - tem invocador de League of Legends no servidor BR?
  - tem historico de partidas de LoL?
  - de que servidor sao as partidas? (le o prefixo do matchId: BR1_ / NA1_ / EUW1_ ...)
  - qual o elo e as filas que joga?

Gasta ~50 requisicoes (menos de 1 minuto). Nao escreve nada no banco.

    python verificar.py
    python verificar.py --sugerir     tenta variacoes de tag para os que falharem
"""

from __future__ import annotations

import sys
from collections import Counter

from uda import config
from uda.kpi import QUEUE_NAMES
from uda.riot import RiotClient, RiotError

OK, AVISO, ERRO = "  OK  ", " AVISO", " ERRO "
LINE = "=" * 74


def verificar(client: RiotClient, player, platform: str) -> dict:
    r: dict = {"riot_id": player.riot_id, "status": ERRO, "notas": []}

    try:
        account = client.account_by_riot_id(player.game_name, player.tag_line)
    except RiotError as exc:
        r["notas"].append(f"falha na consulta: {exc}")
        return r

    if not account or not account.get("puuid"):
        r["notas"].append("Riot ID NAO EXISTE (nome ou tag errados)")
        return r

    r["puuid"] = account["puuid"]
    real_id = f"{account.get('gameName')}#{account.get('tagLine')}"
    r["riot_id_real"] = real_id
    if real_id.lower() != player.riot_id.lower():
        r["notas"].append(f"a Riot devolve como '{real_id}'")

    # --- tem invocador de LoL neste servidor?
    summoner = client.summoner_by_puuid(account["puuid"])
    if summoner:
        r["nivel"] = summoner.get("summonerLevel")
    else:
        r["notas"].append(
            f"SEM invocador de LoL em {platform.upper()} "
            "(conta de outro servidor, ou so joga Valorant/TFT)"
        )

    # --- elo
    try:
        for e in client.league_entries(account["puuid"]):
            if e.get("queueType") == "RANKED_SOLO_5x5":
                r["elo"] = f"{e.get('tier','')} {e.get('rank','')} {e.get('leaguePoints',0)}PDL"
    except RiotError:
        pass

    # --- historico de LoL
    ids = client.match_ids(account["puuid"], count=20)
    r["partidas"] = len(ids)
    if not ids:
        r["notas"].append("ZERO partidas de LoL no historico recente")
        r["status"] = ERRO if not summoner else AVISO
        return r

    # o prefixo do matchId revela o servidor de verdade
    servidores = Counter(m.split("_")[0] for m in ids)
    r["servidores"] = dict(servidores)
    fora = {s: n for s, n in servidores.items() if s.upper() != platform.upper()}
    if fora:
        r["notas"].append(f"joga em OUTRO servidor: {fora}")

    detalhe = client.match(ids[0])
    if detalhe:
        info = detalhe.get("info", {})
        qid = info.get("queueId", 0)
        r["ultima_fila"] = QUEUE_NAMES.get(qid, f"queueId {qid}")
        r["ultima_versao"] = info.get("gameVersion", "?")
        n_participantes = len(info.get("participants", []))
        if n_participantes != 10:
            r["notas"].append(f"ultima partida com {n_participantes} jogadores (modo especial)")

    if not summoner or fora:
        r["status"] = AVISO
    elif not r["notas"]:
        r["status"] = OK
    else:
        r["status"] = AVISO
    return r


def sugerir(client: RiotClient, player) -> list[str]:
    """Tenta variacoes comuns de tag quando o Riot ID nao e encontrado."""
    achados = []
    for tag in ("BR1", "br1", player.tag_line.upper(), player.tag_line.capitalize()):
        if tag.lower() == player.tag_line.lower() and tag != player.tag_line:
            continue
        try:
            a = client.account_by_riot_id(player.game_name, tag)
        except RiotError:
            continue
        if a and a.get("puuid"):
            cand = f"{a.get('gameName')}#{a.get('tagLine')}"
            if cand not in achados:
                achados.append(cand)
    return achados


def main() -> int:
    settings = config.load_settings(require_key=True)
    client = RiotClient(settings.api_key, settings.platform, settings.routing)

    print(LINE)
    print("  VERIFICACAO DAS CONTAS - UNIAO DOS AFUNDADOS")
    print(f"  plataforma {settings.platform}  |  roteamento {settings.routing}")
    print(LINE)

    resultados = []
    for player in settings.players:
        r = verificar(client, player, settings.platform)
        resultados.append((player, r))

        cabecalho = f"[{r['status']}] {r['riot_id']:<26}"
        extras = []
        if r.get("nivel"):
            extras.append(f"nv {r['nivel']}")
        if r.get("elo"):
            extras.append(r["elo"])
        if r.get("partidas") is not None:
            extras.append(f"{r['partidas']} partidas")
        if r.get("servidores"):
            extras.append("/".join(r["servidores"]))
        print(cabecalho + "  " + " · ".join(extras))
        for nota in r["notas"]:
            print(f"            -> {nota}")

    print(LINE)
    ok = sum(1 for _, r in resultados if r["status"] == OK)
    avisos = [(p, r) for p, r in resultados if r["status"] == AVISO]
    erros = [(p, r) for p, r in resultados if r["status"] == ERRO]
    print(f"  {ok} contas prontas | {len(avisos)} com aviso | {len(erros)} com erro")
    print(f"  {client.calls} chamadas gastas")

    if erros and "--sugerir" in sys.argv:
        print("\n  Procurando variacoes de tag para as contas com erro...")
        for player, _ in erros:
            achados = sugerir(client, player)
            if achados:
                print(f"    {player.riot_id}  ->  talvez seja: {', '.join(achados)}")
            else:
                print(f"    {player.riot_id}  ->  nenhuma variacao encontrada")
    elif erros:
        print("\n  Rode 'python verificar.py --sugerir' para tentar achar as tags certas.")

    if erros or avisos:
        print("\n  Corrija players.json antes de rodar 'python run.py'.")
    else:
        print("\n  Tudo certo. Pode rodar 'python run.py'.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido.")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERRO: {exc}", file=sys.stderr)
        sys.exit(1)

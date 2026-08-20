"""Coletor de PARTIDAS PERSONALIZADAS (inhouse) direto do cliente do LoL.

    python personalizadas.py            coleta o que houver de novo
    python personalizadas.py --resumo   so mostra o que ja esta guardado
    python personalizadas.py --importar coleta e ja joga no banco local (teste)
    python personalizadas.py --lockfile "D:\\Riot Games\\...\\lockfile"

Roda LOCAL, com o cliente aberto e o usuario logado. A Riot nao devolve
personalizada em lugar nenhum da API publica -- Match-V5 com queue=0 devolve
lista vazia e type=custom devolve HTTP 400 dizendo que so aceita
[ranked, normal, tourney, tutorial]. O cliente e a unica fonte que existe.

O que este script faz, e so isso:
  1. acha o cliente pelo PROCESSO (a pasta de instalacao muda de maquina para
     maquina; nada de caminho fixo);
  2. lista o historico da conta logada e deduplica por gameId;
  3. baixa o detalhe das personalizadas que ainda nao estao em disco;
  4. resolve o PUUID real de cada Riot ID visto, uma unica vez, e guarda no cache.

Grava tudo em personalizadas/ , que VAI para o git. Quem converte e grava no
banco e uda/inhouse.py, la no build -- inclusive no GitHub Actions, que nunca
vera este cliente. Sem o commit, o dado nao chega no site.

Cada conta so enxerga o proprio historico, e sao ~20 partidas de janela. Rodar
com frequencia (ou por mais de um membro do grupo) so aumenta a cobertura: os
arquivos sao nomeados por gameId, entao coletas diferentes se somam sozinhas.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
import urllib3

from uda import config, inhouse, store
from uda.riot import RiotClient, RiotError

# Certificado do LCU e auto-assinado (Riot Games root CA). verify=False e a
# unica opcao viavel; o alvo e 127.0.0.1, nao ha man-in-the-middle a temer.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RAIZ = Path(__file__).resolve().parent
PASTA = RAIZ / "personalizadas"
PROCESSO = "LeagueClientUx.exe"
# Janela do historico. A paginacao do LCU e quebrada (begIndex=100 devolve a
# MESMA primeira pagina), entao o corte real vem do "parou de trazer id novo".
PAGINA = 100
MAX_PAGINAS = 6


class ClienteFechado(RuntimeError):
    pass


# ------------------------------------------------------------- achar o cliente

def _powershell(comando: str) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClienteFechado(f"Nao consegui consultar os processos: {exc}") from exc
    return (proc.stdout or "").strip()


def _do_lockfile(caminho: Path) -> tuple[int, str]:
    """NOME:PID:PORTA:SENHA:PROTOCOLO"""
    texto = caminho.read_text(encoding="utf-8").strip()
    partes = texto.split(":")
    if len(partes) < 5:
        raise ClienteFechado(f"lockfile em formato inesperado: {caminho}")
    return int(partes[2]), partes[3]


def descobrir_credenciais(lockfile: str | None = None) -> tuple[int, str, str]:
    """(porta, senha, de_onde_veio).

    A ordem importa: o processo e a fonte confiavel porque carrega a porta e o
    token na propria linha de comando, e o ExecutablePath revela a pasta de
    instalacao seja ela qual for -- nesta maquina o cliente esta em S:\\, em
    outra pode estar em C:\\Riot Games ou num SSD externo. Caminho fixo no codigo
    quebraria na primeira maquina diferente.
    """
    if lockfile:
        caminho = Path(lockfile)
        if not caminho.exists():
            raise ClienteFechado(f"lockfile nao encontrado em {caminho}")
        porta, senha = _do_lockfile(caminho)
        return porta, senha, f"lockfile informado ({caminho})"

    bruto = _powershell(
        f"Get-CimInstance Win32_Process -Filter \"Name='{PROCESSO}'\" | "
        "Select-Object -First 1 ProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    if not bruto:
        raise ClienteFechado(
            "O cliente do LoL nao esta aberto.\n"
            f"  Nao existe processo {PROCESSO} nesta maquina.\n"
            "  Abra o League of Legends, faca login, espere a tela inicial "
            "carregar e rode de novo."
        )
    try:
        proc = json.loads(bruto)
    except ValueError as exc:
        raise ClienteFechado(f"Resposta inesperada do PowerShell: {bruto[:200]}") from exc
    if isinstance(proc, list):
        proc = proc[0]

    linha = proc.get("CommandLine") or ""
    porta = re.search(r"--app-port=(\d+)", linha)
    token = re.search(r"--remoting-auth-token=([^\s\"']+)", linha)
    if porta and token:
        return int(porta.group(1)), token.group(1), "linha de comando do processo"

    # Windows pode esconder CommandLine de processo elevado. O lockfile fica na
    # pasta de instalacao, que o ExecutablePath entrega.
    exe = proc.get("ExecutablePath") or ""
    if exe:
        caminho = Path(exe).parent / "lockfile"
        if caminho.exists():
            p, s = _do_lockfile(caminho)
            return p, s, f"lockfile em {caminho}"

    raise ClienteFechado(
        "Achei o processo do cliente, mas nao a porta e o token.\n"
        "  Isso costuma acontecer quando o LoL foi aberto como administrador e "
        "este terminal nao.\n"
        "  Abra o terminal como administrador, ou passe o caminho na mao:\n"
        "    python personalizadas.py --lockfile \"<pasta do LoL>\\lockfile\""
    )


# ------------------------------------------------------------------- o cliente

class Cliente:
    def __init__(self, porta: int, senha: str) -> None:
        self.base = f"https://127.0.0.1:{porta}"
        self.sessao = requests.Session()
        self.sessao.verify = False
        self.sessao.headers["Authorization"] = "Basic " + base64.b64encode(
            f"riot:{senha}".encode("utf-8")
        ).decode("ascii")
        self.sessao.headers["Accept"] = "application/json"
        self.chamadas = 0

    def get(self, caminho: str, aceita_404: bool = False):
        self.chamadas += 1
        try:
            resp = self.sessao.get(self.base + caminho, timeout=20)
        except requests.RequestException as exc:
            raise ClienteFechado(
                f"O cliente parou de responder em {caminho} ({exc}).\n"
                "  Ele provavelmente foi fechado no meio da coleta. "
                "O que ja foi baixado esta salvo -- e so abrir e rodar de novo."
            ) from exc
        if resp.status_code == 404 and aceita_404:
            return None
        if resp.status_code in (401, 403):
            raise ClienteFechado(
                "O cliente recusou a autenticacao (HTTP "
                f"{resp.status_code}).\n  O token muda toda vez que o cliente "
                "reinicia. Feche e abra o LoL, e rode de novo."
            )
        if resp.status_code != 200:
            raise ClienteFechado(
                f"HTTP {resp.status_code} em {caminho}: {resp.text[:200]}"
            )
        return resp.json()

    def invocador(self) -> dict:
        dados = self.get("/lol-summoner/v1/current-summoner", aceita_404=True) or {}
        if not dados.get("puuid") and not dados.get("gameName"):
            raise ClienteFechado(
                "O cliente esta aberto mas nao ha ninguem logado.\n"
                "  Faca login na conta e espere a tela inicial antes de rodar."
            )
        return dados

    def historico(self) -> list[dict]:
        """Lista as partidas da conta logada, deduplicando por gameId.

        Duas armadilhas conhecidas deste endpoint, as duas tratadas aqui:
          1. cada jogo vem com participants[] de tamanho 1 (so o dono da conta).
             Serve para DESCOBRIR o gameId; os 10 jogadores so aparecem no
             detalhe, em /games/{gameId}.
          2. a paginacao nao funciona: begIndex=100, 200, ... devolvem a MESMA
             primeira pagina. Sem deduplicar, 18 partidas viram 108. O laco para
             assim que uma pagina nao trouxer nenhum id inedito.
        """
        vistos: dict[int, dict] = {}
        for pagina in range(MAX_PAGINAS):
            inicio = pagina * PAGINA
            dados = self.get(
                "/lol-match-history/v1/products/lol/current-summoner/matches"
                f"?begIndex={inicio}&endIndex={inicio + PAGINA}",
                aceita_404=True,
            )
            jogos = []
            if isinstance(dados, dict):
                jogos = ((dados.get("games") or {}).get("games")
                         if isinstance(dados.get("games"), dict)
                         else dados.get("games")) or []
            elif isinstance(dados, list):
                jogos = dados
            novos = 0
            for jogo in jogos:
                gid = jogo.get("gameId")
                if gid and gid not in vistos:
                    vistos[gid] = jogo
                    novos += 1
            if not jogos or not novos:
                break
        return sorted(vistos.values(), key=lambda g: -(g.get("gameCreation") or 0))

    def detalhe(self, game_id: int) -> dict | None:
        return self.get(f"/lol-match-history/v1/games/{game_id}", aceita_404=True)


# -------------------------------------------------------------------- coleta

def coletar(cliente: Cliente, dir_bruto: Path) -> tuple[int, int, int]:
    """(novas, ja_tinha, descartadas). Incremental: arquivo que ja existe em
    disco nao e baixado nem reescrito, entao rodar de novo e barato e nao
    perde nada do que foi capturado antes."""
    dir_bruto.mkdir(parents=True, exist_ok=True)
    historico = cliente.historico()
    if not historico:
        print("  O historico da conta logada voltou vazio. Se o cliente acabou "
              "de abrir, espere ele terminar de sincronizar e tente de novo.")
        return 0, 0, 0

    print(f"  {len(historico)} partidas unicas no historico desta conta")
    novas = existentes = descartadas = 0

    for resumo in historico:
        gid = resumo.get("gameId")
        destino = dir_bruto / f"{gid}.json"
        if destino.exists():
            existentes += 1
            continue
        # O resumo ja traz gameType/queueId: filtrar aqui evita baixar o
        # detalhe de toda ranqueada da semana so para descartar depois.
        if not inhouse.aceitar_resumo(resumo):
            descartadas += 1
            continue

        jogo = cliente.detalhe(gid)
        if not jogo:
            print(f"  ! detalhe de {gid} indisponivel no cliente")
            continue
        ok, motivo = inhouse.aceitar(jogo)
        if not ok:
            descartadas += 1
            continue

        destino.write_text(
            json.dumps(jogo, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        novas += 1
        print(f"  + {gid}  {str(jogo.get('gameMode') or '?'):<12} "
              f"fila {str(jogo.get('queueId') or '?'):<5} "
              f"{len(jogo.get('participants') or []):>2} jogadores  "
              f"{(jogo.get('gameCreationDate') or '')[:16]}")

    return novas, existentes, descartadas


# ------------------------------------------------------------- PUUID de verdade

def resolver_puuids(dir_bruto: Path, cache_path: Path) -> tuple[int, int]:
    """Riot ID -> PUUID real, uma chamada por conta, para sempre.

    participantIdentities[].player.puuid do LCU tem 36 caracteres: e um UUID
    interno, nao o PUUID de 78 que o resto do banco usa. Casar por ele juntaria
    contas diferentes. O que e real no LCU e gameName + tagLine, entao a ponte
    passa pela Account-V1 (by-riot-id) -- e o resultado fica no cache para nao
    gastar chamada a cada execucao nem depender da chave no GitHub Actions.
    """
    cache = inhouse.carregar_cache(cache_path)
    contas = inhouse.contas_do_acervo(dir_bruto)
    faltam = [c for k, c in sorted(contas.items())
              if k not in cache["contas"] and k not in cache["sem_conta"]]
    if not faltam:
        print(f"  {len(cache['contas'])} contas ja resolvidas, nada novo")
        return 0, 0

    try:
        settings = config.load_settings(require_key=True)
    except SystemExit:
        print(f"  ! {len(faltam)} contas novas sem PUUID e sem RIOT_API_KEY no "
              ".env.\n    As partidas ficam salvas; rode de novo com a chave "
              "para completar o cache.")
        return 0, len(faltam)

    client = RiotClient(settings.api_key, settings.platform, settings.routing)
    ok = falhou = 0
    for conta in faltam:
        chave = inhouse.chave_conta(conta["gameName"], conta["tagLine"])
        try:
            resp = client.account_by_riot_id(conta["gameName"], conta["tagLine"])
        except RiotError as exc:
            print(f"  ! {conta['gameName']}#{conta['tagLine']}: {exc}")
            falhou += 1
            continue
        if resp and resp.get("puuid"):
            cache["contas"][chave] = {
                "gameName": resp.get("gameName", conta["gameName"]),
                "tagLine": resp.get("tagLine", conta["tagLine"]),
                "puuid": resp["puuid"],
                "icon": conta.get("icon", 0),
                "em": int(time.time()),
            }
            ok += 1
            print(f"  ok {resp.get('gameName')}#{resp.get('tagLine')}")
        else:
            # Conta renomeada ou deletada. Guardar a negativa evita repetir a
            # chamada a cada execucao; apagar a entrada do arquivo forca retry.
            cache["sem_conta"][chave] = {
                "gameName": conta["gameName"], "tagLine": conta["tagLine"],
                "em": int(time.time()),
            }
            falhou += 1
            print(f"  x  {conta['gameName']}#{conta['tagLine']} nao encontrada "
                  "(renomeou? conta de outra regiao?)")
        inhouse.salvar_cache(cache, cache_path)
    return ok, falhou


# --------------------------------------------------------------------- resumo

def resumo_acervo(dir_bruto: Path, cache_path: Path) -> None:
    arquivos = sorted(dir_bruto.glob("*.json")) if dir_bruto.exists() else []
    if not arquivos:
        print("\nNenhuma personalizada guardada ainda.")
        return
    por_modo: dict[str, int] = {}
    pessoas: dict[str, int] = {}
    primeiro = ultimo = None
    curtas = 0
    for arq in arquivos:
        try:
            jogo = json.loads(arq.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            continue
        rotulo = inhouse.QUEUE_LABEL.get(
            jogo.get("queueId"), f"{jogo.get('gameMode', '?')} (fila "
            f"{jogo.get('queueId', '?')})")
        por_modo[rotulo] = por_modo.get(rotulo, 0) + 1
        if (jogo.get("gameDuration") or 0) < inhouse.MIN_DUR:
            curtas += 1
        quando = jogo.get("gameCreation") or 0
        primeiro = quando if primeiro is None else min(primeiro, quando)
        ultimo = quando if ultimo is None else max(ultimo, quando)
        for ident in jogo.get("participantIdentities") or []:
            p = ident.get("player") or {}
            chave = f"{p.get('gameName', '?')}#{p.get('tagLine', '?')}"
            pessoas[chave] = pessoas.get(chave, 0) + 1

    dia = lambda ms: time.strftime("%d/%m/%Y", time.localtime(ms / 1000))
    print(f"\n{len(arquivos)} personalizadas guardadas em {dir_bruto}")
    if primeiro:
        print(f"  periodo: {dia(primeiro)} a {dia(ultimo)}")
    for modo, n in sorted(por_modo.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {modo}")
    if curtas:
        print(f"  ({curtas} com menos de {inhouse.MIN_DUR}s -- remake, fora da conta)")

    cache = inhouse.carregar_cache(cache_path)
    print(f"\n{len(pessoas)} pessoas em campo "
          f"({len(cache['contas'])} com PUUID resolvido):")
    for nome, n in sorted(pessoas.items(), key=lambda kv: -kv[1]):
        marca = "ok" if inhouse.chave_conta(*nome.rsplit("#", 1)) in cache["contas"] else "--"
        print(f"  {marca} {n:>3} jogos  {nome}")


# ---------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Coletor de personalizadas do cliente do LoL")
    parser.add_argument("--resumo", action="store_true",
                        help="so mostra o que ja esta guardado, sem tocar no cliente")
    parser.add_argument("--importar", action="store_true",
                        help="depois de coletar, ja grava no banco local")
    parser.add_argument("--lockfile", default=None,
                        help="caminho do lockfile, se a descoberta automatica falhar")
    args = parser.parse_args()

    dir_bruto = PASTA / "bruto"
    cache_path = inhouse.caminho_cache(PASTA)

    print("=" * 62)
    print("  UNIAO DOS AFUNDADOS - Personalizadas")
    print("=" * 62)

    if args.resumo:
        resumo_acervo(dir_bruto, cache_path)
        return 0

    print("\n[1/3] Procurando o cliente do LoL")
    try:
        porta, senha, origem = descobrir_credenciais(args.lockfile)
        print(f"  porta {porta} ({origem})")
        cliente = Cliente(porta, senha)
        eu = cliente.invocador()
        nome = f"{eu.get('gameName', '?')}#{eu.get('tagLine', '?')}"
        print(f"  conta logada: {nome}")

        print("\n[2/3] Lendo o historico")
        novas, existentes, descartadas = coletar(cliente, dir_bruto)
        print(f"  {novas} novas | {existentes} ja guardadas | "
              f"{descartadas} descartadas (fila publica ou treino)")
        if not novas:
            print("  Nenhuma personalizada nova.")
    except ClienteFechado as exc:
        print(f"\n  {exc}")
        print("\n  O que ja foi coletado antes continua valendo:")
        resumo_acervo(dir_bruto, cache_path)
        return 1

    print("\n[3/3] Resolvendo PUUID das contas novas (Account-V1)")
    resolver_puuids(dir_bruto, cache_path)

    resumo_acervo(dir_bruto, cache_path)

    if args.importar:
        print("\nImportando para o banco local...")
        from uda import riot
        conn = store.connect(config.DB_PATH)
        try:
            res = inhouse.importar(conn, PASTA, riot.champion_index())
            print(f"  {res['partidas']} partidas no banco")
        finally:
            conn.close()

    print("\nPronto. Agora faca o commit da pasta personalizadas/ para o dado")
    print("chegar no site:")
    print("    git add personalizadas")
    print('    git commit -m "personalizadas: novas partidas"')
    print("    git push")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido. O que ja foi baixado esta salvo.")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERRO: {exc}", file=sys.stderr)
        sys.exit(1)

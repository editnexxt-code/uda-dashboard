"""Cliente da Riot API com rate limit, retry e cache de Data Dragon.

Regra de ouro do BR:
  br1.api.riotgames.com       -> Summoner-V4, League-V4, Spectator
  americas.api.riotgames.com  -> Account-V1 (Riot ID) e Match-V5
Chamar Match-V5 no br1 devolve 404.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Any
from urllib.parse import quote

import requests

# Chute inicial ate a Riot dizer os limites de verdade pelos headers.
# Development Key: 20 req/s e 100 req/2min.
DEV_LIMITS = ((20, 1.0), (100, 120.0))


class RiotError(RuntimeError):
    pass


def _parse_limit_header(valor: str) -> list[tuple[int, float]]:
    """'20:1,100:120' -> [(20, 1.0), (100, 120.0)]"""
    saida = []
    for parte in (valor or "").split(","):
        parte = parte.strip()
        if ":" not in parte:
            continue
        try:
            cont, jan = parte.split(":")
            saida.append((int(cont), float(jan)))
        except ValueError:
            continue
    return saida


class RateLimiter:
    """Janela deslizante por host, calibrada pelos headers da propria Riot.

    A Riot mantem o contador do lado dela numa janela continua. Um processo que
    comeca do zero logo depois de outro (ex.: verificar.py e depois run.py)
    dispara em cima de uma janela ja cheia e toma 429, e cada 429 custa dezenas
    de segundos parados. Por isso, na primeira resposta de cada host, o contador
    local e sincronizado com o X-App-Rate-Limit-Count que a Riot devolve.
    """

    def __init__(self, limits=DEV_LIMITS) -> None:
        self._windows = [(count, span, deque()) for count, span in limits]
        self._sincronizado = False

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            espera = 0.0
            for count, span, hits in self._windows:
                while hits and now - hits[0] >= span:
                    hits.popleft()
                if len(hits) >= count:
                    espera = max(espera, span - (now - hits[0]) + 0.02)
            if espera <= 0:
                for _, _, hits in self._windows:
                    hits.append(now)
                return
            time.sleep(espera)

    def observe(self, headers) -> None:
        """Ajusta limites e contador a partir dos headers da resposta."""
        limites = _parse_limit_header(headers.get("X-App-Rate-Limit", ""))
        if limites and [(c, s) for c, s, _ in self._windows] != limites:
            antigos = {s: h for c, s, h in self._windows}
            self._windows = [
                (c, s, antigos.get(s, deque())) for c, s in limites
            ]

        if self._sincronizado:
            return
        contagens = dict(
            (janela, usados) for usados, janela
            in _parse_limit_header(headers.get("X-App-Rate-Limit-Count", ""))
        )
        if not contagens:
            return
        # Assume o pior: as requisicoes ja gastas sao todas recentes.
        agora = time.monotonic()
        for _, span, hits in self._windows:
            gastas = contagens.get(span, 0)
            faltam = gastas - len(hits)
            for _ in range(max(0, faltam)):
                hits.appendleft(agora)
        self._sincronizado = True


class RiotClient:
    def __init__(self, api_key: str, platform: str = "br1", routing: str = "americas",
                 verbose: bool = True) -> None:
        self.platform = platform
        self.routing = routing
        self.verbose = verbose
        self.calls = 0
        self.throttled = 0
        # A Riot conta o limite de aplicacao SEPARADAMENTE por host de roteamento.
        # americas (Match-V5/Account-V1) e br1 (Summoner/League) tem baldes proprios,
        # entao um limitador por host aproveita a folga que um balde unico jogava fora.
        self._limiters: dict[str, RateLimiter] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {"X-Riot-Token": api_key, "Accept": "application/json"}
        )

    # ---------------------------------------------------------------- interno

    def _get(self, host: str, path: str, params: dict | None = None,
             allow_404: bool = False) -> Any:
        url = f"https://{host}{path}"
        limiter = self._limiters.setdefault(host, RateLimiter())
        for attempt in range(6):
            limiter.acquire()
            self.calls += 1
            try:
                resp = self._session.get(url, params=params, timeout=20)
            except requests.RequestException as exc:
                if attempt == 5:
                    raise RiotError(f"Falha de rede em {path}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue

            limiter.observe(resp.headers)

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                if allow_404:
                    return None
                raise RiotError(f"404 em {url} (host errado ou conta inexistente)")
            if resp.status_code == 429:
                self.throttled += 1
                tipo = resp.headers.get("X-Rate-Limit-Type", "?")
                delay = float(resp.headers.get("Retry-After", 10)) + 0.5
                self._log(f"    429 ({tipo}) em {host}: aguardando {delay:.0f}s")
                # Um 429 significa que a contagem local esta atras da da Riot.
                # Ressincroniza na proxima resposta em vez de insistir no chute.
                limiter._sincronizado = False
                time.sleep(delay)
                continue
            if resp.status_code in (401, 403):
                raise RiotError(
                    "403/401 da Riot: a chave e invalida ou expirou. "
                    "Development Keys duram 24h -- gere outra em "
                    "https://developer.riotgames.com e atualize o .env"
                )
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise RiotError(f"HTTP {resp.status_code} em {url}: {resp.text[:200]}")
        raise RiotError(f"Desisti apos varias tentativas em {url}")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)
            sys.stdout.flush()

    # ------------------------------------------------------------- endpoints

    def account_by_riot_id(self, game_name: str, tag_line: str) -> dict | None:
        path = (
            "/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
        )
        return self._get(f"{self.routing}.api.riotgames.com", path, allow_404=True)

    def summoner_by_puuid(self, puuid: str) -> dict | None:
        return self._get(
            f"{self.platform}.api.riotgames.com",
            f"/lol/summoner/v4/summoners/by-puuid/{puuid}",
            allow_404=True,
        )

    def league_entries(self, puuid: str) -> list[dict]:
        data = self._get(
            f"{self.platform}.api.riotgames.com",
            f"/lol/league/v4/entries/by-puuid/{puuid}",
            allow_404=True,
        )
        return data or []

    def match_ids(self, puuid: str, count: int = 100, start: int = 0,
                  start_time: int | None = None) -> list[str]:
        params: dict[str, Any] = {"start": start, "count": min(100, count)}
        if start_time:
            params["startTime"] = start_time
        data = self._get(
            f"{self.routing}.api.riotgames.com",
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params=params,
            allow_404=True,
        )
        return data or []

    def match(self, match_id: str) -> dict | None:
        return self._get(
            f"{self.routing}.api.riotgames.com",
            f"/lol/match/v5/matches/{match_id}",
            allow_404=True,
        )


# ------------------------------------------------------- Data Dragon (sem key)

_DDRAGON_CACHE: dict[str, Any] = {}


def ddragon_version() -> str:
    if "version" not in _DDRAGON_CACHE:
        try:
            versions = requests.get(
                "https://ddragon.leagueoflegends.com/api/versions.json", timeout=15
            ).json()
            _DDRAGON_CACHE["version"] = versions[0]
        except Exception:
            _DDRAGON_CACHE["version"] = "15.1.1"
    return _DDRAGON_CACHE["version"]


def champion_index() -> dict[int, dict[str, str]]:
    """championId -> {key: 'Aatrox', name: 'Aatrox'} para montar icones do ddragon."""
    if "champs" in _DDRAGON_CACHE:
        return _DDRAGON_CACHE["champs"]
    out: dict[int, dict[str, str]] = {}
    try:
        ver = ddragon_version()
        data = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/pt_BR/champion.json",
            timeout=20,
        ).json()["data"]
        for entry in data.values():
            out[int(entry["key"])] = {"key": entry["id"], "name": entry["name"]}
    except Exception:
        pass
    _DDRAGON_CACHE["champs"] = out
    return out

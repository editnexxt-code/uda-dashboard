"""Configuracao: le .env e players.json sem depender de libs externas."""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "uda.sqlite3"
OUT_PATH = ROOT / "dashboard.html"
TEMPLATE_PATH = Path(__file__).resolve().parent / "template.html"


def load_env(path: Path = ROOT / ".env") -> None:
    """Carrega KEY=VALUE do .env para os.environ (sem sobrescrever o ambiente)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Player:
    game_name: str
    tag_line: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


# Copiar Riot ID do Discord/navegador costuma trazer caracteres invisiveis junto
# (isolates bidirecionais, zero-width space, BOM). Eles viram %E2%81%A6 na URL e a
# Riot devolve 404 numa conta que existe. Cirilico, acento e CJK sao legitimos e ficam.
_INVISIVEIS = {"​", "‌", "‍", "﻿", "­", "⁠"}


def limpar(texto: str) -> str:
    limpo = "".join(
        c for c in texto
        if c not in _INVISIVEIS and unicodedata.category(c) not in ("Cf", "Cc")
    )
    # NFC: garante que "á" seja 1 codepoint, e nao "a" + acento combinante.
    return unicodedata.normalize("NFC", limpo).strip()


@dataclass(frozen=True)
class Settings:
    api_key: str
    platform: str
    routing: str
    players: tuple[Player, ...]
    match_count: int
    window_days: int
    min_games: int


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def load_settings(require_key: bool = True) -> Settings:
    load_env()
    roster = json.loads((ROOT / "players.json").read_text(encoding="utf-8-sig"))
    region = roster.get("region", {})

    key = os.environ.get("RIOT_API_KEY", "").strip()
    if require_key and (not key or key.startswith("RGAPI-cole")):
        raise SystemExit(
            "\n  Falta a chave da Riot.\n"
            "  1) Acesse https://developer.riotgames.com e copie sua Development Key\n"
            f"  2) Crie o arquivo {ROOT / '.env'} com a linha:\n"
            "     RIOT_API_KEY=RGAPI-...\n"
            "  (a chave de desenvolvimento expira a cada 24h -- e so gerar de novo)\n"
        )

    jogadores = []
    for p in roster["players"]:
        nome, tag = limpar(p["gameName"]), limpar(p["tagLine"])
        if nome != p["gameName"] or tag != p["tagLine"]:
            print(f"  aviso: caracteres invisiveis removidos de '{nome}#{tag}'")
        jogadores.append(Player(nome, tag))

    return Settings(
        api_key=key,
        platform=region.get("platform", "br1"),
        routing=region.get("routing", "americas"),
        players=tuple(jogadores),
        match_count=max(1, min(100, _int_env("MATCH_COUNT", 100))),
        window_days=_int_env("WINDOW_DAYS", 90),
        min_games=max(1, _int_env("MIN_GAMES", 5)),
    )

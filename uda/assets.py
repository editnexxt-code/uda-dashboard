"""Embute os icones dentro do HTML.

Sem isso o painel depende de ddragon.leagueoflegends.com e raw.communitydragon.org
em toda abertura: sem internet, ou num visualizador com CSP restritiva, TODAS as
imagens somem de uma vez. Aqui os icones sao baixados uma unica vez, reduzidos,
convertidos para WebP e viram data URI dentro do proprio arquivo.

Os PNG de campeao do ddragon tem 120x120 e ~12 KB. Reduzidos para 64x64 em WebP
ficam com ~2 KB, entao o pacote inteiro (14 invocadores + ~130 campeoes + elos)
cabe em poucas centenas de KB.
"""

from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import requests

CACHE = Path(__file__).resolve().parent.parent / "assets" / "cache"
TIERS = ["iron", "bronze", "silver", "gold", "platinum", "emerald",
         "diamond", "master", "grandmaster", "challenger", "unranked"]

PROFILE_PX = 72
CHAMPION_PX = 64


def _coletar(node: Any, icons: set[int], champs: set[int], tiers: set[str]) -> None:
    """Varre o payload atras de tudo que vira imagem na tela."""
    if isinstance(node, dict):
        for chave, valor in node.items():
            if chave in ("icon", "iconA", "iconB") and isinstance(valor, int):
                icons.add(valor)
            elif chave == "icons" and isinstance(valor, list):
                icons.update(v for v in valor if isinstance(v, int))
            elif chave == "championId" and isinstance(valor, int) and valor:
                champs.add(valor)
            elif chave == "tier" and isinstance(valor, str) and valor:
                tiers.add(valor.lower())
            else:
                _coletar(valor, icons, champs, tiers)
    elif isinstance(node, list):
        for item in node:
            _coletar(item, icons, champs, tiers)


def _baixar(url: str, nome: str) -> bytes | None:
    """Baixa com cache em disco. Segunda execucao nao toca a rede."""
    destino = CACHE / nome
    if destino.exists() and destino.stat().st_size > 0:
        return destino.read_bytes()
    try:
        resp = requests.get(url, timeout=25)
        if resp.status_code != 200 or not resp.content:
            return None
    except requests.RequestException:
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resp.content)
    return resp.content


def _webp(raw: bytes, px: int) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        # Sem Pillow, embute o PNG original mesmo (arquivo maior, mas funciona).
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        im = im.resize((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=88, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _svg_data_uri(raw: bytes) -> str:
    from urllib.parse import quote
    return "data:image/svg+xml," + quote(raw.decode("utf-8", "replace"), safe="")


def construir(payload: dict, ddragon_ver: str, champ_index: dict[int, dict[str, str]],
              verbose: bool = True) -> dict[str, dict[str, str]]:
    icons: set[int] = set()
    champs: set[int] = set()
    tiers: set[str] = set()
    _coletar(payload, icons, champs, tiers)
    tiers = {t for t in tiers if t in TIERS} | {"unranked"}

    if verbose:
        print(f"  embutindo icones: {len(icons)} invocadores, "
              f"{len(champs)} campeoes, {len(tiers)} emblemas de elo")

    pacote: dict[str, dict[str, str]] = {"profile": {}, "champion": {}, "crest": {}}

    def perfil(pid: int):
        raw = _baixar(
            f"https://ddragon.leagueoflegends.com/cdn/{ddragon_ver}/img/profileicon/{pid}.png",
            f"profile-{ddragon_ver}-{pid}.png")
        uri = _webp(raw, PROFILE_PX) if raw else None
        if uri:
            pacote["profile"][str(pid)] = uri

    def campeao(cid: int):
        entrada = champ_index.get(cid)
        if not entrada:
            return
        chave = entrada["key"]
        raw = _baixar(
            f"https://ddragon.leagueoflegends.com/cdn/{ddragon_ver}/img/champion/{chave}.png",
            f"champ-{ddragon_ver}-{chave}.png")
        uri = _webp(raw, CHAMPION_PX) if raw else None
        if uri:
            pacote["champion"][str(cid)] = uri

    def elo(tier: str):
        raw = _baixar(
            "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/"
            f"global/default/images/ranked-mini-crests/{tier}.svg",
            f"crest-{tier}.svg")
        if raw:
            pacote["crest"][tier] = _svg_data_uri(raw)

    tarefas: list[tuple] = (
        [(perfil, i) for i in icons]
        + [(campeao, c) for c in champs]
        + [(elo, t) for t in tiers]
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda t: t[0](t[1]), tarefas))

    if verbose:
        tamanho = sum(len(v) for grupo in pacote.values() for v in grupo.values())
        faltou = len(tarefas) - sum(len(g) for g in pacote.values())
        print(f"  {sum(len(g) for g in pacote.values())} icones embutidos "
              f"(~{tamanho / 1024 / 1024:.1f} MB em base64)"
              + (f", {faltou} nao baixaram" if faltou else ""))
    return pacote

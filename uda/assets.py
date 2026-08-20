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
import html
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import requests

CACHE = Path(__file__).resolve().parent.parent / "assets" / "cache"
TIERS = ["iron", "bronze", "silver", "gold", "platinum", "emerald",
         "diamond", "master", "grandmaster", "challenger", "unranked"]

PROFILE_PX = 72
CHAMPION_PX = 64

# Um unico arquivo traz os ~170 campeoes com skills em pt-BR (2.3 MB baixados
# uma vez); pedir champion/{Nome}.json daria 170 requisicoes para o mesmo dado.
CHAMPION_FULL = ("https://ddragon.leagueoflegends.com/cdn/{ver}"
                 "/data/pt_BR/championFull.json")
RESUMO_MAX = 180


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


def _resumo(bruto: str, limite: int = RESUMO_MAX) -> str:
    """Descricao do ddragon vem com <br> e <spellName>; a tela quer texto puro.

    O corte respeita a palavra: cortar no meio de uma so ganharia 5 caracteres
    e deixaria a frase feia em 140 campeoes de uma vez.
    """
    if not bruto:
        return ""
    texto = re.sub(r"<br\s*/?>", " ", bruto, flags=re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = re.sub(r"\s+", " ", html.unescape(texto)).strip()
    if len(texto) <= limite:
        return texto
    return texto[:limite].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."


def detalhes(payload: dict, ddragon_ver: str,
             verbose: bool = True) -> dict[str, dict[str, Any]]:
    """Ficha de cada campeao que o grupo jogou, para o dossie.

    So entra quem aparece no payload: guardar os 170 custaria o dobro sem nada
    na tela apontar para eles. "icone" e o nome do arquivo, nao a URL -- a arte
    vem do CDN em <img>, embutir 5 icones por campeao em base64 estouraria o HTML.
    """
    icons: set[int] = set()
    champs: set[int] = set()
    tiers: set[str] = set()
    _coletar(payload, icons, champs, tiers)
    if not champs:
        return {}

    bruto = _baixar(CHAMPION_FULL.format(ver=ddragon_ver),
                    f"championFull-{ddragon_ver}.json")
    try:
        # Sem o ddragon o dossie fica sem skills, mas o resto do painel abre:
        # nada aqui pode derrubar o build.
        data = json.loads(bruto.decode("utf-8"))["data"] if bruto else {}
    except (ValueError, KeyError, AttributeError, UnicodeDecodeError):
        data = {}
    if not data:
        if verbose:
            print("  dossie: championFull.json indisponivel, seguindo sem skills")
        return {}

    fichas: dict[str, dict[str, Any]] = {}
    for entrada in data.values():
        try:
            cid = int(entrada["key"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid not in champs:
            continue
        passiva = entrada.get("passive") or {}
        fichas[str(cid)] = {
            "key": entrada.get("id") or "",
            "name": entrada.get("name") or "",
            "title": entrada.get("title") or "",
            "tags": entrada.get("tags") or [],
            # A ordem que o ddragon entrega ja e Q, W, E, R.
            "spells": [
                {
                    "slot": slot,
                    "nome": s.get("name") or "",
                    "icone": (s.get("image") or {}).get("full") or "",
                    "resumo": _resumo(s.get("description") or ""),
                }
                for slot, s in zip("QWER", entrada.get("spells") or [])
            ],
            "passive": {
                "nome": passiva.get("name") or "",
                "icone": (passiva.get("image") or {}).get("full") or "",
                "resumo": _resumo(passiva.get("description") or ""),
            },
        }

    if verbose:
        peso = len(json.dumps(fichas, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))
        print(f"  dossie: {len(fichas)} campeoes (~{peso / 1024:.0f} KB no payload)")
    return fichas


# ---------------------------------------------------------------- arsenal

ITEM_PX = 40
SPELL_PX = 40
RUNE_PX = 40


def extras(payload: dict, ddragon_ver: str, verbose: bool = True) -> dict:
    """Icones de item, feitico e runa -- o que o placar e o Arsenal desenham.

    Mesma razao dos icones de campeao: sem embutir, o placar abre com 70 quadrados
    vazios em qualquer lugar que bloqueie host externo. A 40px em WebP cada um
    fica em ~1 KB, entao os ~360 itens do banco cabem em menos de meio mega.
    """
    itens: set[int] = set()
    feiticos: set[int] = set()
    runas: set[int] = set()

    for placar in (payload.get("placares") or {}).values():
        for time_ in placar.get("times", []):
            for j in time_.get("jogadores", []):
                itens.update(i for i in (j.get("itens") or []) if i)
                feiticos.update(s for s in (j.get("feiticos") or []) if s)
                if j.get("keystone"):
                    runas.add(j["keystone"])
    arsenal = payload.get("arsenal") or {}
    for grupo in ("itens", "baratos", "trinkets"):
        itens.update(x["id"] for x in arsenal.get(grupo, []) if x.get("id"))
    for r in arsenal.get("runas", []):
        runas.add(r["id"])

    pacote: dict[str, dict[str, str]] = {"item": {}, "spell": {}, "rune": {}}

    def item(iid: int):
        raw = _baixar(
            f"https://ddragon.leagueoflegends.com/cdn/{ddragon_ver}/img/item/{iid}.png",
            f"item-{ddragon_ver}-{iid}.png")
        uri = _webp(raw, ITEM_PX) if raw else None
        if uri:
            pacote["item"][str(iid)] = uri

    # O ddragon indexa feitico e runa por NOME de arquivo, nao por id numerico,
    # entao e preciso do catalogo para traduzir 4 -> SummonerFlash.png.
    cat_spell = {}
    bruto = _baixar(
        f"https://ddragon.leagueoflegends.com/cdn/{ddragon_ver}/data/pt_BR/summoner.json",
        f"summoner-{ddragon_ver}.json")
    if bruto:
        try:
            for f in json.loads(bruto.decode("utf-8"))["data"].values():
                cat_spell[int(f["key"])] = f["image"]["full"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            pass

    cat_rune = {}
    bruto = _baixar(
        f"https://ddragon.leagueoflegends.com/cdn/{ddragon_ver}/data/pt_BR/runesReforged.json",
        f"runes-{ddragon_ver}.json")
    if bruto:
        try:
            for arvore in json.loads(bruto.decode("utf-8")):
                for slot in arvore.get("slots", []):
                    for perk in slot.get("runes", []):
                        cat_rune[perk["id"]] = perk["icon"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            pass

    def feitico(sid: int):
        nome = cat_spell.get(sid)
        if not nome:
            return
        raw = _baixar(
            f"https://ddragon.leagueoflegends.com/cdn/{ddragon_ver}/img/spell/{nome}",
            f"spell-{ddragon_ver}-{nome}")
        uri = _webp(raw, SPELL_PX) if raw else None
        if uri:
            pacote["spell"][str(sid)] = uri

    def runa(rid: int):
        caminho = cat_rune.get(rid)
        if not caminho:
            return
        raw = _baixar(f"https://ddragon.leagueoflegends.com/cdn/img/{caminho}",
                      f"rune-{caminho.replace('/', '-')}")
        uri = _webp(raw, RUNE_PX) if raw else None
        if uri:
            pacote["rune"][str(rid)] = uri

    tarefas = ([(item, i) for i in itens] + [(feitico, s) for s in feiticos]
               + [(runa, r) for r in runas])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda t: t[0](t[1]), tarefas))

    if verbose:
        tam = sum(len(v) for g in pacote.values() for v in g.values())
        print(f"  arsenal embutido: {len(pacote['item'])} itens, "
              f"{len(pacote['spell'])} feiticos, {len(pacote['rune'])} runas "
              f"(~{tam / 1024 / 1024:.1f} MB)")
    return pacote

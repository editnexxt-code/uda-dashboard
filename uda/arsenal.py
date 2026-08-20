"""O que o grupo compra, equipa e leva pra rota.

Os itens ja estao no banco (coluna `items`, o inventario final de cada partida),
entao nada aqui custa uma requisicao a mais de Match-V5. O que vem do Data Dragon
e so o catalogo: nome, preco e icone -- arquivos estaticos, sem chave.

Sobre "mais comprado": o inventario final NAO e a lista de compras. Um item
vendido no meio do jogo some, e os trinkets aparecem em quase toda partida.
Por isso a tela separa lendarios de trinket/consumivel: misturar poria a
Sentinela Invisivel em primeiro lugar para sempre e enterraria a informacao real.
"""

from __future__ import annotations

import json
from collections import defaultdict

import requests

from .assets import _baixar
from .kpi import _r, _safe_div

ITEM_JSON = "https://ddragon.leagueoflegends.com/cdn/{ver}/data/pt_BR/item.json"
SPELL_JSON = "https://ddragon.leagueoflegends.com/cdn/{ver}/data/pt_BR/summoner.json"
RUNES_JSON = "https://ddragon.leagueoflegends.com/cdn/{ver}/data/pt_BR/runesReforged.json"

# Preco minimo para um item contar como "de verdade". Trinket custa 0, componente
# barato polui o topo; 1300 deixa passar bota completa e corta o resto.
OURO_LENDARIO = 1300
TOPO = 18


def _catalogo(url: str, nome: str) -> dict:
    bruto = _baixar(url, nome)
    if not bruto:
        return {}
    try:
        return json.loads(bruto.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _tabela_itens(rows, players, itens: dict) -> dict:
    """Conta cada item por quantas partidas ele terminou no inventario."""
    total_por_item: dict[int, int] = defaultdict(int)
    vitorias: dict[int, int] = defaultdict(int)
    donos: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    partidas = 0

    for row in rows:
        bruto = row["items"]
        if not bruto:
            continue
        try:
            ids = json.loads(bruto)
        except (ValueError, TypeError):
            continue
        partidas += 1
        vistos = {i for i in ids if i}
        for iid in vistos:
            total_por_item[iid] += 1
            vitorias[iid] += 1 if row["win"] else 0
            if row["puuid"] in players:
                donos[iid][row["puuid"]] += 1

    def montar(filtro):
        saida = []
        for iid, n in total_por_item.items():
            ficha = itens.get(str(iid))
            if not ficha or not filtro(ficha):
                continue
            top = max(donos[iid].items(), key=lambda kv: kv[1], default=None)
            saida.append({
                "id": iid,
                "nome": ficha.get("name", f"Item {iid}"),
                "ouro": (ficha.get("gold") or {}).get("total", 0),
                "partidas": n,
                "share": _r(_safe_div(n, partidas) * 100, 1),
                "winrate": _r(_safe_div(vitorias[iid], n) * 100, 1),
                "dono": players[top[0]]["gameName"] if top and top[0] in players else None,
                "donoIcon": players[top[0]]["icon"] if top and top[0] in players else None,
                "donoN": top[1] if top else 0,
            })
        saida.sort(key=lambda d: -d["partidas"])
        return saida[:TOPO]

    lendarios = montar(lambda f: (f.get("gold") or {}).get("total", 0) >= OURO_LENDARIO)
    baratos = montar(lambda f: 0 < (f.get("gold") or {}).get("total", 0) < OURO_LENDARIO)
    gratis = montar(lambda f: (f.get("gold") or {}).get("total", 0) == 0)
    return {"lendarios": lendarios, "baratos": baratos, "trinkets": gratis,
            "partidas": partidas}


def _tabela_runas(rows, runas: list) -> list[dict]:
    """Runa principal (keystone) mais equipada, com aproveitamento."""
    nomes: dict[int, dict] = {}
    for arvore in runas or []:
        for slot in arvore.get("slots", [])[:1]:          # so a primeira fileira
            for perk in slot.get("runes", []):
                nomes[perk["id"]] = {"nome": perk["name"], "icone": perk["icon"],
                                     "arvore": arvore.get("name", "")}
    cont: dict[int, dict] = defaultdict(lambda: {"n": 0, "v": 0})
    for row in rows:
        k = row["keystone"]
        if not k:
            continue
        cont[k]["n"] += 1
        cont[k]["v"] += 1 if row["win"] else 0
    total = sum(c["n"] for c in cont.values())
    saida = []
    for kid, c in cont.items():
        ficha = nomes.get(kid)
        if not ficha:
            continue
        saida.append({
            "id": kid, "nome": ficha["nome"], "arvore": ficha["arvore"],
            "icone": ficha["icone"], "partidas": c["n"],
            "share": _r(_safe_div(c["n"], total) * 100, 1),
            "winrate": _r(_safe_div(c["v"], c["n"]) * 100, 1),
        })
    saida.sort(key=lambda d: -d["partidas"])
    return saida[:TOPO]


def _tabela_feiticos(rows, spells: dict) -> list[dict]:
    """A dupla de feiticos de invocador, contada como par."""
    por_id: dict[int, dict] = {}
    for ficha in (spells or {}).values():
        try:
            por_id[int(ficha["key"])] = ficha
        except (KeyError, ValueError, TypeError):
            pass
    cont: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "v": 0})
    for row in rows:
        a, b = row["spell1_id"], row["spell2_id"]
        if not a or not b:
            continue
        cont[tuple(sorted((a, b)))]["n"] += 1
        cont[tuple(sorted((a, b)))]["v"] += 1 if row["win"] else 0
    total = sum(c["n"] for c in cont.values())
    saida = []
    for (a, b), c in cont.items():
        fa, fb = por_id.get(a), por_id.get(b)
        if not fa or not fb:
            continue
        saida.append({
            "nome": f"{fa['name']} + {fb['name']}",
            "icones": [fa["image"]["full"], fb["image"]["full"]],
            "partidas": c["n"],
            "share": _r(_safe_div(c["n"], total) * 100, 1),
            "winrate": _r(_safe_div(c["v"], c["n"]) * 100, 1),
        })
    saida.sort(key=lambda d: -d["partidas"])
    return saida[:12]


def construir(rows, players, ddragon_ver: str, verbose: bool = True) -> dict:
    """Bloco do Arsenal. Sem o Data Dragon devolve vazio e a aba some sozinha."""
    itens = _catalogo(ITEM_JSON.format(ver=ddragon_ver),
                      f"item-{ddragon_ver}.json").get("data", {})
    spells = _catalogo(SPELL_JSON.format(ver=ddragon_ver),
                       f"summoner-{ddragon_ver}.json").get("data", {})
    runas = _catalogo(RUNES_JSON.format(ver=ddragon_ver), f"runes-{ddragon_ver}.json")
    if isinstance(runas, dict):
        runas = []

    if not itens:
        if verbose:
            print("  arsenal: catalogo de itens indisponivel, aba desativada")
        return {}

    tabelas = _tabela_itens(rows, players, itens)
    saida = {
        "itens": tabelas["lendarios"],
        "baratos": tabelas["baratos"],
        "trinkets": tabelas["trinkets"],
        "partidas": tabelas["partidas"],
        "runas": _tabela_runas(rows, runas),
        "feiticos": _tabela_feiticos(rows, spells),
        "ddragon": ddragon_ver,
    }
    if verbose:
        print(f"  arsenal: {len(saida['itens'])} itens lendarios, "
              f"{len(saida['runas'])} runas, {len(saida['feiticos'])} duplas de feitico")
    return saida

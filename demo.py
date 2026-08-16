"""Gera um dashboard de DEMONSTRACAO com dados falsos.

Serve para conferir o visual e validar o pipeline sem gastar chave da Riot.
Escreve em data/demo.sqlite3 e dashboard-demo.html -- nao encosta no banco real.

    python demo.py
"""

from __future__ import annotations

import random
import sys
import time
import webbrowser

from uda import config, kpi, render, store

CHAMPS = [
    (266, "Aatrox"), (103, "Ahri"), (84, "Akali"), (22, "Ashe"), (51, "Caitlyn"),
    (122, "Darius"), (119, "Draven"), (245, "Ekko"), (81, "Ezreal"), (114, "Fiora"),
    (86, "Garen"), (39, "Irelia"), (24, "Jax"), (202, "Jhin"), (222, "Jinx"),
    (141, "Kayn"), (64, "LeeSin"), (89, "Leona"), (99, "Lux"), (54, "Malphite"),
    (75, "Nasus"), (92, "Riven"), (875, "Sett"), (412, "Thresh"), (67, "Vayne"),
    (234, "Viego"), (157, "Yasuo"), (777, "Yone"), (238, "Zed"), (145, "Kaisa"),
]
POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
QUEUES = [420] * 11 + [440] * 4 + [450] * 3 + [400] * 2
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]


def main() -> None:
    rng = random.Random(20260816)
    settings = config.load_settings(require_key=False)

    db_path = config.ROOT / "data" / "demo.sqlite3"
    if db_path.exists():
        db_path.unlink()
    conn = store.connect(db_path)

    now_ms = int(time.time() * 1000)
    roster = []
    for i, player in enumerate(settings.players):
        puuid = f"DEMO-PUUID-{i:02d}-" + "x" * 60
        # habilidade entre 0.30 e 0.95 define o quao bem o jogador performa
        skill = 0.30 + (i * 0.055) + rng.uniform(-0.05, 0.05)
        roster.append({"puuid": puuid, "p": player, "skill": min(0.95, skill)})

        store.upsert_player(conn, puuid, player.game_name, player.tag_line,
                            rng.choice([29, 588, 4568, 6, 3379, 5123, 909]),
                            rng.randint(80, 720), int(time.time()))
        tier = TIERS[min(len(TIERS) - 1, int(skill * 7))]
        wins = rng.randint(40, 160)
        store.replace_ranks(conn, puuid, [
            {"queueType": "RANKED_SOLO_5x5", "tier": tier,
             "rank": rng.choice(["I", "II", "III", "IV"]),
             "leaguePoints": rng.randint(0, 99), "wins": wins,
             "losses": int(wins * rng.uniform(0.7, 1.35)),
             "hotStreak": rng.random() < 0.2},
            {"queueType": "RANKED_FLEX_SR", "tier": rng.choice(TIERS),
             "rank": rng.choice(["I", "II", "III", "IV"]),
             "leaguePoints": rng.randint(0, 99), "wins": rng.randint(5, 60),
             "losses": rng.randint(5, 60), "hotStreak": False},
        ], int(time.time()))

    tracked = {r["puuid"] for r in roster}
    match_no = 0

    def make_participant(puuid: str, skill: float, team: int, win: bool,
                         minutes: float, pos: str) -> dict:
        cid, cname = rng.choice(CHAMPS)
        base = skill * 1.25
        kills = max(0, int(rng.gauss(3 + base * 5, 2.6)))
        deaths = max(0, int(rng.gauss(9 - base * 5, 2.2)))
        assists = max(0, int(rng.gauss(5 + base * 6, 3.4)))
        if win:
            kills += rng.randint(0, 3)
            deaths = max(0, deaths - rng.randint(0, 2))
        return {
            "puuid": puuid, "teamId": team, "win": win,
            "championId": cid, "championName": cname, "teamPosition": pos,
            "kills": kills, "deaths": deaths, "assists": assists,
            "totalMinionsKilled": int(minutes * (2.9 + base * 3.4) * rng.uniform(.82, 1.15)),
            "neutralMinionsKilled": int(minutes * rng.uniform(0, 1.6)),
            "goldEarned": int(minutes * (270 + base * 190) * rng.uniform(.88, 1.12)),
            "totalDamageDealtToChampions": int(minutes * (330 + base * 520) * rng.uniform(.75, 1.28)),
            "totalDamageTaken": int(minutes * (620 + base * 200) * rng.uniform(.8, 1.25)),
            "damageDealtToObjectives": int(minutes * rng.uniform(60, 420)),
            "totalHealsOnTeammates": int(rng.uniform(0, 4200)),
            "totalDamageShieldedOnTeammates": int(rng.uniform(0, 3000)),
            "visionScore": int(minutes * (0.5 + skill * 0.9) * rng.uniform(.7, 1.4)),
            "wardsPlaced": int(minutes * rng.uniform(.25, .75)),
            "wardsKilled": int(minutes * rng.uniform(.03, .22)),
            "visionWardsBoughtInGame": rng.randint(0, 5),
            "firstBloodKill": rng.random() < 0.09,
            "firstBloodAssist": rng.random() < 0.11,
            "doubleKills": max(0, int(rng.gauss(base, 1))),
            "tripleKills": 1 if rng.random() < skill * 0.12 else 0,
            "quadraKills": 1 if rng.random() < skill * 0.03 else 0,
            "pentaKills": 1 if rng.random() < skill * 0.012 else 0,
            "turretTakedowns": max(0, int(rng.gauss(1.6 + skill, 1.2))),
            "gameEndedInEarlySurrender": False,
            "gameEndedInSurrender": rng.random() < 0.22,
        }

    print("Gerando partidas ficticias...")
    for member in roster:
        for _ in range(rng.randint(55, 95)):
            match_no += 1
            queue = rng.choice(QUEUES)
            minutes = rng.uniform(18, 41) if queue != 450 else rng.uniform(13, 24)
            duration = int(minutes * 60)
            created = now_ms - rng.randint(0, 88) * 86400_000 - rng.randint(0, 86_000_000)

            # chance do time ter mais gente da UDA junto
            mates = [member]
            if rng.random() < 0.28:
                pool = [r for r in roster if r is not member]
                mates += rng.sample(pool, k=rng.randint(1, 2))

            blue_win = rng.random() < (0.5 + (member["skill"] - 0.6) * 0.45)
            parts, slot = [], 0
            for team, is_blue in ((100, True), (200, False)):
                for i in range(5):
                    win = blue_win if is_blue else not blue_win
                    if is_blue and slot < len(mates):
                        m = mates[slot]
                        parts.append(make_participant(
                            m["puuid"], m["skill"], team, win, minutes, POSITIONS[i]))
                        slot += 1
                    else:
                        parts.append(make_participant(
                            f"BOT-{match_no}-{team}-{i}", rng.uniform(.35, .8),
                            team, win, minutes, POSITIONS[i]))

            store.save_match(conn, {
                "metadata": {"matchId": f"BR1_DEMO{match_no:07d}"},
                "info": {
                    "gameCreation": created, "gameDuration": duration,
                    "queueId": queue, "gameVersion": "15.16.1",
                    "gameMode": "CLASSIC" if queue != 450 else "ARAM",
                    "participants": parts,
                },
            }, tracked)
        conn.commit()

    store.set_meta(conn, "last_fetch", str(int(time.time())))
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"{total} partidas ficticias no banco. Calculando KPIs...")

    payload = kpi.build_payload(
        conn, window_days=90, min_games=settings.min_games,
        champ_index={cid: {"key": name, "name": name} for cid, name in CHAMPS},
        ddragon_ver="15.16.1", platform=settings.platform, demo=True,
    )
    out = config.ROOT / "dashboard-demo.html"
    render.render(payload, config.TEMPLATE_PATH, out)
    conn.close()

    print(f"\nDemo pronta: {out}")
    print("Os numeros sao INVENTADOS -- serve so para ver o visual.")
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()

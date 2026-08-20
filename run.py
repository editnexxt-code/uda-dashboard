"""UNIAO DOS AFUNDADOS - Dashboard.

Uso:
    python run.py              busca dados novos e regera o dashboard
    python run.py --build      so regera o HTML com o que ja esta no banco
    python run.py --open       regera e abre no navegador
    python run.py --days 30    muda a janela de analise desta execucao
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from uda import assets, config, fetch, inhouse, kpi, render, riot, store


def main() -> int:
    parser = argparse.ArgumentParser(description="UNIAO DOS AFUNDADOS - Dashboard")
    parser.add_argument("--build", action="store_true",
                        help="nao chama a API, so recalcula o HTML")
    parser.add_argument("--open", action="store_true",
                        help="abre o dashboard no navegador ao terminar")
    parser.add_argument("--days", type=int, default=None,
                        help="janela de analise em dias (0 = tudo)")
    args = parser.parse_args()

    print("=" * 62)
    print("  UNIAO DOS AFUNDADOS - Dashboard")
    print("=" * 62)

    settings = config.load_settings(require_key=not args.build)
    window = args.days if args.days is not None else settings.window_days
    conn = store.connect(config.DB_PATH)
    # store.connect ja migrou o schema; o backfill le o raw ja gravado e so roda
    # uma vez por versao. Vale nos dois modos: no --build e a unica chance de
    # preencher as colunas novas das partidas antigas.
    store.backfill(conn)

    try:
        if not args.build:
            fetch.run(settings, conn)
        else:
            print("\nModo --build: usando apenas o banco local.")
            roster = {(p.game_name.lower(), p.tag_line.lower())
                      for p in settings.players}
            saiu = store.prune_players(conn, roster)
            if saiu:
                print(f"  removidos do banco: {', '.join(saiu)}")
            corrigidos = store.refresh_tracked(conn)
            if corrigidos:
                print(f"  {corrigidos} participacoes reassociadas ao roster atual")

        champ_index = riot.champion_index()
        ddragon_ver = riot.ddragon_version()

        # As personalizadas entram AQUI, nao no fetch: elas nao existem na API da
        # Riot. O coletor local (personalizadas.py) so escreve arquivo; e este
        # import que leva o arquivo para o banco -- no PC de quem coletou e,
        # igualzinho, no GitHub Actions, que le a pasta versionada.
        inhouse.importar(conn, config.ROOT / "personalizadas", champ_index)

        print("\nCalculando KPIs...")
        payload = kpi.build_payload(
            conn,
            window_days=window,
            min_games=settings.min_games,
            champ_index=champ_index,
            ddragon_ver=ddragon_ver,
            platform=settings.platform,
        )
        # Icones embutidos: sem isso o painel depende de CDN e fica sem imagem
        # nenhuma offline ou em visualizador com CSP restritiva.
        payload["icons"] = assets.construir(payload, ddragon_ver, champ_index)
        payload["icons"].update(assets.extras(payload, ddragon_ver))

        total = payload["data"]["all"]["team"]["uniqueMatches"]
        if not total:
            print("\n  Aviso: nenhuma partida na janela de "
                  f"{window} dias. Tente 'python run.py --build --days 0'.")

        out = render.render(payload, config.TEMPLATE_PATH, config.OUT_PATH)
        print(f"\nPronto: {out}")
        print(f"  {total} partidas | janela de {window or 'todos os'} dias "
              f"| minimo {settings.min_games} jogos para o ranking")

        if args.open:
            webbrowser.open(out.as_uri())
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido. O que ja foi baixado esta salvo no banco.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"\nERRO: {exc}", file=sys.stderr)
        sys.exit(1)

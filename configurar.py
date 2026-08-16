"""Grava a chave da Riot no .env, sem mostrar ela na tela.

    python configurar.py

A chave e digitada direto no seu terminal, vai para o arquivo .env local e nao
aparece no historico do console. O .env ja esta no .gitignore.
"""

from __future__ import annotations

import getpass
import re
import sys

from uda.config import ROOT

ENV = ROOT / ".env"
PADRAO = re.compile(r"^RGAPI-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")

DEFAULTS = {
    "MATCH_COUNT": "100",
    "WINDOW_DAYS": "90",
    "MIN_GAMES": "5",
}

CABECALHO = """# UNIAO DOS AFUNDADOS - configuracao local
# Este arquivo NAO deve ser compartilhado. Ele contem sua chave da Riot.
"""

COMENTARIOS = {
    "MATCH_COUNT": "# Partidas por jogador na primeira carga (max 100).",
    "WINDOW_DAYS": "# Janela de analise do dashboard, em dias. 0 = historico completo.",
    "MIN_GAMES": "# Minimo de partidas para entrar no ranking.",
}


def ler_env_atual() -> dict[str, str]:
    valores = dict(DEFAULTS)
    if not ENV.exists():
        return valores
    for linha in ENV.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        if chave in DEFAULTS:
            valores[chave] = valor.strip()
    return valores


def _limpar(bruto: str) -> str:
    """Tira aspas, espacos e qualquer caractere invisivel vindo da colagem."""
    return "".join(
        c for c in bruto.strip().strip('"').strip("'")
        if c.isprintable() and not c.isspace()
    )


def pedir_chave() -> str:
    # --stdin le de um pipe (automacao e terminais que travam com entrada oculta).
    if "--stdin" in sys.argv:
        return _limpar(sys.stdin.readline())

    print("Cole a chave da Riot (RGAPI-...) e aperte Enter.\n")

    # Primeira tentativa com entrada oculta. Em muitos consoles do Windows o
    # Ctrl+V nao funciona com entrada oculta, entao caimos para entrada visivel.
    tentativas = [
        ("oculta", "  Chave (nao aparece enquanto digita): "),
        ("visivel", "  Chave (agora aparece na tela): "),
        ("visivel", "  Ultima tentativa -- Chave: "),
    ]

    for modo, prompt in tentativas:
        if modo == "oculta":
            try:
                chave = _limpar(getpass.getpass(prompt))
            except Exception:
                continue
        else:
            chave = _limpar(input(prompt))

        if chave.startswith("RGAPI-"):
            return chave

        if not chave:
            print("     nada foi colado.")
        else:
            print(f"     isso nao comeca com 'RGAPI-' (veio '{chave[:12]}...').")

        if modo == "oculta":
            print("     Seu terminal pode estar bloqueando a colagem no modo oculto.")
            print("     Vou pedir de novo com a digitacao VISIVEL.\n")

    return ""


def testar(chave: str) -> bool:
    """Faz uma unica chamada real para provar que a chave funciona."""
    try:
        from uda.riot import RiotClient, RiotError
    except ImportError:
        return True

    print("\nTestando a chave contra a API da Riot...")
    client = RiotClient(chave, verbose=False)
    try:
        conta = client.account_by_riot_id("Gui", "Gui")
    except RiotError as exc:
        print(f"  A chave NAO funcionou: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  Nao deu para testar agora ({exc}). A chave foi salva mesmo assim.")
        return True

    if conta and conta.get("puuid"):
        print("  Chave valida. A Riot respondeu normalmente.")
    else:
        print("  A chave funciona (a Riot respondeu), mas o Riot ID de teste nao existe.")
        print("  Isso ja e um sinal: rode 'python verificar.py' para auditar as 12 contas.")
    return True


def main() -> int:
    chave = pedir_chave()

    if not chave:
        print("\nNada foi digitado. Nenhuma alteracao feita.")
        return 1

    if not chave.startswith("RGAPI-"):
        print(f"\n  Isso nao parece uma chave da Riot (comeca com '{chave[:6]}...').")
        print("  A chave certa comeca com 'RGAPI-'. Nenhuma alteracao feita.")
        return 1

    if not PADRAO.match(chave):
        print("\n  Aviso: o formato nao bate com o padrao usual da Riot")
        print("  (RGAPI- seguido de 8-4-4-4-12 caracteres hexadecimais).")
        if input("  Salvar assim mesmo? [s/N] ").strip().lower() not in ("s", "sim"):
            print("  Cancelado.")
            return 1

    valores = ler_env_atual()
    linhas = [CABECALHO, f"RIOT_API_KEY={chave}", ""]
    for nome, padrao in DEFAULTS.items():
        linhas.append(COMENTARIOS[nome])
        linhas.append(f"{nome}={valores.get(nome, padrao)}")
        linhas.append("")

    ENV.write_text("\n".join(linhas), encoding="utf-8")
    print(f"\nChave salva em {ENV}")
    print(f"  (mostrando so o fim, para conferencia: ...{chave[-6:]})")

    testar(chave)

    print("\nProximo passo:")
    print("  python verificar.py     audita todas as contas antes de gastar a coleta")
    print("  python run.py --open    faz a coleta completa e abre o dashboard")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado. Nenhuma alteracao feita.")
        sys.exit(130)

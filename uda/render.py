"""Injeta o payload calculado dentro do template e escreve o dashboard.html."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

MARKER = "/*__UDA_DATA__*/null"
LOGO_MARKER = "__UDA_LOGO__"
LOGO_FILE = Path(__file__).resolve().parent.parent / "assets" / "uda-crest-192.png"


def _logo_data_uri() -> str:
    """Brasao embutido: o HTML continua funcionando se for compartilhado sozinho."""
    if not LOGO_FILE.exists():
        return ""
    b64 = base64.b64encode(LOGO_FILE.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"

# O payload vira um literal JavaScript dentro de <script>. Um "<" vindo de um nome
# pode empurrar o tokenizer do HTML para o estado script-data-double-escaped
# (a sequencia "<!--<script" e a pior delas) e a pagina abre em branco.
# Todo "<" do blob esta necessariamente dentro de uma string JSON -- os unicos
# caracteres estruturais do JSON sao {} [] : , e aspas -- entao escapar e sempre
# seguro: "<" num literal JS volta a ser "<" na leitura.
# U+2028 e U+2029 sao validos em JSON mas quebravam literais JS antes do ES2019.
ESCAPES = (
    ("<", "\\u003c"),
    (">", "\\u003e"),
    ("&", "\\u0026"),
    (" ", "\\u2028"),
    (" ", "\\u2029"),
)


def render(payload: dict[str, Any], template_path: Path, out_path: Path) -> Path:
    template = template_path.read_text(encoding="utf-8")
    if MARKER not in template:
        raise RuntimeError(f"Marcador {MARKER} nao encontrado em {template_path}")

    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for bruto, escapado in ESCAPES:
        blob = blob.replace(bruto, escapado)

    saida = template.replace(MARKER, blob).replace(LOGO_MARKER, _logo_data_uri())
    out_path.write_text(saida, encoding="utf-8")
    return out_path

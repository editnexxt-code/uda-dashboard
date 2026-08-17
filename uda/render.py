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
    """Brasao embutido: o HTML continua funcionando se for compartilhado sozinho.

    Em WebP porque o marcador aparece TRES vezes no template (favicon, brasao da
    barra lateral e a const que alimenta a marca d'agua dos graficos) -- e como
    str.replace troca todas as ocorrencias, o PNG de 75KB entrava 3x, virando
    ~300KB de base64 num arquivo de 1.7MB. O mesmo brasao em WebP fica em ~7KB.
    Sem Pillow, volta para o PNG: arquivo maior, mas continua funcionando.
    """
    if not LOGO_FILE.exists():
        return ""
    raw = LOGO_FILE.read_bytes()
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=90, method=6)
        if buf.tell() < len(raw):
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/webp;base64,{b64}"
    except Exception:  # noqa: BLE001
        pass
    b64 = base64.b64encode(raw).decode("ascii")
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

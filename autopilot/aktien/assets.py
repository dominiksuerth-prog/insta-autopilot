"""Schriften für die Reels einmalig besorgen (frei lizenziert, OFL) und in data/aktien/fonts ablegen.

Quelle: Fontsource auf GitHub (.woff), umgewandelt in .ttf – matplotlib kann nur .ttf/.otf lesen.
"""
import io
import os
import urllib.request

from .config import FONT_DIR

FONTS = {
    "anton-latin-400-normal": "anton",
    "space-grotesk-latin-700-normal": "space-grotesk",
    "space-grotesk-latin-500-normal": "space-grotesk",
    "jetbrains-mono-latin-700-normal": "jetbrains-mono",
    "jetbrains-mono-latin-500-normal": "jetbrains-mono",
}
BASE = "https://raw.githubusercontent.com/fontsource/font-files/main/fonts/google/{fam}/files/{name}.woff"


def setup_fonts():
    from fontTools.ttLib import TTFont
    os.makedirs(FONT_DIR, exist_ok=True)
    for name, fam in FONTS.items():
        out = os.path.join(FONT_DIR, name + ".ttf")
        if os.path.exists(out):
            continue
        data = urllib.request.urlopen(BASE.format(fam=fam, name=name), timeout=30).read()
        f = TTFont(io.BytesIO(data))
        f.flavor = None
        f.save(out)
        print("Schrift geladen:", name)

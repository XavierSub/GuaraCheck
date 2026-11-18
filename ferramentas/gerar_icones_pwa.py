"""Gera os ícones do app instalável (PWA) a partir de assets/guara-icone.png (o pin do guará, com fundo transparente).
Rode de novo se o desenho mudar:  python ferramentas/gerar_icones_pwa.py   (precisa do Pillow: pip install pillow)

  assets/icone-512.png            ícone comum, 512x512, o pin ocupa ~80% da altura
  assets/icone-maskable-512.png   ícone "maskable" (o Android recorta em círculo/quadrado arredondado): o pin fica na zona segura (~65%)
"""
from pathlib import Path
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FUNDO = (245, 247, 237, 255)          # #f5f7ed, o fundo do site (e o background_color do manifesto)


def icone(tam: int, altura_pin: float, destino: str):
    pin = Image.open(ASSETS / "guara-icone.png").convert("RGBA")
    alt = round(tam * altura_pin)
    pin = pin.resize((round(pin.width * alt / pin.height), alt), Image.LANCZOS)
    tela = Image.new("RGBA", (tam, tam), FUNDO)
    tela.alpha_composite(pin, ((tam - pin.width) // 2, (tam - pin.height) // 2))
    tela.convert("RGB").save(ASSETS / destino, optimize=True)
    print("gerado", destino)


if __name__ == "__main__":
    icone(512, 0.80, "icone-512.png")
    icone(512, 0.65, "icone-maskable-512.png")

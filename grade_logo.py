"""Rodapé da GRADE: troca o bloco do GP Poker Club (canto inferior direito) pela logo da North.
A logo da Union Prime (à esquerda) não é tocada. Não mexe em mais nada na arte."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

NORTH_ID = "ID: 982037"


def _fonte(px):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"):
        try:
            return ImageFont.truetype(p, px)
        except Exception:
            pass
    return ImageFont.load_default()


def acha_gp(A):
    """caixa (x0,y0,x1,y1) do bloco GP no canto inferior direito, ou None"""
    H, W = A.shape[:2]
    wx0, wx1, wy0, wy1 = int(W * .52), int(W * .842), int(H * .952), int(H * .988)
    lum = A[wy0:wy1, wx0:wx1].max(axis=2)
    ok = lum > 115
    rr = np.where(ok.sum(axis=1) > 1)[0]
    cc = np.where(ok.sum(axis=0) > 1)[0]
    if len(rr) < 8 or len(cc) < int(W * .08):
        return None
    return wx0 + cc.min(), wy0 + rr.min(), wx0 + cc.max(), wy0 + rr.max()


def aplica_logo_grade(img, logo_path):
    img = img.convert("RGB")
    A = np.array(img).astype(float)
    H, W = A.shape[:2]
    box = acha_gp(A)
    if box is None:
        return None
    gx0, gy0, gx1, gy1 = box
    cx, cy = (gx0 + gx1) // 2, (gy0 + gy1) // 2
    mx, my = int(W * .012), int(H * .004)
    px0, px1 = max(int(W * .5), gx0 - mx), min(W - 1, gx1 + int(W * .006))
    py0, py1 = max(int(H * .948), gy0 - my), min(H - 1, gy1 + int(H * .011))
    # fundo: interpola, linha a linha, entre uma faixa limpa à esquerda e outra à direita do bloco
    lw = max(6, int(W * .012))
    L = A[py0:py1, max(0, px0 - lw - 4):max(1, px0 - 4)].mean(axis=1)
    R = A[py0:py1, min(W - 1, px1 + 4):min(W, px1 + 4 + lw)]
    R = R.mean(axis=1) if R.shape[1] else L
    t = np.linspace(0, 1, px1 - px0)[None, :, None]
    fill = L[:, None, :] * (1 - t) + R[:, None, :] * t
    fill = fill + np.random.default_rng(3).normal(0, 1.2, fill.shape)
    out = A.copy()
    m = np.zeros((H, W)); m[py0:py1, px0:px1] = 1
    m = np.array(Image.fromarray((m * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(2))) / 255
    full = A.copy(); full[py0:py1, px0:px1] = fill
    out = A * (1 - m[..., None]) + full * m[..., None]
    base = Image.fromarray(np.clip(out, 0, 255).astype("uint8")).convert("RGBA")
    # logo North horizontal: emblema + palavra, e o ID
    lg = Image.open(logo_path).convert("RGBA")
    lw_, lh_ = lg.size
    cut = int(lh_ * .625)
    emb = lg.crop((0, 0, lw_, cut)); emb = emb.crop(emb.getchannel("A").getbbox())
    pal = lg.crop((0, cut, lw_, lh_)); pal = pal.crop(pal.getchannel("A").getbbox())
    h = int(H * .042)
    e = emb.resize((int(emb.width * h / emb.height), h), Image.LANCZOS)
    ph = int(h * .78)
    p = pal.resize((int(pal.width * ph / pal.height), ph), Image.LANCZOS)
    fonte = _fonte(int(h * .42)); d = ImageDraw.Draw(base)
    tw = d.textlength(NORTH_ID, font=fonte)
    gap = int(h * .28)
    total = e.width + gap + p.width + gap * 2 + tw
    maxw = W * .31
    k = min(1.0, maxw / total)
    if k < 1:
        e = e.resize((int(e.width * k), int(e.height * k)), Image.LANCZOS)
        p = p.resize((int(p.width * k), int(p.height * k)), Image.LANCZOS)
        fonte = _fonte(int(h * .42 * k)); tw = d.textlength(NORTH_ID, font=fonte); gap = int(gap * k)
        total = e.width + gap + p.width + gap * 2 + tw
    x = int(cx - total / 2); x = max(int(W * .5), min(x, int(W - total - W * .02)))
    base.alpha_composite(e, (x, int(cy - e.height / 2)))
    x2 = x + e.width + gap
    base.alpha_composite(p, (x2, int(cy - p.height / 2)))
    x3 = x2 + p.width + gap * 2
    bb = d.textbbox((0, 0), NORTH_ID, font=fonte)
    d.text((x3, cy - (bb[1] + bb[3]) / 2), NORTH_ID, font=fonte, fill=(235, 235, 235, 255))
    return base.convert("RGB")

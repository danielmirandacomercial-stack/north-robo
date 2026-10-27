"""North Robô — pega os banners no Drive e manda para o Telegram.
- Grade: assim que aparecer a versão de hoje.
- Torneios (exceto satélites): 15 min antes do início, com a logo da North no rodapé.
"""
import io, json, os, re, sys, unicodedata, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, requests, pytesseract
from PIL import Image, ImageOps, ImageDraw, ImageFilter
from scipy.ndimage import gaussian_filter1d

TZ = ZoneInfo("America/Sao_Paulo")
ANTECEDENCIA_MIN = int(os.environ.get("ANTECEDENCIA_MIN", "15"))
JANELA_APOS_MIN = int(os.environ.get("JANELA_APOS_MIN", "30"))    # ainda envia se atrasar até X min após o início
PRAZO_GRADE_H = int(os.environ.get("PRAZO_GRADE_H", "12"))        # avisa se a grade não saiu até esta hora
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
DIAS = ["SEGUNDA", "TERCA", "QUARTA", "QUINTA", "SEXTA", "SABADO", "DOMINGO"]
HERE = os.path.dirname(os.path.abspath(__file__))

def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").upper().strip()

# ---------- Google Drive ----------
class Drive:
    def __init__(self):
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30)
        d = r.json()
        if "access_token" not in d:
            raise AuthError(d)
        self.h = {"Authorization": "Bearer " + d["access_token"]}
    def list(self, folder):
        out, tok = [], None
        while True:
            p = {"q": f"'{folder}' in parents and trashed=false", "pageSize": 200, "supportsAllDrives": "true",
                 "includeItemsFromAllDrives": "true", "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,shortcutDetails)"}
            if tok: p["pageToken"] = tok
            d = requests.get("https://www.googleapis.com/drive/v3/files", headers=self.h, params=p, timeout=30).json()
            if "files" not in d: raise RuntimeError(f"Drive: {d}")
            out += d["files"]; tok = d.get("nextPageToken")
            if not tok: return out
    def download(self, fid):
        r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}", headers=self.h,
                         params={"alt": "media", "supportsAllDrives": "true"}, timeout=60)
        r.raise_for_status(); return r.content

class AuthError(Exception): pass

def is_folder(f):
    return f["mimeType"] == "application/vnd.google-apps.folder" or \
        (f["mimeType"] == "application/vnd.google-apps.shortcut" and
         f.get("shortcutDetails", {}).get("targetMimeType") == "application/vnd.google-apps.folder")
def fid(f):
    return f.get("shortcutDetails", {}).get("targetId", f["id"])

def pasta_feed(drive, root, hoje):
    """root/<DIA>/ (subpasta com 'FEED' no nome, se existir)"""
    dia = DIAS[hoje.weekday()]
    dias = [f for f in drive.list(root) if is_folder(f) and norm(f["name"]) == dia]
    if not dias: return None, f"pasta do dia ({dia}) não encontrada"
    pasta = fid(dias[0]); itens = drive.list(pasta)
    feed = [f for f in itens if is_folder(f) and "FEED" in norm(f["name"])]
    if feed: itens = drive.list(fid(feed[0]))
    return [f for f in itens if not is_folder(f) and f["mimeType"].startswith("image/")], None

# ---------- Telegram ----------
def tg(method, **kw):
    files = kw.pop("files", None)
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                      data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], **kw}, files=files, timeout=60)
    if not r.ok: print("Telegram erro:", r.text, file=sys.stderr)
    return r.ok
def tg_msg(t): return tg("sendMessage", text=t)
def tg_img(png, caption):
    if len(png) < 9_000_000 and tg("sendPhoto", caption=caption, files={"photo": ("arte.png", png)}):
        return True
    return tg("sendDocument", caption=caption, files={"document": ("arte.png", png)})

# ---------- OCR: data e hora no topo da arte do torneio ----------
def le_data_hora(img):
    W, H = img.size
    top = img.convert("RGB").crop((0, 0, W, int(H * 0.22)))
    g = ImageOps.invert(ImageOps.autocontrast(top.convert("L"))).resize((top.width * 2, top.height * 2))
    t = pytesseract.image_to_string(g, config="--psm 6")
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2}).{0,25}?(\d{1,2})\s*[:.]\s*(\d{2})", t)
    if not m: return None
    d, mo, h, mi = map(int, m.groups())
    if not (1 <= d <= 31 and 1 <= mo <= 12 and h < 24 and mi < 60): return None
    return d, mo, h, mi

# ---------- rodapé: Union Prime à esquerda + logo North à direita ----------
def aplica_logo(img):
    img = img.convert("RGB"); W, H = img.size; A = np.array(img).astype(float)
    fx = lambda v: int(v / 1336 * W); fy = lambda v: int(v / 1655 * H)
    lx0, lx1, ly0, ly1 = fx(455), fx(885), fy(1352), fy(1505)
    crop = A[ly0:ly1, lx0:lx1]; lum = crop.max(axis=2)
    alpha = np.clip((lum - 95) / 60, 0, 1); ok = alpha > 0.6
    rr = np.where(ok.sum(axis=1) > 2)[0]; cc = np.where(ok.sum(axis=0) > 2)[0]
    if len(rr) < 20 or len(cc) < 50: return None   # não achou a logo Union: não mexe
    bx0, bx1, by0, by1 = cc.min(), cc.max(), rr.min(), rr.max()
    uni = Image.fromarray(np.dstack([crop, alpha * 255]).astype("uint8")[max(0, by0 - 3):by1 + 4, max(0, bx0 - 3):bx1 + 4], "RGBA")
    x0, y0, x1, y1 = fx(440), fy(1340), fx(900), fy(1515)
    top = gaussian_filter1d(A[y0 - 12:y0 - 2, x0:x1].mean(axis=0), 12, axis=0)
    bot = gaussian_filter1d(A[y1 + 2:y1 + 12, x0:x1].mean(axis=0), 12, axis=0)
    t = np.linspace(0, 1, y1 - y0)[:, None, None]
    fill = top[None] * (1 - t) + bot[None] * t
    sig = A[y0 - 40:y0 - 5, x0:x1].std(axis=(0, 1)).mean() * 0.6
    fill = fill + np.random.default_rng(1).normal(0, sig, fill.shape)
    full = A.copy(); full[y0:y1, x0:x1] = fill
    m = np.zeros(A.shape[:2]); m[y0 + 8:y1 - 8, x0 + 8:x1 - 8] = 1
    m = np.array(Image.fromarray((m * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(5))) / 255
    clean = Image.fromarray(np.clip(A * (1 - m[..., None]) + full * m[..., None], 0, 255).astype("uint8")).convert("RGBA")
    north = Image.open(os.path.join(HERE, "logo_north_branca.png")).convert("RGBA")
    uh = int(H * 140 / 1655); u = uni.resize((int(uni.width * uh / uni.height), uh), Image.LANCZOS)
    nh = int(H * 210 / 1655); n = north.resize((int(north.width * nh / north.height), nh), Image.LANCZOS)
    gap = int(W * 70 / 1336); sx = (W - (u.width + gap + n.width)) // 2; cy = int(H * 1428 / 1655)
    clean.alpha_composite(u, (sx, cy - u.height // 2))
    lx = sx + u.width + gap // 2
    ImageDraw.Draw(clean).line((lx, cy - int(H * .036), lx, cy + int(H * .036)), fill=(200, 160, 70, 255), width=max(2, W // 650))
    clean.alpha_composite(n, (sx + u.width + gap, cy - nh // 2))
    return clean.convert("RGB")

# ---------- estado (evita repetir) ----------
def load_state():
    try: return json.load(open(STATE_FILE))
    except Exception: return {}
def save_state(s):
    # guarda só os últimos 3 dias
    lim = (dt.datetime.now(TZ) - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    s = {k: v for k, v in s.items() if v.get("dia", "9999") >= lim}
    json.dump(s, open(STATE_FILE, "w"), indent=1, ensure_ascii=False)

def main(now=None, drive=None):
    now = now or dt.datetime.now(TZ); hoje = now.date(); hoje_s = hoje.isoformat()
    st = load_state(); drive = drive or Drive()
    files, erro = pasta_feed(drive, os.environ["DRIVE_FOLDER_ID"], hoje)
    if erro:
        k = "erro-pasta-" + hoje_s
        if k not in st: tg_msg("⚠️ North Robô: " + erro); st[k] = {"dia": hoje_s}
        save_state(st); return
    corte_grade = dt.datetime.combine(hoje, dt.time(0), TZ) - dt.timedelta(hours=PRAZO_GRADE_H)
    # --- grade ---
    grades = [f for f in files if "GRADE" in norm(f["name"])]
    achou = False
    for f in grades:
        mod = dt.datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")).astimezone(TZ)
        if mod < corte_grade: continue
        achou = True
        k = f"grade-{hoje_s}-{f['id']}-{f['modifiedTime']}"
        if k in st: continue
        antes = any(x.startswith(f"grade-{hoje_s}-") for x in st)
        png = drive.download(f["id"])
        if tg_img(png, ("🔄 GRADE ATUALIZADA " if antes else "📅 GRADE ") + hoje.strftime("%d/%m")): st[k] = {"dia": hoje_s}
    if not achou and now.hour >= PRAZO_GRADE_H:
        k = "grade-atrasada-" + hoje_s
        if k not in st: tg_msg(f"⏰ A grade de hoje ({hoje.strftime('%d/%m')}) ainda não foi atualizada no Drive."); st[k] = {"dia": hoje_s}
    # --- torneios ---
    for f in files:
        n = norm(f["name"])
        if "GRADE" in n or n.startswith("SAT"): continue
        k = f"torneio-{hoje_s}-{f['id']}"
        if k in st: continue
        mod = dt.datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")).astimezone(TZ)
        if mod < corte_grade - dt.timedelta(days=3): continue          # claramente antigo, nem baixa
        png = drive.download(f["id"]); img = Image.open(io.BytesIO(png))
        dh = le_data_hora(img)
        if not dh: 
            k2 = "ocr-falha-" + hoje_s + f["id"]
            if k2 not in st: tg_msg(f"⚠️ Não consegui ler data/hora da arte {f['name']}."); st[k2] = {"dia": hoje_s}
            continue
        d, mo, h, mi = dh
        if (d, mo) != (hoje.day, hoje.month): continue                 # arte de outro dia
        inicio = dt.datetime.combine(hoje, dt.time(h, mi), TZ)
        if not (inicio - dt.timedelta(minutes=ANTECEDENCIA_MIN) <= now <= inicio + dt.timedelta(minutes=JANELA_APOS_MIN)): continue
        out = aplica_logo(img)
        if out is None:
            tg_msg(f"⚠️ Não achei a logo Union na arte {f['name']}; enviando sem a logo North.")
            buf = png
        else:
            b = io.BytesIO(); out.save(b, "PNG", optimize=True); buf = b.getvalue()
        nome = os.path.splitext(f["name"])[0]
        if tg_img(buf, f"🃏 {nome} começa às {h:02d}:{mi:02d}"): st[k] = {"dia": hoje_s}
    save_state(st)

if __name__ == "__main__":
    try: main()
    except AuthError as e:
        try: tg_msg("🔑 North Robô: a autorização do Google expirou. Gere uma nova chave (autorizar_drive.html) e atualize GOOGLE_REFRESH_TOKEN no GitHub.")
        finally: print("Auth:", e, file=sys.stderr); sys.exit(1)

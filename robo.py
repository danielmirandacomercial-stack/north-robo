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
def _ocr(im, cfg):
    return pytesseract.image_to_string(im, config=cfg).strip()

def _prep(crop, sc):
    g = ImageOps.invert(ImageOps.autocontrast(crop)).resize((crop.width * sc, crop.height * sc), Image.LANCZOS)
    return ImageOps.expand(g, 30, fill=255)

def _hora(t):
    t = t.replace("O", "0").replace("o", "0").replace(" ", "")
    m = re.search(r"(\d{1,2})[:Hh.](\d{2})", t) or re.search(r"^\D*(\d{2})(\d{2})\D*$", t)
    if m: h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})[Hh]\D*$", t)
        if not m: return None
        h, mi = int(m.group(1)), 0
    return (h, mi) if h < 24 and mi < 60 and mi % 5 == 0 else None

def le_data_hora(img):
    W, H = img.size
    gray = img.convert("L")
    # procura a linha de data: maior bloco de texto claro na faixa central do topo (ignora curvas finas)
    cx0, cx1, cy1 = int(W * 0.22), int(W * 0.78), int(H * 0.22)
    ok = np.array(gray.crop((cx0, 0, cx1, cy1))) > 200
    rows = np.where(ok.sum(axis=1) > 3)[0]
    if len(rows) < 5: return None
    blocks, st, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - prev > 6: blocks.append((st, prev)); st = r
        prev = r
    blocks.append((st, prev))
    fortes = [b for b in blocks if ok[b[0]:b[1] + 1].sum() >= 400]
    if not fortes: return None
    y0, y1 = fortes[0]          # a linha de data é o primeiro texto de verdade (curvas finas ficam abaixo de 400 px)
    colsum = ok[y0:y1 + 1].sum(axis=0); cols = np.where(colsum > 0)[0]
    groups, st, prev = [], cols[0], cols[0]
    for c in cols[1:]:
        if c - prev > 40: groups.append((st, prev)); st = c
        prev = c
    groups.append((st, prev))
    gx0, gx1 = max(groups, key=lambda g: colsum[g[0]:g[1] + 1].sum())
    if gx1 - gx0 < 50: return None
    x0, x1 = gx0 + cx0, gx1 + cx0
    line = gray.crop((max(0, x0 - 5), max(0, y0 - 5), min(W, x1 + 5), min(cy1, y1 + 5)))
    w = line.width
    # data: parte da esquerda da linha
    dm = None
    for sc in (2, 3, 4):
        t = _ocr(_prep(line, sc), "--psm 7")
        m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", t)
        if m and 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12: dm = (int(m.group(1)), int(m.group(2))); break
    # hora: parte da direita da linha
    hm = None
    for sc in (4, 3, 6, 2):
        t = _ocr(_prep(line.crop((int(w * 0.66), 0, w, line.height)), sc), "--psm 7 -c tessedit_char_whitelist=0123456789:HhxX")
        hm = _hora(t)
        if hm: break
    if not (dm and hm): return None
    return dm[0], dm[1], hm[0], hm[1]

# ---------- informações da arte (GTD, buy-in, late) e legenda ----------
PKO = {"THE START", "THE LOW", "HIGH ROLLER", "HIGH ROLLER LIGHT", "MINI HIGH ROLLER"}   # editar se mudar
SITE = "www.northpokeronline.com.br"

def _band(gray, a, b, sc, thr=False, cfg="--psm 6"):
    W, H = gray.size; c = gray.crop((0, int(H * a), W, int(H * b)))
    c = c.point(lambda v: 0 if v > 120 else 255) if thr else ImageOps.invert(ImageOps.autocontrast(c))
    c = c.resize((int(c.width * sc), int(c.height * sc)), Image.LANCZOS)
    return pytesseract.image_to_string(c, config=cfg).strip().replace("\n", " ")

def le_info(img):
    g = img.convert("L"); info = {}
    info["buyin"] = info["late"] = None
    for a, b, sc in ((.63, .74, 2), (.60, .72, 2), (.63, .74, 1), (.60, .72, 3), (.58, .76, 2)):
        t = _band(g, a, b, sc)
        if not info["buyin"]:
            m = re.search(r"BU\w{0,2}\W*IN\s*(\d+)", t, re.I) or re.search(r"(\d+)\s*LATE", t, re.I)
            if m: info["buyin"] = m.group(1)
        if not info["late"]:
            m = re.search(r"LATE\s*(\d+)", t, re.I)
            if m: info["late"] = m.group(1)
        if info["buyin"] and info["late"]: break
    info["gtd"] = None
    for a, b, sc, thr in ((.34, .64, .5, True), (.38, .60, .75, True), (.34, .64, .75, True), (.35, .56, .75, False), (.35, .56, 1, False)):
        t = _band(g, a, b, sc, thr=thr)
        m = re.search(r"(\d+)\s*(K?)\s*G\w{0,2}D", t)
        if m and (m.group(1) != "1" or m.group(2)): info["gtd"] = (m.group(1) + m.group(2)).upper(); break
        if re.search(r"[TI1lA~|]\s*K\s*G\w{0,2}D", t): info["gtd"] = "1K"; break
    return info

MODELOS = [
 "🔥 {nome} começa às {hora}!\n{linha}\nA mesa tá armada, bora sentar? ♠️",
 "♠️ Hora de dar all-in! {nome} às {hora}.\n{linha}\nPega sua cadeira! 🃏",
 "🃏 Show de cartas! {nome} abre às {hora}.\n{linha}\nQuem vem pra mesa?",
 "⏰ Faltam poucos minutos pro {nome} ({hora})!\n{linha}\nNão fica de fora! 🔥",
 "🚀 Bora subir o stack! {nome} às {hora}.\n{linha}\nO flop espera por você ♥️",
 "♣️ {nome} vai rolar às {hora}!\n{linha}\nChega, senta e joga! 🎯",
 "🔔 Última chamada! {nome} às {hora}.\n{linha}\nVem buscar o seu pote! 💰",
 "♦️ A bolha estoura às {hora}! {nome}.\n{linha}\nTá com o jogo? Então bora! 🔥",
]

MODELOS_GRADE = [
 "🔥 A grade de hoje tá no ar! {dia}, {data}.\nTorneio pra todo gosto, do começo ao fim da noite. Escolhe o seu e bora pra mesa! ♠️",
 "🃏 Grade de {dia} ({data}) liberada!\nDá uma olhada nos horários e já separa o seu stack. Hoje tem jogo! 🔥",
 "♠️ Salve, galera! Confira a grade de hoje ({data}).\nTem GTD rolando, bounty e muita ação. Quem vem? 🎯",
 "🚀 Bora pra mais um dia de poker! Grade de {dia}, {data}.\nMarca o horário do seu torneio favorito e senta com a gente ♥️",
]
DIAS_NOME = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

def legenda_grade(hoje, idx, atualizada=False):
    t = MODELOS_GRADE[idx % len(MODELOS_GRADE)].format(dia=DIAS_NOME[hoje.weekday()], data=hoje.strftime("%d/%m"))
    if atualizada: t = "🔄 GRADE ATUALIZADA\n" + t
    return t + f"\n\n🪙 Fichas no site {SITE}"

def monta_legenda(nome, hora, info, idx):
    partes = []
    if info.get("gtd"): partes.append(f"💰 {info['gtd']} GTD")
    if info.get("buyin"): partes.append(f"🎟️ Buy-in {info['buyin']}")
    if info.get("late"): partes.append(f"⏳ Late {info['late']} min")
    linha = " · ".join(partes) if partes else "Garanta sua vaga!"
    txt = MODELOS[idx % len(MODELOS)].format(nome=nome.upper(), hora=hora, linha=linha)
    if nome.upper() in PKO: txt += "\n🎯 Bounty: cada eliminação vale prêmio!"
    return txt + f"\n\n🪙 Fichas no site {SITE}"

# ---------- rodapé: Union Prime à esquerda + logo North à direita ----------
def aplica_logo(img):
    img = img.convert("RGB"); W, H = img.size; A = np.array(img).astype(float)
    lx0, lx1, ly0, ly1 = int(W * .32), int(W * .68), int(H * .815), int(H * .945)
    crop = A[ly0:ly1, lx0:lx1]; lum = crop.max(axis=2)
    alpha = np.clip((lum - 95) / 60, 0, 1); ok = alpha > 0.6
    rr = np.where(ok.sum(axis=1) > 2)[0]; cc = np.where(ok.sum(axis=0) > 2)[0]
    if len(rr) < 20 or len(cc) < 50: return None   # não achou a logo Union: não mexe
    bx0, bx1, by0, by1 = cc.min(), cc.max(), rr.min(), rr.max()
    uni = Image.fromarray(np.dstack([crop, alpha * 255]).astype("uint8")[max(0, by0 - 3):by1 + 4, max(0, bx0 - 3):bx1 + 4], "RGBA")
    # apaga exatamente onde a logo estava (caixa detectada + margem)
    m_ = 16
    x0, x1 = max(0, lx0 + bx0 - m_), min(W, lx0 + bx1 + m_); y0, y1 = max(14, ly0 + by0 - m_), min(H - 14, ly0 + by1 + m_)
    top = gaussian_filter1d(A[y0 - 10:y0 - 2, x0:x1].mean(axis=0), 10, axis=0)
    bot = gaussian_filter1d(A[y1 + 2:y1 + 10, x0:x1].mean(axis=0), 10, axis=0)
    t = np.linspace(0, 1, y1 - y0)[:, None, None]
    fill = top[None] * (1 - t) + bot[None] * t
    ref = A[y1 + 2:y1 + 14, x0:x1]; mad = np.median(np.abs(ref - np.median(ref, axis=(0, 1)))) * 1.4826
    fill = fill + np.random.default_rng(1).normal(0, min(mad, 6), fill.shape)
    full = A.copy(); full[y0:y1, x0:x1] = fill
    m = np.zeros(A.shape[:2]); m[y0 + 6:y1 - 6, x0 + 6:x1 - 6] = 1
    m = np.array(Image.fromarray((m * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(4))) / 255
    clean = Image.fromarray(np.clip(A * (1 - m[..., None]) + full * m[..., None], 0, 255).astype("uint8")).convert("RGBA")
    north = Image.open(os.path.join(HERE, "logo_north_branca.png")).convert("RGBA")
    uh = int(H * 140 / 1655); u = uni.resize((int(uni.width * uh / uni.height), uh), Image.LANCZOS)
    nh = int(H * 210 / 1655); n = north.resize((int(north.width * nh / north.height), nh), Image.LANCZOS)
    gap = int(W * 70 / 1336); sx = (W - (u.width + gap + n.width)) // 2; cy = ly0 + (by0 + by1) // 2
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
        rot = st.get("_rot", {"dia": hoje_s, "n": 0}); idx = rot["n"]
        if tg_img(png, legenda_grade(hoje, idx, antes)): st[k] = {"dia": hoje_s}; st["_rot"] = {"dia": hoje_s, "n": idx + 1}
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
        try: info = le_info(img)
        except Exception: info = {}
        rot = st.get("_rot", {"dia": hoje_s, "n": 0}); idx = rot["n"]
        leg = monta_legenda(nome, f"{h:02d}:{mi:02d}", info, idx)
        if tg_img(buf, leg): st[k] = {"dia": hoje_s}; st["_rot"] = {"dia": hoje_s, "n": idx + 1}
    save_state(st)

if __name__ == "__main__":
    try: main()
    except AuthError as e:
        try: tg_msg("🔑 North Robô: a autorização do Google expirou. Gere uma nova chave (autorizar_drive.html) e atualize GOOGLE_REFRESH_TOKEN no GitHub.")
        finally: print("Auth:", e, file=sys.stderr); sys.exit(1)

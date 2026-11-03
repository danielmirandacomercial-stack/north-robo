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

MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]

def acha_dia(drive, pasta, dia, mes, nivel=0):
    """procura a pasta do dia ('02. TERÇA-FEIRA', 'TERÇA'...) descendo até 3 níveis; prefere a pasta do mês atual."""
    subs = [f for f in drive.list(pasta) if is_folder(f)]
    ach = [f for f in subs if dia in norm(f["name"]) and "HIGH" not in norm(f["name"]) and "STORY" not in norm(f["name"])]
    if ach: return fid(ach[0])
    if nivel >= 3: return None
    subs = [f for f in subs if "STORY" not in norm(f["name"])]
    subs.sort(key=lambda f: (0 if mes in norm(f["name"]) else 1, 0 if "TODOS" in norm(f["name"]) else 1))
    for f in subs:
        r = acha_dia(drive, fid(f), dia, mes, nivel + 1)
        if r: return r
    return None

def pasta_feed(drive, root, hoje):
    """pasta do dia (em qualquer nível abaixo de root) -> subpasta FEED (ignora STORY)"""
    dia = DIAS[hoje.weekday()]
    pasta = acha_dia(drive, root, dia, MESES[hoje.month - 1])
    if not pasta: return None, f"pasta do dia ({dia}) não encontrada"
    itens = drive.list(pasta)
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

# ---------- reconhecimento pela imagem (não depende do nome do arquivo) ----------
def _txt(img, a, b, sc=2, thr=None, cfg="--psm 6", x0=0.0, x1=1.0):
    g = img.convert("L"); W, H = g.size
    c = g.crop((int(W * x0), int(H * a), int(W * x1), int(H * b)))
    c = c.point(lambda v: 0 if v > thr else 255) if thr else ImageOps.invert(ImageOps.autocontrast(c))
    c = ImageOps.expand(c.resize((c.width * sc, c.height * sc), Image.LANCZOS), 30, fill=255)
    return pytesseract.image_to_string(c, config=cfg).strip()

def le_data_hora2(img):
    """linha do topo: 'dd/mm • DIA • hh:mm'"""
    for a, b in ((.08, .14), (.07, .15), (.06, .17), (.09, .13)):
        for sc in (2, 3):
            t = _txt(img, a, b, sc, cfg="--psm 7", x0=.2, x1=.8).upper().replace("O", "0")
            dm = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", t)
            hs = re.findall(r"(\d{1,2})\s*[:H.]\s*(\d{2})", t)
            if dm and hs:
                d, mo, h, mi = int(dm.group(1)), int(dm.group(2)), int(hs[-1][0]), int(hs[-1][1])
                if 1 <= d <= 31 and 1 <= mo <= 12 and h < 24 and mi < 60: return d, mo, h, mi
    return le_data_hora(img)      # método antigo (artes antigas)

def classifica(img):
    """-> dict(tipo='torneio'|'sat'|'grade'|'outro', dh=(d,m,h,mi)|None, nome=str, dia=indice do dia da semana|None)"""
    dh = le_data_hora2(img)
    if dh:
        sat = any(w.startswith("SAT") for w in re.findall(r"[A-Z]+", norm(_txt(img, .22, .40, 2, thr=190))))
        if not sat: sat = "VAGA" in norm(_txt(img, .38, .62))
        toks = re.sub(r"[^A-Z0-9 ]", " ", norm(_txt(img, .25, .37, 2, thr=170, x0=.15, x1=.85))).split()
        while toks and len(toks[0]) == 1: toks.pop(0)          # sobras dos traços decorativos
        while toks and len(toks[-1]) == 1: toks.pop()
        nome = " ".join(toks)
        return {"tipo": "sat" if sat else "torneio", "dh": list(dh), "nome": nome}
    t = norm(_txt(img, .12, .45))
    if "HORARIO" in t or "EVENTO" in t:
        w = norm(_txt(img, .07, .22))
        dia = next((i for i, d in enumerate(DIAS) if d in w), None)
        return {"tipo": "grade", "dh": None, "nome": "", "wd": dia}
    return {"tipo": "outro", "dh": None, "nome": ""}

# ---------- High Stakes: layouts variam por arte; data e nome vêm do nome da pasta ("DD.MM - NOME"), hora é lida na arte ----------
def _hora_ok(h, mi): return h < 24 and mi < 60 and (h >= 10 or h <= 4)

def hs_hora(img):
    g = img.convert("L"); W, H = g.size; topo = g.crop((0, 0, W, int(H * .62)))
    for inv in (False, True):
        im = ImageOps.invert(ImageOps.autocontrast(topo)) if inv else topo
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        d = pytesseract.image_to_data(im, config="--psm 11", output_type=pytesseract.Output.DICT)
        toks = [(d["text"][i].strip().upper().replace("O", "0").replace("S", "5"), d["top"][i] + d["height"][i] // 2)
                for i in range(len(d["text"])) if d["text"][i].strip()]
        tempos = [(int(m.group(1)), int(m.group(2)), y) for t, y in toks for m in [re.fullmatch(r"(\d{1,2})[H:](\d{2})", t)]
                  if m and _hora_ok(int(m.group(1)), int(m.group(2)))]
        datas = [y for t, y in toks if re.fullmatch(r"\d{1,2}/\d{2}", t)]
        if tempos:   # o horário de início é o que está na linha da data (ou o mais alto da arte)
            t = min(tempos, key=lambda t: abs(t[2] - datas[0])) if datas else min(tempos, key=lambda t: t[2])
            return t[0], t[1]
    # fallback: lado direito da linha da data, varrendo alturas
    from collections import Counter
    votos = Counter()
    for a in [x / 100 for x in range(5, 19)]:
        for x0 in (.5, .55, .6):
            c = g.crop((int(W * x0), int(H * a), int(W * .95), int(H * (a + .045))))
            for sc in (3, 5):
                im = ImageOps.expand(ImageOps.invert(ImageOps.autocontrast(c)).resize((c.width * sc, c.height * sc), Image.LANCZOS), 30, fill=255)
                hm = _hora(pytesseract.image_to_string(im, config="--psm 7 -c tessedit_char_whitelist=0123456789:HhxX").strip())
                if hm and hm[1] % 5 == 0 and _hora_ok(*hm): votos[hm] += 1
    return votos.most_common(1)[0][0] if votos else None

def hs_torneios(drive, root, hoje):
    """-> [(arquivo, nome)] das artes de feed de hoje em HIGH STAKES/TORNEIOS/<DD.MM - NOME>/ (ignora STORY e formato vertical)"""
    out = []
    for hsf in [f for f in drive.list(root) if is_folder(f) and "HIGH" in norm(f["name"])]:
        tor = [f for f in drive.list(fid(hsf)) if is_folder(f) and "TORNEIO" in norm(f["name"]) and "GRADE" not in norm(f["name"])]
        for t in tor:
            for pf in [f for f in drive.list(fid(t)) if is_folder(f)]:
                m = re.match(r"\s*(\d{1,2})\.(\d{1,2})\s*[-–]?\s*(.*)", pf["name"])
                if not m or (int(m.group(1)), int(m.group(2))) != (hoje.day, hoje.month): continue
                nome = re.sub(r"\s+", " ", norm(m.group(3))).strip()
                for f in drive.list(fid(pf)):
                    if is_folder(f) or not f["mimeType"].startswith("image/") or "STORY" in norm(f["name"]): continue
                    out.append((f, nome))
    return out

# ---------- informações da arte (GTD, buy-in, late) e legenda ----------
PKO = {"THE START", "THE LOW", "HIGH ROLLER", "HIGH ROLLER LIGHT", "MINI HIGH ROLLER", "HR LIGHT", "MINI HR", "HR"}   # editar se mudar
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
UNION_TPL = "iVBORw0KGgoAAAANSUhEUgAAAK4AAABBCAAAAABtdcBlAAACUklEQVR42u1a7Y7DIAyLI97/lX0/2rWBFUiB62Aa0mnarR8mBMdOC1liUEQEIroGXOyYIasMigjWgSuUZZLhlRALwV0ocS1B/MZv/MZvfN2gCCdlOM1xMReBy5kriObrHBeAy7krtK6lIbSElnPDZWQvMCFejdDGXgiYDm8oqvXp9Htoc6S7i6aYL4ex5jFVe2y0HWB2xmnK4xPFLi6yVa3Vkeyfr/rNUpG0RZ707/HAZNl5zoeVzCAet1YB9N0Lt3MlNxv7a/nI9xurszOCOYx0EJFMgOFd9kpSEP8qID9t79kJl5UupieJ8LDEYVvkXeyB0jVJw3o6onPFW6uz3R4n/6K6i3kTbp93w7D81ae8amEG9M83eOPTHN4rmkQ+k7bDwVyZeLztCqCS3F1Exk0KT9FKVvczoumdsKEENpUADJcWwUkoMd2zvLM4Ema0uuqkP3zWENEDl8ZkunOgVyWgdK5mF/ak2pR4cPwT9uq4ylhU54XoHHMRxPz8AgVj/XgiRWL9sIATxmT9HIiNHSXy3lHMZ4kuUdal92mJiYK50jNohQuPW7vJkbnZc1RVY79RY/UxAXvZO9RsCt2Xp/MI9DMDcs0JNuuubUske7l3wwap4N0k9P3gcif0Cwm6kzpyztKTu4jvCLiKYkvhZ/J3ZwX1EekCJJMG2ohCrxNvrACLsQEHieDuq0s6zKAgHy2aVrXtR7NPM1z4XXRo6bcnimDSnman+cEw94iKjsUQr9bzHhxgcRkh/K6JBQn3dBvqKd841LVe2Qpf8lLcnNH9AwSLx5RuDwu8AAAAAElFTkSuQmCC"

def acha_union(img):
    """localiza a logo Union Prime por correlação com um modelo (funciona com fundos e posições variados) -> (x0,y0,x1,y1,score)"""
    from scipy.signal import fftconvolve
    W, H = img.size; k = 561 / W
    g = np.array(img.convert("L").resize((561, int(H * k)), Image.LANCZOS)).astype(float)
    y0 = int(g.shape[0] * .72); B = (g[y0:] > 150).astype(float)
    t = (np.array(Image.open(io.BytesIO(__import__("base64").b64decode(UNION_TPL))).convert("L")) > 100).astype(float); th, tw = t.shape
    if B.shape[0] < th or B.shape[1] < tw: return None
    corr = fftconvolve(B, t[::-1, ::-1], mode="valid"); loc = fftconvolve(B, np.ones_like(t), mode="valid")
    sc = corr / np.sqrt(t.sum() * np.maximum(loc, 1) + 1e-9)
    iy, ix = np.unravel_index(sc.argmax(), sc.shape)
    return int(ix / k), int((iy + y0) / k), int((ix + tw) / k), int((iy + y0 + th) / k), float(sc[iy, ix])

def aplica_logo(img):
    img = img.convert("RGB"); W, H = img.size; A = np.array(img).astype(float)
    m_ = acha_union(img)
    if m_ and m_[4] >= .4:
        bx0, by0, bx1, by1 = m_[:4]; lx0 = ly0 = 0
        crop = A[by0:by1, bx0:bx1]; lum = crop.max(axis=2)
        alpha = np.clip((lum - 95) / 60, 0, 1)
        uni = Image.fromarray(np.dstack([crop, alpha * 255]).astype("uint8"), "RGBA")
        ocx = (bx0 + bx1) // 2
    else:   # método antigo (posição fixa no rodapé)
        lx0, lx1, ly0, ly1 = int(W * .32), int(W * .68), int(H * .815), int(H * .945)
        crop = A[ly0:ly1, lx0:lx1]; lum = crop.max(axis=2)
        alpha = np.clip((lum - 95) / 60, 0, 1); ok = alpha > 0.6
        rr = np.where(ok.sum(axis=1) > 2)[0]; cc = np.where(ok.sum(axis=0) > 2)[0]
        if len(rr) < 20 or len(cc) < 50: return None   # não achou a logo Union: não mexe
        bx0, bx1, by0, by1 = cc.min(), cc.max(), rr.min(), rr.max()
        uni = Image.fromarray(np.dstack([crop, alpha * 255]).astype("uint8")[max(0, by0 - 3):by1 + 4, max(0, bx0 - 3):bx1 + 4], "RGBA")
        bx0, bx1, by0, by1 = lx0 + bx0, lx0 + bx1, ly0 + by0, ly0 + by1; ocx = W // 2
    # apaga exatamente onde a logo estava (caixa detectada + margem)
    m_ = 16
    x0, x1 = max(0, bx0 - m_), min(W, bx1 + m_); y0, y1 = max(14, by0 - m_), min(H - 14, by1 + m_)
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
    gap = int(W * 70 / 1336); tot = u.width + gap + n.width
    sx = min(max(ocx - tot // 2, 20), W - 20 - tot); cy = (by0 + by1) // 2
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
    # --- classifica cada arte pela imagem (resultado guardado: cada arte é lida uma vez) ---
    arts = []
    for f in sorted(files, key=lambda x: x["modifiedTime"]):
        mod = dt.datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")).astimezone(TZ)
        if mod < corte_grade - dt.timedelta(days=3): continue          # claramente antiga
        kc = f"cls-{f['id']}-{f['modifiedTime']}"
        if kc in st: c = st[kc]
        else:
            try: png = drive.download(f["id"]); c = classifica(Image.open(io.BytesIO(png)))
            except Exception as e:
                print("falha ao ler", f["name"], e, file=sys.stderr); continue
            if c["tipo"] == "outro":
                k2 = "ocr-falha-" + hoje_s + f["id"]
                if k2 not in st: tg_msg(f"⚠️ Não consegui identificar a arte {f['name']} (nem grade, nem torneio)."); st[k2] = {"dia": hoje_s}
            st[kc] = {**c, "dia": hoje_s}
        arts.append((f, mod, st[kc]))
    # --- grade ---
    achou = False
    for f, mod, c in arts:
        if c["tipo"] != "grade": continue
        if c.get("wd") is not None and c["wd"] != hoje.weekday(): continue   # grade de outro dia (lida na imagem)
        if c.get("wd") is None and mod < corte_grade: continue            # sem dia legível: vale a data de modificação
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
    # --- torneios (satélites não são enviados) ---
    for f, mod, c in arts:
        if c["tipo"] != "torneio": continue
        k = f"torneio-{hoje_s}-{f['id']}"
        if k in st: continue
        d, mo, h, mi = c["dh"]
        if (d, mo) != (hoje.day, hoje.month): continue                 # arte de outro dia
        inicio = dt.datetime.combine(hoje, dt.time(h, mi), TZ)
        if not (inicio - dt.timedelta(minutes=ANTECEDENCIA_MIN) <= now <= inicio + dt.timedelta(minutes=JANELA_APOS_MIN)): continue
        png = drive.download(f["id"]); img = Image.open(io.BytesIO(png))
        out = aplica_logo(img)
        if out is None:
            tg_msg(f"⚠️ Não achei a logo Union na arte {c['nome'] or f['name']}; enviando sem a logo North.")
            buf = png
        else:
            b = io.BytesIO(); out.save(b, "PNG", optimize=True); buf = b.getvalue()
        nome = c["nome"] or os.path.splitext(f["name"])[0]
        try: info = le_info(img)
        except Exception: info = {}
        rot = st.get("_rot", {"dia": hoje_s, "n": 0}); idx = rot["n"]
        leg = monta_legenda(nome, f"{h:02d}:{mi:02d}", info, idx)
        if tg_img(buf, leg): st[k] = {"dia": hoje_s}; st["_rot"] = {"dia": hoje_s, "n": idx + 1}
    # --- High Stakes (série): pasta com a data do dia; hora lida na arte ---
    try: hs = hs_torneios(drive, os.environ["DRIVE_FOLDER_ID"], hoje)
    except AuthError: raise
    except Exception as e: print("HS erro:", e, file=sys.stderr); hs = []
    for f, nome in hs:
        k = f"hs-{hoje_s}-{f['id']}"
        if k in st: continue
        kc = f"hshora-{f['id']}-{f['modifiedTime']}"
        if kc not in st:
            png = drive.download(f["id"]); img = Image.open(io.BytesIO(png))
            if img.height / img.width > 1.5: st[kc] = {"dia": hoje_s, "skip": True}; continue     # formato story
            hm = hs_hora(img)
            if not hm:
                k2 = "ocr-falha-" + hoje_s + f["id"]
                if k2 not in st: tg_msg(f"⚠️ High Stakes: não consegui ler o horário da arte {f['name']}."); st[k2] = {"dia": hoje_s}
                continue
            st[kc] = {"dia": hoje_s, "h": hm[0], "mi": hm[1]}
        c = st[kc]
        if c.get("skip"): continue
        h, mi = c["h"], c["mi"]
        inicio = dt.datetime.combine(hoje, dt.time(h, mi), TZ)
        if not (inicio - dt.timedelta(minutes=ANTECEDENCIA_MIN) <= now <= inicio + dt.timedelta(minutes=JANELA_APOS_MIN)): continue
        png = drive.download(f["id"]); img = Image.open(io.BytesIO(png)); out = aplica_logo(img)
        if out is None:
            tg_msg(f"⚠️ Não achei a logo Union na arte {f['name']}; enviando sem a logo North."); buf = png
        else:
            b = io.BytesIO(); out.save(b, "PNG", optimize=True); buf = b.getvalue()
        rot = st.get("_rot", {"dia": hoje_s, "n": 0}); idx = rot["n"]
        leg = "⭐ HIGH STAKES\n" + monta_legenda(nome, f"{h:02d}:{mi:02d}", {}, idx)
        if tg_img(buf, leg): st[k] = {"dia": hoje_s}; st["_rot"] = {"dia": hoje_s, "n": idx + 1}
    save_state(st)

if __name__ == "__main__":
    try: main()
    except AuthError as e:
        try: tg_msg("🔑 North Robô: a autorização do Google expirou. Gere uma nova chave (autorizar_drive.html) e atualize GOOGLE_REFRESH_TOKEN no GitHub.")
        finally: print("Auth:", e, file=sys.stderr); sys.exit(1)

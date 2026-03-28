"""
PhotoBox Backend - Canon 700D Integration
Flask server yang menangani: kamera, template, Google Drive, QR Code
"""

import cmd
import os
import io
import json
import time
import base64
import threading
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import qrcode

# Google Drive
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

app = Flask(__name__, static_folder='frontend', static_url_path='')
app.config['SECRET_KEY'] = 'photobox-secret-2024'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ─── Konfigurasi ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
PHOTOS_DIR = BASE_DIR / 'photos'
SESSIONS_DIR = BASE_DIR / 'sessions'
TEMPLATES_DIR = BASE_DIR / 'templates'
FONTS_DIR = BASE_DIR / 'fonts'
ASSETS_DIR = BASE_DIR / 'assets'

for d in [PHOTOS_DIR, SESSIONS_DIR, TEMPLATES_DIR, FONTS_DIR, ASSETS_DIR]:
    d.mkdir(exist_ok=True)

GOOGLE_DRIVE_FOLDER = "PhotoBox Sessions"
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# State global
camera_connected = False
current_session = None
preview_active = False


# ─── Kamera Canon via digiCamControl HTTP API ────────────────────────────────
#
# digiCamControl menyediakan HTTP server di port 5513.
# Ini JAUH lebih reliable daripada memanggil CameraControlCmd.exe via subprocess.
#
# CARA PAKAI:
#   1. Buka digiCamControl (biarkan tetap terbuka / jalan di background)
#   2. Di digiCamControl: Tools → Settings → Web Server → centang "Enable web server"
#      (biasanya sudah aktif secara default di port 5513)
#   3. Jalankan python app.py
#

import sys
import glob
import urllib.request
import urllib.error

DCC_HOST    = "http://localhost:5513"   # digiCamControl web API
MJPEG_URL   = "http://127.0.0.1:5514/live"  # MJPEG stream untuk live preview
PREVIEW_TMP = str(BASE_DIR / 'photos' / '_preview_tmp.jpg')


def dcc_get(endpoint: str, timeout: int = 10):
    """Helper: HTTP GET ke digiCamControl web API"""
    url = f"{DCC_HOST}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read(), resp.status
    except urllib.error.URLError as e:
        raise ConnectionError(f"digiCamControl tidak merespons di {DCC_HOST}: {e}")


def check_camera() -> bool:
    """
    Cek apakah digiCamControl web server aktif.
    HANYA ping endpoint ringan — TIDAK hit /liveview.jpg
    karena bisa crash DCC jika Live View belum aktif.
    """
    # Cukup ping root atau devices.json — tidak trigger Live View
    for endpoint in ["/devices.json", "/"]:
        try:
            urllib.request.urlopen(f"{DCC_HOST}{endpoint}", timeout=3)
            print("✅ digiCamControl web server aktif")
            return True
        except Exception:
            continue
    print("⚠️  digiCamControl tidak merespons di port 5513")
    return False


def capture_photo(output_path: str) -> bool:
    import os
    import time
    import shutil
    import urllib.request
    import threading

    dcc_base = r"D:\app file\digicamControl\foto"
    os.makedirs(dcc_base, exist_ok=True) # Pastikan folder ada

    try:
        # 1. CATAT JUMLAH FILE SEBELUM MEMOTRET
        initial_files = set(f for f in os.listdir(dcc_base) if f.lower().endswith('.jpg'))

        # 2. MATIKAN LIVE VIEW (Supaya kamera bisa motret)
        try:
            urllib.request.urlopen(f"{DCC_HOST}/?CMD=LiveViewWnd_Hide", timeout=1)
        except Exception: pass
        time.sleep(0.1) 

        # 3. TRIGGER FOTO
        print("📸 Menjepret kamera...")
        try:
            urllib.request.urlopen(f"{DCC_HOST}/?CMD=Capture", timeout=2)
        except Exception: pass

        # 4. FAST POLLING (Berdasarkan selisih file, BUKAN WAKTU)
        start_poll = time.time()
        found_file = False
        
        while (time.time() - start_poll) < 8.0:
            # Cek daftar file sekarang
            current_files = set(f for f in os.listdir(dcc_base) if f.lower().endswith('.jpg'))
            
            # Cari tahu file apa yang baru saja muncul
            new_files = current_files - initial_files
            valid_new_files = [f for f in new_files if "_prev" not in f.lower() and "thumb" not in f.lower()]

            if valid_new_files:
                latest_file_name = valid_new_files[0]
                latest_file_path = os.path.join(dcc_base, latest_file_name)
                
                try:
                    # Pastikan file sudah utuh (di atas 100KB)
                    if os.path.getsize(latest_file_path) > 100000:
                        time.sleep(0.05) # Ekstra jeda agar Windows selesai lock file
                        shutil.copy2(latest_file_path, output_path)
                        print(f"✅ Foto cepat diamankan: {latest_file_name}")
                        found_file = True
                        break # Langsung keluar dari loop!
                except Exception:
                    pass

            time.sleep(0.1) # Cek 10x per detik

        # 5. BANGUNKAN KAMERA DI BACKGROUND
        def wakeup_camera():
            time.sleep(0.2) 
            try: urllib.request.urlopen(f"{DCC_HOST}/?CMD=LiveViewWnd_Show", timeout=2)
            except Exception: pass

        threading.Thread(target=wakeup_camera, daemon=True).start()

        return found_file

    except Exception as e:
        print(f"Capture error: {e}")
        return False

def start_live_preview():
    # """
    # Hanya bertugas sebagai 'Remote Control' untuk menaikkan cermin kamera.
    # Video akan disedot secara otomatis oleh tag <img> di web frontend.
    # """
    # import urllib.request
    # try:
    #     print("▶ Meminta kamera menaikkan cermin (Live View On)...")
    #     urllib.request.urlopen(f"{DCC_HOST}/?CMD=LiveViewWnd_Show", timeout=2)
    # except Exception as e:
    #     print(f"   [Info] Mengabaikan respons timeout DCC: {e}")
    pass

def stop_live_preview():
    # """Mematikan Live View di digiCamControl."""
    # import urllib.request
    # try:
    #     print("⏹ Meminta kamera mematikan Live View...")
    #     urllib.request.urlopen(f"{DCC_HOST}/?CMD=LiveViewWnd_Hide", timeout=2)
    # except Exception:
    #     pass
    pass

# ─── Template Engine ─────────────────────────────────────────────────────────
#
# Template dibaca dari: assets/templates/*.json
# Gambar background dari: assets/templates/*.png  (hasil download Canva)
# Thumbnail otomatis dibuat di: assets/thumbnails/*.jpg
#

ASSETS_TEMPLATES_DIR = BASE_DIR / 'assets' / 'templates'
ASSETS_THUMBS_DIR    = BASE_DIR / 'assets' / 'thumbnails'
ASSETS_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_THUMBS_DIR.mkdir(parents=True, exist_ok=True)


def load_templates_from_disk() -> dict:
    """
    Baca template dari assets/templates/.
    Prioritas:
      1. Jika ada .json → pakai JSON (manual atau auto-generated)
      2. Jika ada .png tapi TIDAK ada .json → auto-detect slot dari area transparan,
         simpan JSON otomatis, lalu pakai
    Sehingga user cukup taruh PNG saja — sistem otomatis proses.
    """
    import json as _json
    templates = {}

    # ── 1. Load semua JSON yang ada ──────────────────────────────────────────
    json_ids = set()
    for jf in sorted(ASSETS_TEMPLATES_DIR.glob('*.json')):
        try:
            with open(jf, encoding='utf-8') as f:
                cfg = _json.load(f)
            tid = cfg.get('id') or jf.stem
            cfg['id'] = tid
            if 'slots' in cfg and 'photo_slots' not in cfg:
                cfg['photo_slots'] = cfg['slots']
            templates[tid] = cfg
            json_ids.add(jf.stem)
        except Exception as e:
            print(f"⚠️  Gagal load {jf.name}: {e}")

    # ── 2. Scan PNG yang belum punya JSON → auto-detect ──────────────────────
    for png_path in sorted(ASSETS_TEMPLATES_DIR.glob('*.png')):
        tid = png_path.stem
        if tid in json_ids:
            continue  # sudah ada JSON, skip

        print(f"🔍 PNG tanpa JSON ditemukan: {png_path.name} → auto-detect slot...")
        try:
            cfg = auto_generate_json_from_png(str(png_path), template_id=tid)
            if 'slots' in cfg and 'photo_slots' not in cfg:
                cfg['photo_slots'] = cfg['slots']
            templates[tid] = cfg
            json_ids.add(tid)
            print(f"  ✅ Template '{tid}' berhasil dibuat dari PNG")
        except Exception as e:
            print(f"  ❌ Auto-detect gagal untuk {png_path.name}: {e}")

    return templates


def detect_slots_from_png(png_path: str, sort_order: str = 'top_bottom_col') -> list:
    """
    Otomatis deteksi slot foto dari area TRANSPARAN (alpha=0) di PNG Canva.

    Algoritma:
      1. Baca channel alpha PNG
      2. Threshold: pixel dengan alpha < 10 = transparan = area slot
      3. Temukan connected components (blob) menggunakan flood fill sederhana
      4. Filter blob terlalu kecil (noise)
      5. Urutkan sesuai sort_order:
         - 'top_bottom_col': kiri ke kanan per kolom, atas ke bawah (default)
         - 'reading': kiri ke kanan, atas ke bawah (seperti membaca)

    Return: list slot dict [{id, x, y, w, h, shape, round_radius}]
    """
    import numpy as np

    img = Image.open(png_path).convert('RGBA')
    w, h = img.size
    arr = np.array(img)

    # Channel alpha — 0 = transparan, 255 = opaque
    alpha = arr[:, :, 3]

    # Buat binary mask: True = transparan
    transparent = alpha < 10

    # ── Connected Components via labeling sederhana ──────────────────────────
    # Gunakan scipy jika ada, fallback ke implementasi manual
    try:
        from scipy import ndimage
        labeled, num_features = ndimage.label(transparent)
    except ImportError:
        labeled, num_features = _simple_label(transparent)

    MIN_AREA = (w * h) * 0.005  # minimal 0.5% dari total area
    MAX_AREA = (w * h) * 0.95   # maksimal 95% (jangan ambil seluruh gambar)

    regions = []
    for label_id in range(1, num_features + 1):
        mask = labeled == label_id
        area = mask.sum()
        if area < MIN_AREA or area > MAX_AREA:
            continue

        # Bounding box
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        y1, y2 = int(rows[0]), int(rows[-1])
        x1, x2 = int(cols[0]), int(cols[-1])
        bw = x2 - x1 + 1
        bh = y2 - y1 + 1

        # Deteksi apakah slot lingkaran:
        # Rasio area transparan vs bounding box mendekati π/4 ≈ 0.785
        fill_ratio = area / (bw * bh)
        is_round = 0.65 < fill_ratio < 0.90 and abs(bw - bh) < min(bw, bh) * 0.2

        regions.append({
            'x': x1, 'y': y1, 'w': bw, 'h': bh,
            'cx': x1 + bw//2,   # center x (untuk sorting)
            'cy': y1 + bh//2,   # center y
            'area': int(area),
            'shape': 'round' if is_round else 'rect',
            'round_radius': 0,
        })

    if not regions:
        return []

    # ── Sorting ────────────────────────────────────────────────────────────────
    if sort_order == 'top_bottom_col':
        # Kelompokkan ke kolom berdasarkan center-x (toleransi 15% lebar gambar)
        col_tol = w * 0.15
        regions_sorted = sorted(regions, key=lambda r: r['cx'])
        columns = []
        for r in regions_sorted:
            placed = False
            for col in columns:
                if abs(r['cx'] - col[0]['cx']) < col_tol:
                    col.append(r)
                    placed = True
                    break
            if not placed:
                columns.append([r])

        # Urutkan tiap kolom dari atas ke bawah
        for col in columns:
            col.sort(key=lambda r: r['cy'])

        # Flatten: kolom kiri dulu
        columns.sort(key=lambda col: col[0]['cx'])
        ordered = [r for col in columns for r in col]

    else:
        # Reading order: atas ke bawah, kiri ke kanan
        ordered = sorted(regions, key=lambda r: (r['cy'], r['cx']))

    # ── Assign ID ─────────────────────────────────────────────────────────────
    slots = []
    for i, r in enumerate(ordered):
        slots.append({
            'id':           i + 1,
            'x':            r['x'],
            'y':            r['y'],
            'w':            r['w'],
            'h':            r['h'],
            'shape':        r['shape'],
            'round_radius': 0,
        })

    print(f"  ✅ Terdeteksi {len(slots)} slot dari PNG")
    for s in slots:
        print(f"     Slot {s['id']}: x={s['x']} y={s['y']} w={s['w']} h={s['h']} shape={s['shape']}")

    return slots


def _simple_label(binary_mask):
    """
    Fallback connected-component labeling tanpa scipy.
    Menggunakan BFS flood fill.
    """
    import collections
    h, w = binary_mask.shape
    labeled = [[0]*w for _ in range(h)]
    label_id = 0

    def bfs(sr, sc, lid):
        q = collections.deque([(sr, sc)])
        labeled[sr][sc] = lid
        while q:
            r, c = q.popleft()
            for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
                nr, nc = r+dr, c+dc
                if 0<=nr<h and 0<=nc<w and binary_mask[nr][nc] and labeled[nr][nc]==0:
                    labeled[nr][nc] = lid
                    q.append((nr, nc))

    for r in range(h):
        for c in range(w):
            if binary_mask[r][c] and labeled[r][c] == 0:
                label_id += 1
                bfs(r, c, label_id)

    import numpy as np
    return np.array(labeled), label_id


def auto_generate_json_from_png(png_path: str, template_id: str = None,
                                 name: str = None) -> dict:
    """
    Buat JSON template otomatis dari PNG Canva.
    Dipanggil dari API endpoint /api/templates/scan-png
    """
    import json as _json

    img = Image.open(png_path)
    cw, ch = img.size

    tid  = template_id or Path(png_path).stem
    tname = name or tid.replace('_', ' ').title()

    slots = detect_slots_from_png(png_path, sort_order='top_bottom_col')

    config = {
        'id':               tid,
        'name':             tname,
        'description':      f'Template dengan {len(slots)} foto',
        'photo_count':      len(slots),
        'canvas_size':      [cw, ch],
        'background_image': Path(png_path).name,
        'background_color': '#ffffff',
        'slots':            slots,
    }

    # Simpan JSON otomatis
    json_path = ASSETS_TEMPLATES_DIR / f"{tid}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        _json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"✅ JSON disimpan: {json_path}")
    return config


def apply_template(session_id: str, template_id: str, photo_paths: list) -> dict:
    """
    Gabungkan foto ke dalam template — output Full HD.

    Alur:
      1. Tentukan skala render: minimal 1920px pada sisi terpanjang (Full HD baseline)
      2. Buka background PNG dari Canva (jika ada) ATAU buat canvas warna solid
      3. Paste foto ke setiap slot (crop center, support rect/round/rounded-rect)
      4. Overlay kembali background PNG di atas foto (agar border/dekorasi Canva menutupi)
      5. Tambah branding tanggal
      6. Simpan dua file:
         - grid_{template_id}.jpg  → komposit template sebelum branding (referensi)
         - result_{template_id}.jpg → final dengan branding (untuk share)

    Return: dict {"grid_path": ..., "result_path": ...}
    """
    templates = load_templates_from_disk()
    config = templates.get(template_id)
    if not config:
        raise ValueError(f"Template tidak ditemukan: {template_id}")

    orig_cw, orig_ch = config['canvas_size']

    # ── Hitung skala Full HD ───────────────────────────────────────────────────
    # Pastikan sisi terpanjang minimal 1920px; jika template sudah lebih besar → pakai aslinya.
    FULLHD_MIN = 1920
    scale_fhd  = max(1.0, FULLHD_MIN / max(orig_cw, orig_ch))
    cw = int(orig_cw * scale_fhd)
    ch = int(orig_ch * scale_fhd)
    print(f"🖼  Render canvas: {cw}×{ch}  (skala ×{scale_fhd:.2f} dari template {orig_cw}×{orig_ch})")

    bg_color = _hex_to_rgb(config.get('background_color', '#ffffff'))
    # Scale slots sesuai skala FHD
    slots_orig = config.get('photo_slots', [])
    slots = [{
        **s,
        'x': int(s['x'] * scale_fhd),
        'y': int(s['y'] * scale_fhd),
        'w': int(s['w'] * scale_fhd),
        'h': int(s['h'] * scale_fhd),
        'round_radius': int(s.get('round_radius', 0) * scale_fhd),
    } for s in slots_orig]
    required = config.get('photo_count', len(slots))

    # ── 1. Buat canvas dengan background ──────────────────────────────────────
    bg_img_name = config.get('background_image', '')
    bg_path = ASSETS_TEMPLATES_DIR / bg_img_name if bg_img_name else None

    bg_layer = None
    if bg_path and bg_path.exists():
        try:
            bg_layer = Image.open(bg_path).convert('RGBA').resize((cw, ch), Image.LANCZOS)
        except Exception as e:
            print(f"⚠️  Gagal load background image: {e}")

    # ── 2. Paste foto ke slot ──────────────────────────────────────────────────
    photo_layer = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))

    for i, slot in enumerate(slots[:required]):
        if i >= len(photo_paths):
            break
        try:
            # Buka foto asli resolusi penuh dari kamera — Canon 700D ~5184×3456
            photo = Image.open(photo_paths[i]).convert('RGB')
            photo = _center_crop(photo, slot['w'], slot['h'])

            if config.get('grayscale'):
                photo = photo.convert('L').convert('RGB')

            shape  = slot.get('shape', 'rect')
            radius = slot.get('round_radius', 0)

            if shape == 'round':
                _paste_round(photo_layer, photo, slot)
            elif radius > 0:
                _paste_rounded_rect(photo_layer, photo, slot, radius)
            else:
                photo_layer.paste(photo.convert('RGBA'), (slot['x'], slot['y']))

        except Exception as e:
            print(f"  ⚠️  Slot {i+1} error: {e}")
            _paste_placeholder_rgba(photo_layer, slot, i+1)

    # ── 3. Composite layers ────────────────────────────────────────────────────
    base = Image.new('RGBA', (cw, ch), bg_color + (255,))
    if bg_layer:
        base.alpha_composite(bg_layer)
    base.alpha_composite(photo_layer)
    if bg_layer:
        base.alpha_composite(bg_layer)  # overlay dekorasi Canva di atas foto

    # ── 4. Simpan grid (komposit bersih, tanpa branding) ──────────────────────
    session_dir  = SESSIONS_DIR / session_id
    grid_path    = str(session_dir / f"grid_{template_id}.jpg")
    grid_img     = base.convert('RGB')
    grid_img.save(grid_path, 'JPEG', quality=98, subsampling=0)
    print(f"✅ Grid disimpan: {grid_path}  [{cw}×{ch}]")

    # ── 5. Branding + simpan result final ─────────────────────────────────────
    final = grid_img.copy()
    draw  = ImageDraw.Draw(final)
    _add_branding_scaled(draw, (cw, ch), config, scale_fhd)

    result_path = str(session_dir / f"result_{template_id}.jpg")
    final.save(result_path, 'JPEG', quality=98, subsampling=0)
    print(f"✅ Hasil final disimpan: {result_path}  [{cw}×{ch}]")

    return {"grid_path": grid_path, "result_path": result_path}


def generate_thumbnail(template_id: str, config: dict) -> str:
    """
    Buat thumbnail preview template (200×280px) untuk ditampilkan di UI.
    Jika background PNG ada → pakai sebagai thumbnail.
    Jika tidak → gambar slot placeholder.
    """
    thumb_path = ASSETS_THUMBS_DIR / f"{template_id}.jpg"

    # Gunakan background PNG jika ada
    bg_img_name = config.get('background_image', '')
    bg_path = ASSETS_TEMPLATES_DIR / bg_img_name if bg_img_name else None

    cw, ch = config.get('canvas_size', [600, 800])

    if bg_path and bg_path.exists():
        try:
            img = Image.open(bg_path).convert('RGB')
            img.thumbnail((240, 320), Image.LANCZOS)
            img.save(str(thumb_path), 'JPEG', quality=85)
            return str(thumb_path)
        except Exception as e:
            print(f"  Thumbnail dari PNG gagal: {e}")

    # Fallback: gambar placeholder
    TW, TH = 240, 320
    bg_color = _hex_to_rgb(config.get('background_color', '#ffffff'))
    thumb = Image.new('RGB', (TW, TH), bg_color)
    draw  = ImageDraw.Draw(thumb)

    scale  = min((TW-16)/cw, (TH-16)/ch)
    ox     = (TW - cw*scale) / 2
    oy     = (TH - ch*scale) / 2

    slot_color = (180, 180, 190) if bg_color[0] > 128 else (60, 60, 80)

    for i, slot in enumerate(config.get('photo_slots', [])):
        x = int(ox + slot['x']*scale)
        y = int(oy + slot['y']*scale)
        w = max(4, int(slot['w']*scale))
        h = max(4, int(slot['h']*scale))

        if slot.get('shape') == 'round':
            draw.ellipse([x, y, x+w, y+h], fill=slot_color)
        else:
            r = int(slot.get('round_radius', 0) * scale)
            if r > 0:
                draw.rounded_rectangle([x, y, x+w, y+h], radius=r, fill=slot_color)
            else:
                draw.rectangle([x, y, x+w, y+h], fill=slot_color)

        # Nomor
        draw.text((x+w//2, y+h//2), str(i+1),
                  fill=(255,255,255,160) if bg_color[0]<128 else (0,0,0,100),
                  anchor='mm')

    thumb.save(str(thumb_path), 'JPEG', quality=85)
    return str(thumb_path)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except:
        return (255, 255, 255)


def _center_crop(img: Image.Image, tw: int, th: int) -> Image.Image:
    sw, sh = img.size
    sr, tr = sw/sh, tw/th
    if sr > tr:
        nw = int(sh * tr)
        img = img.crop(((sw-nw)//2, 0, (sw-nw)//2+nw, sh))
    elif sr < tr:
        nh = int(sw / tr)
        img = img.crop((0, (sh-nh)//2, sw, (sh-nh)//2+nh))
    return img.resize((tw, th), Image.LANCZOS)


def _paste_round(canvas: Image.Image, photo: Image.Image, slot: dict):
    mask = Image.new('L', (slot['w'], slot['h']), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, slot['w']-1, slot['h']-1], fill=255)
    photo_rgba = photo.convert('RGBA')
    photo_rgba.putalpha(mask)
    canvas.alpha_composite(photo_rgba, (slot['x'], slot['y']))


def _paste_rounded_rect(canvas: Image.Image, photo: Image.Image, slot: dict, radius: int):
    mask = Image.new('L', (slot['w'], slot['h']), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, slot['w']-1, slot['h']-1], radius=radius, fill=255
    )
    photo_rgba = photo.convert('RGBA')
    photo_rgba.putalpha(mask)
    canvas.alpha_composite(photo_rgba, (slot['x'], slot['y']))


def _paste_placeholder_rgba(canvas: Image.Image, slot: dict, num: int):
    colors = [(180,60,60),(60,100,180),(60,160,80),(180,140,40),(140,60,180),(80,160,160)]
    c = colors[(num-1) % len(colors)] + (200,)
    ph = Image.new('RGBA', (slot['w'], slot['h']), c)
    canvas.alpha_composite(ph, (slot['x'], slot['y']))


def _add_branding(draw, size, config):
    """Branding teks di resolusi asli template (legacy — dipakai thumbnail)."""
    text  = f"PhotoBox Studio  •  {datetime.now().strftime('%d %b %Y')}"
    try:
        font = ImageFont.truetype(str(FONTS_DIR / 'Quicksand-Bold.ttf'), 24)
    except:
        font = ImageFont.load_default()
    bg   = _hex_to_rgb(config.get('background_color', '#ffffff'))
    dark = (bg[0]*0.299 + bg[1]*0.587 + bg[2]*0.114) > 128
    color = (120,120,120) if dark else (180,180,180)
    try:
        bb = draw.textbbox((0,0), text, font=font)
        tw = bb[2]-bb[0]
    except:
        tw = len(text)*12
    draw.text(((size[0]-tw)//2, size[1]-38), text, font=font, fill=color)


def _add_branding_scaled(draw, size, config, scale: float = 1.0):
    """
    Branding teks dengan ukuran font yang sudah discale sesuai resolusi canvas Full HD.
    Font size minimal 28px di resolusi asli, discale proporsional.
    """
    text      = f"PhotoBox Studio  •  {datetime.now().strftime('%d %b %Y')}"
    font_size = max(28, int(28 * scale))
    try:
        font = ImageFont.truetype(str(FONTS_DIR / 'Quicksand-Bold.ttf'), font_size)
    except:
        font = ImageFont.load_default()
    bg    = _hex_to_rgb(config.get('background_color', '#ffffff'))
    dark  = (bg[0]*0.299 + bg[1]*0.587 + bg[2]*0.114) > 128
    color = (120,120,120) if dark else (180,180,180)
    try:
        bb = draw.textbbox((0,0), text, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
    except:
        tw = len(text) * font_size // 2
        th = font_size
    margin = int(16 * scale)
    draw.text(((size[0]-tw)//2, size[1] - th - margin), text, font=font, fill=color)


# ─── Google Drive Integration ────────────────────────────────────────────────

def get_drive_service():
    """Autentikasi dan kembalikan Google Drive service"""
    creds = None
    token_path = BASE_DIR / 'token.pickle'
    creds_path = BASE_DIR / 'credentials.json'

    if token_path.exists():
        with open(token_path, 'rb') as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                return None, "credentials.json tidak ditemukan. Silakan setup Google API."
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'wb') as f:
            pickle.dump(creds, f)

    service = build('drive', 'v3', credentials=creds)
    return service, None


def get_or_create_folder(service, folder_name):
    """Buat atau ambil folder di Google Drive (root My Drive)"""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false and 'root' in parents"
    results = service.files().list(q=query, fields='files(id, name)').execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    return folder['id']


def get_or_create_subfolder(service, folder_name: str, parent_id: str) -> str:
    """Buat atau ambil subfolder di dalam parent folder tertentu."""
    # Escape tanda kutip tunggal dalam nama folder
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"name='{safe_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false "
        f"and '{parent_id}' in parents"
    )
    results = service.files().list(q=query, fields='files(id, name)').execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id],
    }
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    return folder['id']


def upload_file_to_drive(service, file_path: str, folder_id: str,
                          file_name: str = None, mimetype: str = 'image/jpeg') -> dict:
    """
    Upload satu file ke folder Drive tertentu.
    Return dict: {success, file_id, view_url, download_url}
    """
    fname = file_name or Path(file_path).name
    file_metadata = {'name': fname, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mimetype, resumable=True)
    try:
        f = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink'
        ).execute()
        service.permissions().create(
            fileId=f['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        return {
            "success":      True,
            "file_id":      f['id'],
            "view_url":     f.get('webViewLink'),
            "download_url": f.get('webContentLink', f.get('webViewLink')),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "file": fname}


def upload_session_to_drive(session: dict) -> dict:
    """
    Upload semua aset satu sesi ke Google Drive dengan struktur folder:

      PhotoBox Sessions/
      └── {session_id}/
          ├── raw/
          │   ├── photo_1.jpg   ← foto mentah resolusi penuh dari kamera
          │   ├── photo_2.jpg
          │   └── ...
          ├── grid_{template}.jpg   ← komposit template tanpa branding
          └── result_{template}.jpg ← foto final dengan branding  ← QR Code diarahkan ke ini

    Return: {
        "success": bool,
        "folder_url": str,         # URL folder sesi di Drive
        "result_url": str,         # URL result final (untuk QR)
        "uploaded_files": [...]
    }
    """
    service, error = get_drive_service()
    if error:
        return {"success": False, "error": error}

    session_id  = session['id']
    session_dir = SESSIONS_DIR / session_id

    try:
        # ── Buat / ambil struktur folder ──────────────────────────────────────
        root_folder_id    = get_or_create_folder(service, GOOGLE_DRIVE_FOLDER)
        session_folder_id = get_or_create_subfolder(service, session_id, root_folder_id)
        raw_folder_id     = get_or_create_subfolder(service, "raw", session_folder_id)

        uploaded = []
        result_url = None

        # ── Upload foto mentah ─────────────────────────────────────────────────
        for raw_path in sorted(session.get('photos', [])):
            if not os.path.exists(raw_path):
                continue
            fname = Path(raw_path).name
            res = upload_file_to_drive(service, raw_path, raw_folder_id,
                                       file_name=fname, mimetype='image/jpeg')
            res['label'] = f"raw/{fname}"
            uploaded.append(res)
            status = "✅" if res['success'] else "❌"
            print(f"  {status} Upload {res['label']}: {res.get('error','ok')}")

        # ── Upload grid (komposit bersih) ─────────────────────────────────────
        grid_path = session.get('grid_path')
        if grid_path and os.path.exists(grid_path):
            fname = Path(grid_path).name
            res = upload_file_to_drive(service, grid_path, session_folder_id,
                                       file_name=fname, mimetype='image/jpeg')
            res['label'] = fname
            uploaded.append(res)
            status = "✅" if res['success'] else "❌"
            print(f"  {status} Upload {res['label']}: {res.get('error','ok')}")

        # ── Upload result final ────────────────────────────────────────────────
        result_path = session.get('result_path')
        if result_path and os.path.exists(result_path):
            fname = Path(result_path).name
            res = upload_file_to_drive(service, result_path, session_folder_id,
                                       file_name=fname, mimetype='image/jpeg')
            res['label'] = fname
            uploaded.append(res)
            status = "✅" if res['success'] else "❌"
            print(f"  {status} Upload {res['label']}: {res.get('error','ok')}")
            if res['success']:
                result_url = res['download_url']

        # URL folder sesi di Drive
        folder_meta = service.files().get(
            fileId=session_folder_id, fields='webViewLink'
        ).execute()
        folder_url = folder_meta.get('webViewLink', '')

        failed = [u for u in uploaded if not u['success']]
        return {
            "success":        len(failed) == 0,
            "partial":        len(failed) > 0 and len(uploaded) > len(failed),
            "folder_url":     folder_url,
            "result_url":     result_url,
            "uploaded_files": uploaded,
            "errors":         [u['error'] for u in failed],
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Legacy helper (masih dipakai internal jika perlu) ─────────────────────────
def upload_to_drive(file_path: str, session_id: str) -> dict:
    """Upload satu file ke root PhotoBox Sessions/ (legacy — dipertahankan untuk kompatibilitas)."""
    service, error = get_drive_service()
    if error:
        return {"success": False, "error": error}
    try:
        folder_id = get_or_create_folder(service, GOOGLE_DRIVE_FOLDER)
        file_name = f"PhotoBox_{session_id}_{datetime.now().strftime('%H%M%S')}.jpg"
        res = upload_file_to_drive(service, file_path, folder_id,
                                   file_name=file_name, mimetype='image/jpeg')
        # normalise key ke format lama
        res['share_url'] = res.get('download_url')
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_qr_code(url: str, output_path: str) -> str:
    """Generate QR code dari URL Google Drive"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#1a1a2e", back_color="white")
    img.save(output_path)
    return output_path


# ─── Session Management ──────────────────────────────────────────────────────

def create_session() -> str:
    session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    (session_dir / 'photos').mkdir(exist_ok=True)

    session_data = {
        "id": session_id,
        "created_at": datetime.now().isoformat(),
        "photos": [],
        "template": None,
        "grid_path": None,
        "result_path": None,
        "drive_url": None,
        "drive_result": None,
        "status": "capturing"
    }

    with open(session_dir / 'session.json', 'w') as f:
        json.dump(session_data, f, indent=2)

    return session_id


def load_session(session_id: str) -> dict:
    path = SESSIONS_DIR / session_id / 'session.json'
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def save_session(session_data: dict):
    path = SESSIONS_DIR / session_data['id'] / 'session.json'
    with open(path, 'w') as f:
        json.dump(session_data, f, indent=2)


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('frontend', 'index.html')


# Cache status kamera agar tidak spam DCC setiap request
_cam_status_cache = {"connected": False, "checked_at": 0}
CAM_CACHE_TTL = 15  # detik


@app.route('/api/status')
def api_status():
    """
    Cek status server dan kamera.
    Gunakan cache 15 detik agar tidak spam DCC dengan HTTP request.
    """
    global _cam_status_cache
    now = time.time()

    # Pakai cache jika masih fresh
    if now - _cam_status_cache["checked_at"] < CAM_CACHE_TTL:
        cam = _cam_status_cache["connected"]
    else:
        cam = check_camera()
        _cam_status_cache = {"connected": cam, "checked_at": now}

    return jsonify({
        "camera_connected": cam,
        "camera_model":     "Canon EOS 700D" if cam else None,
        "server":           "running",
        "version":          "1.0.0"
    })


@app.route('/api/camera/preview/start', methods=['POST'])
def api_preview_start():
    start_live_preview()
    return jsonify({"status": "preview started"})


@app.route('/api/camera/preview/stop', methods=['POST'])
def api_preview_stop():
    stop_live_preview()
    return jsonify({"status": "preview stopped"})


@app.route('/api/session/start', methods=['POST'])
def api_session_start():
    global current_session
    session_id = create_session()
    current_session = load_session(session_id)
    return jsonify({"session_id": session_id, "status": "started"})


@app.route('/api/session/<session_id>/capture', methods=['POST'])
def api_capture(session_id):
    """Ambil 1 foto, atau retake foto spesifik"""
    session = load_session(session_id)
    if not session:
        return jsonify({"error": "Session tidak ditemukan"}), 404

    data = request.json or {}
    # Ambil index retake dari request jika ada
    retake_index = data.get('retake_index')

    if retake_index is not None:
        photo_index = int(retake_index)
    else:
        photo_index = len(session['photos']) + 1

    photo_path = str(SESSIONS_DIR / session_id / 'photos' / f'photo_{photo_index}.jpg')

    # Coba capture dari kamera, fallback ke demo mode
    if check_camera():
        success = capture_photo(photo_path)
    else:
        success = _create_demo_photo(photo_path, photo_index)

    if success:
        # Jika bukan retake, tambahkan ke list
        if retake_index is None:
            session['photos'].append(photo_path)
        else:
            # Jika retake, timpa (replace) urutan yang lama
            idx = photo_index - 1
            if idx < len(session['photos']):
                session['photos'][idx] = photo_path
            else:
                session['photos'].append(photo_path)

        save_session(session)

        # Kirim thumbnail ke frontend
        with open(photo_path, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode()

        return jsonify({
            "success": True,
            "photo_index": photo_index,
            "total": len(session['photos']),
            "thumbnail": f"data:image/jpeg;base64,{img_data}"
        })

    return jsonify({"success": False, "error": "Gagal mengambil foto"}), 500


def _create_demo_photo(path: str, index: int) -> bool:
    """Buat foto demo saat kamera tidak tersambung"""
    colors = [(220, 60, 80), (60, 120, 220), (80, 180, 80), (200, 140, 40)]
    img = Image.new('RGB', (1200, 900), colors[index - 1])
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(str(FONTS_DIR / 'Quicksand-Bold.ttf'), 120)
    except:
        font = ImageFont.load_default()
    draw.text((450, 350), f"#{index}", fill=(255, 255, 255), font=font)
    draw.text((350, 500), "Demo Mode", fill=(255, 255, 255, 180), font=font)
    img.save(path, 'JPEG', quality=90)
    return True

@app.route('/api/session/<session_id>/upload_video', methods=['POST'])
def api_upload_video(session_id):
    """Menerima kiriman klip video dari browser"""
    if 'video' not in request.files:
        return jsonify({"error": "No video file"}), 400
    
    file = request.files['video']
    photo_index = request.form.get('photo_index', '1')
    save_path = SESSIONS_DIR / session_id / f'vid_{photo_index}.webm'
    file.save(str(save_path))
    return jsonify({"success": True})


def apply_video_template(session_id: str, template_id: str) -> str:
    """Menggabungkan potongan video ke dalam template menggunakan FFmpeg."""
    templates = load_templates_from_disk()
    config = templates.get(template_id)
    session_dir = SESSIONS_DIR / session_id

    orig_cw, orig_ch = config['canvas_size']
    # Kita rendahkan sedikit resolusinya agar rendering FFmpeg secepat kilat (lebar 1080px)
    scale = max(1.0, 1080 / max(orig_cw, orig_ch))
    cw, ch = int(orig_cw * scale), int(orig_ch * scale)
    
    bg_color = config.get('background_color', '#ffffff').replace('#', '')
    slots = config.get('photo_slots', [])
    required = config.get('photo_count', len(slots))

    out_path = str(session_dir / f"live_result_{template_id}.mp4")

    # Siapkan perintah FFmpeg
    cmd = ['ffmpeg.exe', '-y']
    
    # Input 0: Background Polos
    cmd.extend(['-f', 'lavfi', '-i', f'color=c=0x{bg_color}:s={cw}x{ch}'])

    # Input 1 sampai N: Klip Video (.mp4)
    for i in range(required):
        vid_path = session_dir / f"vid_{i+1}.mp4"
        if vid_path.exists():
            cmd.extend(['-i', str(vid_path)])
        else:
            # Fallback jika video gagal direkam: pakai foto statis
            img_path = session_dir / 'photos' / f'photo_{i+1}.jpg'
            cmd.extend(['-loop', '1', '-t', '5', '-i', str(img_path)])

    # Input N+1: Template Overlay (PNG Canva)
    bg_path = ASSETS_TEMPLATES_DIR / config.get('background_image', '')
    has_bg = bg_path.exists()
    if has_bg:
        cmd.extend(['-i', str(bg_path)])

    # Rangkai Filter FFmpeg (Sihir Compositing)
    filter_str = ""
    last_ov = "0:v"
    
    for i, slot in enumerate(slots[:required]):
        sw, sh = int(slot['w'] * scale), int(slot['h'] * scale)
        sx, sy = int(slot['x'] * scale), int(slot['y'] * scale)
        
        # Scale, crop, lalu tempel di atas kanvas
        filter_str += f"[{i+1}:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh}[v{i+1}]; "
        # eof_action=pass membuat video membeku di frame terakhir ketika durasinya habis
        filter_str += f"[{last_ov}][v{i+1}]overlay={sx}:{sy}:eof_action=pass[ov{i+1}]; "
        last_ov = f"ov{i+1}"

    if has_bg:
        bg_idx = required + 1
        filter_str += f"[{bg_idx}:v]scale={cw}:{ch}[fg]; "
        filter_str += f"[{last_ov}][fg]overlay=0:0[out]"
        last_ov = "out"
    else:
        filter_str += f"[{last_ov}]copy[out]"

    # Eksekusi! -t 7 mengunci durasi hasil akhir persis 7 Detik.
    cmd.extend([
        '-filter_complex', filter_str, 
        '-map', f'[{last_ov}]', 
        '-c:v', 'h264_nvenc',   
        '-preset', 'fast',      
        '-tune', 'ull',         
        '-b:v', '2M',
        '-t', '5',                
        '-pix_fmt', 'yuv420p', 
        out_path
    ])

    print("🎥 Mulai merender Live Photo Video 7 Detik...")
    subprocess.run(cmd, capture_output=True)
    print("✅ Live Photo selesai dirender!")
    
    return out_path

@app.route('/api/session/<session_id>/apply-template', methods=['POST'])
def api_apply_template(session_id):
    import threading
    import base64
    from pathlib import Path
    
    session = load_session(session_id)
    if not session:
        return jsonify({"error": "Session tidak ditemukan"}), 404

    data = request.json or {}
    template_id = data.get('template_id')
    
    if not template_id:
        return jsonify({"error": "template_id tidak diberikan"}), 400
    
    try:
        # 1. BIKIN FOTO STATIS DULU (Proses instan, langsung jadi)
        templates = load_templates_from_disk()
        if template_id not in templates:
            return jsonify({"error": "Template tidak valid"}), 400
            
        required = templates[template_id].get('photo_count', 4)
        
        # Terapkan template overlay ke foto-foto jepretan
        paths = apply_template(session_id, template_id, session['photos'][:required])
        
        # === UPDATE STATUS AWAL UNTUK VIDEO ===
        # Beritahu sistem bahwa video mulai diproses
        session['video_status'] = 'processing'
        save_session(session)

        # 2. THREAD PEMBUAT VIDEO (Berjalan paralel di latar belakang)
        def render_bg():
            # Load ulang file JSON sesi di dalam thread agar tidak ada tabrakan data (Race Condition)
            bg_session = load_session(session_id) 
            try:
                # Panggil FFmpeg untuk merender klip-klip rekaman
                apply_video_template(session_id, template_id)
                bg_session['video_status'] = 'ready' # Lapor ke JSON: Sukses!
                print(f"✅ [Thread] Video berhasil dibuat untuk sesi {session_id}")
            except Exception as e:
                print(f"❌ [Thread] Video background error: {e}")
                bg_session['video_status'] = 'error' # Lapor ke JSON: Gagal!
            
            # Simpan laporan status terakhir ke file JSON
            save_session(bg_session) 

        # Jalankan mesin pembuat video tanpa menyuruh web menunggu
        threading.Thread(target=render_bg, daemon=True).start()

        # 3. SIMPAN DATA DAN KEMBALIKAN KE WEB DETIK ITU JUGA
        video_name = f"live_result_{template_id}.mp4"
        
        session['template']    = template_id
        session['grid_path']   = paths['grid_path']
        session['result_path'] = paths['result_path']
        session['video_path']  = str(SESSIONS_DIR / session_id / video_name)
        session['status']      = 'template_applied'
        save_session(session)

        # Baca hasil foto statis (JPEG) dan ubah ke Base64 agar web langsung bisa menampilkannya
        with open(paths['result_path'], 'rb') as f:
            img_data = base64.b64encode(f.read()).decode()

        # Buat URL jalur agar web bisa memantau dan mengakses videonya nanti
        video_url = f"/sessions/{session_id}/{video_name}"

        return jsonify({
            "success":      True,
            "result_image": f"data:image/jpeg;base64,{img_data}",
            "result_path":  paths['result_path'],
            "grid_path":    paths['grid_path'],
            "video_url":    video_url
        })
        
    except Exception as e:
        print(f"Error di api_apply_template: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/session/<session_id>/start_record', methods=['POST'])
def api_start_record(session_id):
    import subprocess
    import threading
    import time
    import urllib.request
    
    data = request.json or {}
    photo_index = data.get('photo_index', 1)
    out_path = str(SESSIONS_DIR / session_id / f'vid_{photo_index}.mp4')
    
    def record_task():
        cmd = [
            'ffmpeg.exe', '-y', 
            '-use_wallclock_as_timestamps', '1', 
            '-f', 'image2pipe',       
            '-vcodec', 'mjpeg',       
            '-i', '-',                
            '-c:v', 'libx264',        
            '-preset', 'ultrafast',
            
            # ==========================================
            # SIHIR BARU: Frame Interpolation (Blend Mode)
            # Menyulap input 5-8 FPS menjadi output 30 FPS yang sangat mulus
            # dengan menciptakan bayangan transisi antar foto!
            # ==========================================
            '-vf', 'minterpolate=fps=30:mi_mode=blend', 
            
            '-pix_fmt', 'yuv420p',
            out_path
        ]
        
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start_time = time.time()
        
        # ==========================================
        # DURASI BARU: Rekam tepat selama 4.0 detik
        # ==========================================
        while time.time() - start_time < 4.0:
            try:
                req = urllib.request.Request(f"{DCC_HOST}/liveview.jpg")
                with urllib.request.urlopen(req, timeout=0.5) as response:
                    process.stdin.write(response.read())
            except Exception:
                pass
                
        try:
            process.stdin.close()
            process.wait(timeout=3)
        except Exception:
            process.kill()

    threading.Thread(target=record_task, daemon=True).start()
    return jsonify({"success": True})

# Tambahkan helper route agar Web bisa mengakses video di dalam folder sessions
@app.route('/sessions/<session_id>/<filename>')
def serve_session_file(session_id, filename):
    return send_from_directory(str(SESSIONS_DIR / session_id), filename)

@app.route('/api/session/<session_id>/status', methods=['GET'])
def api_session_status(session_id):
    """Endpoint bagi web untuk mengecek apakah video sedang diproses, sukses, atau error."""
    session = load_session(session_id)
    if not session:
        return jsonify({"error": "Sesi tidak ditemukan"}), 404
    
    # Secara default anggap 'processing' jika belum ada
    status = session.get('video_status', 'processing') 
    return jsonify({"video_status": status})

@app.route('/api/session/<session_id>/upload-drive', methods=['POST'])
def api_upload_drive(session_id):
    """
    Upload semua aset sesi ke Google Drive:
      - Foto mentah resolusi penuh (folder raw/)
      - Grid komposit (grid_*.jpg)
      - Foto final dengan branding (result_*.jpg)
    QR Code diarahkan ke URL result final.
    """
    session = load_session(session_id)
    if not session:
        return jsonify({"error": "Session tidak ditemukan"}), 404
    if not session.get('result_path'):
        return jsonify({"error": "Belum ada hasil foto — terapkan template terlebih dahulu"}), 400

    print(f"☁️  Memulai upload sesi {session_id} ke Google Drive...")
    result = upload_session_to_drive(session)

    if result.get('success') or result.get('partial'):
        # Tentukan URL untuk QR: result final jika ada, fallback ke folder
        qr_target = result.get('result_url') or result.get('folder_url', '')

        # Generate QR Code
        qr_path = str(SESSIONS_DIR / session_id / 'qrcode.png')
        if qr_target:
            generate_qr_code(qr_target, qr_path)

        session['drive_url']    = result.get('folder_url', '')
        session['drive_result'] = result.get('result_url', '')
        session['status']       = 'uploaded'
        save_session(session)

        qr_data = None
        if os.path.exists(qr_path):
            with open(qr_path, 'rb') as f:
                qr_data = base64.b64encode(f.read()).decode()

        uploaded_count = sum(1 for u in result.get('uploaded_files', []) if u.get('success'))
        total_count    = len(result.get('uploaded_files', []))

        return jsonify({
            "success":         True,
            "partial":         result.get('partial', False),
            "folder_url":      result.get('folder_url'),
            "result_url":      result.get('result_url'),
            "drive_url":       result.get('folder_url'),   # kompatibilitas frontend lama
            "qr_code":         f"data:image/png;base64,{qr_data}" if qr_data else None,
            "uploaded_count":  uploaded_count,
            "total_count":     total_count,
            "message":         f"Berhasil upload {uploaded_count}/{total_count} file",
            "errors":          result.get('errors', []),
        })

    return jsonify({"success": False, "error": result.get('error', 'Upload gagal')}), 500


@app.route('/api/session/<session_id>/print', methods=['POST'])
def api_print(session_id):
    """Print hasil foto ke printer default"""
    session = load_session(session_id)
    if not session or not session.get('result_path'):
        return jsonify({"error": "Belum ada hasil foto"}), 400

    try:
        # Linux: lp command
        result = subprocess.run(
            ['lp', '-o', 'fit-to-page', session['result_path']],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": "Sedang dicetak..."})
        # Windows fallback
        os.startfile(session['result_path'], 'print')
        return jsonify({"success": True, "message": "Sedang dicetak..."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/templates')
def api_templates():
    """
    Baca template dari assets/templates/*.json dan kembalikan ke frontend.
    Otomatis generate thumbnail jika belum ada.
    """
    templates_dict = load_templates_from_disk()
    result = []
    for tid, cfg in templates_dict.items():
        slots = cfg.get('photo_slots', [])
        shapes = list(set(s.get('shape','rect') for s in slots))

        # Generate thumbnail jika belum ada
        thumb_path = ASSETS_THUMBS_DIR / f"{tid}.jpg"
        if not thumb_path.exists():
            try:
                generate_thumbnail(tid, cfg)
            except Exception as e:
                print(f"  Thumbnail {tid} gagal: {e}")

        result.append({
            "id":               tid,
            "name":             cfg.get("name", tid),
            "description":      cfg.get("description", ""),
            "photo_count":      cfg.get("photo_count", len(slots)),
            "canvas_size":      list(cfg.get("canvas_size", [600, 800])),
            "photo_slots":      slots,
            "has_bg_image":     bool(cfg.get("background_image")),
            "thumbnail_url":    f"/api/templates/{tid}/thumbnail",
            "shapes":           shapes,
        })
    return jsonify(result)


@app.route('/assets/templates/<filename>')
def serve_template_asset(filename):
    """Serve file PNG template langsung ke frontend."""
    return send_from_directory(str(ASSETS_TEMPLATES_DIR), filename)


@app.route('/api/templates/<template_id>/thumbnail')
def api_template_thumbnail(template_id):
    """Serve thumbnail template."""
    thumb_path = ASSETS_THUMBS_DIR / f"{template_id}.jpg"

    # Generate jika belum ada
    if not thumb_path.exists():
        templates = load_templates_from_disk()
        cfg = templates.get(template_id)
        if cfg:
            try:
                generate_thumbnail(template_id, cfg)
            except Exception:
                pass

    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype='image/jpeg')

    # 1x1 pixel placeholder jika gagal
    from io import BytesIO
    img = Image.new('RGB', (1,1), (30,30,40))
    buf = BytesIO()
    img.save(buf, 'JPEG')
    buf.seek(0)
    return send_file(buf, mimetype='image/jpeg')


@app.route('/api/templates/scan-png', methods=['POST'])
def api_scan_png():
    """
    Upload PNG dan otomatis deteksi slot transparan.
    Menyimpan JSON dan PNG ke assets/templates/.
    Request: multipart/form-data dengan field 'file' dan opsional 'id', 'name'
    """
    if 'file' not in request.files:
        return jsonify({"error": "Tidak ada file yang diupload"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "Nama file kosong"}), 400

    template_id   = request.form.get('id', '').strip()
    template_name = request.form.get('name', '').strip()

    # Sanitize filename
    from werkzeug.utils import secure_filename
    filename = secure_filename(file.filename)
    if not template_id:
        template_id = Path(filename).stem.lower().replace('-', '_').replace(' ', '_')

    # Simpan PNG ke assets/templates/
    png_save_path = ASSETS_TEMPLATES_DIR / f"{template_id}.png"
    file.save(str(png_save_path))
    print(f"📥 PNG disimpan: {png_save_path}")

    try:
        config = auto_generate_json_from_png(
            str(png_save_path),
            template_id=template_id,
            name=template_name or template_id.replace('_', ' ').title()
        )

        # Generate thumbnail
        try:
            generate_thumbnail(template_id, config)
        except Exception as e:
            print(f"  Thumbnail gagal: {e}")

        return jsonify({
            "success":      True,
            "template_id":  template_id,
            "photo_count":  config.get('photo_count', 0),
            "slots":        config.get('slots', []),
            "canvas_size":  config.get('canvas_size', []),
            "message":      f"Berhasil mendeteksi {config.get('photo_count',0)} slot foto"
        })

    except Exception as e:
        # Hapus PNG jika gagal
        if png_save_path.exists():
            png_save_path.unlink()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/templates/<template_id>/regenerate', methods=['POST'])
def api_regenerate_template(template_id):
    """
    Re-scan PNG untuk template yang sudah ada — berguna jika PNG diupdate.
    Menghapus JSON lama dan generate ulang dari PNG.
    """
    png_path = ASSETS_TEMPLATES_DIR / f"{template_id}.png"
    if not png_path.exists():
        return jsonify({"error": f"PNG tidak ditemukan: {template_id}.png"}), 404

    # Hapus JSON lama
    json_path = ASSETS_TEMPLATES_DIR / f"{template_id}.json"
    if json_path.exists():
        json_path.unlink()

    # Hapus thumbnail lama
    thumb_path = ASSETS_THUMBS_DIR / f"{template_id}.jpg"
    if thumb_path.exists():
        thumb_path.unlink()

    try:
        config = auto_generate_json_from_png(str(png_path), template_id=template_id)
        generate_thumbnail(template_id, config)
        return jsonify({
            "success":     True,
            "photo_count": config.get('photo_count', 0),
            "slots":       config.get('slots', []),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/drive/status')
def api_drive_status():
    """Cek status koneksi Google Drive"""
    token_path = BASE_DIR / 'token.pickle'
    creds_path = BASE_DIR / 'credentials.json'
    return jsonify({
        "credentials_exists": creds_path.exists(),
        "token_exists": token_path.exists(),
        "ready": creds_path.exists() and token_path.exists()
    })


# ─── WebSocket Events ─────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    emit('connected', {'status': 'WebSocket terhubung'})


@socketio.on('start_preview')
def on_start_preview():
    start_live_preview()


@socketio.on('stop_preview')
def on_stop_preview():
    stop_live_preview()

@app.route('/api/camera/snapshot')
def api_camera_snapshot():
    """Mengambil 1 frame statis dari live view untuk dibekukan di frontend"""
    import urllib.request
    import io
    from flask import send_file
    try:
        # Tembak API bawaan digiCamControl untuk minta 1 gambar statis
        req = urllib.request.Request(f"{DCC_HOST}/liveview.jpg")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return send_file(io.BytesIO(resp.read()), mimetype='image/jpeg')
    except Exception:
        # Jika gagal, kembalikan gambar transparan agar web tidak error
        return send_file(io.BytesIO(b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'), mimetype='image/gif')


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 60)
    print("  📸 PhotoBox Server - Canon 700D Integration")
    print("  Buka browser: http://localhost:5000")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

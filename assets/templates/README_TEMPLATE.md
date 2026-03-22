# 📁 Cara Menambah Template dari Canva

## Struktur Folder

```
assets/
├── templates/
│   ├── nama_template.json     ← definisi slot
│   └── nama_template.png      ← gambar template dari Canva (OPSIONAL)
└── thumbnails/
    └── nama_template.jpg      ← thumbnail otomatis (dibuat sistem)
```

---

## Langkah 1 — Desain di Canva

1. Buka Canva → buat desain baru
2. Tentukan ukuran canvas (contoh: 600×1900px untuk strip)
3. Desain background, border, teks, dekorasi sesuka Anda
4. **Biarkan area foto KOSONG** (transparan atau warna polos) — sistem akan mengisi foto di area ini
5. Download sebagai **PNG** (aktifkan "Transparent background" jika ada area transparan)
6. Simpan ke folder `assets/templates/` dengan nama sesuai ID template

---

## Langkah 2 — Buat File JSON

Buat file `assets/templates/nama_template.json`:

```json
{
  "id": "nama_template",
  "name": "Nama Tampil",
  "description": "Deskripsi singkat template",
  "photo_count": 4,
  "canvas_size": [600, 1900],
  "background_image": "nama_template.png",
  "background_color": "#ffffff",
  "slots": [
    {
      "id": 1,
      "x": 50,
      "y": 50,
      "w": 500,
      "h": 380,
      "shape": "rect",
      "round_radius": 0
    }
  ]
}
```

### Penjelasan Field

| Field | Keterangan |
|-------|-----------|
| `canvas_size` | Ukuran canvas [lebar, tinggi] dalam pixel — **harus sama dengan ukuran PNG Canva** |
| `background_image` | Nama file PNG dari Canva. Kosongkan (`""`) jika hanya pakai warna |
| `background_color` | Warna background jika tidak ada gambar |
| `photo_count` | Jumlah foto yang akan diambil untuk template ini |
| `slots[].x, y` | Koordinat pojok kiri atas slot foto (pixel) |
| `slots[].w, h` | Lebar dan tinggi slot foto (pixel) |
| `slots[].shape` | `"rect"` = kotak, `"round"` = lingkaran |
| `slots[].round_radius` | Radius sudut membulat (0 = kotak tajam, 8 = agak bulat) |
| `grayscale` | `true` = foto otomatis hitam-putih |

---

## Cara Mudah Menentukan Koordinat Slot

1. Buka file PNG di **Paint** atau **Photoshop**
2. Hover ke pojok kiri atas area foto kosong → catat koordinat X, Y
3. Hover ke pojok kanan bawah → hitung W = X2-X1, H = Y2-Y1
4. Masukkan ke JSON

Atau gunakan tool online: **https://www.image-map.net/**

---

## Contoh: Template dari Canva dengan 3 Foto

```json
{
  "id": "my_template",
  "name": "Template Cantik",
  "description": "Template buatan saya dari Canva",
  "photo_count": 3,
  "canvas_size": [1080, 1920],
  "background_image": "my_template.png",
  "background_color": "#ffeedd",
  "slots": [
    {"id": 1, "x": 60,  "y": 80,   "w": 960, "h": 700, "shape": "rect", "round_radius": 12},
    {"id": 2, "x": 60,  "y": 820,  "w": 460, "h": 500, "shape": "rect", "round_radius": 12},
    {"id": 3, "x": 560, "y": 820,  "w": 460, "h": 500, "shape": "rect", "round_radius": 12}
  ]
}
```

Setelah file JSON dan PNG disimpan, **restart app.py** — template baru otomatis muncul di photobox!

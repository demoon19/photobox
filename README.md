# 📸 PhotoBox System (Canon Camera Integration)

A full-stack Photo Booth system built with Flask, Web Interface, and Camera Integration, designed to capture, process, and generate customizable photo strips with template support, QR sharing, and cloud storage.

---

## 🚀 Overview

PhotoBox is a smart photobooth application that integrates:

- DSLR Camera (Canon via digiCamControl)
- Web-based interface (frontend)
- Backend processing with Flask
- Customizable photo strip templates
- Google Drive upload
- QR Code for instant access to results

This system is suitable for events, booths, and automated photo stations.

---

## ✨ Features

### Photo Capture
- Capture images directly from Canon DSLR
- Real-time preview support (via WebSocket)

### Template System
- Multiple photostrip templates (JSON-based)
- Dynamic layout rendering
- Custom frames, colors, and styles

### Image Processing
- Combine multiple photos into a single strip
- Apply filters and effects using Pillow
- Export high-quality images

### Cloud Integration
- Upload results to Google Drive
- Auto-organized session storage

### QR Code Sharing
- Generate QR Code for each session
- Easy download access for users

### Session Management
- Store session data (JSON)
- Save captured images per session
- Organized file structure

---

## 🛠️ Tech Stack

Backend:
- Python
- Flask
- Flask-SocketIO
- Pillow
- Google Drive API

Frontend:
- HTML, CSS, JavaScript

Hardware:
- Canon DSLR (via digiCamControl)


---

---

## 📂 Project Structure
``
photobox-main/
│── app.py
│── app1.py
│── requirements.txt
│
├── frontend/
├── assets/
├── sessions/
├── photos/
├── templates/
├── fonts/
``

---

## ⚙️ Installation

```bash
git clone https://github.com/demoon19/photobox/tree/photoboxV3
cd photobox-main
pip install -r requirements.txt
```

---

## ▶️ Running the App

```bash
python app.py
```

Open in browser:
http://localhost:5000

---

## 📷 Camera Setup

1. Install digiCamControl
2. Enable Web Server (default port: 5513)
3. Connect Canon DSLR

---

## ☁️ Google Drive Setup

1. Create credentials in Google Cloud Console
2. Enable Google Drive API
3. Download credentials.json
4. Run the app and authenticate

---

## 📸 Workflow

1. User opens web interface  
2. Select template  
3. Capture photos  
4. System processes images  
5. Generate photo strip  
6. QR Code generated  
7. Optional upload to Google Drive  

---

## 📌 Future Improvements

- AI filters
- Mobile optimization
- Printer integration
- Online gallery
- Authentication system

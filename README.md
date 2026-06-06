# ShipSheet

**ShipSheet** is a browser-based tool for arranging shipping labels onto print-ready A4 PDFs. Upload a PDF where each page contains one label, draw a crop region over the label area, choose how many labels per page, and download a perfectly arranged A4 sheet — ready to print.

No cloud, no subscriptions, no installs beyond Python. Runs entirely on your own machine.

---

## How It Works

Most shipping platforms (Shiprocket, Delhivery, etc.) generate label PDFs where each page is a full A4 with one small label sitting in the middle — surrounded by wasted space. Printing these one-per-page burns through paper fast.

ShipSheet lets you:
1. Upload that multi-page PDF
2. Draw a box around just the label on the preview
3. Pick your layout (4, 6, 8… labels per page)
4. Download a new A4 PDF with labels tightly packed and print-ready

---

## Features

- **Visual crop selector** — draw the crop region directly on a live preview of your PDF; the same crop is applied to every page
- **Zoomable preview** — scroll to zoom, middle-click or space+drag to pan, pinch-to-zoom on touch; inspect labels at full resolution before committing
- **Smart auto-layout** — automatically finds the column/row arrangement that maximises label size for your chosen count per page
- **Preset + custom counts** — quick buttons for 4 / 6 / 8 / 10 / 12 / 16 / 20 labels per page, plus a custom number input
- **Adjustable spacing** — margin and gap sliders in millimetres
- **High-res export** — labels are rendered at 4× resolution internally before being placed, so output is crisp at print DPI
- **Session-based** — multiple users can run jobs simultaneously without interference
- **No keyboard required** — fully operable with just the mouse

---

## Tech Stack

| Layer | Library |
|---|---|
| Backend | Python · Flask |
| PDF rendering | PyMuPDF (`fitz`) |
| Image processing | Pillow |
| PDF generation | ReportLab |
| Frontend | Vanilla JS · HTML Canvas |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/sanglap/shipsheet.git
cd shipsheet
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
python app.py
```

Then open **http://localhost:5000** in your browser.

---

## Usage

```
1. Drop or browse for your shipping label PDF
2. The first page appears as a preview in the canvas
3. Click and drag to draw a box around just the label area
   → Scroll to zoom in for precision
   → Middle-click or Space+drag to pan
4. Click "Confirm Crop Region"
5. Choose labels per page (or enter a custom number)
6. Adjust margin and gap if needed
7. Click "Generate PDF"
8. Download your print-ready A4 sheet
```

---

## Project Structure

```
shipsheet/
├── app.py                  # Flask backend — upload, process, download routes
├── templates/
│   └── index.html          # Single-page frontend (HTML + CSS + JS)
├── uploads/                # Temporary uploaded PDFs (auto-created)
└── outputs/                # Generated output PDFs (auto-created)
```

---

## Configuration

At the top of `app.py`:

```python
PREVIEW_SCALE = 1.0   # Scale for the canvas preview render
EXPORT_SCALE  = 4.0   # Scale for high-res export (increase for even sharper output)
```

Default spacing values are set in the frontend sliders:
- **Margin:** 3 mm
- **Gap:** 1 mm

Maximum upload size is 100 MB. To change it:

```python
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
```

---

## Limitations

- The crop region is drawn on **page 1** and applied uniformly to all pages — all labels must be in the same position on every page (which is the case for all standard label PDFs)
- Sessions are stored **in-memory** — restarting the server clears them
- Uploaded and output files are not automatically deleted — add a cleanup job if running long-term

---

## License

MIT

---

Made with ❤️ by [sanglap](https://github.com/sanglap)

import fitz
import math
import io
import uuid
import base64
from flask import Flask, request, jsonify, send_file, render_template, Response
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A3, A4, A5, A6
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

PREVIEW_SCALE = 3.0
EXPORT_SCALE  = 4.0

# In-memory session store — holds raw PDF bytes only, cleared after processing
sessions = {}

# ── Page size catalogue (pt = points; 1 in = 72 pt) ──
PAGE_SIZES = {
    'A3':     A3,           # 841.89 × 1190.55 pt  ≈ 11.69 × 16.54 in
    'A4':     A4,           # 595.27 × 841.89  pt  ≈  8.27 × 11.69 in
    'A5':     A5,           # 419.53 × 595.27  pt  ≈  5.83 ×  8.27 in
    'A6':     A6,           # 297.64 × 419.53  pt  ≈  4.13 ×  5.83 in
    'Letter': (612, 792),   #                         8.50 × 11.00 in
    'Legal':  (612, 1008),  #                         8.50 × 14.00 in
    'Custom': None,         # resolved from custom_w_in / custom_h_in in request
}


def in_to_pt(inches):
    return inches * 72.0


def mm_to_pt(mm):
    return mm * 2.83465


def find_best_layout(items_per_page, img_w, img_h, page_w, page_h, margin, gap):
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin
    best = None
    for cols in range(1, items_per_page + 1):
        rows   = math.ceil(items_per_page / cols)
        cell_w = (usable_w - gap * (cols - 1)) / cols
        cell_h = (usable_h - gap * (rows - 1)) / rows
        scale  = min(cell_w / img_w, cell_h / img_h)
        if scale <= 0:
            continue
        area = (img_w * scale) * (img_h * scale)
        if best is None or area > best['area']:
            best = {
                'cols': cols, 'rows': rows,
                'label_w': img_w * scale, 'label_h': img_h * scale,
                'area': area,
            }
    return best


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'File must be a PDF'}), 400

    # Read entirely into memory — never written to disk
    pdf_bytes = f.read()

    pdf = fitz.open(stream=pdf_bytes, filetype='pdf')
    page_count = len(pdf)
    pix = pdf[0].get_pixmap(matrix=fitz.Matrix(PREVIEW_SCALE, PREVIEW_SCALE))
    preview_b64 = base64.b64encode(pix.tobytes('png')).decode('utf-8')
    pdf.close()

    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        'pdf_bytes':  pdf_bytes,
        'page_count': page_count,
        'preview_w':  pix.width,
        'preview_h':  pix.height,
    }

    return jsonify({
        'session_id': session_id,
        'preview':    f'data:image/png;base64,{preview_b64}',
        'page_count': page_count,
        'preview_w':  pix.width,
        'preview_h':  pix.height,
    })


@app.route('/api/process', methods=['POST'])
def process():
    data       = request.json
    session_id = data.get('session_id')
    crop       = data.get('crop')               # {x1, y1, x2, y2} in preview-image px
    ipp        = int(data.get('items_per_page', 0))
    margin_mm  = float(data.get('margin_mm', 3))
    gap_mm     = float(data.get('gap_mm', 1))
    page_size  = data.get('page_size', 'A4')    # key from PAGE_SIZES
    custom_w   = data.get('custom_w_in')        # inches, only when page_size == 'Custom'
    custom_h   = data.get('custom_h_in')

    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404

    # Resolve page dimensions (portrait orientation enforced)
    if page_size == 'Custom':
        if not custom_w or not custom_h:
            return jsonify({'error': 'Custom size requires width and height in inches'}), 400
        pw = in_to_pt(float(custom_w))
        ph = in_to_pt(float(custom_h))
        page_w, page_h = (pw, ph) if ph >= pw else (ph, pw)   # ensure portrait
    elif page_size in PAGE_SIZES:
        page_w, page_h = PAGE_SIZES[page_size]
    else:
        return jsonify({'error': f'Unknown page size: {page_size}'}), 400

    pdf_bytes = sessions[session_id]['pdf_bytes']

    # Scale crop coords from preview-image space → high-res export space
    sf   = EXPORT_SCALE / PREVIEW_SCALE
    rx1, ry1 = int(crop['x1'] * sf), int(crop['y1'] * sf)
    rx2, ry2 = int(crop['x2'] * sf), int(crop['y2'] * sf)

    # Crop every page in memory
    pdf        = fitz.open(stream=pdf_bytes, filetype='pdf')
    total_pages = len(pdf)
    if ipp == 0:
        ipp = total_pages

    export_mat     = fitz.Matrix(EXPORT_SCALE, EXPORT_SCALE)
    cropped_images = []
    for page_num in range(total_pages):
        pix   = pdf[page_num].get_pixmap(matrix=export_mat, alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes('png')))
        cropped_images.append(image.crop((rx1, ry1, rx2, ry2)))
    pdf.close()

    # Layout
    img_w, img_h = cropped_images[0].size
    margin       = mm_to_pt(margin_mm)
    gap          = mm_to_pt(gap_mm)
    layout       = find_best_layout(ipp, img_w, img_h, page_w, page_h, margin, gap)

    if not layout:
        return jsonify({'error': 'Labels do not fit on selected page size with current margins'}), 400

    cols    = layout['cols']
    rows    = layout['rows']
    label_w = layout['label_w']
    label_h = layout['label_h']
    cell_w  = (page_w - 2 * margin - gap * (cols - 1)) / cols
    cell_h  = (page_h - 2 * margin - gap * (rows - 1)) / rows

    # Render output PDF into BytesIO — zero disk I/O
    buf        = io.BytesIO()
    pdf_canvas = canvas.Canvas(buf, pagesize=(page_w, page_h))
    idx        = 0
    total      = len(cropped_images)

    while idx < total:
        for i, image in enumerate(cropped_images[idx:idx + ipp]):
            row, col = divmod(i, cols)
            x      = margin + col * (cell_w + gap)
            y      = page_h - margin - (row + 1) * cell_h - row * gap
            draw_x = x + (cell_w - label_w) / 2
            draw_y = y + (cell_h - label_h) / 2
            pdf_canvas.drawImage(ImageReader(image), draw_x, draw_y,
                                 width=label_w, height=label_h)
        idx += ipp
        if idx < total:
            pdf_canvas.showPage()

    pdf_canvas.save()

    # Discard the session immediately — no data persists after response
    del sessions[session_id]

    # Stream the PDF directly in the response
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name='shipsheet_output.pdf',
    ), 200, {
        'X-Total-Labels':  str(total),
        'X-Items-Per-Page': str(ipp),
        'X-Cols':          str(cols),
        'X-Rows':          str(rows),
        'X-Output-Pages':  str(math.ceil(total / ipp)),
        'X-Page-Size':     page_size,
    }


if __name__ == '__main__':
    app.run(debug=True, port=5000)
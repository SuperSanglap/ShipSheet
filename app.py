import fitz
import math
import io
import os
import uuid
import base64
from flask import Flask, request, jsonify, send_file, render_template
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

PREVIEW_SCALE = 1.5
EXPORT_SCALE = 4.0

def mm_to_pt(mm):
    return mm * 2.83465

def find_best_layout(items_per_page, img_w, img_h, page_w, page_h, margin, gap):
    usable_w = page_w - (2 * margin)
    usable_h = page_h - (2 * margin)
    best = None
    for cols in range(1, items_per_page + 1):
        rows = math.ceil(items_per_page / cols)
        cell_w = (usable_w - gap * (cols - 1)) / cols
        cell_h = (usable_h - gap * (rows - 1)) / rows
        scale = min(cell_w / img_w, cell_h / img_h)
        if scale <= 0:
            continue
        final_w = img_w * scale
        final_h = img_h * scale
        area = final_w * final_h
        if best is None or area > best["area"]:
            best = {"cols": cols, "rows": rows, "label_w": final_w,
                    "label_h": final_h, "area": area}
    return best

# In-memory session store
sessions = {}

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

    session_id = str(uuid.uuid4())
    pdf_path = os.path.join(UPLOAD_FOLDER, f'{session_id}.pdf')
    f.save(pdf_path)

    # Render first page as preview
    pdf = fitz.open(pdf_path)
    page = pdf[0]
    mat = fitz.Matrix(PREVIEW_SCALE, PREVIEW_SCALE)
    pix = page.get_pixmap(matrix=mat)
    preview_bytes = pix.tobytes("png")
    pdf.close()

    preview_b64 = base64.b64encode(preview_bytes).decode('utf-8')

    sessions[session_id] = {
        'pdf_path': pdf_path,
        'page_count': len(fitz.open(pdf_path)),
        'preview_w': pix.width,
        'preview_h': pix.height,
    }

    return jsonify({
        'session_id': session_id,
        'preview': f'data:image/png;base64,{preview_b64}',
        'page_count': sessions[session_id]['page_count'],
        'preview_w': pix.width,
        'preview_h': pix.height,
    })

@app.route('/api/process', methods=['POST'])
def process():
    data = request.json
    session_id = data.get('session_id')
    crop = data.get('crop')  # {x1, y1, x2, y2} in preview pixels
    items_per_page = int(data.get('items_per_page', 0))
    margin_mm = float(data.get('margin_mm', 3))
    gap_mm = float(data.get('gap_mm', 1))

    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404

    sess = sessions[session_id]
    pdf_path = sess['pdf_path']

    scale_factor = EXPORT_SCALE / PREVIEW_SCALE
    rx1 = int(crop['x1'] * scale_factor)
    ry1 = int(crop['y1'] * scale_factor)
    rx2 = int(crop['x2'] * scale_factor)
    ry2 = int(crop['y2'] * scale_factor)

    pdf = fitz.open(pdf_path)
    total_pages = len(pdf)

    if items_per_page == 0:
        items_per_page = total_pages

    export_matrix = fitz.Matrix(EXPORT_SCALE, EXPORT_SCALE)
    cropped_images = []

    for page_num in range(total_pages):
        page = pdf[page_num]
        pix = page.get_pixmap(matrix=export_matrix, alpha=False)
        img_bytes = pix.tobytes("png")
        image = Image.open(io.BytesIO(img_bytes))
        cropped = image.crop((rx1, ry1, rx2, ry2))
        cropped_images.append(cropped)

    pdf.close()

    sample = cropped_images[0]
    img_w, img_h = sample.size
    page_w, page_h = A4
    margin = mm_to_pt(margin_mm)
    gap = mm_to_pt(gap_mm)

    layout = find_best_layout(items_per_page, img_w, img_h, page_w, page_h, margin, gap)
    cols = layout["cols"]
    label_w = layout["label_w"]
    label_h = layout["label_h"]

    usable_w = page_w - (2 * margin)
    usable_h = page_h - (2 * margin)
    cell_w = (usable_w - gap * (cols - 1)) / cols
    cell_h = (usable_h - gap * (layout["rows"] - 1)) / layout["rows"]

    output_id = str(uuid.uuid4())
    output_path = os.path.join(OUTPUT_FOLDER, f'{output_id}.pdf')

    pdf_canvas = canvas.Canvas(output_path, pagesize=A4)
    index = 0
    total = len(cropped_images)

    while index < total:
        page_labels = cropped_images[index:index + items_per_page]
        for i, image in enumerate(page_labels):
            row = i // cols
            col = i % cols
            x = margin + col * (cell_w + gap)
            y = page_h - margin - (row + 1) * cell_h - row * gap
            draw_x = x + (cell_w - label_w) / 2
            draw_y = y + (cell_h - label_h) / 2
            pdf_canvas.drawImage(ImageReader(image), draw_x, draw_y,
                                 width=label_w, height=label_h)
        index += items_per_page
        if index < total:
            pdf_canvas.showPage()

    pdf_canvas.save()

    sessions[session_id]['output_path'] = output_path
    sessions[session_id]['output_id'] = output_id

    return jsonify({
        'success': True,
        'output_id': output_id,
        'total_labels': total,
        'items_per_page': items_per_page,
        'cols': cols,
        'rows': layout["rows"],
        'output_pages': math.ceil(total / items_per_page),
    })

@app.route('/api/download/<output_id>')
def download(output_id):
    path = os.path.join(OUTPUT_FOLDER, f'{output_id}.pdf')
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=True, download_name='labels_arranged_A4.pdf',
                     mimetype='application/pdf')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
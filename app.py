import fitz
import math
import io
import uuid
import base64
from flask import Flask, request, jsonify, render_template
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A3, A4, A5, A6
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB

PREVIEW_SCALE = 1.0
EXPORT_SCALE  = 4.0

PAGE_SIZES = {
    'A3':     A3,
    'A4':     A4,
    'A5':     A5,
    'A6':     A6,
    'Letter': (612, 792),
    'Legal':  (612, 1008),
    'Custom': None,
}

def in_to_pt(i): return i * 72.0
def mm_to_pt(m): return m * 2.83465

def find_best_layout(ipp, img_w, img_h, page_w, page_h, margin, gap):
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin
    best = None
    for cols in range(1, ipp + 1):
        rows   = math.ceil(ipp / cols)
        cell_w = (usable_w - gap * (cols - 1)) / cols
        cell_h = (usable_h - gap * (rows - 1)) / rows
        scale  = min(cell_w / img_w, cell_h / img_h)
        if scale <= 0:
            continue
        area = (img_w * scale) * (img_h * scale)
        if best is None or area > best['area']:
            best = {'cols': cols, 'rows': rows,
                    'label_w': img_w * scale, 'label_h': img_h * scale,
                    'area': area}
    return best


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_pdf():
    """
    Receive one PDF file. Extract page count and (optionally) a preview image.
    Return the PDF bytes as base64 to be stored in the browser.
    The server stores nothing — zero session state.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'File must be a PDF'}), 400

    is_first = request.form.get('is_first', 'false').lower() == 'true'

    pdf_bytes = f.read()
    try:
        pdf        = fitz.open(stream=pdf_bytes, filetype='pdf')
        page_count = len(pdf)
        preview_b64 = None
        if is_first:
            pix = pdf[0].get_pixmap(matrix=fitz.Matrix(PREVIEW_SCALE, PREVIEW_SCALE))
            preview_b64 = base64.b64encode(pix.tobytes('png')).decode('utf-8')
        pdf.close()
    except Exception as e:
        return jsonify({'error': f'Could not read PDF: {e}'}), 400

    # Send the PDF bytes back to the browser as base64 — browser owns the data
    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

    resp = {
        'file_id':    str(uuid.uuid4()),
        'name':       f.filename,
        'page_count': page_count,
        'pdf_b64':    pdf_b64,          # browser stores this
    }
    if preview_b64:
        resp['preview']   = f'data:image/png;base64,{preview_b64}'
        resp['preview_w'] = pix.width
        resp['preview_h'] = pix.height

    return jsonify(resp)


@app.route('/api/process', methods=['POST'])
def process():
    """
    Fully stateless. The browser sends all PDF bytes (as base64 list) with the request.
    Server processes and returns the output PDF as base64. Nothing is stored anywhere.
    """
    data      = request.json or {}
    pdfs_b64  = data.get('pdfs', [])       # [{ file_id, name, pdf_b64, page_count }, ...]
    crop      = data.get('crop')
    ipp       = int(data.get('items_per_page', 0))
    margin_mm = float(data.get('margin_mm', 3))
    gap_mm    = float(data.get('gap_mm', 1))
    page_size = data.get('page_size', 'A4')
    custom_w  = data.get('custom_w_in')
    custom_h  = data.get('custom_h_in')

    if not pdfs_b64:
        return jsonify({'error': 'No PDF data provided — please re-upload your files'}), 400
    if not crop:
        return jsonify({'error': 'No crop region provided'}), 400

    # Resolve page size
    if page_size == 'Custom':
        if not custom_w or not custom_h:
            return jsonify({'error': 'Custom size requires width and height in inches'}), 400
        pw = in_to_pt(float(custom_w))
        ph = in_to_pt(float(custom_h))
        page_w, page_h = (pw, ph) if ph >= pw else (ph, pw)
    elif page_size in PAGE_SIZES:
        page_w, page_h = PAGE_SIZES[page_size]
    else:
        return jsonify({'error': f'Unknown page size: {page_size}'}), 400

    sf  = EXPORT_SCALE / PREVIEW_SCALE
    rx1, ry1 = int(crop['x1'] * sf), int(crop['y1'] * sf)
    rx2, ry2 = int(crop['x2'] * sf), int(crop['y2'] * sf)
    if rx2 <= rx1 or ry2 <= ry1:
        return jsonify({'error': 'Invalid crop region — please redraw the selection'}), 400

    # Decode and crop every page of every PDF
    cropped_images = []
    export_mat = fitz.Matrix(EXPORT_SCALE, EXPORT_SCALE)
    try:
        for entry in pdfs_b64:
            pdf_bytes = base64.b64decode(entry['pdf_b64'])
            pdf = fitz.open(stream=pdf_bytes, filetype='pdf')
            for page_num in range(len(pdf)):
                pix   = pdf[page_num].get_pixmap(matrix=export_mat, alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes('png')))
                cropped_images.append(image.crop((rx1, ry1, rx2, ry2)))
            pdf.close()
    except Exception as e:
        return jsonify({'error': f'PDF processing error: {e}'}), 500

    total = len(cropped_images)
    if total == 0:
        return jsonify({'error': 'No pages found in uploaded files'}), 400

    img_w, img_h = cropped_images[0].size
    if img_w == 0 or img_h == 0:
        return jsonify({'error': 'Crop region is empty — please redraw the selection'}), 400

    if ipp == 0:
        ipp = total

    margin = mm_to_pt(margin_mm)
    gap    = mm_to_pt(gap_mm)
    layout = find_best_layout(ipp, img_w, img_h, page_w, page_h, margin, gap)
    if not layout:
        return jsonify({'error': 'Labels do not fit — try reducing margins/gap or a larger page size'}), 400

    cols, rows = layout['cols'], layout['rows']
    label_w, label_h = layout['label_w'], layout['label_h']
    cell_w = (page_w - 2 * margin - gap * (cols - 1)) / cols
    cell_h = (page_h - 2 * margin - gap * (rows - 1)) / rows

    try:
        buf        = io.BytesIO()
        pdf_canvas = canvas.Canvas(buf, pagesize=(page_w, page_h))
        idx = 0
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
        buf.seek(0)
        out_b64 = base64.b64encode(buf.read()).decode('utf-8')
    except Exception as e:
        return jsonify({'error': f'PDF generation error: {e}'}), 500

    return jsonify({
        'pdf_b64':        out_b64,
        'filename':       f'shipsheet_{page_size.lower()}.pdf',
        'total_labels':   total,
        'items_per_page': ipp,
        'cols':           cols,
        'rows':           rows,
        'output_pages':   math.ceil(total / ipp),
        'page_size':      page_size,
    })


if __name__ == '__main__':
    app.run(debug=False)
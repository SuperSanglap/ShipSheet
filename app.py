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

# sessions[session_id] = {
#   'pdfs': [ {'bytes': bytes, 'name': str, 'page_count': int}, ... ],
#   'preview_w': int, 'preview_h': int,   ← from first PDF first page
# }
sessions = {}

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
    Add one PDF to a session.
    Pass session_id in form data to append to an existing session.
    Omit it (or pass empty) to start a new session.
    Returns session_id, file_id, page_count for this file, total_pages across
    all files, and (on first upload) a base64 preview of page 1.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'File must be a PDF'}), 400

    session_id = request.form.get('session_id', '').strip() or None

    pdf_bytes = f.read()
    try:
        pdf        = fitz.open(stream=pdf_bytes, filetype='pdf')
        page_count = len(pdf)
        first_pix  = pdf[0].get_pixmap(matrix=fitz.Matrix(PREVIEW_SCALE, PREVIEW_SCALE))
        pdf.close()
    except Exception as e:
        return jsonify({'error': f'Could not read PDF: {e}'}), 400

    # Create or retrieve session
    if session_id and session_id in sessions:
        sess = sessions[session_id]
    else:
        session_id = str(uuid.uuid4())
        preview_b64 = base64.b64encode(first_pix.tobytes('png')).decode('utf-8')
        sess = {
            'pdfs':      [],
            'preview':   f'data:image/png;base64,{preview_b64}',
            'preview_w': first_pix.width,
            'preview_h': first_pix.height,
        }
        sessions[session_id] = sess

    file_id = str(uuid.uuid4())
    sess['pdfs'].append({
        'file_id':    file_id,
        'name':       f.filename,
        'bytes':      pdf_bytes,
        'page_count': page_count,
    })

    total_pages = sum(p['page_count'] for p in sess['pdfs'])

    resp = {
        'session_id':  session_id,
        'file_id':     file_id,
        'name':        f.filename,
        'page_count':  page_count,
        'total_pages': total_pages,
        'file_count':  len(sess['pdfs']),
    }
    # Only include preview on first upload
    if len(sess['pdfs']) == 1:
        resp['preview']   = sess['preview']
        resp['preview_w'] = sess['preview_w']
        resp['preview_h'] = sess['preview_h']

    return jsonify(resp)


@app.route('/api/remove_file', methods=['POST'])
def remove_file():
    """Remove one PDF from a session by file_id."""
    data       = request.json or {}
    session_id = data.get('session_id')
    file_id    = data.get('file_id')

    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404

    sess = sessions[session_id]
    before = len(sess['pdfs'])
    sess['pdfs'] = [p for p in sess['pdfs'] if p['file_id'] != file_id]

    if len(sess['pdfs']) == before:
        return jsonify({'error': 'File not found'}), 404

    total_pages = sum(p['page_count'] for p in sess['pdfs'])
    return jsonify({'total_pages': total_pages, 'file_count': len(sess['pdfs'])})


@app.route('/api/process', methods=['POST'])
def process():
    data      = request.json or {}
    session_id = data.get('session_id')
    crop       = data.get('crop')
    ipp        = int(data.get('items_per_page', 0))
    margin_mm  = float(data.get('margin_mm', 3))
    gap_mm     = float(data.get('gap_mm', 1))
    page_size  = data.get('page_size', 'A4')
    custom_w   = data.get('custom_w_in')
    custom_h   = data.get('custom_h_in')

    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found — please re-upload your PDF(s)'}), 404

    sess = sessions[session_id]
    if not sess['pdfs']:
        return jsonify({'error': 'No files in session'}), 400
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

    # Crop every page of every PDF in order
    cropped_images = []
    export_mat = fitz.Matrix(EXPORT_SCALE, EXPORT_SCALE)
    try:
        for pdf_entry in sess['pdfs']:
            pdf = fitz.open(stream=pdf_entry['bytes'], filetype='pdf')
            for page_num in range(len(pdf)):
                pix   = pdf[page_num].get_pixmap(matrix=export_mat, alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes('png')))
                cropped_images.append(image.crop((rx1, ry1, rx2, ry2)))
            pdf.close()
    except Exception as e:
        return jsonify({'error': f'PDF processing error: {e}'}), 500

    total = len(cropped_images)
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
        idx        = 0
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
        pdf_b64 = base64.b64encode(buf.read()).decode('utf-8')
    except Exception as e:
        return jsonify({'error': f'PDF generation error: {e}'}), 500

    del sessions[session_id]

    return jsonify({
        'pdf_b64':        pdf_b64,
        'filename':       f'shipsheet_{page_size.lower()}.pdf',
        'total_labels':   total,
        'items_per_page': ipp,
        'cols':           cols,
        'rows':           rows,
        'output_pages':   math.ceil(total / ipp),
        'page_size':      page_size,
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
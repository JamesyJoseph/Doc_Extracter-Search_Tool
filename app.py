from flask import *
from pymongo import MongoClient
import fitz 
import os
from pdf2image import convert_from_path
from PIL import Image

app = Flask(__name__)
UPLOAD_FOLDER = 'source'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = ' '


client = MongoClient("mongodb://localhost:27017/")
db = client["pdf_database"]
collection = db["documents"]
preview_collection = db["recent_uploads"]




import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Users\james\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"

def extract_pdf_text(filepath):
    poppler_path = r"C:\Users\james\Downloads\Release-24.08.0-0\poppler-24.08.0\Library\bin"
    doc = fitz.open(filepath)
    text = ""
    for page in doc:
        page_text = page.get_text()
        if page_text.strip():
            text += page_text
        else:
            images = convert_from_path(filepath, poppler_path=poppler_path)
            for img in images:
                text += pytesseract.image_to_string(img)
            break  
    doc.close()
    return text


IMPORTANT_KEYWORDS = [
    "height", "area", "bedroom", "bedrooms", "dimension", "dimensions", "sqft", "location", "floor", "size", "bhk"
]

def extract_key_value_pairs(text):
    kv_pairs = {}
    lines = text.splitlines()
    for line in lines:
        if ':' in line:
            key, value = line.split(':', 1)
            kv_pairs[key.strip()] = value.strip()
    return kv_pairs


@app.route('/', methods=['GET', 'POST'])
def home():

    if not session.pop('uploaded', False):
        preview_collection.delete_many({})
    
    preview = preview_collection.find_one()
    
        
    if request.method == 'POST':
        uploaded_files = request.files.getlist('pdfs')
        for uploaded_file in uploaded_files:
            if uploaded_file.filename.endswith('.pdf'):
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], uploaded_file.filename)
                uploaded_file.save(filepath)

                content = extract_pdf_text(filepath)
                
                summary = extract_key_value_pairs(content)

                
                if not collection.find_one({"filename": uploaded_file.filename}):
                    data = {
                        "filename": uploaded_file.filename,
                        "content": content,
                        "keywords": content.lower().split()[:50]
                    }
                    collection.insert_one(data)
                    
                preview_collection.delete_many({})
                preview_collection.insert_one({
                    "filename": uploaded_file.filename,
                    "summary": summary,
                    "content": content
                })


                preview = preview_collection.find_one()
                
                session['uploaded'] = True

        return redirect('/')
    
    if request.args.get('refresh') == '1':
        preview_collection.delete_many({})
       
    all_filenames = collection.distinct("filename")
    return render_template('home.html', filenames=all_filenames, preview=preview)


@app.route('/update_preview', methods=['POST'])
def update_preview():
    summary_data = request.form.to_dict(flat=False)
    updated_summary = {}

    for key in request.form:
        if key.startswith("summary["):
            actual_key = key[8:-1] 
            updated_summary[actual_key] = request.form.get(key)


    new_key = request.form.get('new_key', '').strip()
    new_value = request.form.get('new_value', '').strip()
    if new_key and new_value:
        updated_summary[new_key] = new_value

    doc = preview_collection.find_one()
    if doc:
        preview_collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {"summary": updated_summary}}
        )
        doc['summary'] = updated_summary
    return render_template('preview_section.html', preview=doc)


@app.route('/push_to_original', methods=['POST'])
def push_to_original():
    doc = preview_collection.find_one()
    if not doc:
        flash("No preview available to push.", "danger")
        return redirect('/')

    try:
        existing = collection.find_one({"filename": doc["filename"]})
        if existing:
            collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "summary": doc.get("summary", {}),
                    "content": doc.get("content", "")
                }}
            )
            flash(f"Updated existing record: {doc['filename']}", "success")
        else:
            collection.insert_one(doc)
            flash(f"Inserted new document: {doc['filename']}", "success")

    except Exception as e:
        flash(f"Error while pushing to database: {str(e)}", "danger")

    return redirect('/')




import re
from markupsafe import Markup

def highlight_snippet(text, keyword, context_words=10, max_snippets=5):
    words = text.split()
    snippets = []
    keyword_lower = keyword.lower()

    for i, word in enumerate(words):
        if keyword_lower in word.lower():
            start = max(i - context_words, 0)
            end = min(i + context_words + 1, len(words))
            snippet = " ".join(words[start:end])
            snippet = re.sub(f'({re.escape(keyword)})', r'<mark>\1</mark>', snippet, flags=re.IGNORECASE)
            snippets.append(snippet)
            if len(snippets) >= max_snippets:
                break
    return snippets if snippets else ["<i>No matching content found.</i>"]

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '')
    filename = request.args.get('filename', '')

    search_filter = {}
    if query:
        search_filter["content"] = {"$regex": query, "$options": "i"}
    if filename:
        search_filter["filename"] = {"$regex": filename, "$options": "i"}

    documents = collection.find(search_filter)

    results = []
    for doc in documents:
        if query:
            snippets = highlight_snippet(doc['content'], query)
        else:
            brief = " ".join(doc['content'].split()[:50]) + "..."
            snippets = [brief]
        results.append({
            "filename": doc['filename'],
            "snippets": snippets,
            "link": url_for('serve_pdf', filename=doc['filename'])
        })

    # Get all unique filenames from MongoDB
    all_filenames = collection.distinct("filename")

    return render_template('result.html', results=results, query=query, filename=filename, filenames=all_filenames)


@app.route('/pdfs/<path:filename>')
def serve_pdf(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/clear', methods=['POST'])
def clear_database():
    collection.delete_many({})
    return redirect('/')

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True)
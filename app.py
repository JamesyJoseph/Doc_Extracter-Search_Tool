from flask import *
from pymongo import MongoClient
import fitz 
import os
from pdf2image import convert_from_path
from PIL import Image
import re


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


def extract_structured_units(text):
    pattern = re.compile(
        r"The\s+(?P<unit_name>[A-Z\s]+[A-Z])\s*[\n\r]*"
        r"(?P<level>[A-Z\s\+]+)?\s*\|\s*"
        r"(?P<area>[\d,\.]+\s*SQ\s*FT)\s*\|\s*"
        r"(?P<beds>\d+)\s*BEDS?\s*\|\s*"
        r"(?P<baths>[\d\.]+)\s*BATHS?",
        re.IGNORECASE
    )

    units = []
    for match in pattern.finditer(text):
        units.append({
            "unit_name": match.group("unit_name").strip().title(),
            "level": match.group("level").strip().title() if match.group("level") else "",
            "area": match.group("area").strip(),
            "beds": match.group("beds").strip(),
            "baths": match.group("baths").strip()
        })
    return units



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
                    
                structured_units = extract_structured_units(content)

                preview_collection.delete_many({})
                preview_collection.insert_one({
                    "filename": uploaded_file.filename,
                    "summary": summary,
                    "content": content,
                    "units": structured_units  
                })


                preview = preview_collection.find_one()
                flash("PDF uploaded!", "upload")
                session['uploaded'] = True

        return redirect('/')
    
    if request.args.get('refresh') == '1':       
        preview_collection.delete_many({})
       
    all_filenames = collection.distinct("filename")
    return render_template('home.html', filenames=all_filenames, preview=preview)


import json
from collections import defaultdict

@app.route('/update_preview', methods=['POST'])
def update_preview():
    import json, re
    from flask import flash, redirect, url_for

    preview = preview_collection.find_one(sort=[('_id', -1)])
    if not preview:
        flash("No preview available to update. Please upload a file.", "danger")
        return redirect(url_for('home'))

    existing_units = preview.get("units", [])
    updated_units = {}     # updates to existing units
    new_units = {}         # new entries reusing same indexes (0, 1, ...) but outside existing range

    existing_len = len(existing_units)

    for key, value in request.form.items():
        unit_match = re.match(r'units\[(\d+)\]\[(.+?)\]', key)
        if unit_match:
            index = int(unit_match.group(1))
            field = unit_match.group(2)
            value = value.strip()

            if index < existing_len:
                if index not in updated_units:
                    updated_units[index] = {}
                updated_units[index][field] = value
            else:
                if index not in new_units:
                    new_units[index] = {}
                new_units[index][field] = value

    merged_units = []
    any_updates = False
    for idx, original in enumerate(existing_units):
        merged = original.copy()
        if idx in updated_units:
            merged.update(updated_units[idx])
            any_updates = True
        merged_units.append(merged)

    for idx in sorted(new_units.keys()):
        new_unit = new_units[idx]
        if new_unit:  
            merged_units.append(new_unit)
    if any_updates:
        flash("Existing unit(s) updated successfully.", "update")
    
    new_unit_data = request.form.get("new_unit_fields")
    if new_unit_data:
        try:
            new_unit = json.loads(new_unit_data)
            if isinstance(new_unit, dict) and new_unit:
                merged_units.append(new_unit)
                flash("New unit(s) inserted", "insert")
        except json.JSONDecodeError:
            flash("Invalid new unit data submitted.", "warning")

    preview_collection.update_one(
        {"_id": preview["_id"]},
        {"$set": {"units": merged_units}}
    )

    updated_preview = preview_collection.find_one({"_id": preview["_id"]})
    return render_template("home.html", preview=updated_preview)



@app.route('/push_to_original', methods=['POST'])
def push_to_original():
    preview = preview_collection.find_one(sort=[('_id', -1)])
    if not preview:
        flash("No preview available to push.", "error")
        return redirect(url_for('home'))

    existing = collection.find_one({"filename": preview["filename"]})
    new_units = preview.get("units", [])
    
    if existing:
        collection.update_one(
            {"_id": existing["_id"]},
            {"$push": {"units": {"$each": new_units}}}
        )
        flash("Updations/New Units appended to database.", "success")
    else:
        collection.insert_one(preview)
        flash("New document added to original collection.", "info")

    return redirect(url_for('home'))




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
    query = request.args.get('q', '').strip()
    filename = request.args.get('filename', '').strip()

    search_filter = []
    if query:
        regex = {"$regex": query, "$options": "i"}

        search_filter.extend([
            {"content": regex},
            {"summary": regex},
            {"keywords": regex},
            {"units": {
                "$elemMatch": {
                    "$or": [
                        {k: regex} for k in ["unit_name", "area", "beds", "baths", "level", "Price", "Bed", "name", "floor"]
                    ]
                }
            }}
        ])

    final_filter = {}
    if filename:
        final_filter["filename"] = {"$regex": filename, "$options": "i"}
    
    if query:
        final_filter["$or"] = search_filter

    documents = collection.find(final_filter)
    
    results = []
    for doc in documents:
        snippets = []

        if "content" in doc and isinstance(doc["content"], str) and query.lower() in doc["content"].lower():
            snippets.append(f"<strong>Content:</strong> ...{highlight_snippet(doc['content'], query)}...")

        if "summary" in doc and isinstance(doc["summary"], str) and query.lower() in doc["summary"].lower():
            snippets.append(f"<strong>Summary:</strong> ...{highlight_snippet(doc['summary'], query)}...")

        if "keywords" in doc and isinstance(doc["keywords"], list):
            matched_keywords = [kw for kw in doc["keywords"] if isinstance(kw, str) and query.lower() in kw.lower()]
            if matched_keywords:
                snippets.append(f"<strong>Keywords:</strong> " + ", ".join(matched_keywords))

        if "units" in doc and isinstance(doc["units"], list):
            for unit in doc["units"]:
                for k, v in unit.items():
                    if isinstance(v, str) and query.lower() in v.lower():
                        snippets.append(f"<strong>Unit {k}:</strong> {v}")

        if snippets:
            results.append({
                "filename": doc["filename"],
                "snippets": snippets,
                "link": url_for('serve_pdf', filename=doc['filename'])
            })

    all_filenames = collection.distinct("filename")

    return render_template(
        'result.html',
        results=results,
        query=query,
        filename=filename,
        filenames=all_filenames
    )





@app.route('/pdfs/<path:filename>')
def serve_pdf(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/clear', methods=['POST'])
def clear_database():
    collection.delete_many({})
    flash("Database Cleared Successfully","warning")
    return redirect('/')

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True)
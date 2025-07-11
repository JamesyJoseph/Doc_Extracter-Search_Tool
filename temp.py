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
                    "units": structured_units  # <-- include extracted unit table data
                })


                preview = preview_collection.find_one()
                
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

    # Parse all form fields
    for key, value in request.form.items():
        unit_match = re.match(r'units\[(\d+)\]\[(.+?)\]', key)
        if unit_match:
            index = int(unit_match.group(1))
            field = unit_match.group(2)
            value = value.strip()

            # Classify as update or new
            if index < existing_len:
                if index not in updated_units:
                    updated_units[index] = {}
                updated_units[index][field] = value
            else:
                if index not in new_units:
                    new_units[index] = {}
                new_units[index][field] = value

    # Merge updates with existing units
    merged_units = []
    for idx, original in enumerate(existing_units):
        merged = original.copy()
        if idx in updated_units:
            merged.update(updated_units[idx])
        merged_units.append(merged)

    # Append any new units
    for idx in sorted(new_units.keys()):
        new_unit = new_units[idx]
        if new_unit:  # avoid empty units
            merged_units.append(new_unit)

    # If extra unit is passed via JSON string
    new_unit_data = request.form.get("new_unit_fields")
    if new_unit_data:
        try:
            new_unit = json.loads(new_unit_data)
            if isinstance(new_unit, dict) and new_unit:
                merged_units.append(new_unit)
        except json.JSONDecodeError:
            flash("Invalid new unit data submitted.", "warning")

    # Update in DB
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
        flash("No preview available to push.", "danger")
        return redirect(url_for('home'))

    # Find matching document (by filename, ID, etc.)
    existing = collection.find_one({"filename": preview["filename"]})
    new_units = preview.get("units", [])

    if existing:
        # Append units to existing document
        collection.update_one(
            {"_id": existing["_id"]},
            {"$push": {"units": {"$each": new_units}}}
        )
        flash("Units appended to existing document.", "success")
    else:
        # Insert as new
        collection.insert_one(preview)
        flash("New document added to original collection.", "success")

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
    query = request.args.get('q', '')
    filename = request.args.get('filename', '')

    search_filter = {}
    conditions = []

    if query:
        # Search in content
        conditions.append({"content": {"$regex": query, "$options": "i"}})

        # Search inside any field of any unit
        conditions.append({"units": {
            "$elemMatch": {
                "$or": [
                    {"unit_name": {"$regex": query, "$options": "i"}},
                    {"area": {"$regex": query, "$options": "i"}},
                    {"beds": {"$regex": query, "$options": "i"}},
                    {"baths": {"$regex": query, "$options": "i"}},
                    {"level": {"$regex": query, "$options": "i"}},
                    {"Price": {"$regex": query, "$options": "i"}},
                    {"Bed": {"$regex": query, "$options": "i"}},
                    {"name": {"$regex": query, "$options": "i"}}
                ]
            }
        }})

    if filename:
        conditions.append({"filename": {"$regex": filename, "$options": "i"}})

    if conditions:
        search_filter["$and"] = conditions

    documents = collection.find(search_filter)

    results = []
    for doc in documents:
        snippets = []

        # Highlight from content
        if query and "content" in doc and query.lower() in doc["content"].lower():
            snippets.extend(highlight_snippet(doc['content'], query))

        # Highlight from units
        if query and "units" in doc:
            for unit in doc["units"]:
                for key, val in unit.items():
                    if isinstance(val, str) and query.lower() in val.lower():
                        snippets.append(f"<strong>{key}</strong>: {val}")

        # If no query but want to show preview
        if not query:
            brief = " ".join(doc['content'].split()[:50]) + "..."
            snippets = [brief]

        results.append({
            "filename": doc['filename'],
            "snippets": snippets,
            "link": url_for('serve_pdf', filename=doc['filename'])
        })

    # Get all filenames
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
    return redirect('/')

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True)
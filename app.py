from flask import *
from pymongo import MongoClient
import fitz 
import os
from pdf2image import convert_from_path
from PIL import Image

app = Flask(__name__)
UPLOAD_FOLDER = 'source'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER



client = MongoClient("mongodb://localhost:27017/")
db = client["pdf_database"]
collection = db["documents"]

from pdf2image import convert_from_path
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




@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        uploaded_files = request.files.getlist('pdfs')
        for uploaded_file in uploaded_files:
            if uploaded_file.filename.endswith('.pdf'):
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], uploaded_file.filename)
                uploaded_file.save(filepath)

                content = extract_pdf_text(filepath)
                if not collection.find_one({"filename": uploaded_file.filename}):
                    data = {
                        "filename": uploaded_file.filename,
                        "content": content,
                        "keywords": content.lower().split()[:50]
                    }
                    collection.insert_one(data)
                else:
                    print(f"{uploaded_file.filename} already exists in the database.")
        return redirect('/')

    return render_template('home.html')

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '')
    filename = request.args.get('filename', '')

    search_filter = {}
    if query:
        search_filter["content"] = {"$regex": query, "$options": "i"}
    if filename:
        search_filter["filename"] = {"$regex": filename, "$options": "i"}

    results = collection.find(search_filter)
    return render_template('result.html', results=results, query=query, filename=filename)

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

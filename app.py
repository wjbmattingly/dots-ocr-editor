import os
import json
import yaml
import re
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Layout categories as specified
LAYOUT_CATEGORIES = [
    'Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 
    'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'
]

def load_config():
    """Load configuration from config.yaml"""
    try:
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        return {'data_dir': '/Users/wjm55/data/focus/focus_output'}

def get_available_files(data_dir):
    """Get list of available JSON/image pairs from the data directory, organized by folder"""
    files_by_folder = {}
    if os.path.exists(data_dir):
        for root, dirs, filenames in os.walk(data_dir):
            folder_name = os.path.basename(root)
            if folder_name == os.path.basename(data_dir):
                folder_name = "Root"
            
            for filename in filenames:
                if filename.endswith('.json'):
                    json_path = os.path.join(root, filename)
                    # Look for corresponding image files
                    base_name = filename.replace('.json', '')
                    image_extensions = ['.png', '.jpg', '.jpeg']
                    
                    for ext in image_extensions:
                        # Try different naming patterns
                        possible_names = [
                            base_name + '_original' + ext,
                            base_name + ext,
                            base_name + '_annotated' + ext
                        ]
                        
                        image_path = None
                        for possible_name in possible_names:
                            test_path = os.path.join(root, possible_name)
                            if os.path.exists(test_path):
                                image_path = test_path
                                break
                        
                        if image_path:
                            rel_json_path = os.path.relpath(json_path, data_dir)
                            rel_image_path = os.path.relpath(image_path, data_dir)
                            
                            if folder_name not in files_by_folder:
                                files_by_folder[folder_name] = []
                            
                            # Extract page number for sorting
                            page_match = re.search(r'page_(\d+)', filename)
                            page_num = int(page_match.group(1)) if page_match else 0
                            
                            files_by_folder[folder_name].append({
                                'name': base_name,
                                'json_path': rel_json_path,
                                'image_path': rel_image_path,
                                'full_json_path': json_path,
                                'full_image_path': image_path,
                                'page_num': page_num,
                                'folder': folder_name
                            })
                            break
    
    # Sort files within each folder by page number
    for folder in files_by_folder:
        files_by_folder[folder].sort(key=lambda x: x['page_num'])
    
    return files_by_folder

@app.route('/')
def index():
    """Main page showing file browser or editor"""
    config = load_config()
    files_by_folder = get_available_files(config['data_dir'])
    return render_template('index.html', files_by_folder=files_by_folder, categories=LAYOUT_CATEGORIES)

@app.route('/editor')
def editor():
    """OCR Editor interface"""
    return render_template('editor.html', categories=LAYOUT_CATEGORIES)

@app.route('/api/load_file')
def load_file():
    """Load a specific JSON file and return its data"""
    file_path = request.args.get('path')
    config = load_config()
    
    if not file_path:
        return jsonify({'error': 'No file path provided'}), 400
    
    full_path = os.path.join(config['data_dir'], file_path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        with open(full_path, 'r') as f:
            data = json.load(f)
        
        # Add unique IDs to each bounding box if not present
        for i, item in enumerate(data):
            if 'id' not in item:
                item['id'] = str(uuid.uuid4())
            if 'reading_order' not in item:
                item['reading_order'] = i
        
        return jsonify({
            'data': data,
            'file_path': file_path
        })
    except Exception as e:
        return jsonify({'error': f'Error loading file: {str(e)}'}), 500

@app.route('/api/save_file', methods=['POST'])
def save_file():
    """Save the edited JSON data back to file"""
    data = request.json
    file_path = data.get('file_path')
    bbox_data = data.get('data')
    
    if not file_path or not bbox_data:
        return jsonify({'error': 'Missing file path or data'}), 400
    
    config = load_config()
    full_path = os.path.join(config['data_dir'], file_path)
    
    try:
        # Remove temporary IDs and reading_order before saving if desired
        clean_data = []
        for item in bbox_data:
            clean_item = {k: v for k, v in item.items() if k not in ['id']}
            clean_data.append(clean_item)
        
        with open(full_path, 'w') as f:
            json.dump(clean_data, f, indent=2)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Error saving file: {str(e)}'}), 500

@app.route('/api/upload', methods=['POST'])
def upload_files():
    """Handle file uploads for drag-and-drop functionality"""
    if 'json_file' not in request.files or 'image_file' not in request.files:
        return jsonify({'error': 'Both JSON and image files are required'}), 400
    
    json_file = request.files['json_file']
    image_file = request.files['image_file']
    
    if json_file.filename == '' or image_file.filename == '':
        return jsonify({'error': 'No files selected'}), 400
    
    try:
        # Save files
        json_filename = secure_filename(json_file.filename)
        image_filename = secure_filename(image_file.filename)
        
        json_path = os.path.join(app.config['UPLOAD_FOLDER'], json_filename)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
        
        json_file.save(json_path)
        image_file.save(image_path)
        
        # Load and validate JSON
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Add unique IDs and reading order
        for i, item in enumerate(data):
            if 'id' not in item:
                item['id'] = str(uuid.uuid4())
            if 'reading_order' not in item:
                item['reading_order'] = i
        
        return jsonify({
            'data': data,
            'json_path': json_filename,
            'image_path': image_filename,
            'uploaded': True
        })
    except Exception as e:
        return jsonify({'error': f'Error processing upload: {str(e)}'}), 500

@app.route('/api/navigate')
def navigate():
    """Navigate to next/previous page in the same folder"""
    current_path = request.args.get('current_path')
    direction = request.args.get('direction')  # 'next' or 'prev'
    
    if not current_path or not direction:
        return jsonify({'error': 'Missing parameters'}), 400
    
    config = load_config()
    files_by_folder = get_available_files(config['data_dir'])
    
    # Find current file and its folder
    current_folder = None
    current_index = None
    
    for folder_name, files in files_by_folder.items():
        for i, file_info in enumerate(files):
            if file_info['json_path'] == current_path:
                current_folder = folder_name
                current_index = i
                break
        if current_folder:
            break
    
    if current_folder is None:
        return jsonify({'error': 'Current file not found'}), 404
    
    # Calculate new index
    files_in_folder = files_by_folder[current_folder]
    if direction == 'next':
        new_index = (current_index + 1) % len(files_in_folder)
    else:  # prev
        new_index = (current_index - 1) % len(files_in_folder)
    
    new_file = files_in_folder[new_index]
    
    return jsonify({
        'json_path': new_file['json_path'],
        'image_path': new_file['image_path'],
        'name': new_file['name'],
        'page_num': new_file['page_num'],
        'current_page': new_index + 1,
        'total_pages': len(files_in_folder),
        'folder': current_folder
    })

@app.route('/api/image/<path:filename>')
def serve_image(filename):
    """Serve images from the data directory or uploads"""
    config = load_config()
    
    # Check if it's an uploaded file
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(upload_path):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    
    # Otherwise serve from data directory
    data_path = os.path.join(config['data_dir'], filename)
    if os.path.exists(data_path):
        directory = os.path.dirname(data_path)
        filename = os.path.basename(data_path)
        return send_from_directory(directory, filename)
    
    return "Image not found", 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=7090)

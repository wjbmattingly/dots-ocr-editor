import os
import json
import yaml
import re
import sqlite3
import datetime
import zipfile
import io
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database configuration
DATABASE_PATH = 'ocr_editor.db'

def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Create pages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            folder_name TEXT NOT NULL,
            page_name TEXT NOT NULL,
            bbox_data TEXT NOT NULL,
            is_validated BOOLEAN DEFAULT FALSE,
            last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create validation_log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS validation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (page_id) REFERENCES pages (id)
        )
    ''')
    
    # Create exports_log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exports_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_type TEXT NOT NULL,
            export_scope TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            exported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def save_page_to_db(file_path, folder_name, page_name, bbox_data):
    """Save or update page data in database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Convert bbox_data to JSON string
    bbox_json = json.dumps(bbox_data)
    
    # Insert or update page
    cursor.execute('''
        INSERT OR REPLACE INTO pages (file_path, folder_name, page_name, bbox_data, last_modified)
        VALUES (?, ?, ?, ?, ?)
    ''', (file_path, folder_name, page_name, bbox_json, datetime.datetime.now()))
    
    conn.commit()
    conn.close()

def get_page_from_db(file_path):
    """Get page data from database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM pages WHERE file_path = ?', (file_path,))
    row = cursor.fetchone()
    
    conn.close()
    
    if row:
        return {
            'id': row['id'],
            'file_path': row['file_path'],
            'folder_name': row['folder_name'],
            'page_name': row['page_name'],
            'bbox_data': json.loads(row['bbox_data']),
            'is_validated': bool(row['is_validated']),
            'last_modified': row['last_modified'],
            'created_at': row['created_at']
        }
    return None

def get_validation_status(file_path):
    """Get validation status for a file"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT is_validated FROM pages WHERE file_path = ?', (file_path,))
    row = cursor.fetchone()
    
    conn.close()
    
    return bool(row['is_validated']) if row else False

# Initialize database on startup
init_database()

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
    
    # Sort folders alphabetically
    sorted_folders = dict(sorted(files_by_folder.items()))
    
    return sorted_folders

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
    
    # First try to get from database
    db_data = get_page_from_db(file_path)
    if db_data:
        # Add unique IDs and reading order if not present
        for i, item in enumerate(db_data['bbox_data']):
            if 'id' not in item:
                item['id'] = str(uuid.uuid4())
            if 'reading_order' not in item:
                item['reading_order'] = i
        
        return jsonify({
            'data': db_data['bbox_data'],
            'file_path': file_path,
            'is_validated': db_data['is_validated'],
            'last_modified': db_data['last_modified'],
            'source': 'database'
        })
    
    # Fall back to loading from file system
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
        
        # Save to database for future use
        folder_name = os.path.dirname(file_path).split('/')[-1] if '/' in file_path else 'Root'
        page_name = os.path.basename(file_path).replace('.json', '')
        save_page_to_db(file_path, folder_name, page_name, data)
        
        return jsonify({
            'data': data,
            'file_path': file_path,
            'is_validated': False,
            'source': 'filesystem'
        })
    except Exception as e:
        return jsonify({'error': f'Error loading file: {str(e)}'}), 500

@app.route('/api/save_file', methods=['POST'])
def save_file():
    """Save the edited JSON data to database and optionally to file"""
    data = request.json
    file_path = data.get('file_path')
    bbox_data = data.get('data')
    save_to_filesystem = data.get('save_to_filesystem', False)
    
    if not file_path or not bbox_data:
        return jsonify({'error': 'Missing file path or data'}), 400
    
    try:
        # Save to database
        folder_name = os.path.dirname(file_path).split('/')[-1] if '/' in file_path else 'Root'
        page_name = os.path.basename(file_path).replace('.json', '')
        save_page_to_db(file_path, folder_name, page_name, bbox_data)
        
        # Optionally save to filesystem
        if save_to_filesystem:
            config = load_config()
            full_path = os.path.join(config['data_dir'], file_path)
            
            # Remove temporary IDs and reading_order before saving to file
            clean_data = []
            for item in bbox_data:
                clean_item = {k: v for k, v in item.items() if k not in ['id']}
                clean_data.append(clean_item)
            
            with open(full_path, 'w') as f:
                json.dump(clean_data, f, indent=2)
        
        return jsonify({'success': True, 'saved_to_db': True, 'saved_to_file': save_to_filesystem})
    except Exception as e:
        return jsonify({'error': f'Error saving: {str(e)}'}), 500

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

@app.route('/api/validate_page', methods=['POST'])
def validate_page():
    """Mark a page as validated"""
    data = request.json
    file_path = data.get('file_path')
    is_validated = data.get('is_validated', True)
    
    if not file_path:
        return jsonify({'error': 'Missing file path'}), 400
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update validation status
        cursor.execute('''
            UPDATE pages SET is_validated = ? WHERE file_path = ?
        ''', (is_validated, file_path))
        
        if cursor.rowcount == 0:
            return jsonify({'error': 'Page not found in database'}), 404
        
        # Log validation if marking as validated
        if is_validated:
            cursor.execute('''
                INSERT INTO validation_log (page_id)
                SELECT id FROM pages WHERE file_path = ?
            ''', (file_path,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'is_validated': is_validated})
    except Exception as e:
        return jsonify({'error': f'Error updating validation: {str(e)}'}), 500

@app.route('/api/export', methods=['POST'])
def export_data():
    """Export data (page, folder, or entire project)"""
    data = request.json
    export_type = data.get('export_type')  # 'page', 'folder', 'project'
    export_scope = data.get('export_scope')  # file_path for page, folder_name for folder
    
    if not export_type:
        return jsonify({'error': 'Missing export type'}), 400
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Prepare export data
        if export_type == 'page':
            if not export_scope:
                return jsonify({'error': 'Missing file path for page export'}), 400
            
            cursor.execute('SELECT * FROM pages WHERE file_path = ?', (export_scope,))
            pages = cursor.fetchall()
            export_name = f"page_{os.path.basename(export_scope).replace('.json', '')}"
            
        elif export_type == 'folder':
            if not export_scope:
                return jsonify({'error': 'Missing folder name for folder export'}), 400
            
            cursor.execute('SELECT * FROM pages WHERE folder_name = ?', (export_scope,))
            pages = cursor.fetchall()
            export_name = f"folder_{export_scope}"
            
        elif export_type == 'project':
            cursor.execute('SELECT * FROM pages')
            pages = cursor.fetchall()
            export_name = "entire_project"
            
        else:
            return jsonify({'error': 'Invalid export type'}), 400
        
        if not pages:
            return jsonify({'error': 'No data found for export'}), 404
        
        # Create ZIP file in memory
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for page in pages:
                # Add JSON data
                json_filename = f"{page['page_name']}.json"
                clean_data = []
                bbox_data = json.loads(page['bbox_data'])
                
                for item in bbox_data:
                    clean_item = {k: v for k, v in item.items() if k not in ['id']}
                    clean_data.append(clean_item)
                
                zip_file.writestr(json_filename, json.dumps(clean_data, indent=2))
                
                # Add metadata
                metadata = {
                    'file_path': page['file_path'],
                    'folder_name': page['folder_name'],
                    'page_name': page['page_name'],
                    'is_validated': bool(page['is_validated']),
                    'last_modified': page['last_modified'],
                    'created_at': page['created_at']
                }
                
                metadata_filename = f"{page['page_name']}_metadata.json"
                zip_file.writestr(metadata_filename, json.dumps(metadata, indent=2))
        
        # Log export
        cursor.execute('''
            INSERT INTO exports_log (export_type, export_scope, file_count)
            VALUES (?, ?, ?)
        ''', (export_type, export_scope or 'all', len(pages)))
        
        conn.commit()
        conn.close()
        
        zip_buffer.seek(0)
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{export_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )
        
    except Exception as e:
        return jsonify({'error': f'Error exporting data: {str(e)}'}), 500

@app.route('/api/folders')
def get_folders():
    """Get folder structure for sidebar"""
    try:
        config = load_config()
        files_by_folder = get_available_files(config['data_dir'])
        return jsonify(files_by_folder)
    except Exception as e:
        return jsonify({'error': f'Error getting folders: {str(e)}'}), 500

@app.route('/api/stats')
def get_stats():
    """Get project statistics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get overall stats
        cursor.execute('SELECT COUNT(*) as total_pages FROM pages')
        total_pages = cursor.fetchone()['total_pages']
        
        cursor.execute('SELECT COUNT(*) as validated_pages FROM pages WHERE is_validated = 1')
        validated_pages = cursor.fetchone()['validated_pages']
        
        # Get folder stats
        cursor.execute('''
            SELECT folder_name, 
                   COUNT(*) as total,
                   SUM(CASE WHEN is_validated = 1 THEN 1 ELSE 0 END) as validated
            FROM pages 
            GROUP BY folder_name
            ORDER BY folder_name
        ''')
        folder_stats = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'total_pages': total_pages,
            'validated_pages': validated_pages,
            'validation_percentage': round((validated_pages / total_pages * 100) if total_pages > 0 else 0, 1),
            'folder_stats': folder_stats
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting stats: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=7090)

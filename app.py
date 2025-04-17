import os
import time
import uuid
import requests
import base64
from bs4 import BeautifulSoup
from flask import Flask, request, render_template, redirect, url_for, send_from_directory, flash
from openai import OpenAI
from dotenv import load_dotenv

# --- Configuration ---
# load_dotenv()  # Load environment variables from .env file
dotenv_loaded = load_dotenv() # Load and check if it found a file

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24) # Needed for flashing messages
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['GENERATED_FOLDER'] = os.path.join('static', 'generated')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max upload size 16MB

# Ensure upload and generated directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)

# --- API Keys & Defaults ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
D_ID_API_KEY = os.getenv("D_ID_API_KEY")

# --- Add print statements for debugging ---
print("-" * 20)
print(f"dotenv file loaded successfully: {dotenv_loaded}")
print(f"Loaded OPENAI_API_KEY: {OPENAI_API_KEY[:5]}...{OPENAI_API_KEY[-4:]}" if OPENAI_API_KEY else "OPENAI_API_KEY not found")
print(f"Loaded D_ID_API_KEY: {'Exists' if D_ID_API_KEY else 'D_ID_API_KEY not found'}") # Don't print the full D-ID key structure
print("-" * 20)
# --- End print statements ---

# Replace with a publicly accessible URL to a default avatar image if desired
# OLD URL: DEFAULT_AVATAR_URL = "https://images.pexels.com/photos/3775131/pexels-photo-3775131.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750&dpr=1"
# NEW URL (Example - ensure license permits usage if needed for production):
DEFAULT_AVATAR_URL = "https://images.pexels.com/photos/415829/pexels-photo-415829.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750&dpr=1"
D_ID_API_URL = "https://api.d-id.com"

# --- OpenAI Client ---
if not OPENAI_API_KEY:
    print("Warning: OPENAI_API_KEY environment variable not set.")
    # Handle the absence of the key appropriately, maybe disable OpenAI features
    openai_client = None
else:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"Error initializing OpenAI client: {e}")
        openai_client = None

if not D_ID_API_KEY:
    print("Warning: D_ID_API_KEY environment variable not set.")
    # Handle the absence of the key appropriately, maybe disable D-ID features

# --- Helper Functions ---

def scrape_product_data(url):
    """Scrapes product title and description from a URL."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # --- Title Extraction (add more selectors as needed) ---
        title = None
        title_selectors = ['#productTitle', 'h1'] # Common Amazon ID and generic H1
        for selector in title_selectors:
            title_element = soup.select_one(selector)
            if title_element:
                title = title_element.get_text(strip=True)
                break
        if not title: # Fallback to <title> tag
             title_tag = soup.find('title')
             if title_tag:
                 title = title_tag.get_text(strip=True)

        # --- Description Extraction (add more selectors as needed) ---
        description = None
        desc_selectors = [
            '#feature-bullets .a-list-item', # Amazon feature bullets
            '#productDescription',           # Amazon product description
            'meta[name="description"]'       # Meta description tag
        ]
        desc_parts = []

        # Try feature bullets first
        bullet_elements = soup.select('#feature-bullets .a-list-item')
        if bullet_elements:
            for item in bullet_elements:
                text = item.get_text(strip=True)
                if text: # Avoid empty strings
                    desc_parts.append(text)
            description = ". ".join(desc_parts) + "."
        else:
            # Try other selectors if bullets not found
            for selector in desc_selectors[1:]: # Skip bullets selector now
                desc_element = soup.select_one(selector)
                if desc_element:
                    if selector == 'meta[name="description"]':
                        description = desc_element.get('content', '').strip()
                    else:
                        description = desc_element.get_text(strip=True)
                    if description: # Found a description
                         break

        # Basic cleanup
        if description:
            description = ' '.join(description.split()) # Remove extra whitespace

        return {
            "title": title or "Product",
            "description": description or "No description found."
        }

    except requests.exceptions.RequestException as e:
        print(f"Error scraping URL {url}: {e}")
        return None
    except Exception as e:
        print(f"Error parsing HTML from {url}: {e}")
        return None

def generate_marketing_script(title, description):
    """Generates a short marketing script using OpenAI."""
    if not openai_client:
        return "Error: OpenAI client not initialized. Check API key."

    prompt = f"""
    Create a short, enthusiastic, and catchy promotional script (2-4 sentences)
    for a product based on the following information. Make it sound like someone
    is presenting it in a short video clip.

    Product Title: {title}
    Product Description/Features: {description}

    Script:
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o", # Or use "gpt-4" if available
            messages=[
                {"role": "system", "content": "You are a helpful assistant creating marketing scripts."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.7,
            n=1,
            stop=None,
        )
        script = response.choices[0].message.content.strip()
        return script
    except Exception as e:
        print(f"Error calling OpenAI API: {e}")
        return f"Error generating script: {e}"

def create_d_id_talk(script, image_url):
    """Creates a talking avatar video using D-ID API and polls for completion."""
    if not D_ID_API_KEY:
        return {"error": "D-ID API key not configured."}

    # --- Correctly format the D-ID Authorization Header ---
    # Assumes D_ID_API_KEY in .env is "your_email@example.com:your_actual_api_key"
    try:
        api_key_bytes = D_ID_API_KEY.encode('utf-8')
        api_key_base64 = base64.b64encode(api_key_bytes).decode('utf-8')
        auth_header = f"Basic {api_key_base64}"
    except Exception as e:
        print(f"Error encoding D-ID API Key: {e}. Ensure it's in 'email:key' format in .env")
        return {"error": "Invalid D-ID API key format in .env file."}
    # --- End Authorization Header correction ---

    url = f"{D_ID_API_URL}/talks"
    payload = {
        "script": {
            "type": "text",
            "input": script,
            # Optional: Configure voice (see D-ID docs for options)
            # "provider": {
            #     "type": "microsoft",
            #     "voice_id": "en-US-JennyNeural"
            # },
            # "ssml": "false"
        },
        "source_url": image_url,
        # Optional: Configure face enhancement, etc.
        # "config": {
        #     "fluent": "false",
        #     "pad_audio": "0.0"
        # }
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": auth_header # <-- Use the correctly formatted header
    }

    try:
        # --- 1. Create the talk ---
        print(f"Sending request to D-ID: {url}") # Debug print
        # print(f"D-ID Headers: {headers}") # Uncomment carefully for debugging - exposes encoded key
        # print(f"D-ID Payload: {payload}") # Debug print
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f"D-ID Create Response Status: {response.status_code}") # Debug print
        # print(f"D-ID Create Response Body: {response.text}") # Debug print for detailed errors
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        creation_data = response.json()
        talk_id = creation_data.get('id')

        if not talk_id:
             # Try to get more specific error detail from D-ID response
            error_desc = creation_data.get('description', creation_data.get('message', 'Unknown error'))
            kind = creation_data.get('kind', '')
            return {"error": f"D-ID talk creation failed: {kind} - {error_desc}"}

        print(f"D-ID talk created with ID: {talk_id}")

        # --- 2. Poll for completion ---
        status_url = f"{D_ID_API_URL}/talks/{talk_id}"
        start_time = time.time()
        timeout_seconds = 300 # 5 minutes timeout for video generation

        while time.time() - start_time < timeout_seconds:
            # Use the same auth header for polling
            status_response = requests.get(status_url, headers=headers, timeout=15)
            print(f"Polling D-ID Status: {status_response.status_code}") # Debug print
            status_response.raise_for_status()
            status_data = status_response.json()
            status = status_data.get('status')

            print(f"Polling D-ID talk {talk_id}: Status = {status}")

            if status == 'done':
                result_url = status_data.get('result_url')
                if result_url:
                    return {"result_url": result_url}
                else:
                    return {"error": "D-ID talk finished but no result URL found."}
            elif status == 'error':
                error_details = status_data.get('error', status_data.get('result', {}).get('error', 'Unknown D-ID processing error'))
                return {"error": f"D-ID video generation failed: {error_details}"}
            elif status in ['created', 'started']:
                time.sleep(5) # Wait before polling again
            else:
                # Handle unexpected statuses
                return {"error": f"Unexpected D-ID status: {status}"}

        return {"error": "D-ID video generation timed out."}

    except requests.exceptions.HTTPError as e:
        # Handle HTTP errors (like 401, 400, 500) more specifically
        print(f"HTTP Error calling D-ID API: {e.response.status_code} - {e.response.text}")
        error_detail = f"HTTP {e.response.status_code} error"
        try:
            # Try to parse JSON error response from D-ID
            err_json = e.response.json()
            error_detail = err_json.get('description', err_json.get('message', e.response.text))
        except ValueError: # Not JSON
            error_detail = e.response.text
        return {"error": f"D-ID API request failed: {error_detail}"}

    except requests.exceptions.RequestException as e:
        # Handle other network errors (timeout, connection error)
        print(f"Network Error calling D-ID API: {e}")
        return {"error": f"D-ID API request failed: Network error - {e}"}
    except Exception as e:
        print(f"An unexpected error occurred during D-ID processing: {e}")
        return {"error": f"An unexpected error occurred: {e}"}

def download_video(url, save_path):
    """Downloads a video from a URL."""
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading video from {url}: {e}")
        return False
    except Exception as e:
        print(f"Error saving video to {save_path}: {e}")
        return False

# --- Flask Routes ---

@app.route('/', methods=['GET'])
def index():
    """Renders the main form page."""
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate_video_route():
    """Handles form submission, processing, and shows results."""
    product_url = request.form.get('product_url')
    avatar_file = request.files.get('avatar_file')

    if not product_url:
        flash("Product URL is required.", "error")
        return redirect(url_for('index'))

    uploaded_image_path = None
    avatar_source_url = DEFAULT_AVATAR_URL # Start with default

    # --- 1. Handle Avatar Upload ---
    if avatar_file and avatar_file.filename != '':
        try:
            # Secure filename and create unique name
            _, ext = os.path.splitext(avatar_file.filename)
            if ext.lower() not in ['.png', '.jpg', '.jpeg', '.webp']:
                 flash("Invalid image file type. Please use PNG, JPG, JPEG, or WEBP.", "error")
                 return redirect(url_for('index'))

            unique_filename = f"{uuid.uuid4()}{ext}"
            uploaded_image_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            avatar_file.save(uploaded_image_path)

            # IMPORTANT: For D-ID to access this, your Flask app needs to be publicly accessible.
            # During local development, you might need ngrok or similar.
            # Alternatively, upload the image to a public cloud storage (like S3)
            # and use that public URL. For this MVP, we assume local serving works.
            # If D_ID fails with image access errors, this is the likely cause.
            avatar_source_url = url_for('uploaded_file', filename=unique_filename, _external=True)
            print(f"Using uploaded avatar: {avatar_source_url}")

        except Exception as e:
            flash(f"Error saving uploaded file: {e}", "error")
            # Clean up partial upload if it exists
            if uploaded_image_path and os.path.exists(uploaded_image_path):
                os.remove(uploaded_image_path)
            return redirect(url_for('index'))
    else:
         print(f"Using default avatar: {DEFAULT_AVATAR_URL}")


    generated_video_path = None

    try:
        # --- 2. Scrape Product Data ---
        print(f"Scraping product data from: {product_url}")
        scraped_data = scrape_product_data(product_url)
        if not scraped_data:
            flash("Failed to scrape product data from the URL. Please check the link or try another.", "error")
            raise Exception("Scraping failed")

        # Removed storing product_image_url
        # Reverted print statement
        print(f"Scraped data: Title='{scraped_data.get('title')}', Desc length={len(scraped_data.get('description', ''))}")

        # --- 3. Generate Script ---
        print("Generating marketing script...")
        script = generate_marketing_script(scraped_data['title'], scraped_data['description'])
        if script.startswith("Error:"):
            flash(f"Failed to generate script: {script}", "error")
            raise Exception("Script generation failed")

        print(f"Generated script: {script}")

        # --- 4. Generate Video with D-ID ---
        print(f"Requesting video generation from D-ID with avatar: {avatar_source_url}")
        d_id_result = create_d_id_talk(script, avatar_source_url)

        if "error" in d_id_result:
            flash(f"Failed to generate video: {d_id_result['error']}", "error")
            raise Exception("D-ID video generation failed")

        result_url = d_id_result.get("result_url")
        print(f"D-ID video ready at: {result_url}")

        # --- 5. Download Video ---
        print("Downloading generated video...")
        video_filename = f"{uuid.uuid4()}.mp4"
        generated_video_path = os.path.join(app.config['GENERATED_FOLDER'], video_filename)

        if not download_video(result_url, generated_video_path):
            flash("Failed to download the generated video from D-ID.", "error")
            raise Exception("Video download failed")

        print(f"Video saved locally to: {generated_video_path}")

        # --- 6. Render Result ---
        video_url = url_for('static', filename=f'generated/{video_filename}')
        # Removed product_image_url from render_template call
        return render_template('result.html', video_url=video_url)

    except Exception as e:
        # Error already flashed or logged, just redirect back
        print(f"Error during generation process: {e}") # Log the exception
        # Redirect to index, flash message should already be set
        return redirect(url_for('index'))

    finally:
        # --- 7. Cleanup ---
        # Delete the uploaded avatar image if it exists
        if uploaded_image_path and os.path.exists(uploaded_image_path):
            try:
                os.remove(uploaded_image_path)
                print(f"Cleaned up uploaded file: {uploaded_image_path}")
            except OSError as e:
                print(f"Error deleting uploaded file {uploaded_image_path}: {e}")

        # Note: We don't delete the *generated* video here immediately
        # because the result page needs to access it.
        # A more robust solution would involve:
        #   a) A background task/cron job to clean up old generated videos.
        #   b) Storing video info in a temporary session/DB and deleting after download/timeout.
        # For this MVP, manual cleanup or a simple scheduled task is assumed.

# Route to serve uploaded files (needed for D-ID if using local files)
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files from the UPLOAD_FOLDER."""
    # Basic security check (can be enhanced)
    if '..' in filename or filename.startswith('/'):
        return "Invalid filename", 400
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except FileNotFoundError:
        return "File not found", 404


# --- Main Execution ---
if __name__ == '__main__':
    # Make sure API keys are checked before running
    if not OPENAI_API_KEY or not D_ID_API_KEY:
        print("\nERROR: API keys for OpenAI and/or D-ID are missing.")
        print("Please set OPENAI_API_KEY and D_ID_API_KEY in your .env file.\n")
    else:
        # Use host='0.0.0.0' if you need the server to be accessible
        # on your network (e.g., for D-ID callback or testing from other devices).
        # Be aware of security implications.
        app.run(debug=True, host='127.0.0.1', port=5000)
        # For D-ID to access uploaded files served locally, you might need:
        # app.run(debug=True, host='0.0.0.0', port=5000)
        # AND potentially use a tool like ngrok to expose your localhost publicly. 
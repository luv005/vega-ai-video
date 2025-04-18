import os
import time
import uuid
import requests
import base64
from bs4 import BeautifulSoup
from flask import Flask, request, render_template, redirect, url_for, send_from_directory, flash
from openai import OpenAI
from dotenv import load_dotenv
# Ensure these are imported
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
from PIL import Image, ImageOps # Import ImageOps for potential padding/resizing needs
import numpy as np # Import numpy

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
    """Scrapes product title, description, and image URL from a URL."""
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

        # --- Image URL Extraction (Add more selectors as needed) ---
        product_image_url = None
        image_selectors = [
            '#landingImage',                 # Amazon main image (often)
            '#imgBlkFront',                  # Another Amazon image ID
            'meta[property="og:image"]',     # OpenGraph image meta tag (common fallback)
            '.product-image img',            # Generic class selector
            'img[data-main-image="true"]'    # Example data attribute selector
        ]
        for selector in image_selectors:
            img_element = soup.select_one(selector)
            if img_element:
                # For meta tags, get 'content', otherwise 'src'
                src = img_element.get('content') if img_element.name == 'meta' else img_element.get('src')
                if src and src.startswith('http'): # Ensure it's a full URL
                    product_image_url = src
                    break # Found one, stop looking

        # Basic cleanup for image URL (optional, e.g., remove query params if needed)
        # if product_image_url:
        #      product_image_url = product_image_url.split('?')[0] # Simple cleanup example - maybe keep params for logos

        return {
            "title": title or "Product",
            "description": description or "No description found.",
            "image_url": product_image_url # Add the image URL back to the result
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

def create_d_id_talk(script, avatar_image_url):
    """Creates a talking avatar video using D-ID API."""
    if not D_ID_API_KEY:
        return {"error": "D-ID API key not configured."}

    # --- Correctly format the D-ID Authorization Header ---
    try:
        api_key_bytes = D_ID_API_KEY.encode('utf-8')
        api_key_base64 = base64.b64encode(api_key_bytes).decode('utf-8')
        auth_header = f"Basic {api_key_base64}"
    except Exception as e:
        print(f"Error encoding D-ID API Key: {e}. Ensure it's in 'email:key' format in .env")
        return {"error": "Invalid D-ID API key format in .env file."}

    url = f"{D_ID_API_URL}/talks"
    payload = {
        "script": {
            "type": "text",
            "input": script,
        },
        "source_url": avatar_image_url,
    }

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": auth_header
    }

    try:
        # --- 1. Create the talk ---
        print(f"Sending request to D-ID: {url}")
        # print(f"D-ID Payload: {payload}") # Uncomment for debugging payload structure
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f"D-ID Create Response Status: {response.status_code}")
        # print(f"D-ID Create Response Body: {response.text}") # Uncomment for detailed errors
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        creation_data = response.json()
        talk_id = creation_data.get('id')

        if not talk_id:
            error_desc = creation_data.get('description', creation_data.get('message', 'Unknown error'))
            kind = creation_data.get('kind', '')
            return {"error": f"D-ID talk creation failed: {kind} - {error_desc}"}

        print(f"D-ID talk created with ID: {talk_id}")

        # --- 2. Poll for completion ---
        status_url = f"{D_ID_API_URL}/talks/{talk_id}"
        start_time = time.time()
        timeout_seconds = 300 # 5 minutes timeout for video generation

        while time.time() - start_time < timeout_seconds:
            status_response = requests.get(status_url, headers=headers, timeout=15)
            print(f"Polling D-ID Status: {status_response.status_code}")
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
                time.sleep(5)
            else:
                return {"error": f"Unexpected D-ID status: {status}"}

        return {"error": "D-ID video generation timed out."}

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error calling D-ID API: {e.response.status_code} - {e.response.text}")
        error_detail = f"HTTP {e.response.status_code} error"
        try:
            err_json = e.response.json()
            error_detail = err_json.get('description', err_json.get('message', e.response.text))
        except ValueError:
            error_detail = e.response.text
        return {"error": f"D-ID API request failed: {error_detail}"}

    except requests.exceptions.RequestException as e:
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

def download_image(url, save_path):
    """Downloads an image from a URL and validates it."""
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        content_type = response.headers.get('content-type')
        if content_type and not content_type.startswith('image/'):
            print(f"Warning: URL {url} did not return an image content-type ({content_type}). Skipping overlay.")
            return False

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        try:
            img = Image.open(save_path)
            img.verify()
            img.close()
            return True
        except (IOError, SyntaxError, Image.UnidentifiedImageError) as img_err:
            print(f"Downloaded file at {save_path} is not a valid image: {img_err}")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False

    except requests.exceptions.RequestException as e:
        print(f"Error downloading image from {url}: {e}")
        return False
    except Exception as e:
        print(f"Error saving image to {save_path}: {e}")
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
    generated_video_path = None
    product_image_url = None
    downloaded_product_image_path = None # Path for downloaded product image
    final_video_path = None # Path for the composited video
    temp_did_video_path = None # Path for the initial D-ID video download

    try:
        # --- 1. Handle Avatar Upload ---
        if avatar_file and avatar_file.filename != '':
            try:
                _, ext = os.path.splitext(avatar_file.filename)
                if ext.lower() not in ['.png', '.jpg', '.jpeg', '.webp']:
                     flash("Invalid image file type. Please use PNG, JPG, JPEG, or WEBP.", "error")
                     return redirect(url_for('index'))

                unique_filename = f"{uuid.uuid4()}{ext}"
                uploaded_image_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                avatar_file.save(uploaded_image_path)

                # IMPORTANT: Requires public accessibility (e.g., ngrok for local dev)
                avatar_source_url = url_for('uploaded_file', filename=unique_filename, _external=True)
                print(f"Using uploaded avatar: {avatar_source_url}")

            except Exception as e:
                flash(f"Error saving uploaded file: {e}", "error")
                if uploaded_image_path and os.path.exists(uploaded_image_path):
                    os.remove(uploaded_image_path)
                return redirect(url_for('index'))
        else:
             print(f"Using default avatar: {DEFAULT_AVATAR_URL}")

        # --- 2. Scrape Product Data ---
        print(f"Scraping product data from: {product_url}")
        scraped_data = scrape_product_data(product_url)
        if not scraped_data:
            flash("Failed to scrape product data from the URL. Please check the link or try another.", "error")
            raise Exception("Scraping failed")

        product_image_url = scraped_data.get('image_url')
        print(f"Scraped data: Title='{scraped_data.get('title')}', Desc length={len(scraped_data.get('description', ''))}, Image URL: {product_image_url}")

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

        # --- 5. Download D-ID Video ---
        print("Downloading generated D-ID video...")
        temp_video_filename = f"temp_{uuid.uuid4()}.mp4"
        temp_did_video_path = os.path.join(app.config['GENERATED_FOLDER'], temp_video_filename)

        if not download_video(result_url, temp_did_video_path):
            flash("Failed to download the generated video from D-ID.", "error")
            raise Exception("Video download failed")
        print(f"D-ID video saved temporarily to: {temp_did_video_path}")

        # --- 6. Download Product Image (if URL exists) ---
        image_overlay_applied = False
        if product_image_url:
            print(f"Downloading product image from: {product_image_url}")
            img_ext = os.path.splitext(product_image_url)[1]
            if not img_ext or len(img_ext) > 5:
                 img_ext = ".jpg"
            img_filename = f"product_{uuid.uuid4()}{img_ext}"
            downloaded_product_image_path = os.path.join(app.config['GENERATED_FOLDER'], img_filename)

            if download_image(product_image_url, downloaded_product_image_path):
                print(f"Product image saved temporarily to: {downloaded_product_image_path}")
                image_overlay_applied = True
            else:
                print("Failed to download or validate product image. Skipping overlay.")
                if os.path.exists(downloaded_product_image_path):
                    os.remove(downloaded_product_image_path)
                downloaded_product_image_path = None
        else:
            print("No product image URL found in scraped data. Skipping overlay.")

        # --- 7. Composite Video with Image using MoviePy (if image downloaded) ---
        final_video_filename = f"final_{uuid.uuid4()}.mp4"
        final_video_path = os.path.join(app.config['GENERATED_FOLDER'], final_video_filename)

        if image_overlay_applied and downloaded_product_image_path:
            print("Compositing video with product image using MoviePy...")
            try:
                # Load the main video clip first to get dimensions
                video_clip = VideoFileClip(temp_did_video_path)
                vid_w, vid_h = video_clip.w, video_clip.h

                # Load the product image using Pillow
                product_img = Image.open(downloaded_product_image_path).convert("RGBA") # Ensure RGBA for transparency

                # --- Resize image using Pillow directly ---
                target_width = vid_w * 0.25 # Target 25% of video width (Increased from 0.15)
                # Calculate target height maintaining aspect ratio
                img_w, img_h = product_img.size
                aspect_ratio = img_h / img_w
                target_height = int(target_width * aspect_ratio)
                target_size = (int(target_width), target_height)

                # Use the new resampling filter with Pillow 10+
                print(f"Resizing product image to {target_size} using LANCZOS filter.")
                resized_img = product_img.resize(target_size, Image.Resampling.LANCZOS)
                product_img.close() # Close original image

                # --- Create MoviePy ImageClip from the resized Pillow image (as NumPy array) ---
                img_clip = ImageClip(np.array(resized_img))

                # --- Set position and duration ---
                padding = 10 # 10px padding
                # Use the resized image dimensions
                pos_x = vid_w - target_size[0] - padding
                pos_y = vid_h - target_size[1] - padding
                img_clip = img_clip.set_position((pos_x, pos_y)) # Bottom-right

                img_clip = img_clip.set_duration(video_clip.duration)

                # --- Composite and Write ---
                final_clip = CompositeVideoClip([video_clip, img_clip], size=video_clip.size)

                print(f"Writing final video to: {final_video_path}")
                final_clip.write_videofile(final_video_path,
                                           codec='libx264',
                                           audio_codec='aac',
                                           temp_audiofile='temp-audio.m4a',
                                           remove_temp=True,
                                           preset='medium',
                                           ffmpeg_params=["-profile:v","baseline", "-level","3.0", "-pix_fmt", "yuv420p"]
                                          )
                # --- Close clips ---
                resized_img.close()
                img_clip.close()
                video_clip.close()
                final_clip.close()
                print(f"Final composited video saved successfully.")

            except Exception as moviepy_err:
                print(f"Error during MoviePy processing: {moviepy_err}")
                flash("Error occurred while adding product image overlay.", "error")
                final_video_path = temp_did_video_path
                final_video_filename = temp_video_filename
                if 'video_clip' in locals() and video_clip: video_clip.close()
                if 'img_clip' in locals() and img_clip: img_clip.close()
                if 'final_clip' in locals() and final_clip: final_clip.close()
                if 'product_img' in locals() and product_img: product_img.close()
                if 'resized_img' in locals() and resized_img: resized_img.close()

        else:
            # No image overlay applied, the final video is just the D-ID video
            print("Skipping compositing, using original D-ID video.")
            final_video_path = temp_did_video_path
            final_video_filename = temp_video_filename

        # --- 8. Render Result ---
        video_url = url_for('static', filename=f'generated/{final_video_filename}')
        return render_template('result.html', video_url=video_url)

    except Exception as e:
        print(f"Error during generation process: {e}")
        # Redirect to index, flash message should already be set by specific error handlers
        return redirect(url_for('index'))

    finally:
        # --- Cleanup ---
        # Delete the originally uploaded avatar file (if any)
        if uploaded_image_path and os.path.exists(uploaded_image_path):
            try:
                os.remove(uploaded_image_path)
                print(f"Cleaned up uploaded avatar: {uploaded_image_path}")
            except Exception as e:
                print(f"Error cleaning up uploaded avatar {uploaded_image_path}: {e}")

        # Delete the downloaded product image (if any)
        if downloaded_product_image_path and os.path.exists(downloaded_product_image_path):
            try:
                os.remove(downloaded_product_image_path)
                print(f"Cleaned up downloaded product image: {downloaded_product_image_path}")
            except Exception as e:
                print(f"Error cleaning up product image {downloaded_product_image_path}: {e}")

        # Delete the temporary D-ID video (if compositing was successful and created a new file)
        if temp_did_video_path and final_video_path != temp_did_video_path and os.path.exists(temp_did_video_path):
             try:
                 os.remove(temp_did_video_path)
                 print(f"Cleaned up temporary D-ID video: {temp_did_video_path}")
             except Exception as e:
                 print(f"Error cleaning up temporary D-ID video {temp_did_video_path}: {e}")
        # Note: We don't delete the 'final_video_path' as it's being served.
        # Consider adding a mechanism to clean up old generated videos later if needed.


# Route to serve uploaded files (needed for D-ID to access uploaded avatars)
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files statically."""
    # Add security check: ensure filename is safe, doesn't traverse directories
    # For simplicity here, we assume Flask's send_from_directory handles basic safety.
    # In production, add more robust checks (e.g., using werkzeug.utils.secure_filename again)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    # Make sure to run with debug=False in production
    # Consider using a production server like Gunicorn or Waitress
    # For local testing with D-ID needing external access, you might need ngrok:
    # 1. Install ngrok: https://ngrok.com/download
    # 2. Run: ngrok http 5000 (or your Flask port)
    # 3. Use the https://<your-ngrok-id>.ngrok.io URL provided by ngrok
    #    when D-ID needs to fetch the uploaded avatar via the /uploads/ route.
    app.run(debug=True, host='0.0.0.0', port=5000)
    # AND potentially use a tool like ngrok to expose your localhost publicly. 
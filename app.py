import os
import time
import uuid
import requests
import base64
import textwrap # Import textwrap for description formatting
from bs4 import BeautifulSoup
from flask import Flask, request, render_template, redirect, url_for, send_from_directory, flash
from openai import OpenAI
from dotenv import load_dotenv
# Ensure these are imported
from moviepy.editor import (VideoFileClip, ImageClip, CompositeVideoClip, ColorClip,
                            TextClip, concatenate_videoclips, AudioFileClip) # Added ColorClip, TextClip, concatenate, AudioFileClip
from moviepy.video.fx.all import fadein, fadeout # Import fade effects
from PIL import Image, ImageOps, ImageFont, ImageDraw # Import more from Pillow
import numpy as np
import math # For ceiling function
import platform # Import platform module

# --- Configuration ---
# load_dotenv()  # Load environment variables from .env file
dotenv_loaded = load_dotenv() # Load and check if it found a file

# --- Tell MoviePy to use 'magick' binary for ImageMagick 7+ ---
# This is often needed on systems where 'convert' is deprecated
try:
    if platform.system() != "Windows": # Check if not Windows
        # On macOS/Linux with IMv7+, 'magick' is the command.
        # Set the environment variable *for this script's execution context*.
        print("Attempting to configure MoviePy for ImageMagick 7+ ('magick' command)...")
        os.environ["IMAGEMAGICK_BINARY"] = "magick"
        # You might need to verify 'magick' is actually in your system's PATH
        # If it still fails, you might need the full path, e.g., "/usr/local/bin/magick" or "/opt/homebrew/bin/magick"
        # Example: os.environ["IMAGEMAGICK_BINARY"] = "/opt/homebrew/bin/magick"
    else:
        # For Windows, MoviePy often needs the full path to magick.exe
        # Example: os.environ["IMAGEMAGICK_BINARY"] = r"C:\Program Files\ImageMagick-7.1.1-Q16-HDRI\magick.exe"
        # This path needs to be adjusted based on the actual installation location.
        # We'll print a reminder for Windows users.
        print("Detected Windows. Ensure ImageMagick is installed and MoviePy config points to magick.exe if needed.")
except Exception as e:
    print(f"Warning: Could not set IMAGEMAGICK_BINARY environment variable: {e}")
# --- End ImageMagick Configuration ---

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24) # Needed for flashing messages
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['GENERATED_FOLDER'] = os.path.join('static', 'generated')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max upload size 16MB
app.config['FONT_PATH'] = 'fonts/Poppins-Bold.ttf' # CHANGE TO YOUR POPPINS BOLD FILENAME
MAX_PRODUCT_IMAGES = 5 # Limit the number of images to process

# Ensure upload and generated directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)

# Ensure font directory exists if using a bundled font
font_dir = os.path.dirname(app.config['FONT_PATH'])
if font_dir and not os.path.exists(font_dir):
    os.makedirs(font_dir)
    print(f"Created font directory: {font_dir}")
    print(f"Please place a font file (e.g., DejaVuSans-Bold.ttf) at {app.config['FONT_PATH']}")

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
    """Scrapes product title, description, and MULTIPLE image URLs from a URL."""
    print(f"Attempting to scrape: {url}")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        }
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # --- Title Extraction ---
        title = None
        title_selectors = ['#productTitle', 'h1.product-title', 'h1[itemprop="name"]', 'h1']
        for selector in title_selectors:
            el = soup.select_one(selector)
            if el: title = el.get_text(strip=True); break
        if not title: title_tag = soup.find('title'); title = title_tag.get_text(strip=True) if title_tag else "Product"

        # --- Description Extraction ---
        description = None
        desc_selectors = ['#feature-bullets .a-list-item', '#productDescription', 'meta[name="description"]', '.product-description']
        desc_parts = []
        # Try feature bullets first
        bullet_elements = soup.select('#feature-bullets .a-list-item')
        if bullet_elements:
            for item in bullet_elements: desc_parts.append(item.get_text(strip=True))
            description = ". ".join(filter(None, desc_parts)) + "."
        else: # Try other selectors
            for selector in desc_selectors[1:]:
                el = soup.select_one(selector)
                if el:
                    if el.name == 'meta': description = el.get('content', '').strip()
                    else: description = el.get_text(strip=True)
                    if description: break
        if description: description = ' '.join(description.split())
        else: description = "No description found."

        # --- MULTI-Image URL Extraction ---
        image_urls = []
        seen_urls = set()

        # Prioritize common main/large image selectors first
        main_image_selectors = [
            '#landingImage', '#imgBlkFront', '#main-image-container img',
            '.product-image-gallery img', 'meta[property="og:image"]'
        ]
        for selector in main_image_selectors:
            elements = soup.select(selector)
            for img_element in elements:
                src = img_element.get('content') if img_element.name == 'meta' else img_element.get('src')
                if src and src.startswith('http') and src not in seen_urls:
                    # Basic check to avoid tiny icons/spacers if possible
                    if 'base64' not in src and 'spacer' not in src.lower() and 'icon' not in src.lower():
                         # Attempt to get higher resolution version (common Amazon pattern)
                         src = src.replace('_AC_US40_', '_AC_SL1500_').replace('_SX342_', '_SL1500_').replace('_SX466_', '_SL1500_')
                         image_urls.append(src)
                         seen_urls.add(src)
                         if len(image_urls) >= MAX_PRODUCT_IMAGES: break
            if len(image_urls) >= MAX_PRODUCT_IMAGES: break

        # Then look for thumbnails (often in lists or specific divs)
        if len(image_urls) < MAX_PRODUCT_IMAGES:
            thumb_selectors = [
                '#altImages img', '.imageThumbnail img', '.product-thumbnails img',
                'li.thumb img', 'div[data-thumbnail-url] img'
            ]
            for selector in thumb_selectors:
                elements = soup.select(selector)
                for img_element in elements:
                    src = img_element.get('src')
                    if src and src.startswith('http') and src not in seen_urls:
                         if 'base64' not in src and 'spacer' not in src.lower() and 'icon' not in src.lower():
                            # Attempt to get higher resolution version
                            src = src.replace('_AC_US40_', '_AC_SL1500_').replace('_SX342_', '_SL1500_').replace('_SX466_', '_SL1500_')
                            image_urls.append(src)
                            seen_urls.add(src)
                            if len(image_urls) >= MAX_PRODUCT_IMAGES: break
                if len(image_urls) >= MAX_PRODUCT_IMAGES: break

        print(f"Scraped Title: {title}")
        print(f"Scraped Description Length: {len(description)}")
        print(f"Found {len(image_urls)} product images.")

        return {
            "title": title,
            "description": description,
            "image_urls": image_urls # Return a list of URLs
        }

    except requests.exceptions.RequestException as e:
        print(f"Error scraping URL {url}: {e}")
        return None
    except Exception as e:
        print(f"Error parsing HTML from {url}: {e}")
        return None

def generate_marketing_script(title, description):
    """Generates a marketing script suitable for narration using OpenAI."""
    if not openai_client: return "Error: OpenAI client not initialized."

    prompt = f"""
    Create a short, engaging promotional voiceover script (around 4-6 sentences, total ~15-25 seconds reading time)
    for a product based on the following information. Write it as natural spoken language.
    Focus on key benefits or features. Start with an engaging hook and end with a call to action or summary statement.

    Product Title: {title}
    Product Description/Features: {description}

    Voiceover Script:
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o", # Or "gpt-3.5-turbo"
            messages=[
                {"role": "system", "content": "You are a helpful assistant creating concise and engaging voiceover scripts for product videos."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150, # Increased slightly for slightly longer script
            temperature=0.7,
            n=1,
            stop=None,
        )
        script = response.choices[0].message.content.strip()
        # Basic cleanup - remove potential quotation marks around the whole script
        if script.startswith('"') and script.endswith('"'):
            script = script[1:-1]
        print(f"Generated Script for TTS: {script}")
        return script
    except Exception as e:
        print(f"Error calling OpenAI API for script: {e}")
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

# --- NEW Helper: Generate Voiceover ---
def generate_voiceover(text, output_path):
    """Generates an MP3 voiceover file from text using OpenAI TTS."""
    if not openai_client:
        print("Error: Cannot generate voiceover, OpenAI client not initialized.")
        return False
    try:
        # Ensure the output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        print(f"Requesting TTS from OpenAI for text: '{text[:50]}...'")
        response = openai_client.audio.speech.create(
            model="tts-1",      # or "tts-1-hd"
            voice="alloy",    # Other voices: echo, fable, onyx, nova, shimmer
            input=text,
            response_format="mp3" # Specify mp3 format
        )
        print("TTS response received, writing to file.")
        # Stream the response content directly to the file
        response.stream_to_file(output_path)
        print(f"Voiceover saved successfully to: {output_path}")
        return True
    except Exception as e:
        print(f"Error calling OpenAI TTS API: {e}")
        return False

# --- NEW Helper: Get Word Timestamps using Whisper API ---
def get_word_timestamps(audio_path):
    """Transcribes audio using OpenAI Whisper API and returns word timestamps."""
    if not openai_client:
        print("Error: OpenAI client not initialized. Cannot get timestamps.")
        return None

    print(f"Getting word timestamps for: {audio_path}")
    try:
        with open(audio_path, "rb") as audio_file:
            # Use the Whisper API for transcription with word timestamps
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json", # Request detailed JSON output
                timestamp_granularities=["word"] # Request word-level timestamps
            )
        # The response object structure might vary slightly based on library version,
        # but generally, word timestamps are in transcript.words
        if hasattr(transcript, 'words') and transcript.words:
             print(f"Successfully retrieved {len(transcript.words)} word timestamps.")
             return transcript.words
        else:
             print("Warning: Whisper transcription did not return word timestamps.")
             # Fallback: return segment timestamps if available? Or just None.
             # For simplicity, return None if word timestamps are missing.
             return None

    except Exception as e:
        print(f"Error calling OpenAI Whisper API for timestamps: {e}")
        return None

# --- MODIFIED Helper: Generate Slideshow Video with Rounded Captions and Clearer Images ---
def generate_slideshow_video(image_paths, audio_path, output_path, font_path):
    """Generates slideshow with adaptive frame size, rounded captions, and clearer product images."""
    print("Starting slideshow video generation with improved image quality...")
    
    # Default dimensions (will be adjusted based on images)
    DEFAULT_W, DEFAULT_H = 1920, 1080  # 16:9 default
    PORTRAIT_W, PORTRAIT_H = 1080, 1920  # 9:16 portrait
    SQUARE_W, SQUARE_H = 1440, 1440  # 1:1 square
    
    output_fps = 30
    fade_duration = 0.5
    
    # Caption settings
    caption_font_size = 55
    caption_color = 'white'
    caption_bg_color = 'black' # Solid black background
    caption_padding = 20 # Pixels around text for the background
    caption_corner_radius = 25 # Pixels for rounded corners
    caption_max_words_per_segment = 4  # Show 3-4 words at a time
    caption_max_duration_per_segment = 2.5  # Max seconds per caption segment
    
    # Position captions near bottom with space below
    caption_bottom_margin = 50  # Pixels from bottom of frame
    
    # Image quality settings
    image_quality = Image.Resampling.LANCZOS  # Highest quality resampling
    
    clips = []
    audio_clip = None
    final_video_sequence = None
    word_timestamps = None
    text_clips_list = []

    try:
        # 1. Analyze images to determine optimal frame size
        print("Analyzing images to determine optimal frame size...")
        aspect_ratios = []
        
        for img_path in image_paths:
            try:
                with Image.open(img_path) as img:
                    w, h = img.size
                    aspect_ratios.append(w / h)
            except Exception as e:
                print(f"Warning: Could not analyze image {img_path}: {e}")
        
        if not aspect_ratios:
            print("No valid images to analyze. Using default 16:9 ratio.")
            W, H = DEFAULT_W, DEFAULT_H
        else:
            # Calculate average aspect ratio
            avg_aspect = sum(aspect_ratios) / len(aspect_ratios)
            print(f"Average image aspect ratio: {avg_aspect:.2f}")
            
            # Determine frame type based on average aspect ratio
            if avg_aspect < 0.8:  # Portrait-oriented images (tall)
                W, H = PORTRAIT_W, PORTRAIT_H
                print("Using portrait 9:16 frame (1080x1920)")
            elif avg_aspect > 1.2:  # Landscape-oriented images (wide)
                W, H = DEFAULT_W, DEFAULT_H
                print("Using landscape 16:9 frame (1920x1080)")
            else:  # Near-square images
                W, H = SQUARE_W, SQUARE_H
                print("Using square 1:1 frame (1440x1440)")
        
        target_aspect = W / H
        
        # 2. Load Audio & Get Timestamps
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration
        word_timestamps = get_word_timestamps(audio_path)
        if word_timestamps is None:
            print("Warning: Could not get word timestamps. Proceeding without captions.")

        if not image_paths: print("Error: No valid image paths provided."); return False
        num_images = len(image_paths)
        slide_duration = total_duration / num_images
        print(f"Audio duration: {total_duration:.2f}s, {num_images} images, slide duration: {slide_duration:.2f}s")

        # 3. Process images and create base video clips (Fill Frame Logic with improved quality)
        for i, img_path in enumerate(image_paths):
            print(f"Processing image {i+1}/{num_images}: {os.path.basename(img_path)}")
            try:
                # Load image with Pillow
                img_pil = Image.open(img_path).convert("RGB")
                img_w, img_h = img_pil.size
                print(f"  Original size: {img_w}x{img_h}")
                img_aspect = img_w / img_h
                
                # --- IMPROVED Image Processing Logic ---
                if img_aspect > target_aspect:
                    # Image is wider than target: Resize based on height, crop width
                    print("  -> Image is wider. Resizing height and cropping width.")
                    new_h = H
                    new_w = int(new_h * img_aspect)
                    
                    # Use high quality resizing
                    resized_img = img_pil.resize((new_w, new_h), image_quality)
                    
                    # Calculate crop to center the image horizontally
                    crop_x = (new_w - W) / 2
                    crop_box = (crop_x, 0, new_w - crop_x, new_h)
                    final_img = resized_img.crop(crop_box)
                    resized_img.close()
                else:
                    # Image is taller than or equal aspect to target: Resize based on width, crop height
                    print("  -> Image is taller or same aspect. Resizing width and cropping height.")
                    new_w = W
                    new_h = int(new_w / img_aspect)
                    
                    # Use high quality resizing
                    resized_img = img_pil.resize((new_w, new_h), image_quality)
                    
                    # Calculate crop to center the image vertically
                    crop_y = (new_h - H) / 2
                    crop_box = (0, crop_y, new_w, new_h - crop_y)
                    final_img = resized_img.crop(crop_box)
                    resized_img.close()

                img_pil.close()

                # Ensure final image is exactly WxH
                if final_img.size != (W, H):
                     print(f"  -> Final size was {final_img.size}, resizing to {W}x{H}")
                     final_img = final_img.resize((W, H), image_quality)

                print(f"  -> Final frame size for clip: {final_img.size}")

                # Create MoviePy ImageClip with the processed image
                img_clip = ImageClip(np.array(final_img)).set_duration(slide_duration)
                clips.append(img_clip)
                final_img.close()

            except Exception as img_proc_err:
                print(f"Warning: Failed to process image {img_path}: {img_proc_err}. Skipping.")
                if 'img_pil' in locals() and img_pil: img_pil.close()
                if 'resized_img' in locals() and resized_img: resized_img.close()
                if 'final_img' in locals() and final_img: final_img.close()
                continue

        if not clips: print("Error: No image clips created."); return False

        # 4. Concatenate base video clips
        video_sequence = concatenate_videoclips(clips, method="compose")

        # 5. Group Word Timestamps into Small Segments (3-4 words each)
        caption_segments = []
        if word_timestamps:
            print("Grouping word timestamps into small caption segments...")
            current_segment_words = []
            segment_start_time = -1
            segment_end_time = -1

            for i, word_info in enumerate(word_timestamps):
                word_text = word_info.word.strip()
                start = word_info.start
                end = word_info.end

                if not current_segment_words:
                    # Start of a new segment
                    current_segment_words.append(word_text)
                    segment_start_time = start
                    segment_end_time = end
                else:
                    # Check if adding this word exceeds limits
                    time_diff = end - segment_start_time
                    word_count = len(current_segment_words) + 1

                    if time_diff < caption_max_duration_per_segment and word_count <= caption_max_words_per_segment:
                        # Add word to current segment
                        current_segment_words.append(word_text)
                        segment_end_time = end # Update end time
                    else:
                        # Finalize the previous segment
                        segment_text = " ".join(current_segment_words)
                        caption_segments.append({
                            "text": segment_text,
                            "start": segment_start_time,
                            "end": segment_end_time
                        })
                        # Start a new segment with the current word
                        current_segment_words = [word_text]
                        segment_start_time = start
                        segment_end_time = end

                # Add the last segment if loop finishes
                if i == len(word_timestamps) - 1 and current_segment_words:
                    segment_text = " ".join(current_segment_words)
                    caption_segments.append({
                        "text": segment_text,
                        "start": segment_start_time,
                        "end": segment_end_time
                    })
            print(f"Grouped into {len(caption_segments)} caption segments.")

        # 6. Create Timed Composite Caption Clips (Text + Rounded Background)
        if caption_segments:
            print("Creating timed composite caption clips...")
            if not os.path.exists(font_path):
                 print(f"Warning: Caption font file not found at '{font_path}'. Using default."); font_param = 'Arial-Bold'
            else: font_param = font_path

            for segment in caption_segments:
                text = segment["text"]
                start_time = segment["start"]
                end_time = segment["end"]
                duration = end_time - start_time

                if duration <= 0: continue

                # --- Create Text Clip (without background first) to get size ---
                temp_text_clip = TextClip(text, fontsize=caption_font_size, color=caption_color,
                                           font=font_param, method='label') # Use label to get natural size
                txt_w, txt_h = temp_text_clip.size
                temp_text_clip.close() # Close temporary clip

                # --- Calculate background size with padding ---
                bg_w = txt_w + 2 * caption_padding
                bg_h = txt_h + 2 * caption_padding

                # --- Create Rounded Background using Pillow ---
                try:
                    # Create a transparent image for the background
                    bg_image = Image.new('RGBA', (bg_w, bg_h), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(bg_image)
                    # Draw the rounded rectangle with solid black fill
                    draw.rounded_rectangle(
                        [(0, 0), (bg_w, bg_h)],
                        radius=caption_corner_radius,
                        fill=caption_bg_color # Use solid black
                    )
                    # Convert Pillow image to MoviePy ImageClip
                    bg_clip = ImageClip(np.array(bg_image), ismask=False, transparent=True)
                    bg_clip = bg_clip.set_opacity(1.0) # Ensure it's fully opaque

                except Exception as pil_err:
                    print(f"Warning: Failed to create rounded background image: {pil_err}. Using simple TextClip.")
                    # Fallback: Create simple text clip if Pillow fails
                    txt_clip = TextClip(text, fontsize=caption_font_size, color=caption_color,
                                         font=font_param, bg_color=caption_bg_color, # Use simple bg color
                                          method='caption', align='center', size=(W*0.8, None))
                    
                    # Calculate position with bottom margin
                    y_pos = H - caption_bottom_margin - txt_clip.h
                    txt_clip = txt_clip.set_position(('center', y_pos))
                    txt_clip = txt_clip.set_start(start_time).set_duration(duration)
                    text_clips_list.append(txt_clip)
                    continue # Skip the compositing part for this segment

                # --- Create Final Text Clip (potentially with wrapping if needed) ---
                text_clip_final = TextClip(text, fontsize=caption_font_size, color=caption_color,
                                           font=font_param, method='label', align='center')

                # --- Composite Text onto Rounded Background ---
                caption_clip = CompositeVideoClip([
                    bg_clip.set_position('center'),
                    text_clip_final.set_position('center')
                ], size=(bg_w, bg_h))

                # Set timing and position for the composite caption
                # Calculate position with bottom margin
                y_pos = H - caption_bottom_margin - caption_clip.h
                caption_clip = caption_clip.set_position(('center', y_pos))
                caption_clip = caption_clip.set_start(start_time).set_duration(duration)

                text_clips_list.append(caption_clip)

                # Close intermediate clips
                bg_clip.close()
                text_clip_final.close()

            print(f"Created {len(text_clips_list)} composite caption clips.")

        # 7. Composite Base Video + Caption Clips + Audio
        print("Compositing final video...")
        final_composite_elements = [video_sequence]
        if text_clips_list:
            final_composite_elements.extend(text_clips_list)

        final_video_sequence = CompositeVideoClip(final_composite_elements, size=(W, H))
        final_video_sequence = final_video_sequence.set_duration(total_duration)
        final_video_sequence = final_video_sequence.set_audio(audio_clip)

        # 8. Add Overall Fade In/Out
        final_video_sequence = final_video_sequence.fadein(fade_duration).fadeout(fade_duration)

        # 9. Write Video File
        print(f"Writing final slideshow video with dimensions {W}x{H} to: {output_path}")
        final_video_sequence.write_videofile(
            output_path, fps=output_fps, codec='libx264', audio_codec='aac',
            temp_audiofile='temp-slideshow-audio.m4a', remove_temp=True, preset='medium',
            ffmpeg_params=["-profile:v","baseline", "-level","3.0", "-pix_fmt", "yuv420p"], threads=4,
            logger='bar'
        )

        print("Slideshow video generation successful.")
        return True

    except Exception as e:
        print(f"Error during slideshow video generation: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 10. Close all clips
        if audio_clip: audio_clip.close()
        if final_video_sequence: final_video_sequence.close()
        if 'video_sequence' in locals() and video_sequence: video_sequence.close()
        for clip in clips:
            if clip: clip.close()
        for clip in text_clips_list:
             if clip: clip.close()

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
    video_type = request.form.get('video_type', 'product')

    if not product_url:
        flash("Product URL is required.", "error")
        return redirect(url_for('index'))

    # --- Common Setup ---
    uploaded_image_path = None
    avatar_source_url = DEFAULT_AVATAR_URL
    product_image_urls = [] # Now a list
    downloaded_image_paths = [] # List of paths for downloaded images
    final_video_path = None
    final_video_filename = None
    generated_audio_path = None # Path for TTS audio

    try:
        # --- Scrape Product Data ---
        print(f"Scraping product data from: {product_url}")
        scraped_data = scrape_product_data(product_url)
        if not scraped_data:
            flash("Failed to scrape product data. Check URL or website structure.", "error")
            raise Exception("Scraping failed")

        product_image_urls = scraped_data.get('image_urls', [])
        product_title = scraped_data.get('title', 'Product')
        product_description = scraped_data.get('description', 'No description found.')
        print(f"Scraped: Title='{product_title}', Desc length={len(product_description)}, Images Found={len(product_image_urls)}")

        # ===========================================
        # --- Branch Logic based on Video Type ---
        # ===========================================

        if video_type == 'avatar':
            # --- AVATAR VIDEO LOGIC (Mostly unchanged, but uses first scraped image for overlay) ---
            print("--- Generating Avatar Video ---")
            temp_did_video_path = None
            downloaded_overlay_image_path = None # Specific path for overlay image

            # --- 1a. Handle Avatar Upload (Only for Avatar Type) ---
            if avatar_file and avatar_file.filename != '':
                try:
                    _, ext = os.path.splitext(avatar_file.filename)
                    if ext.lower() not in ['.png', '.jpg', '.jpeg', '.webp']:
                         flash("Invalid image file type for avatar. Please use PNG, JPG, JPEG, or WEBP.", "error")
                         return redirect(url_for('index'))
                    unique_filename = f"avatar_{uuid.uuid4()}{ext}"
                    uploaded_image_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    avatar_file.save(uploaded_image_path)
                    avatar_source_url = url_for('uploaded_file', filename=unique_filename, _external=True)
                    print(f"Using uploaded avatar: {avatar_source_url}")
                except Exception as e:
                    flash(f"Error saving uploaded avatar file: {e}", "error")
                    if uploaded_image_path and os.path.exists(uploaded_image_path): os.remove(uploaded_image_path)
                    return redirect(url_for('index'))
            else:
                 print(f"Using default avatar: {DEFAULT_AVATAR_URL}")

            # --- 3a. Generate Script (Only for Avatar Type) ---
            print("Generating marketing script...")
            script = generate_marketing_script(product_title, product_description)
            if script.startswith("Error:"):
                flash(f"Failed to generate script: {script}", "error")
                raise Exception("Script generation failed")
            print(f"Generated script: {script}")

            # --- 4a. Generate Video with D-ID ---
            print(f"Requesting video generation from D-ID with avatar: {avatar_source_url}")
            d_id_result = create_d_id_talk(script, avatar_source_url)
            if "error" in d_id_result:
                 flash(f"Failed to generate video: {d_id_result['error']}", "error")
                 raise Exception("D-ID video generation failed")
            result_url = d_id_result.get("result_url")
            print(f"D-ID video ready at: {result_url}")

            # --- 5a. Download D-ID Video ---
            print("Downloading generated D-ID video...")
            temp_video_filename = f"temp_{uuid.uuid4()}.mp4"
            temp_did_video_path = os.path.join(app.config['GENERATED_FOLDER'], temp_video_filename)
            if not download_video(result_url, temp_did_video_path):
                flash("Failed to download the generated video from D-ID.", "error")
                raise Exception("Video download failed")
            print(f"D-ID video saved temporarily to: {temp_did_video_path}")

            # --- 6a. Download Product Image (if URL exists for overlay) ---
            image_overlay_applied = False
            if product_image_urls:
                first_image_url = product_image_urls[0]
                print(f"Downloading first product image for overlay: {first_image_url}")
                img_ext = os.path.splitext(first_image_url)[1] or ".jpg"
                img_filename = f"product_overlay_{uuid.uuid4()}{img_ext}"
                downloaded_overlay_image_path = os.path.join(app.config['GENERATED_FOLDER'], img_filename)
                if download_image(first_image_url, downloaded_overlay_image_path):
                    print(f"Overlay image saved to: {downloaded_overlay_image_path}")
                    image_overlay_applied = True
                else:
                    print("Failed to download overlay image."); downloaded_overlay_image_path = None # Ensure path is None
            else:
                print("No product images found for overlay.")

            # --- 7a. Composite D-ID Video with Image using MoviePy (if applicable) ---
            final_video_filename = f"final_avatar_{uuid.uuid4()}.mp4"
            final_video_path = os.path.join(app.config['GENERATED_FOLDER'], final_video_filename)

            if image_overlay_applied and downloaded_overlay_image_path:
                # (Existing MoviePy overlay logic - slightly adapted variable names)
                print("Compositing D-ID video with product image overlay...")
                try:
                    video_clip = VideoFileClip(temp_did_video_path)
                    img_clip = ImageClip(downloaded_overlay_image_path).resize(width=video_clip.w * 0.25).set_position(('right','bottom')).set_duration(video_clip.duration)
                    final_clip = CompositeVideoClip([video_clip, img_clip], size=video_clip.size)
                    final_clip.write_videofile(final_video_path, codec='libx264', audio_codec='aac', temp_audiofile='temp-avatar-audio.m4a', remove_temp=True, preset='medium', ffmpeg_params=["-profile:v","baseline", "-level","3.0", "-pix_fmt", "yuv420p"])
                    img_clip.close(); video_clip.close(); final_clip.close(); print("Overlay successful.")
                except Exception as e: print(f"Overlay failed: {e}"); final_video_path = temp_did_video_path; final_video_filename = temp_video_filename # Fallback
            else:
                print("Skipping overlay compositing for avatar video.")
                final_video_path = temp_did_video_path
                final_video_filename = temp_video_filename

        elif video_type == 'product':
            # --- PRODUCT SLIDESHOW LOGIC ---
            print("--- Generating Product Slideshow Video ---")

            # 1b. Check for Product Images
            if not product_image_urls:
                flash("Could not find any product images on the provided page. Cannot generate slideshow.", "error")
                raise Exception("Missing product images for slideshow.")
            print(f"Found {len(product_image_urls)} images for slideshow.")

            # 2b. Download ALL Product Images
            print("Downloading product images...")
            for i, img_url in enumerate(product_image_urls):
                img_ext = os.path.splitext(img_url)[1] or ".jpg"
                img_filename = f"product_source_{i}_{uuid.uuid4()}{img_ext}"
                img_path = os.path.join(app.config['GENERATED_FOLDER'], img_filename)
                if download_image(img_url, img_path):
                    downloaded_image_paths.append(img_path)
                else:
                    print(f"Warning: Failed to download image {i+1}: {img_url}")
            if not downloaded_image_paths:
                flash("Failed to download any valid product images.", "error")
                raise Exception("Image downloads failed.")
            print(f"Successfully downloaded {len(downloaded_image_paths)} images.")

            # 3b. Generate Script for Narration
            print("Generating script for voiceover...")
            script = generate_marketing_script(product_title, product_description)
            if script.startswith("Error:"):
                flash(f"Failed to generate script: {script}", "error")
                raise Exception("Script generation failed")

            # 4b. Generate Voiceover Audio
            print("Generating voiceover...")
            audio_filename = f"voiceover_{uuid.uuid4()}.mp3"
            generated_audio_path = os.path.join(app.config['GENERATED_FOLDER'], audio_filename)
            if not generate_voiceover(script, generated_audio_path):
                flash("Failed to generate voiceover audio.", "error")
                raise Exception("TTS generation failed")

            # 5b. Generate Slideshow Video using Helper (Pass font path)
            final_video_filename = f"final_slideshow_{uuid.uuid4()}.mp4"
            final_video_path = os.path.join(app.config['GENERATED_FOLDER'], final_video_filename)

            if not generate_slideshow_video(downloaded_image_paths, generated_audio_path, final_video_path, app.config['FONT_PATH']):
                flash("Failed to generate the final slideshow video.", "error")
                raise Exception("Slideshow video generation failed.")

        else:
            flash("Invalid video type selected.", "error")
            raise Exception("Invalid video type")

        # ===========================================
        # --- Common Rendering Logic ---
        # ===========================================
        if final_video_path and final_video_filename:
            video_url = url_for('static', filename=f'generated/{final_video_filename}')
            print(f"Rendering result page with video: {video_url}")
            return render_template('result.html', video_url=video_url)
        else:
            # This case should ideally not be reached if exceptions are raised correctly
            flash("An unknown error occurred, failed to determine final video.", "error")
            return redirect(url_for('index'))

    except Exception as e:
        print(f"Error during generation process: {e}")
        if "OPENAI_API_KEY" in str(e) or "authentication" in str(e).lower() or "Incorrect API key" in str(e):
             flash("OpenAI API Key error. Please check your .env file and ensure the key is valid and has funds/credits.", "error")
        elif "quota" in str(e).lower():
             flash("OpenAI API request failed: You might have exceeded your usage quota.", "error")
        # Keep the general flash message setting within specific failure points if possible
        return redirect(url_for('index'))

    finally:
        # --- Cleanup ---
        # Delete uploaded avatar (if any)
        if uploaded_image_path and os.path.exists(uploaded_image_path):
            try: os.remove(uploaded_image_path); print(f"Cleaned up uploaded avatar: {uploaded_image_path}")
            except Exception as e: print(f"Error cleaning up avatar {uploaded_image_path}: {e}")

        # Delete downloaded product images (overlay or slideshow sources)
        # Includes overlay image from avatar path now
        if 'downloaded_overlay_image_path' in locals() and downloaded_overlay_image_path and os.path.exists(downloaded_overlay_image_path):
             try: os.remove(downloaded_overlay_image_path); print(f"Cleaned up: {downloaded_overlay_image_path}")
             except Exception as e: print(f"Error cleaning up {downloaded_overlay_image_path}: {e}")
        for img_path in downloaded_image_paths: # For slideshow images
            if os.path.exists(img_path):
                try: os.remove(img_path); print(f"Cleaned up: {img_path}")
                except Exception as e: print(f"Error cleaning up {img_path}: {e}")

        # Delete temporary D-ID video (if avatar flow and compositing happened)
        if 'temp_did_video_path' in locals() and temp_did_video_path and final_video_path != temp_did_video_path and os.path.exists(temp_did_video_path):
             try: os.remove(temp_did_video_path); print(f"Cleaned up: {temp_did_video_path}")
             except Exception as e: print(f"Error cleaning up {temp_did_video_path}: {e}")

        # Delete generated voiceover audio file
        if generated_audio_path and os.path.exists(generated_audio_path):
            try: os.remove(generated_audio_path); print(f"Cleaned up: {generated_audio_path}")
            except Exception as e: print(f"Error cleaning up {generated_audio_path}: {e}")


# Route to serve uploaded files
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files statically."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) 
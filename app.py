import os
import time
import uuid
import requests
import base64
import textwrap # Import textwrap for description formatting
from bs4 import BeautifulSoup
from flask import Flask, request, render_template, redirect, url_for, send_from_directory, flash, session, jsonify
from openai import OpenAI
from dotenv import load_dotenv
# Ensure these are imported
from moviepy.editor import (VideoFileClip, ImageClip, CompositeVideoClip, ColorClip,
                            TextClip, concatenate_videoclips, AudioFileClip) # Added ColorClip, TextClip, concatenate, AudioFileClip
from moviepy.video.fx.all import fadein, fadeout # Import fade effects
from PIL import Image, ImageOps, ImageFont, ImageDraw, ImageFilter # Import more from Pillow and ImageFilter
import numpy as np
import math # For ceiling function
import platform # Import platform module
import shutil # For potential temporary directory cleanup

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
        os.environ["IMAGEMAGICK_BINARY"] = "/opt/homebrew/bin/magick" # Or the actual path from 'which magick'
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
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(24)) # Needed for flashing messages
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['GENERATED_FOLDER'] = os.path.join('static', 'generated')
app.config['CONFIRM_FOLDER'] = os.path.join(app.config['GENERATED_FOLDER'], 'confirm_temp')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max upload size 16MB
app.config['FONT_PATH'] = 'fonts/Poppins-Bold.ttf' # CHANGE TO YOUR POPPINS BOLD FILENAME
MAX_PRODUCT_IMAGES = 8 # Limit the number of images to process

# Ensure upload and generated directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)
os.makedirs(app.config['CONFIRM_FOLDER'], exist_ok=True) # Create confirm folder

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

# --- MODIFIED Helper: Generate Slideshow Video with Cropped Images ---
def generate_slideshow_video(image_paths, audio_path, output_path, font_path):
    """Generates slideshow with images cropped to fit frame, adaptive frame size, and rounded captions."""
    print("Starting slideshow video generation with images cropped to fit frame...")

    # Frame dimensions (will be adjusted based on images)
    DEFAULT_W, DEFAULT_H = 1920, 1080  # 16:9 default
    PORTRAIT_W, PORTRAIT_H = 1080, 1920  # 9:16 portrait
    SQUARE_W, SQUARE_H = 1440, 1440  # 1:1 square

    output_fps = 30
    fade_duration = 0.5 # Duration for fade between images

    # Caption settings (adjust as needed)
    caption_font_size = 55
    caption_color = 'white'
    caption_bg_color = 'black' # Solid black background
    caption_padding = 20
    caption_corner_radius = 25
    caption_max_words_per_segment = 4
    caption_max_duration_per_segment = 2.5
    caption_bottom_margin = 50

    # Image quality settings (LANCZOS is good for resizing)
    image_resampling_quality = Image.Resampling.LANCZOS

    clips = []
    audio_clip = None
    final_video_sequence = None
    word_timestamps = None
    text_clips_list = []

    try:
        # 1. Analyze images to determine optimal frame size (existing logic)
        print("Analyzing images to determine optimal frame size...")
        aspect_ratios = []
        valid_image_paths = [] # Keep track of images that can be opened
        for img_path in image_paths:
            try:
                with Image.open(img_path) as img:
                    w, h = img.size
                    if w > 0 and h > 0: # Basic validation
                        aspect_ratios.append(w / h)
                        valid_image_paths.append(img_path) # Add valid path
                    else:
                        print(f"Warning: Image {img_path} has zero dimension.")
            except Exception as e:
                print(f"Warning: Could not analyze image {img_path}: {e}")

        if not aspect_ratios:
            print("No valid images to analyze. Using default 16:9 ratio.")
            W, H = DEFAULT_W, DEFAULT_H
        else:
            avg_aspect = sum(aspect_ratios) / len(aspect_ratios)
            print(f"Average image aspect ratio: {avg_aspect:.2f}")
            if avg_aspect < 0.8: W, H = PORTRAIT_W, PORTRAIT_H; print("Using portrait 9:16 frame (1080x1920)")
            elif avg_aspect > 1.2: W, H = DEFAULT_W, DEFAULT_H; print("Using landscape 16:9 frame (1920x1080)")
            else: W, H = SQUARE_W, SQUARE_H; print("Using square 1:1 frame (1440x1440)")

        target_aspect = W / H

        # 2. Load Audio & Get Timestamps (existing logic)
        print("Loading audio and getting word timestamps...")
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration
        word_timestamps = get_word_timestamps(audio_path) # Use helper
        if not word_timestamps:
            print("Warning: Could not get word timestamps. Captions will not be generated.")

        # 3. Calculate Image Durations (existing logic)
        num_images = len(valid_image_paths)
        if num_images == 0: raise ValueError("No valid images provided for slideshow.")
        base_image_duration = total_duration / num_images
        print(f"Total duration: {total_duration:.2f}s, Num images: {num_images}, Base duration/image: {base_image_duration:.2f}s")

        # 4. Create Image Clips with Crop-to-Fit
        print(f"Processing {num_images} images for slideshow frame {W}x{H}...")
        current_time = 0
        for i, img_path in enumerate(valid_image_paths):
            start_time = current_time
            # Ensure last clip ends exactly at total_duration
            end_time = min(start_time + base_image_duration, total_duration) if i < num_images - 1 else total_duration
            duration = end_time - start_time
            if duration <= 0: continue # Skip zero-duration clips

            try:
                print(f"Processing image {i+1}: {os.path.basename(img_path)} for duration {duration:.2f}s")
                with Image.open(img_path).convert("RGB") as img: # Convert to RGB
                    img_w, img_h = img.size
                    img_aspect = img_w / img_h

                    # --- CROP-TO-FIT LOGIC ---
                    if abs(img_aspect - target_aspect) < 0.01: # If aspect ratios are very close, just resize
                        print("  Aspect ratio matches frame. Resizing...")
                        resized_img = img.resize((W, H), image_resampling_quality)
                    elif img_aspect > target_aspect: # Image is wider than frame: Resize based on height, crop width
                        print("  Image wider than frame. Resizing height and cropping width...")
                        new_height = H
                        new_width = int(new_height * img_aspect)
                        resized_img = img.resize((new_width, new_height), image_resampling_quality)
                        # Calculate horizontal crop
                        crop_amount = new_width - W
                        left = crop_amount // 2
                        right = left + W
                        resized_img = resized_img.crop((left, 0, right, new_height))
                    else: # Image is taller than frame: Resize based on width, crop height
                        print("  Image taller than frame. Resizing width and cropping height...")
                        new_width = W
                        new_height = int(new_width / img_aspect)
                        resized_img = img.resize((new_width, new_height), image_resampling_quality)
                        # Calculate vertical crop
                        crop_amount = new_height - H
                        top = crop_amount // 2
                        bottom = top + H
                        resized_img = resized_img.crop((0, top, new_width, bottom))
                    # --- END CROP-TO-FIT ---

                    # Convert PIL image to numpy array for MoviePy
                    img_array = np.array(resized_img)

                    # Create ImageClip
                    img_clip = ImageClip(img_array).set_duration(duration).set_start(start_time)

                    # Add fade effect (except for the first image)
                    if i > 0:
                        img_clip = img_clip.fadein(fade_duration / 2) # Fade in overlaps previous fade out

                    # Add to clips list
                    clips.append(img_clip)

            except Exception as e:
                print(f"Error processing image {img_path}: {e}. Skipping.")
                # Adjust duration distribution if an image fails? Maybe not necessary for simple cases.
                # If skipping, we need to adjust total_duration or redistribute time,
                # but for simplicity, we'll let the timeline have a gap or end early.
                # A better approach would recalculate durations.

            current_time = end_time # Move to the start time for the next clip

        if not clips: raise ValueError("No valid image clips could be created.")

        # 5. Concatenate Image Clips with Crossfade
        print("Concatenating image clips with crossfade...")
        # Apply crossfade by overlapping fadein/fadeout
        video_sequence = concatenate_videoclips(clips, method="compose") # Use compose for overlapping fades
        # Ensure final duration matches audio
        video_sequence = video_sequence.set_duration(total_duration)

        # 6. Create Caption Clips (MODIFIED ACCESS METHOD)
        if word_timestamps:
            print("Generating caption clips...")
            font_param = font_path # Use the provided font path directly
            if not os.path.exists(font_param):
                print(f"Warning: Font file not found at {font_param}. MoviePy might use a default.")
                # On some systems, you might need to provide a system font name like "Arial-Bold"
                # font_param = "Arial-Bold" # Example fallback

            current_caption_start = 0
            segment_words = []
            segment_start_time = 0

            # --- MODIFICATION START ---
            # Iterate through word_timestamps using dot notation
            for i, word_info in enumerate(word_timestamps):
                # Access attributes using dot notation
                word = word_info.word
                start = word_info.start
                end = word_info.end
            # --- MODIFICATION END ---

                if not segment_words: # Start of a new segment
                    segment_start_time = start

                segment_words.append(word)
                current_duration = end - segment_start_time
                is_last_word = (i == len(word_timestamps) - 1)

                # Check if segment should end
                if len(segment_words) >= caption_max_words_per_segment or \
                   current_duration >= caption_max_duration_per_segment or \
                   is_last_word:

                    text = " ".join(segment_words)
                    # Use the 'end' time from the current word_info object
                    duration = end - segment_start_time
                    start_time = segment_start_time

                    print(f"  Caption: '{text}' | Start: {start_time:.2f} | Duration: {duration:.2f}")

                    # --- Create Rounded Background ---
                    # Estimate text size first (might need adjustment)
                    try:
                        # Use TextClip to estimate size first (a bit inefficient but often necessary)
                        temp_text_clip = TextClip(text, fontsize=caption_font_size, color=caption_color, font=font_param, method='label')
                        txt_w, txt_h = temp_text_clip.size
                        temp_text_clip.close() # Close the temporary clip

                        bg_w = txt_w + 2 * caption_padding
                        bg_h = txt_h + 2 * caption_padding

                        # Create background image with Pillow
                        bg_image = Image.new('RGBA', (bg_w, bg_h), (0, 0, 0, 0)) # Transparent background
                        draw = ImageDraw.Draw(bg_image)
                        # Draw rounded rectangle (black with some transparency maybe?)
                        # Use solid black for now as requested
                        draw.rounded_rectangle(
                            (0, 0, bg_w, bg_h),
                            radius=caption_corner_radius,
                            fill=caption_bg_color # Use solid black
                        )
                        # Convert Pillow image to MoviePy clip
                        bg_clip = ImageClip(np.array(bg_image), ismask=False, transparent=True).set_opacity(1.0)
                    except Exception as pil_err:
                        print(f"Warning: Failed to create rounded background image: {pil_err}. Using simple TextClip.")
                        # Fallback to simple TextClip with background
                        y_pos = H - caption_bottom_margin - 50 # Estimate height
                        txt_clip = TextClip(text, fontsize=caption_font_size, color=caption_color, font=font_param, bg_color=caption_bg_color, method='caption', align='center', size=(W*0.8, None))
                        # Use the 'start' and 'duration' calculated above
                        txt_clip = txt_clip.set_position(('center', y_pos)).set_start(start_time).set_duration(duration)
                        text_clips_list.append(txt_clip)
                        # Reset segment and continue
                        segment_words = []
                        continue # Skip the rest of the loop for this segment

                    # --- Create Final Text Clip ---
                    text_clip_final = TextClip(text, fontsize=caption_font_size, color=caption_color, font=font_param, method='label', align='center')

                    # --- Composite Text onto Rounded Background ---
                    caption_clip = CompositeVideoClip([
                        bg_clip.set_position('center'),
                        text_clip_final.set_position('center')
                    ], size=(bg_w, bg_h))

                    # Set timing and position for the composite caption
                    y_pos = H - caption_bottom_margin - caption_clip.h # Position based on composite height
                    caption_clip = caption_clip.set_position(('center', y_pos))
                    # Use the 'start' and 'duration' calculated above
                    caption_clip = caption_clip.set_start(start_time).set_duration(duration)

                    text_clips_list.append(caption_clip)

                    # Close intermediate clips
                    bg_clip.close()
                    text_clip_final.close()

                    # Reset for next segment
                    segment_words = []

            print(f"Created {len(text_clips_list)} composite caption clips.")

        # 7. Composite Base Video + Caption Clips + Audio
        print("Compositing final video...")
        final_composite_elements = [video_sequence]
        if text_clips_list:
            final_composite_elements.extend(text_clips_list)
        # Ensure the final composite uses the determined W, H
        final_video_sequence = CompositeVideoClip(final_composite_elements, size=(W, H))
        final_video_sequence = final_video_sequence.set_duration(total_duration).set_audio(audio_clip)

        # 8. Add Overall Fade In/Out (Optional, applied to the whole sequence)
        # final_video_sequence = final_video_sequence.fadein(fade_duration).fadeout(fade_duration)

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
        for clip in clips: # Close individual image clips
            if clip: clip.close()
        for clip in text_clips_list: # Close caption clips
             if clip: clip.close()

# --- Flask Routes ---

@app.route('/', methods=['GET'])
def index():
    """Renders the main form page."""
    # Clear any previous session data on loading the main page
    session.pop('confirm_data', None)
    session.pop('confirm_images', None)
    return render_template('index.html')

# MODIFIED: This route now handles scraping and shows the confirmation page
@app.route('/generate', methods=['POST'])
def generate_confirmation_route():
    """Handles initial form submission, scrapes data, downloads images,
       and renders the confirmation page."""
    product_url = request.form.get('product_url')
    avatar_file = request.files.get('avatar_file') # Keep track of avatar file if provided
    video_type = request.form.get('video_type', 'product')

    if not product_url:
        flash("Product URL is required.", "error")
        return redirect(url_for('index'))

    uploaded_avatar_path_relative = None # Store relative path for session

    # --- Handle Avatar Upload (if provided) ---
    # We save it now so it's available if the user confirms
    if video_type == 'avatar' and avatar_file and avatar_file.filename != '':
        try:
            _, ext = os.path.splitext(avatar_file.filename)
            if ext.lower() not in ['.png', '.jpg', '.jpeg', '.webp']:
                 flash("Invalid image file type for avatar. Please use PNG, JPG, JPEG, or WEBP.", "error")
                 return redirect(url_for('index'))
            unique_filename = f"avatar_{uuid.uuid4()}{ext}"
            # Save to UPLOAD_FOLDER, not confirm folder
            uploaded_avatar_path_absolute = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            avatar_file.save(uploaded_avatar_path_absolute)
            # Store the relative path for later use if confirmed
            uploaded_avatar_path_relative = os.path.join(os.path.basename(app.config['UPLOAD_FOLDER']), unique_filename)
            print(f"Temporarily saved uploaded avatar: {uploaded_avatar_path_relative}")
        except Exception as e:
            flash(f"Error saving uploaded avatar file: {e}", "error")
            return redirect(url_for('index'))

    # --- Scrape Product Data ---
    print(f"Scraping product data from: {product_url}")
    scraped_data = scrape_product_data(product_url)
    if not scraped_data:
        flash("Failed to scrape product data. Check URL or website structure.", "error")
        return redirect(url_for('index'))

    product_image_urls = scraped_data.get('image_urls', [])
    product_title = scraped_data.get('title', 'Product')
    product_description = scraped_data.get('description', 'No description found.')

    if not product_image_urls:
        flash("Could not find any product images on the provided page.", "warning")
        # Allow proceeding without images for avatar-only? Or redirect?
        # For now, let's show confirmation even without images, user might want avatar only.
        # If video_type is 'product', we should probably redirect here.
        if video_type == 'product':
             flash("No product images found, cannot generate product slideshow.", "error")
             return redirect(url_for('index'))


    # --- Download Scraped Images Temporarily for Confirmation ---
    confirm_image_details = [] # List of dicts: {'absolute_path': ..., 'relative_path': ...}
    print(f"Downloading up to {MAX_PRODUCT_IMAGES} images for confirmation...")
    confirm_folder_basename = os.path.basename(app.config['CONFIRM_FOLDER']) # e.g., 'confirm_temp'
    generated_folder_basename = os.path.basename(app.config['GENERATED_FOLDER']) # e.g., 'generated'

    for i, img_url in enumerate(product_image_urls):
        try:
            img_ext = os.path.splitext(img_url)[1] or ".jpg"
            # Ensure extension is valid image type
            if img_ext.lower() not in ['.png', '.jpg', '.jpeg', '.webp', '.gif']:
                img_ext = ".jpg" # Default to jpg if extension is weird
            img_filename = f"confirm_{i}_{uuid.uuid4()}{img_ext}"
            # Save images inside the 'confirm_temp' subfolder within 'generated'
            img_path_absolute = os.path.join(app.config['CONFIRM_FOLDER'], img_filename)
            if download_image(img_url, img_path_absolute):
                # Store relative path for use in template and session
                # Path relative to 'static' folder: e.g., 'generated/confirm_temp/confirm_0_xyz.jpg'
                img_path_relative = os.path.join(generated_folder_basename, confirm_folder_basename, img_filename).replace("\\", "/")
                confirm_image_details.append({
                    'absolute_path': img_path_absolute,
                    'relative_path': img_path_relative
                })
                print(f"Downloaded confirmation image: {img_path_relative}")
            else:
                print(f"Warning: Failed to download confirmation image {i+1}: {img_url}")
        except Exception as e:
             print(f"Error downloading confirmation image {i+1}: {e}")
        if len(confirm_image_details) >= MAX_PRODUCT_IMAGES:
            break # Stop if we hit the max

    if not confirm_image_details and video_type == 'product':
         flash("Failed to download any valid product images for confirmation.", "error")
         # Clean up avatar if it was uploaded
         if uploaded_avatar_path_relative:
             try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(uploaded_avatar_path_relative)))
             except: pass
         return redirect(url_for('index'))

    # --- Store data in session for the next step ---
    session['confirm_data'] = {
        'product_title': product_title,
        'product_description': product_description,
        'video_type': video_type,
        'uploaded_avatar_relative_path': uploaded_avatar_path_relative # Store relative path or None
    }
    # Store only the relative paths and absolute paths for cleanup later
    session['confirm_images'] = [{'relative_path': img['relative_path'], 'absolute_path': img['absolute_path']} for img in confirm_image_details]

    print("Rendering confirmation page...")
    # Pass only relative paths to the template
    return render_template('confirm.html',
                           product_title=product_title,
                           product_description=product_description,
                           confirm_images=[img['relative_path'] for img in confirm_image_details], # Pass only relative paths
                           video_type=video_type)


# NEW Route: Handles the actual video creation after confirmation
@app.route('/create_video', methods=['POST'])
def create_video_route():
    """Handles confirmation form submission asynchronously and generates the final video.
       Returns JSON response."""
    print("Received ASYNC request to create video after confirmation.")

    # --- Retrieve data from session ---
    confirm_data = session.get('confirm_data')
    confirm_images_info = session.get('confirm_images', [])

    if not confirm_data:
        print("Error: Session data missing.")
        # Return JSON error
        return jsonify({'success': False, 'error': 'Session expired or data missing. Please start over.'}), 400

    # --- Retrieve selected images from the POST request body (sent as JSON) ---
    try:
        request_data = request.get_json()
        if not request_data:
            raise ValueError("Missing JSON data in request")
        selected_relative_paths = request_data.get('selected_images', [])
        if not isinstance(selected_relative_paths, list):
             raise ValueError("selected_images should be a list")
    except Exception as e:
        print(f"Error parsing request JSON: {e}")
        return jsonify({'success': False, 'error': f'Invalid request format: {e}'}), 400


    # --- Get data stored in session ---
    product_title = confirm_data.get('product_title', 'Product')
    product_description = confirm_data.get('product_description', '')
    video_type = confirm_data.get('video_type', 'product')
    uploaded_avatar_relative_path = confirm_data.get('uploaded_avatar_relative_path')

    print(f"Retrieved from session: Title='{product_title}', VideoType='{video_type}', Avatar='{uploaded_avatar_relative_path}'")
    print(f"Selected image relative paths from JSON request: {selected_relative_paths}")

    # --- Map selected relative paths back to absolute paths ---
    selected_absolute_paths = []
    path_map = {img['relative_path']: img['absolute_path'] for img in confirm_images_info}
    for rel_path in selected_relative_paths:
        abs_path = path_map.get(rel_path)
        if abs_path and os.path.exists(abs_path):
            selected_absolute_paths.append(abs_path)
        else:
            print(f"Warning: Selected image path not found or invalid: {rel_path}")

    print(f"Selected image absolute paths for generation: {selected_absolute_paths}")

    # --- Common Setup for Generation ---
    final_video_path = None
    final_video_filename = None
    generated_audio_path = None
    temp_did_video_path = None
    downloaded_overlay_image_path = None
    uploaded_avatar_absolute_path = None # Keep track for cleanup

    # Determine avatar source URL for D-ID
    avatar_source_url = DEFAULT_AVATAR_URL
    if uploaded_avatar_relative_path:
        uploaded_avatar_absolute_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(uploaded_avatar_relative_path))
        if os.path.exists(uploaded_avatar_absolute_path):
             avatar_source_url = url_for('uploaded_file', filename=os.path.basename(uploaded_avatar_relative_path), _external=True)
             print(f"Using confirmed uploaded avatar URL: {avatar_source_url}")
        else:
             print(f"Warning: Uploaded avatar file not found at {uploaded_avatar_absolute_path}, using default.")
             uploaded_avatar_absolute_path = None

    try:
        # ===========================================
        # --- Branch Logic based on Video Type ---
        # ===========================================
        error_message = None # Variable to hold potential errors

        if video_type == 'avatar':
            print("--- Generating Confirmed Avatar Video ---")
            overlay_image_path_absolute = selected_absolute_paths[0] if selected_absolute_paths else None
            downloaded_overlay_image_path = overlay_image_path_absolute

            print("Generating marketing script...")
            script = generate_marketing_script(product_title, product_description)
            if script.startswith("Error:"): error_message = f"Script generation failed: {script}"

            if not error_message:
                print(f"Requesting video generation from D-ID with avatar: {avatar_source_url}")
                d_id_result = create_d_id_talk(script, avatar_source_url)
                if "error" in d_id_result: error_message = f"D-ID video generation failed: {d_id_result['error']}"
                else: result_url = d_id_result.get("result_url")

            if not error_message:
                print("Downloading generated D-ID video...")
                temp_video_filename = f"temp_{uuid.uuid4()}.mp4"
                temp_did_video_path = os.path.join(app.config['GENERATED_FOLDER'], temp_video_filename)
                if not download_video(result_url, temp_did_video_path): error_message = "Video download failed"

            if not error_message:
                final_video_filename = f"final_avatar_{uuid.uuid4()}.mp4"
                final_video_path = os.path.join(app.config['GENERATED_FOLDER'], final_video_filename)
                if downloaded_overlay_image_path and os.path.exists(downloaded_overlay_image_path):
                    print("Compositing D-ID video with selected product image overlay...")
                    try:
                        video_clip = VideoFileClip(temp_did_video_path)
                        img_clip = ImageClip(downloaded_overlay_image_path).resize(width=video_clip.w * 0.25).set_position(('right','bottom')).set_duration(video_clip.duration)
                        final_clip = CompositeVideoClip([video_clip, img_clip], size=video_clip.size)
                        final_clip.write_videofile(final_video_path, codec='libx264', audio_codec='aac', temp_audiofile='temp-avatar-audio.m4a', remove_temp=True, preset='medium', ffmpeg_params=["-profile:v","baseline", "-level","3.0", "-pix_fmt", "yuv420p"])
                        img_clip.close(); video_clip.close(); final_clip.close(); print("Overlay successful.")
                    except Exception as e:
                        print(f"Overlay failed: {e}")
                        # Fallback: Use the D-ID video without overlay
                        final_video_path = temp_did_video_path
                        final_video_filename = temp_video_filename
                else:
                    print("No overlay image selected or found. Using D-ID video directly.")
                    final_video_path = temp_did_video_path
                    final_video_filename = temp_video_filename

        elif video_type == 'product':
            print("--- Generating Confirmed Product Slideshow Video ---")
            if not selected_absolute_paths:
                error_message = "No images were selected for the slideshow."

            if not error_message:
                print("Generating script for voiceover...")
                script = generate_marketing_script(product_title, product_description)
                if script.startswith("Error:"): error_message = f"Script generation failed: {script}"

            if not error_message:
                print("Generating voiceover...")
                audio_filename = f"voiceover_{uuid.uuid4()}.mp3"
                generated_audio_path = os.path.join(app.config['GENERATED_FOLDER'], audio_filename)
                if not generate_voiceover(script, generated_audio_path): error_message = "TTS generation failed"

            if not error_message:
                final_video_filename = f"final_slideshow_{uuid.uuid4()}.mp4"
                final_video_path = os.path.join(app.config['GENERATED_FOLDER'], final_video_filename)
                if not generate_slideshow_video(selected_absolute_paths, generated_audio_path, final_video_path, app.config['FONT_PATH']):
                    error_message = "Slideshow video generation failed."

        else:
            error_message = "Invalid video type specified."

        # ===========================================
        # --- Return JSON Response ---
        # ===========================================
        if error_message:
             raise Exception(error_message) # Raise exception to be caught below

        if final_video_path and final_video_filename:
            video_url = url_for('static', filename=f'generated/{final_video_filename}')
            print(f"Generation successful. Video URL: {video_url}")
            # Clear session data only on success before returning
            session.pop('confirm_data', None)
            session.pop('confirm_images', None)
            return jsonify({'success': True, 'video_url': video_url})
        else:
            # This case should ideally not be reached if error handling above is correct
            raise Exception("An unknown error occurred, failed to determine final video.")

    except Exception as e:
        error_msg = f"An error occurred during video generation: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc() # Print full traceback to server logs
        # Return JSON error
        return jsonify({'success': False, 'error': error_msg}), 500

    finally:
        # --- Cleanup (runs even if errors occurred) ---
        print("Cleaning up temporary files...")
        if uploaded_avatar_absolute_path and os.path.exists(uploaded_avatar_absolute_path):
            try: os.remove(uploaded_avatar_absolute_path); print(f"Cleaned up uploaded avatar: {uploaded_avatar_absolute_path}")
            except Exception as e: print(f"Error cleaning up avatar {uploaded_avatar_absolute_path}: {e}")

        confirm_images_to_delete = session.get('confirm_images', []) # Get paths again in case session wasn't cleared on error
        for img_info in confirm_images_to_delete:
            abs_path = img_info.get('absolute_path')
            if abs_path and os.path.exists(abs_path):
                try: os.remove(abs_path); print(f"Cleaned up confirmation image: {abs_path}")
                except Exception as e: print(f"Error cleaning up confirmation image {abs_path}: {e}")

        if temp_did_video_path and os.path.exists(temp_did_video_path):
             if final_video_path != temp_did_video_path:
                 try: os.remove(temp_did_video_path); print(f"Cleaned up temp D-ID video: {temp_did_video_path}")
                 except Exception as e: print(f"Error cleaning up temp D-ID video {temp_did_video_path}: {e}")
             else:
                 print(f"Skipping cleanup of temp D-ID video as it's the final video: {temp_did_video_path}")

        if generated_audio_path and os.path.exists(generated_audio_path):
            try: os.remove(generated_audio_path); print(f"Cleaned up voiceover audio: {generated_audio_path}")
            except Exception as e: print(f"Error cleaning up voiceover audio {generated_audio_path}: {e}")

        # Attempt to clear session data again in finally block, just in case
        session.pop('confirm_data', None)
        session.pop('confirm_images', None)

# Route to serve uploaded files (needed for avatar URL)
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files statically."""
    # Ensure filename is safe (though UUIDs are generally safe)
    # from werkzeug.utils import secure_filename
    # filename = secure_filename(filename)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    # Make sure debug is False in production
    app.run(debug=True, host='0.0.0.0', port=5000) 
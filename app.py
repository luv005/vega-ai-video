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
import json
import re
import hashlib # Import hashlib for hashing image content
import io # Import io for handling image bytes with PIL
from urllib.parse import urlparse # To help get extension from URL
import imagehash # <-- Import imagehash

# --- NEW: Import Playwright ---
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

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
PHASH_THRESHOLD = 5 # Define the perceptual hash threshold (lower = stricter)

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

# --- REVISED Scrape Function (Clean URLs Before Adding) ---
def scrape_product_data(url):
    """Scrapes product data using Playwright, cleaning URLs to get high-res."""
    print(f"Attempting to scrape with Playwright: {url}")
    product_data = {'title': 'Product', 'description': 'No description found.', 'image_urls': []}
    html_content = None

    try:
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(headless=True) # Consider headless=False for debugging blocks
                print("  Playwright browser launched.")
            except Exception as launch_err:
                 print(f"Error launching Playwright browser: {launch_err}")
                 return {'error': 'Failed to launch browser. Run `playwright install`.'}

            # Use a realistic user agent
            user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
            viewport = {'width': 1920, 'height': 1080}
            context = browser.new_context(user_agent=user_agent, viewport=viewport, java_script_enabled=True, ignore_https_errors=True)
            page = context.new_page()
            print(f"  Navigating to {url}...")

            try:
                # Go to the page
                page.goto(url, wait_until='domcontentloaded', timeout=60000) # Wait for initial structure

                # --- *** ADD CAPTCHA/BLOCK DETECTION *** ---
                page_title = page.title().lower()
                page_content_lower = page.content().lower() # Get page content for text checks
                captcha_selectors = ['#captchacharacters', 'input[name="field-keywords"][type="hidden"]'] # Selectors indicating CAPTCHA form
                captcha_texts = ['server busy', 'type the characters you see', 'enter the characters', 'robot check'] # Text indicators

                is_blocked = False
                if "captcha" in page_title or any(text in page_title for text in captcha_texts):
                    is_blocked = True
                    print("  Block detected via page title.")
                else:
                    for selector in captcha_selectors:
                        if page.query_selector(selector):
                            is_blocked = True
                            print(f"  Block detected via selector: {selector}")
                            break
                    if not is_blocked:
                         for text in captcha_texts:
                              if text in page_content_lower:
                                   is_blocked = True
                                   print(f"  Block detected via text content: '{text}'")
                                   break

                if is_blocked:
                    print(f"Error: Playwright encountered a CAPTCHA or block page for URL: {url}")
                    # Optionally take a screenshot for debugging
                    # screenshot_path = f"captcha_screenshot_{uuid.uuid4().hex[:6]}.png"
                    # page.screenshot(path=screenshot_path)
                    # print(f"  Screenshot saved to: {screenshot_path}")
                    raise PlaywrightError("Scraping blocked (CAPTCHA/Block page detected).")
                # --- *** END BLOCK DETECTION *** ---

                # If not blocked, wait for dynamic content to potentially load
                print("  No block detected, waiting for network idle...")
                page.wait_for_load_state('networkidle', timeout=30000) # Wait for network activity to settle

                # Wait for essential elements (main image OR thumbnails)
                print("  Waiting for main image OR thumbnail container...")
                page.wait_for_selector('#imgTagWrapperId, #landingImage, #imgBlkFront, #altImages', timeout=20000)
                print("  Essential elements appear loaded.")
                html_content = page.content()

            except PlaywrightTimeoutError as e:
                print(f"  Timeout during Playwright navigation/waiting: {e}")
                # Try getting content anyway, might be partial or the block page
                html_content = page.content()
                if not html_content: return {'error': 'Playwright timed out loading page content.'}
                # Re-check for blocks if timeout occurred
                if any(text in html_content.lower() for text in captcha_texts):
                     print("  Block detected in content after timeout.")
                     raise PlaywrightError("Scraping blocked (CAPTCHA/Block page detected after timeout).")
                print("  Proceeding with potentially partial content after timeout.")
            except PlaywrightError as e: # Catch errors raised explicitly (like our block detection)
                 print(f"  Playwright operation error: {e}")
                 raise # Re-raise to be caught by the outer try/except
            except Exception as nav_err:
                 print(f"  Error during Playwright navigation/interaction: {nav_err}")
                 raise PlaywrightError(f'Error during browser navigation: {nav_err}') # Wrap as PlaywrightError
            finally:
                 if 'context' in locals() and context: context.close()
                 if browser: browser.close()
                 print("  Playwright browser closed.")

        # --- Process the HTML obtained from Playwright ---
        if html_content:
            soup = BeautifulSoup(html_content, 'html.parser')
            # --- Extract Title ---
            title_tag = soup.select_one('#productTitle')
            title = title_tag.get_text(strip=True) if title_tag else 'Product Title Not Found'
            product_data['title'] = title
            print(f"  Extracted Title: {title}")

            # --- Extract Description ---
            desc_tag = soup.select_one('#feature-bullets')
            description = desc_tag.get_text(separator='\n', strip=True) if desc_tag else 'No description found.'
            product_data['description'] = description
            # print(f"  Extracted Description: {description[:100]}...") # Optional: log description start

            # --- Extract HIGH-RES Images (CLEAN URLs) ---
            image_urls = set()

            # Method 1: data-a-dynamic-image (Primary source - CLEAN URLs)
            main_image_tag = soup.select_one('#imgTagWrapperId img, #landingImage, #imgBlkFront')
            if main_image_tag:
                print("  Looking for 'data-a-dynamic-image'...")
                dynamic_image_data = main_image_tag.get('data-a-dynamic-image')
                if dynamic_image_data and isinstance(dynamic_image_data, str):
                    try:
                        image_map = json.loads(dynamic_image_data)
                        print(f"  Found {len(image_map)} URLs in data-a-dynamic-image.")
                        for img_url in image_map.keys():
                             print(f"    Raw URL from dynamic data: {img_url}")
                             if isinstance(img_url, str) and img_url.startswith('http'):
                                 # *** CLEAN THE URL ***
                                 cleaned_url = re.sub(r'\._[A-Z0-9,_]+_\.', '.', img_url)
                                 print(f"    Cleaned URL: {cleaned_url}")
                                 # Ensure it still looks like an image URL after cleaning
                                 if cleaned_url.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                                     image_urls.add(cleaned_url) # Add the cleaned URL
                                 else:
                                     print(f"    Skipping cleaned URL (invalid extension?): {cleaned_url}")
                    except json.JSONDecodeError:
                        print("  Warning: Failed to parse data-a-dynamic-image JSON.")
                        # Fallback to src if dynamic data fails parsing (CLEAN URL)
                        src = main_image_tag.get('src')
                        if src and src.startswith('http'):
                             print(f"    Raw fallback src: {src}")
                             cleaned_url = re.sub(r'\._.*?_\.', '.', src) # Use simpler regex for basic src fallback
                             print(f"    Cleaned fallback src: {cleaned_url}")
                             if cleaned_url.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                                 image_urls.add(cleaned_url)
                # Fallback to src if dynamic data attribute is missing (CLEAN URL)
                elif main_image_tag.get('src'):
                     src = main_image_tag.get('src')
                     print("  'data-a-dynamic-image' not found. Using 'src' attribute.")
                     if src.startswith('http'):
                         print(f"    Raw fallback src: {src}")
                         cleaned_url = re.sub(r'\._.*?_\.', '.', src)
                         print(f"    Cleaned fallback src: {cleaned_url}")
                         if cleaned_url.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                             image_urls.add(cleaned_url)
            else:
                 print("  Warning: Could not find main image tag.")

            # Method 2: Thumbnail Scraping (#altImages - CLEAN URLs)
            print("  Looking for thumbnails in #altImages...")
            thumbnail_container = soup.select_one('#altImages')
            if thumbnail_container:
                thumb_elements = thumbnail_container.select('li.item img')
                print(f"  Found {len(thumb_elements)} potential thumbnail img elements in #altImages.")
                for thumb in thumb_elements:
                    thumb_src = thumb.get('src')
                    print(f"    Raw thumbnail src: {thumb_src}")
                    if thumb_src and thumb_src.startswith('http') and 'loading-' not in thumb_src and 'spinner' not in thumb_src and 'pixel.gif' not in thumb_src:
                        # *** CLEAN THE URL ***
                        cleaned_url = re.sub(r'\._[A-Z0-9,_]+_\.', '.', thumb_src)
                        print(f"    Cleaned thumbnail URL: {cleaned_url}")
                        # Ensure it still looks like an image URL after cleaning
                        if cleaned_url.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            image_urls.add(cleaned_url) # Add the cleaned URL
                        else:
                            print(f"    Skipping cleaned thumbnail URL (invalid extension?): {cleaned_url}")
            else:
                 print("  Thumbnail container #altImages not found.")

            # Finalize list
            product_data['image_urls'] = list(image_urls)

            # --- Logging ---
            print("-" * 30); print(f"DEBUG: Found {len(product_data['image_urls'])} unique CLEANED URLs after scraping:")
            if product_data['image_urls']:
                for i, img_url in enumerate(product_data['image_urls']): print(f"  URL {i+1}: {img_url}")
            else: print("  No image URLs found.")
            print("-" * 30)
            print(f"Scraped Title via Playwright: {product_data['title']}")
            print(f"Found {len(product_data['image_urls'])} unique image URLs via Playwright.")
        else:
             # This case might happen if Playwright failed very early
             print("Error: No HTML content was retrieved by Playwright.")
             return {'error': 'Playwright failed to retrieve page content.'}

    except PlaywrightError as e: # Catch errors from Playwright steps or explicit raises
        print(f"Playwright scraping failed: {e}")
        return {'error': f'{e}'} # Return the specific error message
    except Exception as e:
        print(f"An unexpected error occurred during Playwright scraping: {e}")
        import traceback
        traceback.print_exc()
        return {'error': f'An unexpected error occurred during scraping: {e}'}

    # Final checks before returning
    if 'error' not in product_data and not product_data.get('image_urls'):
        print("Warning: Scraper finished but found no image URLs.")
        # Optionally return an error here if images are mandatory
        # return {'error': 'No product images could be extracted.'}

    return product_data

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

# --- MODIFIED Helper: Download Image (Return pHash Object) ---
def download_image(url, save_dir):
    """Downloads image, returns path, content hash, and perceptual hash object."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, stream=True, timeout=20)
        response.raise_for_status()
        content_type = response.headers.get('content-type')
        if not content_type or not content_type.lower().startswith('image/'):
            print(f"Skipping non-image content type '{content_type}' from URL: {url}")
            return None, None, None
        image_content = response.content
        if not image_content:
             print(f"Skipping empty image content from URL: {url}")
             return None, None, None

        # Calculate content hash
        content_hash = hashlib.sha256(image_content).hexdigest()

        # Calculate perceptual hash
        perceptual_hash = None
        img_format = 'jpg' # Default format
        try:
            with Image.open(io.BytesIO(image_content)) as img:
                # Convert to grayscale for phash
                perceptual_hash = imagehash.phash(img.convert('L'))
                # Try to get original format for saving
                if img.format: img_format = img.format.lower()

        except Exception as pil_err:
            print(f"  Warning: PIL/imagehash error for {url}: {pil_err}. Cannot calculate pHash.")
            # Attempt to determine format anyway for saving
            # ... (use fallback format detection logic from previous version if needed) ...
            if 'jpeg' in content_type or 'jpg' in content_type: img_format = 'jpg'
            elif 'png' in content_type: img_format = 'png'
            # ... other formats ...

        # Save the image
        if not img_format or len(img_format) > 5: img_format = 'jpg'
        filename = f"img_{content_hash[:10]}_{uuid.uuid4().hex[:8]}.{img_format}"
        save_path = os.path.join(save_dir, filename)
        with open(save_path, 'wb') as f:
            f.write(image_content)

        # Return path, content hash, and the pHash OBJECT (or None)
        return save_path, content_hash, perceptual_hash

    except requests.exceptions.RequestException as e:
        print(f"Error downloading {url}: {e}")
        return None, None, None
    except Exception as e:
        print(f"Error processing image from {url}: {e}")
        return None, None, None

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

# --- MODIFIED Helper: Generate Slideshow Video with Padded Images ---
def generate_slideshow_video(image_paths, audio_path, output_path, font_path):
    """Generates slideshow with images resized to fit frame (padded), adaptive frame size, and rounded captions."""
    print("Starting slideshow video generation with images padded to fit frame...")

    # Frame dimensions (will be adjusted based on images)
    DEFAULT_W, DEFAULT_H = 1920, 1080  # 16:9 default
    PORTRAIT_W, PORTRAIT_H = 1080, 1920  # 9:16 portrait
    SQUARE_W, SQUARE_H = 1440, 1440  # 1:1 square

    output_fps = 30
    fade_duration = 0.5 # Duration for fade between images
    padding_color = (0, 0, 0) # Black padding (R, G, B)

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

        # 4. Create Image Clips with Fit-Inside (Padding)
        print(f"Processing {num_images} images for slideshow frame {W}x{H} with padding...")
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

                    # --- FIT-INSIDE (PADDING) LOGIC ---
                    final_img_pil = None # Variable to hold the final PIL image for the frame

                    if abs(img_aspect - target_aspect) < 0.01: # If aspect ratios are very close, just resize
                        print("  Aspect ratio matches frame. Resizing...")
                        final_img_pil = img.resize((W, H), image_resampling_quality)
                    else:
                        # Create the background canvas
                        background = Image.new('RGB', (W, H), padding_color)

                        if img_aspect > target_aspect: # Image is wider than frame: Fit width, pad top/bottom
                            print("  Image wider than frame. Resizing width and padding height...")
                            new_width = W
                            new_height = int(new_width / img_aspect)
                            resized_img = img.resize((new_width, new_height), image_resampling_quality)
                            # Calculate vertical paste position
                            paste_y = (H - new_height) // 2
                            background.paste(resized_img, (0, paste_y))
                            final_img_pil = background
                        else: # Image is taller than frame: Fit height, pad left/right
                            print("  Image taller than frame. Resizing height and padding width...")
                            new_height = H
                            new_width = int(new_height * img_aspect)
                            resized_img = img.resize((new_width, new_height), image_resampling_quality)
                            # Calculate horizontal paste position
                            paste_x = (W - new_width) // 2
                            background.paste(resized_img, (paste_x, 0))
                            final_img_pil = background
                    # --- END FIT-INSIDE ---

                    # Convert final PIL image (with padding if needed) to numpy array for MoviePy
                    img_array = np.array(final_img_pil)

                    # Create ImageClip
                    img_clip = ImageClip(img_array).set_duration(duration).set_start(start_time)

                    # Add fade effect (except for the first image)
                    if i > 0:
                        img_clip = img_clip.fadein(fade_duration / 2) # Fade in overlaps previous fade out

                    # Add to clips list
                    clips.append(img_clip)

            except Exception as e:
                print(f"Error processing image {img_path}: {e}. Skipping.")
                # Handle skipped images (optional: redistribute time or accept gaps)

            current_time = end_time # Move to the start time for the next clip

        if not clips: raise ValueError("No valid image clips could be created.")

        # 5. Concatenate Image Clips with Crossfade
        print("Concatenating image clips with crossfade...")
        # Apply crossfade by overlapping fadein/fadeout
        video_sequence = concatenate_videoclips(clips, method="compose") # Use compose for overlapping fades
        # Ensure final duration matches audio
        video_sequence = video_sequence.set_duration(total_duration)

        # 6. Create Caption Clips (existing logic using dot notation)
        if word_timestamps:
            print("Generating caption clips...")
            font_param = font_path # Use the provided font path directly
            if not os.path.exists(font_param):
                print(f"Warning: Font file not found at {font_param}. MoviePy might use a default.")

            current_caption_start = 0
            segment_words = []
            segment_start_time = 0

            for i, word_info in enumerate(word_timestamps):
                word = word_info.word
                start = word_info.start
                end = word_info.end

                if not segment_words: # Start of a new segment
                    segment_start_time = start

                segment_words.append(word)
                current_duration = end - segment_start_time
                is_last_word = (i == len(word_timestamps) - 1)

                if len(segment_words) >= caption_max_words_per_segment or \
                   current_duration >= caption_max_duration_per_segment or \
                   is_last_word:

                    text = " ".join(segment_words)
                    duration = end - segment_start_time
                    start_time = segment_start_time

                    print(f"  Caption: '{text}' | Start: {start_time:.2f} | Duration: {duration:.2f}")

                    try:
                        temp_text_clip = TextClip(text, fontsize=caption_font_size, color=caption_color, font=font_param, method='label')
                        txt_w, txt_h = temp_text_clip.size
                        temp_text_clip.close()

                        bg_w = txt_w + 2 * caption_padding
                        bg_h = txt_h + 2 * caption_padding

                        bg_image = Image.new('RGBA', (bg_w, bg_h), (0, 0, 0, 0))
                        draw = ImageDraw.Draw(bg_image)
                        draw.rounded_rectangle((0, 0, bg_w, bg_h), radius=caption_corner_radius, fill=caption_bg_color)
                        bg_clip = ImageClip(np.array(bg_image), ismask=False, transparent=True).set_opacity(1.0)

                    except Exception as pil_err:
                        print(f"Warning: Failed to create rounded background image: {pil_err}. Using simple TextClip.")
                        y_pos = H - caption_bottom_margin - 50
                        txt_clip = TextClip(text, fontsize=caption_font_size, color=caption_color, font=font_param, bg_color=caption_bg_color, method='caption', align='center', size=(W*0.8, None))
                        txt_clip = txt_clip.set_position(('center', y_pos)).set_start(start_time).set_duration(duration)
                        text_clips_list.append(txt_clip)
                        segment_words = []
                        continue

                    text_clip_final = TextClip(text, fontsize=caption_font_size, color=caption_color, font=font_param, method='label', align='center')

                    caption_clip = CompositeVideoClip([
                        bg_clip.set_position('center'),
                        text_clip_final.set_position('center')
                    ], size=(bg_w, bg_h))

                    y_pos = H - caption_bottom_margin - caption_clip.h
                    caption_clip = caption_clip.set_position(('center', y_pos))
                    caption_clip = caption_clip.set_start(start_time).set_duration(duration)

                    text_clips_list.append(caption_clip)

                    bg_clip.close()
                    text_clip_final.close()
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

        # 8. Add Overall Fade In/Out (Optional)
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

    # --- Handle Scraping Failure ---
    if not scraped_data or 'error' in scraped_data:
        error_message = scraped_data.get('error', 'Failed to scrape product data. The URL might be invalid, blocked, or the page structure is unsupported.') if isinstance(scraped_data, dict) else 'Failed to scrape product data.'
        flash(error_message, 'danger')
        print(f"Scraping failed for {product_url}: {error_message}")
        return redirect(url_for('index')) # Redirect back to input form

    product_title = scraped_data.get('title', 'Product')
    product_description = scraped_data.get('description', '')
    image_urls = scraped_data.get('image_urls', [])

    print("-" * 30)
    print(f"DEBUG: URLs received by /generate route for download (Count: {len(image_urls)}):")
    if image_urls:
        for i, img_url in enumerate(image_urls): print(f"  URL {i+1} to download: {img_url}")
    else: print("  No image URLs received from scraper.")
    print("-" * 30)

    if not image_urls:
        flash("Could not find any product images on the provided page.", "warning")
        print("Warning: Proceeding to confirmation page without any scraped images.")

    # --- MODIFIED Download Images with Hashing & Stricter Size Check ---
    confirm_image_details = []
    downloaded_content_hashes = set()
    downloaded_phashes = [] # Store pHash OBJECTS in a list for comparison
    confirm_folder_basename = os.path.basename(app.config['CONFIRM_FOLDER'])
    uploaded_avatar_path_relative = request.form.get('uploaded_avatar_path')
    video_type = request.form.get('video_type', 'product') # Get video type

    print(f"Downloading images and checking for content & VISUAL duplicates (pHash Threshold: {PHASH_THRESHOLD})...")
    urls_to_process = image_urls[:MAX_PRODUCT_IMAGES]
    print(f"Processing max {len(urls_to_process)} URLs based on MAX_PRODUCT_IMAGES={MAX_PRODUCT_IMAGES}")

    for i, img_url in enumerate(urls_to_process):
        print(f"\nProcessing URL {i+1}/{len(urls_to_process)}: {img_url}")

        absolute_path, content_hash, p_hash_obj = download_image(img_url, app.config['CONFIRM_FOLDER'])

        if absolute_path and content_hash:
            print(f"  --> Downloaded to: {os.path.basename(absolute_path)}")
            print(f"  --> Content Hash: {content_hash}")
            print(f"  --> Perceptual Hash Obj: {p_hash_obj}")

            # Check 1: Exact Content Duplicate
            if content_hash not in downloaded_content_hashes:
                print(f"  --> Content Hash is NEW.")

                # Check 2: Visual Duplicate (using Hamming distance)
                is_visual_duplicate = False
                if p_hash_obj is not None:
                    for existing_phash in downloaded_phashes:
                        distance = p_hash_obj - existing_phash
                        print(f"    Comparing pHash distance to {existing_phash}: {distance}")
                        if distance <= PHASH_THRESHOLD:
                            is_visual_duplicate = True
                            print(f"  --> Perceptual Hash is DUPLICATE (Distance {distance} <= {PHASH_THRESHOLD}). Skipping add.")
                            break
                    if not is_visual_duplicate:
                         print(f"  --> Perceptual Hash is Visually NEW (Min distance > {PHASH_THRESHOLD}).")
                else:
                    print(f"  --> Perceptual Hash could not be calculated. Skipping visual check.")

                # Proceed only if NOT a visual duplicate
                if not is_visual_duplicate:
                    size_ok = True # Assume size is okay initially
                    # --- Stricter Size Check ---
                    try:
                        with Image.open(absolute_path) as img:
                            width, height = img.size
                            # *** INCREASED MINIMUM DIMENSION ***
                            min_dimension = 400
                            print(f"  --> Checking Size: {width}x{height} (Min: {min_dimension})")
                            if width < min_dimension or height < min_dimension:
                                 print(f"  --> Size Check FAILED. Skipping image.")
                                 size_ok = False
                                 try: os.remove(absolute_path)
                                 except OSError as e: print(f"  Warning: Could not remove small image file: {e}")
                            else:
                                 print(f"  --> Size Check PASSED.")
                    except Exception as size_err:
                        print(f"  --> Warning: Could not check size: {size_err}")
                        # *** ENSURE size_ok IS FALSE ON ERROR ***
                        size_ok = False
                        # Attempt cleanup even if size check failed due to error
                        if os.path.exists(absolute_path):
                             try: os.remove(absolute_path)
                             except OSError as e: print(f"  Warning: Could not remove file after size check error: {e}")
                    # --- End Size Check ---

                    # Add to list only if content hash is new, not visually duplicate, AND size is okay
                    if size_ok:
                        downloaded_content_hashes.add(content_hash)
                        if p_hash_obj is not None:
                            downloaded_phashes.append(p_hash_obj)

                        relative_path = url_for('static', filename=f'generated/{confirm_folder_basename}/{os.path.basename(absolute_path)}')
                        confirm_image_details.append({
                            'absolute_path': absolute_path,
                            'relative_path': relative_path,
                            'hash': content_hash,
                            'phash': str(p_hash_obj) if p_hash_obj else None
                        })
                        print(f"  --> Added to confirm_image_details. Total unique images: {len(confirm_image_details)}")
                else: # It was a visual duplicate
                     # Clean up the visually duplicate file
                     if os.path.exists(absolute_path):
                         print(f"  --> Removing visually duplicate file: {os.path.basename(absolute_path)}")
                         try: os.remove(absolute_path)
                         except OSError as e: print(f"  Warning: Could not remove duplicate file {absolute_path}: {e}")

            else: # Content hash already seen (exact duplicate)
                print(f"  --> Content Hash is DUPLICATE (Exact match). Skipping add.")
                # Clean up the newly downloaded exact duplicate file
                if os.path.exists(absolute_path):
                    print(f"  --> Removing exact duplicate file: {os.path.basename(absolute_path)}")
                    try: os.remove(absolute_path)
                    except OSError as e: print(f"  Warning: Could not remove duplicate file {absolute_path}: {e}")
        else:
             print(f"  --> Download FAILED or skipped for URL: {img_url}")


    if not confirm_image_details and video_type == 'product':
         flash("Failed to download any valid product images for confirmation.", "error")
         # Clean up avatar if it was uploaded
         if uploaded_avatar_path_relative:
             try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(uploaded_avatar_path_relative)))
             except: pass
         return redirect(url_for('index'))

    # --- Store data in session ---
    session['confirm_data'] = {
        'product_title': product_title,
        'product_description': product_description,
        'video_type': video_type,
        'uploaded_avatar_relative_path': uploaded_avatar_path_relative
    }
    session['confirm_images'] = [{'relative_path': img['relative_path'], 'absolute_path': img['absolute_path']} for img in confirm_image_details]

    print("-" * 30)
    print(f"DEBUG: Final unique images being sent to template (Count: {len(confirm_image_details)}):")
    if confirm_image_details:
        for i, img_detail in enumerate(confirm_image_details):
             print(f"  Image {i+1}: Path={os.path.basename(img_detail['absolute_path'])}, CHash={img_detail['hash'][:10]}..., PHash={img_detail['phash']}")
    else: print("  None")
    print("-" * 30)
    print(f"Rendering confirmation page with {len(confirm_image_details)} unique images.")
    return render_template('confirm.html',
                           product_title=product_title,
                           product_description=product_description,
                           confirm_images=[img['relative_path'] for img in confirm_image_details],
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
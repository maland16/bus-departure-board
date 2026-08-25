from PIL import Image
from playwright.sync_api import sync_playwright
import logging

logger = logging.getLogger(__name__)

# Open the BMP image file

STOP_ID = "147"
TRANSIT_TRACKER_URL = 'https://transit.ucop.me/stops/'
TEMP_PNG_PATH = "temp.png"
OUTPUT_BMP_PATH = "temp.bmp"

def main():
    logging.basicConfig(format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)
    logger.info('Started')
    capture_screenshot(TRANSIT_TRACKER_URL + STOP_ID, TEMP_PNG_PATH)
    convert_image_to_grayscale()

def capture_screenshot(url, output_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context( viewport={'width': 480, 'height': 800} ) # use device_scale_factor=2.0 to upscale
        page = context.new_page()

        try:
            page.goto(url)
            page.screenshot(path=output_path, full_page=True)
        except Exception as e:
            logger.error("Unable to capture webpage!")
            logger.exception(e)
            
        browser.close()

def convert_image_to_grayscale():
    try:
        image = Image.open(TEMP_PNG_PATH)
    except FileNotFoundError as e:
        logger.error("No screenshot found!")
        logger.exception(e)
    else:
        CROP_OUT_HEADER = 180

        cropped_image = image.crop((0, CROP_OUT_HEADER, 480, 700 + CROP_OUT_HEADER))
        grayscale_image = cropped_image.convert('L') # Luminance
        grayscale_image.save(OUTPUT_BMP_PATH)
        logger.info(f"Image successfully converted to grayscale and saved as {OUTPUT_BMP_PATH}")

if __name__ == '__main__':
    main()
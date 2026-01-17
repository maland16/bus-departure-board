from PIL import Image
from playwright.sync_api import sync_playwright
import logging

logger = logging.getLogger(__name__)

# Open the BMP image file


TRANSIT_TRACKER_URL = 'https://transit.ucop.me/stops/147'
TEMP_PNG_PATH = "temp.png"
OUTPUT_BMP_PATH = "temp.bmp"



def main():
    logging.basicConfig(filename='bmpGen.log', format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)
    logger.info('Started')
    capture_screenshot(TRANSIT_TRACKER_URL, TEMP_PNG_PATH)
    convert_image_to_grayscale()
    logger.info(f"Image successfully converted to grayscale and saved as {OUTPUT_BMP_PATH}")


def capture_screenshot(url, output_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context( viewport={'width': 480, 'height': 800} ) # use device_scale_factor=2.0 to upscale
        page = context.new_page()
        page.goto(url)
        page.screenshot(path=output_path, full_page=True)
        browser.close()

def convert_image_to_grayscale():
    image = Image.open(TEMP_PNG_PATH)
    grayscale_image = image.convert('L') # Luminance
    grayscale_image.save(OUTPUT_BMP_PATH)


if __name__ == '__main__':
    main()
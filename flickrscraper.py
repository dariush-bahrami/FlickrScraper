import re
import sys
import urllib.parse
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
import typer
from PIL import Image
from playwright.sync_api import Request, Response, sync_playwright
from sweetlog import Logger, LoggingLevel


class ResponseHandler:
    def __init__(
        self,
        save_directory: Path,
        logger: Logger,
        min_image_size: int,
        max_image_size: int,
    ):
        self.save_directory = save_directory
        self.logger = logger
        self.min_image_size = min_image_size
        self.max_image_size = max_image_size
        self.scraped_image_hashes = set()

    @property
    def scrape_counter(self):
        return len(self.scraped_image_hashes)

    def __call__(self, response: Response):
        request: Request = response.request
        pattern = r"https://live.staticflickr.com/.*"
        # check if request method is "GET" and url matches pattern
        if request.method == "GET" and re.match(pattern, request.url):
            # check if response content type is "image/jpeg"
            if response.headers.get("content-type") == "image/jpeg":
                # get image data
                image_data = response.body()
                # generate file name from image data
                image_hash = sha256(image_data).hexdigest()
                if image_hash not in self.scraped_image_hashes:
                    file_name = f"{image_hash}.jpg"
                    # decode image data
                    image = Image.open(BytesIO(image_data))
                    # check if the image size is within the range
                    if (min(image.size) >= self.min_image_size) and (
                        max(image.size) <= self.max_image_size
                    ):
                        # save image
                        image.save(self.save_directory / file_name)
                        self.logger.info(
                            f"Saved {request.url.split('/')[-1]} as {file_name}"
                        )
                        self.scraped_image_hashes.add(image_hash)


def main(
    query: str,
    number_of_images: int,
    patience: int = 100,
    timeout_seconds: int = 60,
    root_directory: str = "flickr_images",
    seed: int = 42,
    min_image_size: int = 128,
    max_image_size: int = 4096,
    headless: bool = True,
):
    timeout = timeout_seconds * 1000  # ms

    root_directory = Path(root_directory)
    directory = root_directory / query
    directory.mkdir(exist_ok=True, parents=True)

    url = f"https://www.flickr.com/search/?text={query}&view_all=1"
    url = urllib.parse.quote(url, safe=":/?&=")

    rng = np.random.default_rng(seed)

    logger = Logger([sys.stdout], level=LoggingLevel.DEBUG)
    logger.info("Starting Flickr scraper")
    response_handler = ResponseHandler(
        directory, logger, min_image_size, max_image_size
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        page.on("response", response_handler)

        logger.info(f"Going to {url}")
        page.goto(url, timeout=timeout)

        done = False
        patience_counter = 0
        while not done:
            current_scrape_counter = response_handler.scrape_counter
            # wait for page to load based on network idle
            logger.debug("Waiting for page to load")
            page.wait_for_load_state("networkidle", timeout=timeout)
            # scroll up or down randomly (75% down, 25% up)
            if rng.random() < 0.75:
                logger.debug("Scrolling down")
                page.evaluate("window.scrollBy(0, window.innerHeight)")
            else:  # scroll up
                logger.debug("Scrolling up")
                page.evaluate("window.scrollBy(0, -window.innerHeight)")

            # if there is button with the text "Load more results" click on it
            if page.query_selector("text=Load more results") is not None:
                logger.info("Clicking on 'Load more results'")
                page.click("text=Load more results")

            # wait for a random amount of time between 1 and 2 seconds
            sleep_time = rng.integers(1000, 2000).item()
            logger.debug(f"Sleeping for {sleep_time} ms")
            page.wait_for_timeout(sleep_time)

            this_loop_scrapes = response_handler.scrape_counter - current_scrape_counter
            if this_loop_scrapes == 0:
                patience_counter += 1
                logger.debug(f"Patience counter: {patience_counter}")
            else:
                patience_counter = 0
            done = (response_handler.scrape_counter >= number_of_images) or (
                patience_counter >= patience
            )

        # close the browser
        browser.close()


if __name__ == "__main__":
    typer.run(main)

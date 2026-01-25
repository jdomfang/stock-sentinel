"""Weekly ticker refresh script for cron usage."""

import logging

from utils.finance import download_ticker_master_list


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )
    logging.getLogger(__name__).info("Starting weekly ticker refresh job...")
    download_ticker_master_list()


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""End-to-end test: download a children's book from Archive.org and run the import pipeline.

Downloads page images from the Karim Series (كريم في الحديقة العامة), then runs
the full book import pipeline: cover metadata → OCR → cleanup → translate → story creation.

Usage:
    # Full pipeline (requires GEMINI_KEY)
    python3 scripts/test_book_import_e2e.py

    # Download only (no API keys needed)
    python3 scripts/test_book_import_e2e.py --download-only

    # Use already-downloaded images
    python3 scripts/test_book_import_e2e.py --images-dir /tmp/claude/book_test

    # Limit pages for faster/cheaper test
    python3 scripts/test_book_import_e2e.py --max-pages 5
"""

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

import requests
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Archive.org Karim Series - book #2 "كريم في الحديقة العامة" (Karim in the Public Garden)
ARCHIVE_ITEM = "Ar274_WH1"
BOOK_ZIP = "02-كريم في الحديقة العامة_jp2.zip"
BOOK_TITLE = "كريم في الحديقة العامة"

DOWNLOAD_DIR = Path("/tmp/claude/book_test")


def download_book_images(output_dir: Path, max_pages: int | None = None) -> list[Path]:
    """Download JP2 page images from Archive.org and convert to JPEG."""
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_url = f"https://archive.org/download/{ARCHIVE_ITEM}/{requests.utils.quote(BOOK_ZIP)}"
    logger.info(f"Downloading {BOOK_ZIP} from Archive.org...")

    resp = requests.get(zip_url, timeout=120)
    resp.raise_for_status()
    logger.info(f"Downloaded {len(resp.content) / 1024 / 1024:.1f} MB")

    # Extract JP2 files from zip
    image_paths = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        jp2_files = sorted(
            [n for n in zf.namelist() if n.lower().endswith((".jp2", ".jpg", ".jpeg", ".png"))],
        )
        logger.info(f"Found {len(jp2_files)} image files in archive")

        if max_pages:
            jp2_files = jp2_files[: max_pages + 1]  # +1 for cover

        for i, name in enumerate(jp2_files):
            data = zf.read(name)
            # Convert JP2 to JPEG for broader compatibility
            try:
                img = Image.open(io.BytesIO(data))
                out_path = output_dir / f"page_{i:03d}.jpg"
                img.save(out_path, "JPEG", quality=85)
                image_paths.append(out_path)
                logger.info(f"  Page {i}: {img.size[0]}x{img.size[1]} → {out_path.name}")
            except Exception as e:
                logger.warning(f"  Failed to convert {name}: {e}")

    logger.info(f"Saved {len(image_paths)} pages to {output_dir}")
    return image_paths


def load_images_from_dir(images_dir: Path) -> list[Path]:
    """Load already-downloaded images from directory."""
    paths = sorted(images_dir.glob("page_*.jpg"))
    if not paths:
        paths = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
    logger.info(f"Found {len(paths)} images in {images_dir}")
    return paths


def run_import_pipeline(image_paths: list[Path]) -> None:
    """Run the full book import pipeline against downloaded images."""
    # Setup app context
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("TESTING", "1")

    # Use a fresh test DB to avoid migration issues with local dev DB
    test_db_path = Path("/tmp/claude/book_test/test.db")
    test_db_path.unlink(missing_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db_path}"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, Sentence, SentenceWord, Story, StoryWord

    engine = create_engine(f"sqlite:///{test_db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        from app.services.book_import_service import import_book

        # Read image bytes
        cover_bytes = image_paths[0].read_bytes()
        page_bytes_list = [p.read_bytes() for p in image_paths[1:]]

        logger.info("=" * 60)
        logger.info(f"PIPELINE START: {len(image_paths)} images (1 cover + {len(page_bytes_list)} pages)")
        logger.info("=" * 60)

        t0 = time.time()
        story = import_book(
            db=db,
            cover_image=cover_bytes,
            page_images=page_bytes_list,
        )
        total_time = time.time() - t0
        logger.info(f"Total pipeline time: {total_time:.1f}s")

        # Results summary
        logger.info("\n" + "=" * 60)
        logger.info("RESULTS")
        logger.info("=" * 60)
        logger.info(f"  Story ID:      {story.id}")
        logger.info(f"  Title (AR):    {story.title_ar}")
        logger.info(f"  Title (EN):    {story.title_en}")
        logger.info(f"  Source:         {story.source}")
        logger.info(f"  Page count:    {story.page_count}")
        logger.info(f"  Total words:   {story.total_words}")
        logger.info(f"  Known words:   {story.known_count}")
        logger.info(f"  Readiness:     {story.readiness_pct:.1f}%")

        db_sentences = db.query(Sentence).filter_by(story_id=story.id).all()
        logger.info(f"  Sentences:     {len(db_sentences)}")

        story_words = db.query(StoryWord).filter_by(story_id=story.id).all()
        logger.info(f"  Story words:   {len(story_words)}")

        total_sw = 0
        for sent in db_sentences:
            sw_count = db.query(SentenceWord).filter_by(sentence_id=sent.id).count()
            total_sw += sw_count
        logger.info(f"  SentenceWords: {total_sw}")

        logger.info("\n--- Sample Sentences ---")
        for sent in db_sentences[:5]:
            logger.info(f"  [{sent.id}] {sent.arabic_diacritized[:60]}...")
            logger.info(f"         → {sent.english_translation[:60] if sent.english_translation else '(no translation)'}...")

        logger.info("\nPipeline complete!")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Test book import with Archive.org data")
    parser.add_argument("--download-only", action="store_true", help="Only download, don't run pipeline")
    parser.add_argument("--images-dir", type=Path, help="Use existing images directory")
    parser.add_argument("--max-pages", type=int, default=8, help="Max content pages to process (default: 8)")
    args = parser.parse_args()

    if args.images_dir:
        image_paths = load_images_from_dir(args.images_dir)
    else:
        image_paths = download_book_images(DOWNLOAD_DIR, max_pages=args.max_pages)

    if args.download_only:
        logger.info("Download complete. Use --images-dir to run pipeline on these images.")
        return

    if len(image_paths) < 2:
        logger.error("Need at least 2 images (cover + 1 page)")
        sys.exit(1)

    run_import_pipeline(image_paths)


if __name__ == "__main__":
    main()

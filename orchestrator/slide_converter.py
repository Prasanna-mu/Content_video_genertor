import os
import time
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

from config.settings import settings
from utils.filesystem import ensure_dir
from utils.logging import get_logger


logger = get_logger("slide_converter")


@dataclass
class SlideImageResult:
    slide_number: int
    image_path: str
    width: int
    height: int
    success: bool
    error: Optional[str] = None


class SlideConverter:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.output_base_dir = settings.PROCESSING_DIR / "slide_images" / session_id

    def convert_pptx_to_images(self, ppt_path: Path, ppt_job_id: str) -> List[SlideImageResult]:
        """Convert PPTX slides to high-quality PNG images using PowerPoint COM automation."""
        output_dir = self.output_base_dir / ppt_job_id
        ensure_dir(output_dir)

        results = []
        ppt_app = None
        presentation = None

        try:
            import pythoncom
            pythoncom.CoInitialize()
            
            import win32com.client
            ppt_app = win32com.client.DispatchEx("PowerPoint.Application")

            presentation = ppt_app.Presentations.Open(
                str(ppt_path.absolute()),
                ReadOnly=True,
                Untitled=True,
                WithWindow=False
            )

            total_slides = presentation.Slides.Count
            logger.info(f"[{self.session_id}] Found {total_slides} slides in {ppt_path.name}")

            for i in range(1, total_slides + 1):
                try:
                    slide = presentation.Slides(i)
                    image_filename = f"slide_{i:03d}.png"
                    image_path = output_dir / image_filename

                    slide.Export(
                        str(image_path.absolute()),
                        "PNG",
                        1920,
                        1080
                    )

                    time.sleep(0.2)

                    if image_path.exists() and image_path.stat().st_size > 0:
                        try:
                            from PIL import Image
                            with Image.open(image_path) as img:
                                width, height = img.size
                        except Exception:
                            width, height = 1920, 1080

                        logger.info(f"[{self.session_id}] Exported slide {i} to {image_path}")
                        results.append(SlideImageResult(
                            slide_number=i,
                            image_path=str(image_path),
                            width=width,
                            height=height,
                            success=True
                        ))
                    else:
                        error_msg = f"Export failed - file not created or empty: {image_path}"
                        logger.error(f"[{self.session_id}] {error_msg}")
                        results.append(SlideImageResult(
                            slide_number=i,
                            image_path="",
                            width=0,
                            height=0,
                            success=False,
                            error=error_msg
                        ))

                except Exception as e:
                    error_msg = f"Failed to export slide {i}: {e}"
                    logger.error(f"[{self.session_id}] {error_msg}")
                    results.append(SlideImageResult(
                        slide_number=i,
                        image_path="",
                        width=0,
                        height=0,
                        success=False,
                        error=error_msg
                    ))

        except Exception as e:
            error_msg = f"PowerPoint automation initialization failed: {e}"
            logger.error(f"[{self.session_id}] {error_msg}")
            results = [SlideImageResult(
                slide_number=i,
                image_path="",
                width=0,
                height=0,
                success=False,
                error=error_msg
            ) for i in range(1, 100)]
        finally:
            if presentation:
                try:
                    presentation.Close()
                except Exception:
                    pass
            if ppt_app:
                try:
                    ppt_app.Quit()
                except Exception:
                    pass

            time.sleep(0.5)

        successful = sum(1 for r in results if r.success)
        logger.info(f"[{self.session_id}] Conversion complete: {successful}/{len(results)} slides successful")
        return results

    def validate_slide_image(self, image_path: str) -> bool:
        """Validate that a slide image exists and is a valid PNG."""
        try:
            path = Path(image_path)
            if not path.exists():
                logger.warning(f"[{self.session_id}] Slide image not found: {image_path}")
                return False

            if path.stat().st_size == 0:
                logger.warning(f"[{self.session_id}] Slide image is empty: {image_path}")
                return False

            from PIL import Image
            with Image.open(path) as img:
                img.verify()

            with Image.open(path) as img:
                if img.width < 100 or img.height < 100:
                    logger.warning(f"[{self.session_id}] Slide image too small: {img.width}x{img.height}")
                    return False

            return True
        except Exception as e:
            logger.error(f"[{self.session_id}] Slide image validation failed for {image_path}: {e}")
            return False

    def get_existing_slide_images(self, ppt_job_id: str) -> List[SlideImageResult]:
        """Get already converted slide images for a PPT job (for resumability)."""
        output_dir = self.output_base_dir / ppt_job_id
        if not output_dir.exists():
            return []

        results = []
        image_files = sorted(output_dir.glob("slide_*.png"))

        for image_file in image_files:
            try:
                slide_num = int(image_file.stem.split("_")[1])
                from PIL import Image
                with Image.open(image_file) as img:
                    width, height = img.size

                results.append(SlideImageResult(
                    slide_number=slide_num,
                    image_path=str(image_file),
                    width=width,
                    height=height,
                    success=True
                ))
            except Exception as e:
                logger.warning(f"[{self.session_id}] Failed to read existing image {image_file}: {e}")

        return results

    def convert_missing_slides(self, ppt_path: Path, ppt_job_id: str, expected_slide_count: int) -> List[SlideImageResult]:
        """Convert only missing slides (resumable conversion)."""
        existing = self.get_existing_slide_images(ppt_job_id)
        existing_numbers = {r.slide_number for r in existing if r.success}

        missing_numbers = [i for i in range(1, expected_slide_count + 1) if i not in existing_numbers]

        if not missing_numbers:
            logger.info(f"[{self.session_id}] All {expected_slide_count} slides already converted for {ppt_job_id}")
            return existing

        logger.info(f"[{self.session_id}] Converting {len(missing_numbers)} missing slides for {ppt_job_id}")

        all_results = self.convert_pptx_to_images(ppt_path, ppt_job_id)
        return all_results


def convert_slides_for_job(session_id: str, ppt_path: Path, ppt_job_id: str, expected_slide_count: int) -> List[SlideImageResult]:
    """Convenience function to convert slides for a PPT job."""
    converter = SlideConverter(session_id)
    return converter.convert_missing_slides(ppt_path, ppt_job_id, expected_slide_count)


def validate_slide_image(image_path: str, session_id: str) -> bool:
    """Convenience function to validate a slide image."""
    converter = SlideConverter(session_id)
    return converter.validate_slide_image(image_path)
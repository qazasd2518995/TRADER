"""
OCR Service (cross-platform)
Supports RapidOCR (recommended), PaddleOCR, native platform OCR, and Tesseract.
Optimized for Chinese/English mixed text recognition.
"""
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Try RapidOCR first (best balance of accuracy + lightweight)
RAPID_AVAILABLE = False
try:
    from rapidocr import RapidOCR as _RapidOCR
    from rapidocr.utils.typings import OCRVersion as _RapidOCRVersion
    from rapidocr.utils.typings import ModelType as _RapidModelType
    RAPID_AVAILABLE = True
except ImportError:
    pass

# Try PaddleOCR (best Chinese OCR, but heavy)
PADDLE_AVAILABLE = False
try:
    from paddleocr import PaddleOCR as _PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    pass

# Try platform-native OCR
NATIVE_OCR_AVAILABLE = False
if sys.platform == "win32":
    try:
        import asyncio
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile, FileAccessMode
        NATIVE_OCR_AVAILABLE = True
    except ImportError:
        pass
elif sys.platform == "darwin":
    try:
        import Vision
        import Quartz
        NATIVE_OCR_AVAILABLE = True
    except ImportError:
        pass

# Fallback to Tesseract
TESSERACT_AVAILABLE = False
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    pass


class OCRService:
    """OCR service with RapidOCR as primary, PaddleOCR/native/Tesseract as fallbacks."""

    def __init__(self, languages: list = None):
        self.languages = languages or ["zh-Hant-TW", "zh-Hans-CN", "en-US"]
        self._paddle_ocr = None
        self._rapid_ocr = None
        self._tesseract_ready = False

        if RAPID_AVAILABLE:
            self.engine = "rapid"
            logger.info("Using RapidOCR 3.x (recommended)")
        elif PADDLE_AVAILABLE:
            self.engine = "paddle"
            logger.info("Using PaddleOCR (best Chinese accuracy)")
        elif NATIVE_OCR_AVAILABLE:
            self.engine = "native"
            if sys.platform == "darwin":
                logger.info("Using macOS Vision Framework OCR")
            else:
                logger.info("Using Windows native OCR (WinRT)")
        elif TESSERACT_AVAILABLE:
            self._tesseract_ready = self._setup_tesseract()
            if self._tesseract_ready:
                self.engine = "tesseract"
                logger.info("Using Tesseract for OCR")
            else:
                raise RuntimeError(
                    "pytesseract is installed, but tesseract was not found.\n"
                    "Install Tesseract-OCR or enable another OCR engine."
                )
        else:
            raise RuntimeError(
                "No OCR engine available.\n"
                "Install one of:\n"
                "  1. pip install rapidocr onnxruntime  (RapidOCR - recommended)\n"
                "  2. pip install paddlepaddle paddleocr  (PaddleOCR)\n"
                "  3. pip install pyobjc-framework-Vision  (macOS native OCR)\n"
                "     or pip install winsdk  (Windows native OCR)\n"
                "  4. pip install pytesseract Pillow  (+ install Tesseract-OCR)"
            )

    def _get_rapid_ocr(self):
        """Lazy-initialize RapidOCR on first use."""
        if self._rapid_ocr is None:
            logger.info("Loading RapidOCR model...")
            self._rapid_ocr = _RapidOCR(
                params={
                    "Global.log_level": "info",
                    "Det.ocr_version": _RapidOCRVersion.PPOCRV5,
                    "Det.model_type": _RapidModelType.MOBILE,
                    "Rec.ocr_version": _RapidOCRVersion.PPOCRV5,
                    "Rec.model_type": _RapidModelType.MOBILE,
                }
            )
            logger.info("RapidOCR model loaded")
        return self._rapid_ocr

    def _collect_rapid_text_lines(self, result) -> list[str]:
        """Extract recognized text lines from RapidOCR 3.x output."""
        if not result:
            return []

        txts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if txts is not None:
            lines = []
            for i, text in enumerate(txts):
                score = scores[i] if scores and i < len(scores) else 0
                if score >= 0.3:
                    lines.append(text)
            return lines

        return []

    def _get_paddle_ocr(self):
        """Lazy-initialize PaddleOCR on first use."""
        if self._paddle_ocr is None:
            logger.info("Loading PaddleOCR model (first time, may take a few seconds)...")
            import os
            os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
            os.environ['FLAGS_use_mkldnn'] = '0'
            self._paddle_ocr = _PaddleOCR(
                lang="chinese_cht",
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_detection_model_name='PP-OCRv5_mobile_det',
                text_recognition_model_name='PP-OCRv5_mobile_rec',
            )
            logger.info("PaddleOCR model loaded")
        return self._paddle_ocr

    def _setup_tesseract(self):
        """Setup Tesseract path (cross-platform)."""
        import shutil

        # Try platform layer first
        try:
            from copy_trader.platform import PlatformConfig
            path = PlatformConfig().get_tesseract_path()
            if path:
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"Tesseract found at: {path}")
                return True
        except ImportError:
            pass

        # Fallback: shutil.which
        tesseract_path = shutil.which("tesseract")
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            return True

        logger.warning("Tesseract not found.")
        return False

    def _prepare_image_array(self, image_path: str, crop_bottom_ratio: float = 1.0):
        """Load, optionally crop, and downscale images before OCR."""
        from PIL import Image as PILImage
        import numpy as np

        img = PILImage.open(image_path).convert("RGB")
        w, h = img.size

        if crop_bottom_ratio < 1.0:
            top = int(h * (1.0 - crop_bottom_ratio))
            img = img.crop((0, top, w, h))
            logger.debug(f"Cropped image to bottom {crop_bottom_ratio:.0%}: {w}x{h} -> {w}x{h - top}")

        max_width = 1600
        max_height = 900
        scale = min(max_width / img.width, max_height / img.height, 1.0)
        if scale < 1.0:
            new_size = (
                max(1, int(img.width * scale)),
                max(1, int(img.height * scale)),
            )
            img = img.resize(new_size, PILImage.Resampling.LANCZOS)
            logger.debug(f"Downscaled OCR image: {w}x{h} -> {new_size[0]}x{new_size[1]}")

        return np.asarray(img, dtype=np.uint8)

    def extract_newest_bubble_text(self, image_path: str) -> str:
        """
        OCR the full image, then use Y-coordinates to keep only the
        newest (bottom-most) text lines. This avoids reading old signals
        at the top of the chat without needing OpenCV shape detection.

        Falls back to full text if position filtering fails.
        """
        if self.engine != "rapid":
            return self.extract_text(image_path)

        try:
            from PIL import Image as PILImage
            from .bubble_detector import BubbleDetector

            ocr = self._get_rapid_ocr()
            input_data = self._prepare_image_array(image_path)
            result = ocr(input_data)

            # Use the PROCESSED image height (after downscale), not original
            img_h = input_data.shape[0]

            # Use BubbleDetector to filter to newest lines by Y position
            detector = BubbleDetector()
            texts, line_infos = detector.get_newest_lines_from_ocr(result, img_h)

            if texts:
                text = " ".join(texts).strip()
                logger.debug(f"Newest lines OCR: {len(line_infos)}/{len(getattr(result, 'txts', []))} lines → {len(text)} chars")
                return text

        except Exception as e:
            logger.debug(f"Newest-line extraction failed: {e}")

        # Fallback: return all text
        return self.extract_text(image_path)

    def extract_text(self, image_path: str, crop_bottom_ratio: float = 1.0) -> str:
        """
        Extract text from image.

        Args:
            image_path: Path to screenshot image
            crop_bottom_ratio: Crop to bottom N% of image (0.5 = bottom 50%).
                              Use < 1.0 for faster OCR on confirmation reads.
        """
        if not Path(image_path).exists():
            logger.error(f"Image not found: {image_path}")
            return ""

        if self.engine == "rapid":
            return self._extract_with_rapid(image_path, crop_bottom_ratio)
        elif self.engine == "paddle":
            return self._extract_with_paddle(image_path, crop_bottom_ratio)
        elif self.engine == "native":
            if sys.platform == "darwin":
                return self._extract_with_vision(image_path)
            else:
                return self._extract_with_winrt(image_path)
        else:
            return self._extract_with_tesseract(image_path)

    def _extract_with_rapid(self, image_path: str, crop_bottom_ratio: float = 1.0) -> str:
        """Extract text using RapidOCR (ONNX Runtime)."""
        try:
            ocr = self._get_rapid_ocr()
            input_data = self._prepare_image_array(image_path, crop_bottom_ratio)

            result = ocr(input_data)
            lines = self._collect_rapid_text_lines(result)
            if not lines:
                return ""

            text = " ".join(lines)
            return text.strip()

        except Exception as e:
            logger.error(f"RapidOCR error: {e}", exc_info=True)
            # Fallback chain
            if PADDLE_AVAILABLE:
                logger.info("Falling back to PaddleOCR")
                return self._extract_with_paddle(image_path, crop_bottom_ratio)
            elif NATIVE_OCR_AVAILABLE:
                logger.info("Falling back to native OCR")
                if sys.platform == "darwin":
                    return self._extract_with_vision(image_path)
                return self._extract_with_winrt(image_path)
            elif TESSERACT_AVAILABLE and self._tesseract_ready:
                logger.info("Falling back to Tesseract")
                return self._extract_with_tesseract(image_path)
            return ""

    def _extract_with_paddle(self, image_path: str, crop_bottom_ratio: float = 1.0) -> str:
        """Extract text using PaddleOCR v3.4."""
        try:
            ocr = self._get_paddle_ocr()
            input_data = self._prepare_image_array(image_path, crop_bottom_ratio)

            result = ocr.predict(input_data, return_word_box=False)

            if not result or len(result) == 0:
                return ""

            # PaddleOCR v3.4 returns list of OCRResult objects (dict-like)
            # Each has 'rec_texts' (list[str]) and 'rec_scores' (list[float])
            lines = []
            for item in result:
                rec_texts = item.get('rec_texts', [])
                rec_scores = item.get('rec_scores', [])
                for i, text in enumerate(rec_texts):
                    score = rec_scores[i] if i < len(rec_scores) else 0
                    if score >= 0.3:  # Filter very low confidence
                        lines.append(text)

            text = " ".join(lines)
            return text.strip()

        except Exception as e:
            logger.error(f"PaddleOCR error: {e}", exc_info=True)
            # Fallback
            if NATIVE_OCR_AVAILABLE:
                logger.info("Falling back to native OCR")
                if sys.platform == "darwin":
                    return self._extract_with_vision(image_path)
                return self._extract_with_winrt(image_path)
            elif TESSERACT_AVAILABLE and self._tesseract_ready:
                logger.info("Falling back to Tesseract")
                return self._extract_with_tesseract(image_path)
            return ""

    def _extract_with_winrt(self, image_path: str) -> str:
        """Extract text using Windows native OCR (WinRT)."""
        try:
            import asyncio
            import threading

            async def _ocr():
                abs_path = str(Path(image_path).resolve())
                file = await StorageFile.get_file_from_path_async(abs_path)
                stream = await file.open_async(FileAccessMode.READ)
                decoder = await BitmapDecoder.create_async(stream)
                bitmap = await decoder.get_software_bitmap_async()

                for lang_tag in self.languages:
                    try:
                        lang = Language(lang_tag)
                        if OcrEngine.is_language_supported(lang):
                            engine = OcrEngine.try_create_from_language(lang)
                            if engine:
                                result = await engine.recognize_async(bitmap)
                                text = result.text
                                if text and text.strip():
                                    return text
                    except Exception:
                        continue

                engine = OcrEngine.try_create_from_user_profile_languages()
                if engine:
                    result = await engine.recognize_async(bitmap)
                    return result.text
                return ""

            result = [None]
            error = [None]

            def _run_in_thread():
                loop = asyncio.new_event_loop()
                try:
                    result[0] = loop.run_until_complete(_ocr())
                except Exception as e:
                    error[0] = e
                finally:
                    loop.close()

            thread = threading.Thread(target=_run_in_thread)
            thread.start()
            thread.join(timeout=10)

            if error[0]:
                raise error[0]
            return result[0] or ""

        except Exception as e:
            logger.error(f"WinRT OCR error: {e}")
            if TESSERACT_AVAILABLE and self._tesseract_ready:
                logger.info("Falling back to Tesseract")
                return self._extract_with_tesseract(image_path)
            return ""

    def _extract_with_vision(self, image_path: str) -> str:
        """Extract text using macOS Vision Framework."""
        try:
            from Foundation import NSURL
            from Quartz import (
                CGImageSourceCreateWithURL,
                CGImageSourceCreateImageAtIndex,
            )
            from Vision import (
                VNRecognizeTextRequest,
                VNImageRequestHandler,
            )

            file_url = NSURL.fileURLWithPath_(str(Path(image_path).resolve()))
            image_source = CGImageSourceCreateWithURL(file_url, None)
            if image_source is None:
                logger.error(f"Failed to load image: {image_path}")
                return ""

            cg_image = CGImageSourceCreateImageAtIndex(image_source, 0, None)
            if cg_image is None:
                return ""

            handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
            request = VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLanguages_(["zh-Hant", "zh-Hans", "en"])
            request.setRecognitionLevel_(1)  # VNRequestTextRecognitionLevelAccurate
            request.setUsesLanguageCorrection_(True)

            success, error = handler.performRequests_error_([request], None)
            if not success or error:
                logger.error(f"Vision OCR error: {error}")
                return ""

            results = request.results()
            if not results:
                return ""

            lines = []
            for observation in results:
                candidate = observation.topCandidates_(1)
                if candidate and len(candidate) > 0:
                    text = candidate[0].string()
                    confidence = candidate[0].confidence()
                    if confidence >= 0.3:
                        lines.append(text)

            return " ".join(lines).strip()

        except Exception as e:
            logger.error(f"Vision Framework OCR error: {e}")
            if TESSERACT_AVAILABLE and self._tesseract_ready:
                logger.info("Falling back to Tesseract")
                return self._extract_with_tesseract(image_path)
            return ""

    def _extract_with_tesseract(self, image_path: str) -> str:
        """Extract text using Tesseract."""
        try:
            image = Image.open(image_path)
            lang = "chi_tra+chi_sim+eng"
            text = pytesseract.image_to_string(image, lang=lang)
            return text.strip()
        except Exception as e:
            logger.error(f"Tesseract OCR error: {e}")
            return ""


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 1:
        print(f"Testing OCR on: {sys.argv[1]}")
        service = OCRService()
        print(f"Engine: {service.engine}")
        text = service.extract_text(sys.argv[1])
        print(f"\n--- Extracted Text ---\n{text}\n--- End ---")
    else:
        print("Usage: python ocr.py <image_path>")

"""
Chat Message Region Detector
Uses OCR bounding box Y-coordinates to identify the newest messages
in a chat screenshot. No OpenCV shape detection needed — the OCR engine
itself provides the position of each text line.
"""
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChatBubble:
    """A detected text region with position info."""
    x: int
    y: int
    w: int
    h: int
    text: str = ""
    score: float = 0.0
    side: str = "left"

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def bottom(self) -> int:
        return self.y + self.h


class BubbleDetector:
    """
    Extracts newest chat messages using OCR bounding box positions.

    RapidOCR returns each text line with its [x,y] coordinates.
    By sorting lines by Y position and taking the bottom ones,
    we get the newest messages without any OpenCV shape detection.
    """

    # What portion of the bottom to keep as "newest"
    # Increased from 0.45 to 0.60 to capture more recent messages,
    # reducing the chance of missing short messages like "撤" or "SL"
    BOTTOM_RATIO = 0.60

    # Skip lines in top N% (title bar) and bottom N% (input box)
    TOP_SKIP_RATIO = 0.08
    BOTTOM_SKIP_RATIO = 0.03

    def get_newest_lines_from_ocr(self, ocr_result, image_height: int) -> Tuple[List[str], List[dict]]:
        """
        Given a RapidOCR result, return only the bottom-most text lines.

        Args:
            ocr_result: RapidOCR result object with boxes, txts, scores
            image_height: Height of the original image

        Returns:
            (texts, line_infos) where texts are the newest lines joined,
            and line_infos contain position details
        """
        boxes = getattr(ocr_result, 'boxes', None)
        txts = getattr(ocr_result, 'txts', None)
        scores = getattr(ocr_result, 'scores', None)

        if boxes is None or txts is None or len(txts) == 0:
            return [], []

        # Build list of (y_center, text, score, box)
        lines = []
        for i, (box, txt, score) in enumerate(zip(boxes, txts, scores)):
            if score < 0.3:
                continue
            # box = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            y_center = (box[0][1] + box[2][1]) / 2
            y_min = min(p[1] for p in box)
            y_max = max(p[1] for p in box)

            # Skip title bar area
            if y_center < image_height * self.TOP_SKIP_RATIO:
                continue
            # Skip input box area
            if y_center > image_height * (1 - self.BOTTOM_SKIP_RATIO):
                continue

            lines.append({
                'y': y_center,
                'y_min': y_min,
                'y_max': y_max,
                'text': txt,
                'score': score,
                'box': box,
            })

        if not lines:
            return [], []

        # Sort by Y position (top to bottom)
        lines.sort(key=lambda l: l['y'])

        # Find the cutoff: keep bottom BOTTOM_RATIO of the chat area
        min_y = lines[0]['y']
        max_y = lines[-1]['y']
        chat_range = max_y - min_y
        if chat_range <= 0:
            return [l['text'] for l in lines], lines

        cutoff_y = max_y - (chat_range * self.BOTTOM_RATIO)

        # Keep lines below the cutoff
        newest = [l for l in lines if l['y'] >= cutoff_y]

        # Insert message boundary markers ("|MSG|") between lines with large Y gaps.
        # In LINE chat, different messages have visible gaps between bubbles.
        # This helps the regex parser split text from different senders/messages
        # and prevents SL/TP from one message being mixed with another.
        texts = []
        for i, line in enumerate(newest):
            if i > 0:
                gap = line['y_min'] - newest[i - 1]['y_max']
                # A gap > 2% of image height likely indicates a message boundary
                if gap > image_height * 0.02:
                    texts.append("|MSG|")
            texts.append(line['text'])

        logger.debug(
            f"OCR lines: {len(lines)} total, {len(newest)} newest "
            f"(cutoff y={cutoff_y:.0f}, range={min_y:.0f}-{max_y:.0f})"
        )

        return texts, newest

    # Legacy interface for compatibility with ocr.py
    def detect_bubbles(self, image_path: str) -> List[ChatBubble]:
        return []

    def get_newest_bubbles(self, image_path: str, count: int = None) -> List[ChatBubble]:
        return []

    def extract_newest_text_region(self, image_path: str):
        """Legacy interface — returns (None, []) to trigger fallback."""
        return None, []

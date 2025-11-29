import cv2
import numpy as np
from PIL import Image

def decode_qr_image(image_buffer):
    """Decodes a QR code from a streamlit camera buffer."""
    if image_buffer is None:
        return None

    # Convert the file buffer to an OpenCV image
    file_bytes = np.asarray(bytearray(image_buffer.read()), dtype=np.uint8)
    img = cv2.imdecode(file_bytes, 1)

    # Initialize detector
    detector = cv2.QRCodeDetector()

    try:
        # Detect and decode
        data, bbox, _ = detector.detectAndDecode(img)
        if data:
            return data
    except Exception:
        pass
    return None
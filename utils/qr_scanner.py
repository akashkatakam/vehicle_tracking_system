# utils/qr_scanner.py
import streamlit as st
import cv2
import numpy as np
from streamlit_webrtc import VideoTransformerBase, webrtc_streamer

class QrCodeTransformer(VideoTransformerBase):
    """
    This class processes video frames to find QR codes
    using OpenCV's built-in detector.
    """
    def __init__(self, key_to_update):
        self.key_to_update = key_to_update
        self.last_detected_code = None
        # Initialize the OpenCV QR Code detector
        self.detector = cv2.QRCodeDetector()

    def recv(self, frame):
        # Convert the frame to a format OpenCV can read
        img = frame.to_ndarray(format="bgr24")
        
        # Convert to grayscale
        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Detect and decode the QR code
        try:
            data, bbox, straight_qrcode = self.detector.detectAndDecode(gray_img)
            
            # data is the decoded string
            if data and data != self.last_detected_code:
                self.last_detected_code = data
                # Update session state with the new code
                st.session_state[self.key_to_update] = data
                
        except Exception as e:
            # Log errors if any (optional)
            # print(f"Error decoding QR: {e}")
            pass
            
        return frame

def qr_scanner_component(key: str, session_state_key: str):
    """
    Creates a QR code scanner component using streamlit-webrtc
    and OpenCV for decoding.
    
    :param key: A unique streamlit key for this component instance.
    :param session_state_key: The st.session_state key where the
                              scanned QR code value will be stored.
    """
    st.markdown(
        f"""
        <style>
            div[data-testid="stVideo{key}"] video {{
                object-fit: contain;
                border-radius: 5px;
                border: 1px solid #ddd;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    webrtc_streamer(
        key=key,
        video_transformer_factory=lambda: QrCodeTransformer(session_state_key),
        media_stream_constraints={"video": {"facingMode": "environment"}, "audio": False},
        async_processing=True,
    )
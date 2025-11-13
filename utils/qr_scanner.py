# utils/qr_scanner.py
import streamlit as st
import cv2
from pyzbar.pyzbar import decode
from streamlit_webrtc import VideoTransformerBase, webrtc_streamer

class QrCodeTransformer(VideoTransformerBase):
    """
    This class processes video frames to find QR codes.
    When a QR code is found, it writes the data to the
    Streamlit session state key provided.
    """
    def __init__(self, key_to_update):
        self.key_to_update = key_to_update
        self.last_detected_code = None

    def recv(self, frame):
        # Convert the frame to a format OpenCV can read
        img = frame.to_ndarray(format="bgr24")
        
        # Decode QR codes
        decoded_objects = decode(img)
        
        for obj in decoded_objects:
            qr_data = obj.data.decode("utf-8")
            
            # If it's a new code, update session state
            if qr_data and qr_data != self.last_detected_code:
                self.last_detected_code = qr_data
                st.session_state[self.key_to_update] = qr_data
                
        return frame

def qr_scanner_component(key: str, session_state_key: str):
    """
    Creates a QR code scanner component using streamlit-webrtc.
    
    :param key: A unique streamlit key for this component instance.
    :param session_state_key: The st.session_state key where the
                              scanned QR code value will be stored.
    """
    webrtc_streamer(
        key=key,
        video_transformer_factory=lambda: QrCodeTransformer(session_state_key),
        media_stream_constraints={"video": {"facingMode": "environment"}, "audio": False},
        async_processing=True,
    )
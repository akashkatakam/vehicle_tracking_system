import requests
import streamlit as st
from database import SessionLocal
from models import User, UserSession, Branch
import hashlib
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

# --- OTP FUNCTIONS (Unchanged) ---
def send_sms_otp(phone_number: str):
    """Sends the OTP via your chosen SMS Gateway."""
    try:
        api_key = st.secrets.sms_gateway.API_KEY
        api_url = st.secrets.sms_gateway.API_URL

        payload = {
            'api_key': api_key,
            'mobile': phone_number,
            'sender': st.secrets.sms_gateway.SENDER_ID,
        }
        
        response = requests.get(api_url, params=payload)
        response.raise_for_status()
        return response

    except requests.exceptions.RequestException as e:
        st.error(f"Failed to send OTP: {e}")
        return None

def verify_sms_otp(otp_details: str, otp_attempt: str) -> bool:
    """Verifies the OTP."""
    try:
        api_key = st.secrets.sms_gateway.API_KEY
        verify_url = st.secrets.sms_gateway.API_VERIFY_URL

        payload = {
            'api_key': api_key,
            'session_id': otp_details,
            'otp': otp_attempt
        }
        
        response = requests.get(verify_url, params=payload)
        response.raise_for_status()
        
        response_json = response.json()
        if response_json.get("status") == "success":
            return True
        else:
            st.error(f"OTP Verification Failed: {response_json.get('message', 'Invalid OTP')}")
            return False

    except requests.exceptions.RequestException as e:
        st.error(f"Error verifying OTP: {e}")
        return False

# --- SESSION FUNCTIONS ---

def create_user_session(db: Session, user_id: int,cookie_manager):
    """Generates a secure token, saves it to DB, and sets cookie."""
    
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # Set cookie expiry to 7 days from now
    expiry_date = datetime.now() + timedelta(days=7)
    
    new_session = UserSession(
        session_token_hash=token_hash,
        user_id=user_id,
        expiry_date=expiry_date
    )
    db.add(new_session)
    db.commit()
    
    # Set the cookie in the browser (expires in 7 days)
    cookie_manager.set('pdi_auth_token', token, expires_at=expiry_date)

def delete_user_session(db: Session,cookie_manager):
    """Deletes session from DB and browser."""
    token = cookie_manager.get('pdi_auth_token')
    
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        db.query(UserSession).filter(UserSession.session_token_hash == token_hash).delete()
        db.commit()
    
    cookie_manager.delete('pdi_auth_token')

def attempt_auto_login(cookie_manager):
    """
    Safely checks for a valid session token in cookies.
    """
    if st.session_state.get("inventory_logged_in", False):
        return
    
    # This call retrieves all cookies; we check for ours
    cookies = cookie_manager.get_all()
    token = cookies.get('pdi_auth_token')
    
    if not token:
        return

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    with SessionLocal() as db:
        session = db.query(UserSession).filter(
            UserSession.session_token_hash == token_hash
        ).first()
        
        if session and session.expiry_date > datetime.utcnow():
            user = db.query(User).filter(User.id == session.user_id).first()
            if user:
                st.session_state.inventory_logged_in = True
                st.session_state.inventory_user_role = user.role
                st.session_state.inventory_username = user.username
                st.session_state.inventory_branch_id = user.Branch_ID
                st.session_state.inventory_branch_name = get_branch_name(db, user.Branch_ID)
                return
        
        # If session invalid/expired in DB, clean up
        if session:
            db.delete(session)
            db.commit()
        
        # Only delete if it exists to avoid unnecessary reruns
        if token:
            cookie_manager.delete('pdi_auth_token')

def clear_otp_state():
    for key in ["otp_sent", "otp_details", "otp_value", "otp_user_data", "otp_expiry", "otp_user_id", "user_phone_number"]:
        if key in st.session_state:
            del st.session_state[key]

def get_branch_name(db: Session, branch_id: str) -> str:
    if not branch_id:
        return "All Branches"
    branch = db.query(Branch).filter(Branch.Branch_ID == branch_id).first()
    return branch.Branch_Name if branch else "N/A"
import streamlit as st
from database import SessionLocal
from models import User, UserSession, Branch
from streamlit_local_storage import LocalStorage
import hashlib
import secrets
import random
import requests
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

# --- 1. COOKIE MANAGER ---
# Initialize it here to be used by all other files
# --- 2. OTP FUNCTIONS ---
def send_sms_otp(phone_number: str) ->  dict:
    """Sends the OTP via your chosen SMS Gateway."""
    try:
        
        url = f'https://2factor.in/API/V1/{st.secrets.sms_gateway.API_KEY}/SMS/+91{phone_number}/AUTOGEN2/OTP1'

        payload={}
        headers = {}
        response = requests.request("GET", url, headers=headers, data=payload)
        
        if response.status_code == 200:
            st.success(f"OTP sent to ...{phone_number[-4:]}")
            return response
        else:
            st.error("Failed to send OTP.")
            return response
    except Exception as e:
        st.error(f"Error sending OTP: {e}")
        return response

def verify_sms_otp(details:str,otp:str):
    """Verifies the OTP"""
    url = f'https://2factor.in/API/V1/{st.secrets.sms_gateway.API_KEY}/SMS/VERIFY/{details}/{otp}'
    payload={}
    headers = {}

    response = requests.request("GET", url, headers=headers, data=payload)
    if response.status_code == 200:
        return True
    else:
        st.error("** OTP Verification failed **")

# --- 3. SESSION/COOKIE FUNCTIONS ---
def create_user_session(db: Session, user_id: int):
    """Generates a secure token, saves it to the DB, and sets the cookie."""
    storage = LocalStorage()
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expiry = datetime.now() + timedelta(days=7) # 7-day persistent login
    
    new_session = UserSession(
        session_token_hash=token_hash,
        user_id=user_id,
        expiry_date=expiry
    )
    db.add(new_session)
    db.commit()
    
    storage.setItem("pdi_auth_token", token)

def delete_user_session(db: Session):
    """Deletes the session from the DB and the cookie from the browser."""
    storage = LocalStorage()
    token = storage.getItem('pdi_auth_token')
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        db.query(UserSession).filter(UserSession.session_token_hash == token_hash).delete()
        db.commit()
    storage.deleteItem("pdi_auth_token")

def attempt_auto_login():
    """
    Safely checks for a valid session token in localStorage.
    """
    storage = LocalStorage()
    if st.session_state.get("inventory_logged_in", False):
        return  # Already logged in

    token = storage.getItem("pdi_auth_token")

    if not token:
        return  # No token, do nothing

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    with SessionLocal() as db:
        session = db.query(UserSession).filter(
            UserSession.session_token_hash == token_hash
        ).first()

        if session and session.expiry_date > datetime.now():
            # --- VALID SESSION FOUND ---
            user = db.query(User).filter(User.id == session.user_id).first()
            if user:
                # Repopulate session state
                st.session_state.inventory_logged_in = True
                st.session_state.inventory_user_role = user.role
                st.session_state.inventory_username = user.username
                st.session_state.inventory_branch_id = user.Branch_ID
                st.session_state.inventory_branch_name = get_branch_name(db, user.Branch_ID)
                return

        # If session is invalid or expired, delete it
        if session:
            db.delete(session)
            db.commit()
        storage.deleteItem("pdi_auth_token")


def get_branch_name(db: Session, branch_id: str) -> str:
    """Helper to get branch name during login."""
    if not branch_id:
        return "All Branches"
    branch = db.query(Branch).filter(Branch.Branch_ID == branch_id).first()
    return branch.Branch_Name

def clear_otp_state():
    """Cleans up all temporary OTP data from the session."""
    for key in ["otp_sent", "otp_details", "otp_value", "otp_user_data", "otp_expiry", "otp_user_id", "user_phone_number"]:
        if key in st.session_state:
            del st.session_state[key]
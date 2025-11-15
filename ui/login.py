# ui/login.py
import streamlit as st
from database import SessionLocal
from models import User
from datetime import datetime, timedelta

# --- Import all our auth logic ---
from utils.auth_utils import (
    send_sms_otp,
    verify_sms_otp,
    create_user_session,
    clear_otp_state,  # <-- Make sure this is imported
    get_branch_name
)

def render():
    """
    Renders the 2-step (Phone + OTP) login flow
    using pure Streamlit components.
    """
    
    # Center the login form
    col1, col2, col3 = st.columns([1, 1.2, 1])
    
    with col2:
        
        # --- Check if we are waiting for an OTP ---
        if not st.session_state.get("otp_sent", False):
            
            # --- STEP 1: SHOW PHONE NUMBER FORM ---
            st.header("PDI Ops Login")
            st.subheader("Step 1/2: Enter Mobile Number")
            
            with st.container(border=True):
                with st.form("pdi_login"):
                    st.text_input("Mobile Number", placeholder="Enter your 10-digit mobile number", key="login_phone_number", max_chars=10)
                    
                    if st.form_submit_button("Send OTP", use_container_width=True, type="primary"):
                        phone_number = st.session_state.login_phone_number
                        
                        user = None
                        with SessionLocal() as db:
                            # Query by phone number instead of username
                            user = db.query(User).filter(User.phone_number == phone_number).first()
                            
                            allowed_roles = ['Owner', 'PDI', 'Mechanic']
                            # Check if user exists and has the right role
                            if user and user.role in allowed_roles:
                                
                                # --- Credentials valid, send OTP ---
                                response = send_sms_otp(user.phone_number)
                                
                                # Check if the request was successful
                                if response and response.status_code == 200:
                                    response_data = response.json()
                                    
                                    # Store session details from the API response
                                    st.session_state.otp_sent = True
                                    st.session_state.otp_details = response_data.get('Details') # The session ID from your provider
                                    st.session_state.otp_user_id = user.id # Store for session creation
                                    st.session_state.user_phone_number = user.phone_number # Store for verification
                                    st.session_state.otp_user_data = {
                                        "role": user.role,
                                        "username": user.username,
                                        "user_branch_id": user.Branch_ID,
                                        "branch_name": get_branch_name(db, user.Branch_ID)
                                    }
                                    st.session_state.otp_expiry = datetime.now() + timedelta(minutes=5)
                                    st.rerun()
                                # 'send_sms_otp' will show its own error if 'response' is None
                            else:
                                st.error("No user found with this mobile number or role is not permitted.")
        
        else:
            # --- STEP 2: SHOW OTP FORM ---
            if datetime.now() > st.session_state.otp_expiry:
                st.error("OTP expired. Please try again.")
                clear_otp_state() # Clear the state
                st.rerun()
                
            st.header("PDI Ops Login")
            st.subheader("Step 2/2: Verify OTP")
            
            with st.container(border=True):
                with st.form("otp_form"):
                    st.info(f"An OTP has been sent to your registered mobile number ending in ...{st.session_state.user_phone_number[-4:]}")
                    otp_attempt = st.text_input("Enter 6-Digit OTP", max_chars=6)

                    if st.form_submit_button("Verify & Login", use_container_width=True, type="primary"):
                        
                        # --- FIX: Call verify with the user's ATTEMPT ---
                        if verify_sms_otp(st.session_state.otp_details, otp_attempt):
                            # --- SUCCESS! ---
                            st.success("Login Successful!")
                            
                            # Load user data we saved
                            user_data = st.session_state.otp_user_data
                            
                            # Set the real session state
                            st.session_state.inventory_logged_in = True
                            st.session_state.inventory_user_role = user_data["role"]
                            st.session_state.inventory_username = user_data["username"]
                            st.session_state.inventory_branch_id = user_data["user_branch_id"]
                            st.session_state.inventory_branch_name = user_data["branch_name"]

                            # Create the persistent session
                            user_id = st.session_state.otp_user_id
                            with SessionLocal() as db:
                                create_user_session(db, user_id)
                            
                            clear_otp_state() # Clear OTP state on success
                            st.rerun()
                        # 'verify_sms_otp' will show its own error if it fails

                    if st.form_submit_button("Cancel"):
                        clear_otp_state() # Clear OTP state on cancel
                        st.rerun()
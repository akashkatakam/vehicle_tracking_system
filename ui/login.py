# ui/login.py
import streamlit as st
from database import SessionLocal
from models import User

# We only need these two functions now
from utils.auth_utils import (
    create_user_session,
    get_branch_name
)

def render():
    """
    Renders a simple 1-step login flow using ONLY the mobile number.
    WARNING: This bypasses OTP verification.
    """
    
    col1, col2, col3 = st.columns([1, 1.2, 1])
    
    with col2:
        st.header("PDI Ops Login")
        
        with st.container(border=True):
            with st.form("pdi_login"):
                st.subheader("Enter Credentials")
                
                # Only ask for Phone Number
                phone_number = st.text_input("Mobile Number", placeholder="Enter your 10-digit mobile number", max_chars=10)
                
                if st.form_submit_button("Login", use_container_width=True, type="primary"):
                    if not phone_number:
                        st.warning("Please enter a mobile number.")
                    else:
                        with SessionLocal() as db:
                            # 1. Find user by phone number
                            user = db.query(User).filter(User.phone_number == phone_number).first()
                            
                            allowed_roles = ['Owner', 'PDI', 'Mechanic']
                            
                            # 2. If user exists and has permission, LOG THEM IN directly
                            if user and user.role in allowed_roles:
                                st.success(f"Welcome, {user.username}!")
                                
                                # Set session state
                                st.session_state.inventory_logged_in = True
                                st.session_state.inventory_user_role = user.role
                                st.session_state.inventory_username = user.username
                                st.session_state.inventory_branch_id = user.Branch_ID
                                st.session_state.inventory_branch_name = get_branch_name(db, user.Branch_ID)

                                # Create the persistent "Remember Me" session
                                create_user_session(db, user.id)
                                
                                st.rerun()
                            else:
                                st.error("No registered user found with this mobile number.")
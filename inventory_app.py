# inventory_app.py
import streamlit as st
from ui import login, mechanic_tasks, pdi_dashboard
from database import SessionLocal
from utils.auth_utils import attempt_auto_login, delete_user_session

# --- PAGE CONFIG ---
st.set_page_config(page_title="PDI Ops", layout="wide")

# --- SESSION STATE INITIALIZATION ---
# Initialize all session state keys here
if 'inventory_logged_in' not in st.session_state: st.session_state.inventory_logged_in = False
if 'inventory_user_role' not in st.session_state: st.session_state.inventory_user_role = None
if 'inventory_username' not in st.session_state: st.session_state.inventory_username = None
if 'inventory_branch_id' not in st.session_state: st.session_state.inventory_branch_id = None
if 'inventory_branch_name' not in st.session_state: st.session_state.inventory_branch_name = "N/A"

# For PDI/Mechanic views
if 'inward_batch' not in st.session_state: st.session_state.inward_batch = []
if 'transfer_batch' not in st.session_state: st.session_state.transfer_batch = []
if 'scanned_chassis' not in st.session_state: st.session_state.scanned_chassis = ""

# --- MAIN APP ROUTER ---
def main():
    attempt_auto_login()
    if not st.session_state.inventory_logged_in:
        # User is not logged in, render the login page
        login.render()
    else:
        # User is logged in, show the correct dashboard based on role
        
        # --- Common Sidebar for Logged-in Users ---
        with st.sidebar:
            st.success(f"User: **{st.session_state.inventory_username}**")
            st.info(f"Role: **{st.session_state.inventory_user_role}**")
            st.caption(f"Branch: {st.session_state.inventory_branch_name}")
            
            if st.button("Logout", type="primary", use_container_width=True):
                # Clear all session state
                for key in st.session_state.keys():
                    del st.session_state[key]
                with SessionLocal() as db:
                    delete_user_session(db)
                st.rerun()
            st.markdown("---")
        
        # --- Role-based "Router" ---
        role = st.session_state.inventory_user_role
        
        if role in ['Owner', 'PDI']:
            pdi_dashboard.render()
        
        elif role == 'Mechanic':
            mechanic_tasks.render()
        
        else:
            st.error("Invalid user role. Please contact admin.")
            st.session_state.inventory_logged_in = False
            st.rerun()

if __name__ == "__main__":
    main()
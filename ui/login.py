# ui/login.py
import streamlit as st
from database import SessionLocal
from models import User
import inventory_manager as mgr

@st.cache_data(ttl=3600)
def load_all_branches():
    """Loads all branches for login name lookup."""
    with SessionLocal() as db:
        try:
            return mgr.get_all_branches(db)
        except Exception as e:
            st.error(f"Database connection error: {e}")
            return []

def render():
    """
    Renders the beautified, centered login page.
    """
    st.markdown(
        """
        <style>
        /* (Your existing CSS for the login page) */
        [data-testid="stAppViewContainer"] > .main {
            background-image: linear-gradient(to bottom right, #f0f2f6, #ffffff);
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            background-color: #ffffff;
            padding: 2.5rem;
            border-radius: 10px;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.08);
            border: none;
        }
        [data-testid="stForm"] { padding: 0; }
        [data-testid="stFormSubmitButton"] button {
            background-color: #FF4B4B;
            font-weight: 600;
        }
        .login-header { text-align: center; }
        .login-header h1 { font-size: 2.2rem; color: #1a1a1a; }
        </style>
        """,
        unsafe_allow_html=True
    )

    all_branches = load_all_branches()
    if not all_branches:
        st.error("Database Connection Failed. Check .streamlit/secrets.toml")
        st.stop()

    col1, col2, col3 = st.columns([1, 1.5, 1])
    
    with col2:
        st.write("") 
        st.write("")
        
        with st.container(border=True): 
            with st.form("pdi_login"):
                st.markdown(
                    '<div class="login-header"><h1>PDI Ops Login</h1></div>',
                    unsafe_allow_html=True
                )
                
                username = st.text_input("Username", placeholder="Enter your username")
                password = st.text_input("Password", type="password", placeholder="Enter your password")
                
                if st.form_submit_button("Secure Login", use_container_width=True):
                    user = None
                    try:
                        with SessionLocal() as db:
                            user = db.query(User).filter(User.username == username).first()
                        
                        allowed_roles = ['Owner', 'PDI', 'Mechanic']
                        if user and user.verify_password(password) and user.role in allowed_roles:
                            
                            st.session_state.inventory_logged_in = True
                            st.session_state.inventory_user_role = user.role
                            st.session_state.inventory_username = user.username
                            st.session_state.inventory_branch_id = user.Branch_ID
                            
                            if user.Branch_ID:
                                for b in all_branches:
                                    if b.Branch_ID == user.Branch_ID:
                                        st.session_state.inventory_branch_name = b.Branch_Name
                                        break
                            st.rerun()
                        else:
                            st.error("Invalid credentials or role not permitted.")
                    except Exception as e:
                        st.error(f"Login error: {e}")
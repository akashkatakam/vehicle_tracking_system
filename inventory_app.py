import streamlit as st
import pandas as pd
from datetime import date
from color_code import COLOR_CODE_MAP
from database import get_db
import inventory_manager as mgr
# --- UPDATED: Import User model from the new merged file ---
from models import User 
# --- NEW: Import the *new* barcode scanner component ---
from streamlit_qrcode_scanner import qrcode_scanner

# --- PAGE CONFIG ---
st.set_page_config(page_title="Inventory & PDI", layout="wide")

# --- SESSION STATE INITIALIZATION ---
if 'inward_batch' not in st.session_state: st.session_state.inward_batch = []
if 'transfer_batch' not in st.session_state: st.session_state.transfer_batch = []
# if 'sales_batch' not in st.session_state: st.session_state.sales_batch = [] # No longer needed
if 'inventory_logged_in' not in st.session_state: st.session_state.inventory_logged_in = False
if 'inventory_user_role' not in st.session_state: st.session_state.inventory_user_role = None
if 'inventory_username' not in st.session_state: st.session_state.inventory_username = None
if 'inventory_branch_id' not in st.session_state: st.session_state.inventory_branch_id = None
if 'inventory_branch_name' not in st.session_state: st.session_state.inventory_branch_name = "N/A"
# --- NEW: Session state for scanned values ---
if 'scanned_engine' not in st.session_state: st.session_state.scanned_engine = ""
if 'scanned_chassis' not in st.session_state: st.session_state.scanned_chassis = ""


# --- HELPER FUNCTIONS ---
@st.cache_data(ttl=3600)
def load_config_data():
    """Loads branches and vehicle master data once."""
    db = next(get_db())
    try:
        all_branches = mgr.get_all_branches(db)
        head_branches = mgr.get_head_branches(db)
        vehicle_master = mgr.get_vehicle_master_data(db)
        return all_branches, head_branches, vehicle_master
    finally:
        db.close()

def render_login_page():
    """
    Renders the beautified, centered login page.
    This is called when the user is NOT logged in.
    """
    
    st.markdown(
        """
        <style>
        /* A subtle gradient background */
        [data-testid="stAppViewContainer"] > .main {
            background-image: linear-gradient(to bottom right, #f0f2f6, #ffffff);
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
        }
        
        /* Style the st.container(border=True) as our login card */
        [data-testid="stVerticalBlockBorderWrapper"] {
            background-color: #ffffff; /* Solid white card */
            padding: 2.5rem;
            border-radius: 10px;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.08); /* Softer shadow */
            border: none; /* Remove the default Streamlit border */
        }

        /* Remove padding from the form itself */
        [data-testid="stForm"] {
            padding: 0;
        }

        /* Style the login button */
        [data-testid="stFormSubmitButton"] button {
            background-color: #FF4B4B; /* Primary color */
            font-weight: 600;
        }
        
        /* Custom classes for the header text */
        .login-header {
            text-align: center;
        }
        .login-header h1 {
            font-size: 2.2rem;
            color: #1a1a1a;
            font-weight: 600;
            margin-bottom: 0.5rem;
        }
        .login-header p {
            font-size: 4rem;
            line-height: 1;
            margin-bottom: 0.5rem;
            color: #FF4B4B; /* Icon color */
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    
    # Load all branches for name lookup on successful login
    try:
        all_branches,_,_ = load_config_data()
    except Exception as e:
        st.error(f"Database Connection Failed: {e}. Check .streamlit/secrets.toml")
        st.stop()

    # --- Use Streamlit columns for horizontal centering ---
    # [empty space] [login card] [empty space]
    col1, col2, col3 = st.columns([1, 1.5, 1])
    
    with col2:
        # Add some vertical spacing to push the card down a bit
        st.write("") 
        st.write("")
        
        # st.container(border=True) creates the element our CSS targets
        with st.container(border=True): 
            
            # --- Login Form ---
            with st.form("pdi_login"):
                # Use markdown for the centered header
                st.markdown(
                    """
                    <div class="login-header">
                        <h1>PDI Ops Login</h1>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                username = st.text_input("Username", placeholder="Enter your username")
                password = st.text_input("Password", type="password", placeholder="Enter your password")
                
                # Make button full width
                if st.form_submit_button("Secure Login", use_container_width=True):
                    db = next(get_db())
                    user = db.query(User).filter(User.username == username).first()
                    db.close()
                    
                    # Check user, password, and allowed roles
                    allowed_roles = ['Owner', 'PDI', 'Mechanic']
                    if user and user.verify_password(password) and user.role in allowed_roles:
                        
                        st.session_state.inventory_logged_in = True
                        st.session_state.inventory_user_role = user.role
                        st.session_state.inventory_username = user.username
                        st.session_state.inventory_branch_id = user.Branch_ID
                        
                        # Find and set branch name
                        if user.Branch_ID:
                            for b in all_branches:
                                if b.Branch_ID == user.Branch_ID:
                                    st.session_state.inventory_branch_name = b.Branch_Name
                                    break
                        st.rerun()
                    else:
                        st.error("Invalid credentials or role not permitted.")

def vehicle_selection_ui(vehicle_master, key_prefix):
    """Simple Model/Variant/Color selector."""
    c1, c2, c3 = st.columns(3)
    model_list = sorted(vehicle_master.keys()) if vehicle_master else ["No Models"]
    model = c1.selectbox("Model", options=model_list, key=f"{key_prefix}_model")
    var_opts = sorted(vehicle_master.get(model, {}).keys()) if model else []
    variant = c2.selectbox("Variant", options=var_opts, key=f"{key_prefix}_variant")
    col_opts = vehicle_master.get(model, {}).get(variant, []) if variant else []
    color = c3.selectbox("Color", options=col_opts, key=f"{key_prefix}_color")
    return model, variant, color

def display_batch(batch_key, submit_callback):
    batch = st.session_state[batch_key]
    if batch:
        st.markdown("##### 📋 Items in Current Batch")
        st.dataframe(pd.DataFrame(batch), use_container_width=True, hide_index=True)
        c1, c2 = st.columns([1, 5])
        if c1.button("🗑️ Clear", key=f"{batch_key}_clear"):
            st.session_state[batch_key] = []
            st.rerun()
        if c2.button("✅ Submit Batch", key=f"{batch_key}_submit", type="primary", use_container_width=True):
            submit_callback(batch)

# --- STOCK VIEW RENDERER (Used by both Public and PDI views) ---
def render_stock_view_interactive(initial_head_name=None, is_public=False, head_map_global={}):
    
    db = next(get_db())
    try:
        # 1. If public, allow selecting ANY head branch. If private, use the pre-selected one.
        if is_public:
            head_branches = mgr.get_head_branches(db)
            head_map_local = {b.Branch_Name: b.Branch_ID for b in head_branches}
            current_head_name = st.selectbox("Select Territory (Head Branch):", options=head_map_local.keys())
            current_head_id = head_map_local[current_head_name]
        else:
            current_head_name = initial_head_name
            # Find ID from global map loaded in main logic
            current_head_id = head_map_global.get(current_head_name)
            if not current_head_id:
                st.error("Head branch ID not found.")
                return

        st.divider()
        
        # 2. Load all branches managed by this head for the pills
        managed_branches = mgr.get_managed_branches(db, current_head_id)
        managed_map_local = {b.Branch_Name: b.Branch_ID for b in managed_branches}
        all_managed_names = list(managed_map_local.keys())

        # 3. Branch Selection Pills (Default to ALL)
        selected_branches = st.pills(
            f"Filter {current_head_name} Territory by Branch:", 
            options=all_managed_names, 
            selection_mode="multi", 
            default=all_managed_names,
            key=f"stock_pills_{'pub' if is_public else 'priv'}"
        )
        
        if st.button("🔄 Refresh Data", key=f"refresh_{'pub' if is_public else 'priv'}"):
            st.cache_data.clear()
            st.rerun()

        if not selected_branches:
            st.info("Please select at least one branch to view stock.")
            return

        # 4. Fetch Data & Render
        selected_ids = [managed_map_local[name] for name in selected_branches]
        raw_stock_df = mgr.get_multi_branch_stock(db, selected_ids)
        
        if raw_stock_df.empty:
                st.info("Zero stock recorded across selected branches.")
        else:
            total_vehicles = int(raw_stock_df['Stock'].sum())
            st.metric("Total Territory Stock", f"{total_vehicles}")
            st.markdown("### Detailed Stock Breakdown")

            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**1. By Model**")
                model_df = raw_stock_df.groupby('model')['Stock'].sum().reset_index()
                model_df.columns = ['Model', 'Total Qty']
                sel_model = st.dataframe(model_df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True, height=350)
                selected_model_name = model_df.iloc[sel_model.selection.rows[0]]['Model'] if sel_model.selection.rows else None
            with c2:
                st.markdown("**2. By Variant**")
                if selected_model_name:
                    var_df = raw_stock_df[raw_stock_df['model'] == selected_model_name]
                    var_summary = var_df.groupby('variant')['Stock'].sum().reset_index()
                    var_summary.columns = ['Variant', 'Qty']
                    sel_var = st.dataframe(var_summary, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True, height=350)
                    selected_variant_name = var_summary.iloc[sel_var.selection.rows[0]]['Variant'] if sel_var.selection.rows else None
                else:
                    st.caption("👈 Select a Model")
                    selected_variant_name = None
            with c3:
                st.markdown("**3. By Color & Branch**")
                if selected_model_name and selected_variant_name:
                    col_df = raw_stock_df[(raw_stock_df['model'] == selected_model_name) & (raw_stock_df['variant'] == selected_variant_name)]
                    col_pivot = col_df.pivot_table(index='color', columns='Branch_Name', values='Stock', fill_value=0).astype(int)
                    col_pivot['TOTAL'] = col_pivot.sum(axis=1)
                    st.dataframe(col_pivot, use_container_width=True, height=350)
                elif selected_model_name:
                        st.caption("👈 Select a Variant")
                else:
                        st.caption("Wait for selections...")


    except Exception as e:
        st.error(f"Error loading stock data: {e}")
    finally:
        db.close()

# =========================================
# --- PDI/MECHANIC VIEWS ---
# =========================================

def render_pdi_assignment_view(branch_id=None):
    """
    View for PDI Admins to assign tasks.
    Uses a single form with a dropdown searchable by DC Number.
    """
    st.header("PDI Task Assignment")
    db = next(get_db())
    try:
        # Get all sales records that are "PDI Pending"
        pending_pdi_data = mgr.get_sales_records_by_status(db, "PDI Pending", branch_id=branch_id)
        
        if pending_pdi_data.empty:
            st.info("No sales are currently pending PDI assignment.")
            return
            
        # Get all users with the "Mechanic" role
        mechanics = mgr.get_users_by_role(db, "Mechanic")
        mechanic_names = [m.username for m in mechanics]
            
        if not mechanic_names:
            st.error("No 'Mechanic' users found. Please create Mechanic accounts to assign tasks.")
            return

        # --- Assignment Form ---
        with st.form("assign_pdi_form"):
            st.subheader("Assign Task")
            
            # --- Searchable Dropdown ---
            # Create a display string for the selectbox, starting with the DC_Number
            # This makes the selectbox searchable by the DC Number.
            pending_pdi_data['display'] = (
                pending_pdi_data['DC_Number'] + " (" + 
                pending_pdi_data['Customer_Name'] + " - " + 
                pending_pdi_data['Model'] + ")"
            )
            
            # The selectbox is now searchable by the DC Number
            sale_display_str = st.selectbox(
                "Select Sale Record (Searchable by DC No.):", 
                pending_pdi_data['display']
            )
            
            selected_mechanic = st.selectbox("Assign to Mechanic:", mechanic_names)
            
            submitted = st.form_submit_button("Assign Task")
            
            if submitted:
                if not sale_display_str:
                    st.warning("Please select a sale record.")
                else:
                    # Find the original 'id' from the selected display string
                    sale_id = pending_pdi_data[
                        pending_pdi_data['display'] == sale_display_str
                    ].iloc[0]['id']
                    
                    # Call the manager function to update the database
                    mgr.assign_pdi_mechanic(db, int(sale_id), selected_mechanic)
                    
                    st.success(f"Task {sale_display_str} assigned to {selected_mechanic}!")
                    
                    # Clear caches and rerun to refresh the lists
                    st.cache_data.clear()
                    st.rerun()

        st.divider()
        
        # --- List of Pending Tasks ---
        st.subheader("Pending PDI Assignment List")
        st.dataframe(
            pending_pdi_data[['DC_Number', 'Customer_Name', 'Model', 'Variant', 'Paint_Color', 'Sales_Staff']], 
            use_container_width=True
        )
        
    except Exception as e:
        st.error(f"An error occurred: {e}")
    finally:
        db.close()

def render_mechanic_view(username, branch_id=None):
    db = next(get_db())
    try:
        # If username is None (like in PDI admin view), show all 'In Progress' tasks
        if username:
            my_tasks = mgr.get_sales_records_for_mechanic(db, username, branch_id=branch_id)
            st.header("My PDI Tasks")

        if my_tasks.empty:
            st.success("No pending tasks. Great job! Tasks will appear here when assigned.")
            return

        my_tasks['display'] = my_tasks['DC_Number'] + " (" + my_tasks['Customer_Name'] + " - " + my_tasks['Model'] + ")"
        
        # Only show the selectbox if in 'Mechanic' mode (username is provided)
        if username:
            task_display_str = st.selectbox("Select Task to Complete:", my_tasks['display'])
         # PDI admin doesn't complete tasks, so we stop here
        
        if task_display_str:
            selected_task = my_tasks[my_tasks['display'] == task_display_str].iloc[0]
            sale_id = int(selected_task['id'])
            dc_number = str(selected_task['DC_Number'])
            
            st.subheader(f"Complete Task: {selected_task['DC_Number']}")
            st.write(f"**Customer:** {selected_task['Customer_Name']}")
            st.write(f"**Vehicle Request:** {selected_task['Model']} / {selected_task['Variant']} / {selected_task['Paint_Color']}")
            
            st.warning("""
            **Camera Not Working?**
            1.  Make sure this app is open with a secure `https://` URL.
            2.  You **must** give your browser permission to use the camera.
            """)
            
            # --- UPDATED: Barcode Scanning UI ---
            st.divider()
            st.subheader("Scan Vehicle Details")

            # --- Chassis Number Scanner (Now full width) ---
            st.write("**Scan or Enter Chassis Number**")
            # 
            chassis_scan_val = qrcode_scanner(key="chassis_scanner")
            if chassis_scan_val:
                st.session_state.scanned_chassis = chassis_scan_val
            
            chassis_val = st.text_input("Chassis Number:", value=st.session_state.get("scanned_chassis", ""), placeholder="Click Scan button or type Chassis No.")
            
            st.divider()

            # --- Completion Button ---
            if st.button("Mark PDI Complete", type="primary", use_container_width=True):
                if not chassis_val:
                    st.warning("Chassis Number is required.")
                else:
                    try:
                        # --- UPDATED: Capture response from manager ---
                        success, message = mgr.complete_pdi(db, sale_id, chassis_no=chassis_val.strip(), engine_no=None,dc_number=dc_number)
                        
                        if success:
                            st.success(message)
                            st.balloons()
                            # Clear state for next scan
                            st.session_state.scanned_chassis = ""
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            # Show the specific error from the manager
                            st.error(message)

                    except Exception as e:
                        st.error(f"An application error occurred: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
    finally:
        db.close()

def render_pdi_pending_tasks(branch_id: str =None):
    my_tasks = mgr.get_sales_records_by_status(db, "PDI In Progress", branch_id=branch_id)
    st.header("All In-Progress PDI Tasks") # Added header for clarity
    st.dataframe(my_tasks[['DC_Number', 'Customer_Name', 'Model', 'pdi_assigned_to']], use_container_width=True)
# =========================================
# MAIN APP LOGIC
# =========================================

try:
    all_branches, head_branches, vehicle_master = load_config_data()
    all_branch_map = {b.Branch_Name: b.Branch_ID for b in all_branches}
    head_map = {b.Branch_Name: b.Branch_ID for b in head_branches}
except Exception as e:
    st.error(f"Database Connection Failed: {e}. Check .streamlit/secrets.toml")
    st.stop()

# --- SIDEBAR: AUTHENTICATION ---
if not st.session_state.inventory_logged_in:
    # If NOT logged in, show the beautified login page and stop
    render_login_page()
else:
    with st.sidebar:
        st.success(f"User: **{st.session_state.inventory_username}**")
        st.info(f"Role: **{st.session_state.inventory_user_role}**")
        st.caption(f"Branch: {st.session_state.inventory_branch_name}")
        
        if st.button("Logout", type="primary", use_container_width=True):
            # Clear all session state
            for key in st.session_state.keys():
                del st.session_state[key]
            st.rerun()
        st.markdown("---")

    # 2. Render the rest of your application
    role = st.session_state.inventory_user_role
    username = st.session_state.inventory_username
    branch_id = st.session_state.inventory_branch_id

    # --- PDI/Owner Role: Full Ops Center ---
    if role in ['Owner', 'PDI']:
        st.title("🚚 PDI Operations Center")
        with st.sidebar:
            st.header("📍 Operational Setup")
            current_head_name = st.selectbox("Select Head Branch:", options=head_map.keys())
            current_head_id = head_map[current_head_name]
            st.divider()
            st.info(f"Managing: **{current_head_name}**")
            
            db = next(get_db())
            managed_branches = mgr.get_managed_branches(db, current_head_id)
            db.close()
            managed_map = {b.Branch_Name: b.Branch_ID for b in managed_branches}
            sub_branch_map = {k: v for k, v in managed_map.items() if v != current_head_id}

        # --- UPDATED: Removed 'Record Sales' tab ---
        tab_list = [
            "📋 PDI Assignment",
            "🔧 Mechanic Tasks",
            "📊 Stock View",
            "📥 OEM Inward", 
            "📤 Branch Transfer", 
        ]
        
        tab1, tab2, tab3, tab4, tab5 = st.tabs(tab_list)

        with tab1:
            render_pdi_assignment_view(branch_id=branch_id)
        
        with tab2:
            render_pdi_pending_tasks(branch_id=branch_id)
        
        with tab3:
            st.header("Operational Stock View")
            render_stock_view_interactive(initial_head_name=current_head_name, is_public=False, head_map_global=head_map)
            
            st.subheader("🚚 Daily Transfer Summary (Head Office View)")
            try:
                db = next(get_db())
                transfer_summary = mgr.get_daily_transfer_summary(db)
                if not transfer_summary.empty:
                    st.dataframe(transfer_summary, use_container_width=True, hide_index=True, height=300)
                else:
                    st.info("No transfers recorded recently.")
            except Exception as e:
                st.error(f"Error loading transfer summary: {e}")
            finally:
                db.close()

            st.divider()
            st.subheader(f"📜 Detailed Activity for {current_head_name}")
            try:
                db = next(get_db())
                hist_df = mgr.get_recent_transactions(db, current_head_id)
                if not hist_df.empty:
                    st.dataframe(
                        hist_df[['Date', 'Transaction_Type', 'Model', 'Color', 'Quantity', 'Remarks']], 
                        use_container_width=True, hide_index=True, height=400
                    )
                else:
                    st.caption("No recent transactions for this branch.")
            except Exception as e:
                st.error(f"Error loading history: {e}")
            finally:
                db.close()
        
        # --- Existing Inventory Tabs ---
        with tab4:
            st.header(f"Stock Arrival at {current_head_name}")
            st.info("Upload a CSV file to add new vehicles to the VehicleMaster.")
            
            with st.container(border=True):
                c1, c2 = st.columns(2)
                source_options = ["HMSI (OEM)", "Other External"]
                source_in_name = c1.selectbox("Received From:", options=source_options)
                
                # These are now FALLBACKS
                load_no_fallback = c2.text_input("Fallback Load / Invoice No (if not in CSV):")
                date_in_fallback = c1.date_input("Fallback Date Received (if not in CSV):", value=date.today())
                
                remarks_in = c2.text_input("Remarks for entire batch:")
            
            st.subheader("Upload Vehicle Batch CSV")
            uploaded_file = st.file_uploader("Choose a CSV file", type="csv")

            if uploaded_file is not None:
                try:
                    # Read all columns as strings to avoid data type issues
                    df = pd.read_csv(uploaded_file, dtype=str) 
                    st.success("File uploaded. Please review the data below:")
                    st.dataframe(df.head())

                    # --- Data Processing & Validation ---
                    # Standardize column names (chassis_no, load_no, etc.)
                    df.columns = df.columns.str.lower().str.replace(' ', '_').str.replace('reference_number', 'no')
                    
                    required_cols = ['chassis_no', 'model', 'variant', 'color']
                    missing_cols = [col for col in required_cols if col not in df.columns]
                    
                    if missing_cols:
                        st.error(f"CSV is missing required columns: {', '.join(missing_cols)}")
                    else:
                        # --- APPLY COLOR MAPPING ---
                        original_colors = df['color'].copy()
                        # Clean the codes (strip whitespace, make uppercase) to match the map
                        df['color_code'] = df['color'].str.strip().str.upper()
                        df['color'] = df['color_code'].map(COLOR_CODE_MAP)
                        
                        unmapped_mask = df['color'].isna()
                        # Get unique unmapped codes
                        unmapped_codes = original_colors[unmapped_mask].str.strip().str.upper().unique()
                        
                        # Fill unmapped colors back with their original code
                        df['color'] = df['color'].fillna(original_colors)
                        
                        mapped_count = len(df) - len(unmapped_codes)
                        
                        if mapped_count > 0:
                            st.success(f"Successfully mapped {mapped_count} color codes to friendly names.")
                        if len(unmapped_codes) > 0:
                            st.warning(f"The following {len(unmapped_codes)} color codes were not found and were imported as-is: {', '.join(unmapped_codes)}")
                            st.warning("Please update the 'COLOR_CODE_MAP' dictionary at the top of 'inventory_app.py' to include them.")
                        # --- END MAPPING LOGIC ---
                        
                        # Handle fallbacks
                        if 'date_received' not in df.columns:
                            st.warning(f"No 'date_received' column found. Using fallback date: {date_in_fallback}")
                            df['date_received'] = date_in_fallback
                        
                        if 'load_no' not in df.columns:
                            st.warning(f"No 'load_no' or 'load_reference_number' column found. Using fallback: {load_no_fallback}")
                            df['load_no'] = load_no_fallback
                        
                        # Ensure date is in correct format (datetime object)
                        df['date_received'] = pd.to_datetime(df['date_received'])

                        # Ensure engine_no exists, even if as None
                        if 'engine_no' not in df.columns:
                            df['engine_no'] = None
                        
                        # Prune to only necessary columns for the batch
                        final_batch_cols = ['chassis_no', 'engine_no','model', 'variant', 'color', 'date_received', 'load_no','current_branch_id']
                        df_final = df[[col for col in final_batch_cols if col in df.columns]]
                        
                        vehicle_batch = df_final.to_dict('records')
                        
                        st.info(f"Ready to import {len(vehicle_batch)} vehicles.")
                        
                        if st.button("✅ Submit This Batch", type="primary", use_container_width=True):
                            db = next(get_db())
                            try:
                                # Call the manager function
                                mgr.log_bulk_inward_master(db, current_head_id, source_in_name, load_no_fallback,date_in_fallback,remarks_in, vehicle_batch)
                                st.success(f"Successfully logged {len(vehicle_batch)} new vehicles into VehicleMaster!")
                                st.cache_data.clear() 
                                st.rerun()
                            except Exception as e: 
                                st.error(f"Database Error: {e}")
                            finally: 
                                db.close()

                except Exception as e:
                    st.error(f"An error occurred while processing the file: {e}")
        with tab5:
            st.header("Transfer to Sub-Dealer")
            st.info("Scan or enter Chassis Numbers to transfer.")
            if not sub_branch_map:
                st.warning(f"No sub-branches configured for {current_head_name}.")
            else:
                with st.container(border=True):
                    c1, c2 = st.columns(2)
                    dest_name = c1.selectbox("Destination Branch:", options=sub_branch_map.keys())
                    date_out = c2.date_input("Transfer Date:", value=date.today())
                    remarks_out = st.text_input("Transfer Remarks:")
                
                st.subheader("Add Vehicle to Transfer Batch")
                chassis_scan_val = qrcode_scanner(key="transfer_scanner")
                if chassis_scan_val:
                    st.session_state.scanned_chassis = chassis_scan_val
            
                chassis_val = st.text_input("Chassis Number:", value=st.session_state.get("scanned_chassis", ""), placeholder="Click Scan button or type Chassis No.")
                
                # Scan or type chassis
                if st.button("⬇️ Add to Transfer Batch"):
                    if not chassis_val:
                        st.warning("Chassis Number is required.")
                    else:
                        st.session_state.transfer_batch.append(chassis_val)
                        st.session_state.scanned_chassis = "" # Clear for next scan
                        st.success(f"Added {chassis_val} to transfer list.")
                        st.rerun()
                
                # Display transfer batch (just a list of strings)
                if st.session_state.transfer_batch:
                    st.markdown("##### 📋 Vehicles in Current Transfer Batch")
                    st.dataframe(pd.DataFrame(st.session_state.transfer_batch, columns=["Chassis Number"]), use_container_width=True, hide_index=True)
                    c1, c2 = st.columns([1, 5])
                    if c1.button("🗑️ Clear", key="transfer_clear"):
                        st.session_state.transfer_batch = []
                        st.rerun()
                    if c2.button("✅ Submit Batch", key="transfer_submit", type="primary", use_container_width=True):
                        db = next(get_db())
                        try:
                            mgr.log_bulk_transfer_master(db, current_head_id, sub_branch_map[dest_name], date_out, remarks_out, st.session_state.transfer_batch)
                            st.success(f"Transferred {len(st.session_state.transfer_batch)} vehicles to {dest_name}!")
                            st.session_state.transfer_batch = []
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e: st.error(f"Error: {e}")
                        finally: db.close()

    # --- Mechanic Role: Simple View ---
    elif role == 'Mechanic':
        st.title("🔧 My PDI Tasks")
        render_mechanic_view(username, branch_id)
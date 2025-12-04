# ui/pdi_dashboard.py
import streamlit as st
import pandas as pd
from datetime import date
from database import SessionLocal
import inventory_manager as mgr
import models
from streamlit_qrcode_scanner import qrcode_scanner

from ui.color_code import COLOR_CODE_MAP

# --- HELPER FUNCTIONS ---
def load_config_data():
    """Loads branches and vehicle master data once."""
    with SessionLocal() as db:
        try:
            all_branches = mgr.get_all_branches(db)
            head_branches = mgr.get_head_branches(db)
            vehicle_master = mgr.get_vehicle_master_data(db)
            # --- IMPROVEMENT: Load color map from DB ---
            color_map = COLOR_CODE_MAP
            return all_branches, head_branches, vehicle_master, color_map
        except Exception as e:
            st.error(f"Database connection error in load_config_data: {e}")
            return [], [], {}, {}

# (All your other helper functions from the original app go here)
def render_stock_view_interactive(initial_head_name=None, is_public=False, head_map_global={}):
    try:
        if is_public:
            head_map_local = {b.Branch_Name: b.Branch_ID for b in mgr.get_head_branches(SessionLocal())}
            current_head_name = st.selectbox("Select Territory (Head Branch):", options=head_map_local.keys())
            current_head_id = head_map_local[current_head_name]
        else:
            current_head_name = initial_head_name
            current_head_id = head_map_global.get(current_head_name)
            if not current_head_id:
                st.error("Head branch ID not found.")
                return

        st.divider()
        
        with SessionLocal() as db:
            managed_branches = mgr.get_managed_branches(db, current_head_id)
        managed_map_local = {b.Branch_Name: b.Branch_ID for b in managed_branches}
        all_managed_names = list(managed_map_local.keys())

        selected_branches = st.pills(
            f"Filter {current_head_name} Territory by Branch:", 
            options=all_managed_names, 
            selection_mode="multi", 
            default=None,
            key="stock_pills_priv"
        )
        
        if st.button("🔄 Refresh Data", key="refresh_priv"):
            st.cache_data.clear()
            st.rerun()

        if not selected_branches:
            st.info("Please select at least one branch to view stock.")
            return

        selected_ids = [managed_map_local[name] for name in selected_branches]
        
        # --- IMPROVEMENT: Call the cached stock function ---
        raw_stock_df = mgr.get_multi_branch_stock(db=db,branch_ids=selected_ids)
        
        if raw_stock_df.empty:
                st.info("Zero stock recorded across selected branches.")
        else:
            # (The rest of your stock view rendering logic... df grouping, etc.)
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

def render_pdi_assignment_view(branch_id=None):
    st.header("PDI Task Assignment")
    try:
        with SessionLocal() as db:
            pending_pdi_data = mgr.get_sales_records_by_status(db, "PDI Pending", branch_id=branch_id)
            mechanics = mgr.get_users_by_role(db, "Mechanic")
            
        if pending_pdi_data.empty:
            st.info("No sales are currently pending PDI assignment.")
            return
            
        mechanic_names = [m.username for m in mechanics]
            
        if not mechanic_names:
            st.error("No 'Mechanic' users found.")
            return

        with st.form("assign_pdi_form"):
            st.subheader("Assign Task")
            pending_pdi_data['display'] = (
                pending_pdi_data['DC_Number'] + " (" + 
                pending_pdi_data['Customer_Name'] + " - " + 
                pending_pdi_data['Model'] + ")"
            )
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
                    sale_id = pending_pdi_data[
                        pending_pdi_data['display'] == sale_display_str
                    ].iloc[0]['id']
                    
                    try:
                        with SessionLocal() as db:
                            mgr.assign_pdi_mechanic(db, int(sale_id), selected_mechanic)
                        st.success(f"Task assigned to {selected_mechanic}!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Database error on assignment: {e}")

        st.divider()
        st.subheader("Pending PDI Assignment List")
        st.dataframe(
            pending_pdi_data[['DC_Number', 'Customer_Name', 'Model', 'Variant', 'Paint_Color', 'Sales_Staff']], 
            use_container_width=True
        )
    except Exception as e:
        st.error(f"An error occurred: {e}")

def render_pdi_pending_tasks(branch_id: str =None):
    try:
        with SessionLocal() as db:
            my_tasks = mgr.get_sales_records_by_status(db, "PDI In Progress", branch_id=branch_id)
        st.header("All In-Progress PDI Tasks")
        if my_tasks.empty:
            st.info("No tasks are currently in progress.")
        else:
            st.dataframe(my_tasks[['DC_Number', 'Customer_Name', 'Model', 'pdi_assigned_to']], use_container_width=True)
    except Exception as e:
        st.error(f"Error loading pending tasks: {e}")

@st.dialog("📊 Sales Report",width= 'large')
def show_sales_report_popup(head_map):
        st.write("Select a date range to generate the sales matrix.")
        
        # Default to "This Month"
        today = date.today()
        
        date_range = st.date_input(
            "Select Date Range",
            value=(today, today),
            max_value=today
        )
        
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_d, end_d = date_range
            
            if st.button(f"Generate Report ({start_d.strftime('%d-%b')} to {end_d.strftime('%d-%b')})", type="primary"):
                try:
                    with SessionLocal() as db:
                        # 1. Fetch the Master Report (All Data)
                        master_df = mgr.get_sales_report(db, start_d, end_d)
                    
                    if master_df.empty:
                        st.warning("No sales recorded for this period.")
                    else:
                        st.write(f"### Sales Report: {start_d} to {end_d}")
                        
                        # 2. Iterate through each Head Branch (Territory)
                        # head_map is available from the main render() scope
                        for head_name, head_id in head_map.items():
                            
                            # Get all sub-branches for this territory
                            with SessionLocal() as db:
                                territory_branches = mgr.get_managed_branches(db, head_id)
                            
                            territory_names = [b.Branch_Name for b in territory_branches]
                            
                            # 3. Filter the Master Report for this Territory
                            # (We check if the index 'Branch_Name' is in our list)
                            territory_df = master_df[master_df.index.isin(territory_names)]
                            
                            # Only show the table if there is data
                            if not territory_df.empty:
                                st.divider()
                                st.subheader(f"📍 {head_name}")
                                st.dataframe(territory_df, use_container_width=True)
                                
                                # Show Territory Total
                                t_total = territory_df['TOTAL'].sum()
                                st.markdown(f"**Total {head_name} Sales: :green[{int(t_total)}]**")

                        st.divider()
                        
                        # 4. Global Download Button
                        csv = master_df.to_csv().encode('utf-8')
                        st.download_button(
                            "⬇️ Download Full Report (CSV)",
                            csv,
                            f"sales_report_{start_d}_{end_d}.csv",
                            "text/csv",
                            key='download-sales-popup'
                        )

                except Exception as e:
                    st.error(f"Error loading report: {e}")
        else:
            st.info("Please select an end date.")

# --- MAIN RENDER FUNCTION ---
def render():
    st.title("🚚 PDI Operations Center")
    
    # --- Load Data ---
    try:
        all_branches, head_branches, vehicle_master, COLOR_CODE_MAP = load_config_data()
        all_branch_map = {b.Branch_Name: b.Branch_ID for b in all_branches}
        head_map = {b.Branch_Name: b.Branch_ID for b in head_branches}
    except Exception as e:
        st.error(f"Database Connection Failed: {e}.")
        st.stop()

    branch_id = st.session_state.inventory_branch_id

    # --- Sidebar Setup ---
    is_super_owner = (branch_id is None) or (branch_id == "All Branches") # Assuming "All Branches" placeholder

    # 1. Initialize session state for the actively managed Head Branch ID
    if 'pdi_active_head_id' not in st.session_state:
        # If Super Owner, start with the first head branch; otherwise, use their assigned branch
        default_id = branch_id if branch_id else next(iter(head_map.values()))
        st.session_state.pdi_active_head_id = default_id
    
    # --- Start Sidebar Content ---
    with st.sidebar:
        st.header("📍 Operational Setup")

        if is_super_owner:
            # --- SUPER OWNER LOGIC ---
            # Determine the current selection index
            current_id = st.session_state.pdi_active_head_id
            keys_list = list(head_map.keys())
            
            # Use safe lookup for default index
            default_index = 0
            for name, hid in head_map.items():
                if hid == current_id:
                    default_index = keys_list.index(name)
                    break

            selected_head_name = st.selectbox(
                "Select Head Branch (Super User):",
                options=keys_list,
                index=default_index,
                key="super_owner_head_selector"
            )
            
            # Update the session variable when selection changes
            current_head_id = head_map[selected_head_name]
            st.session_state.pdi_active_head_id = current_head_id
            current_head_name = selected_head_name
            
        else:
            # --- BRANCH-SPECIFIC USER LOGIC ---
            # User is locked to their assigned branch_id
            current_head_id = branch_id
            current_head_name = st.session_state.inventory_branch_name # Use the name we saved on login
            
            # Display their branch name as a locked field
            st.text_input(
                "Head Branch (Access Locked):", 
                value=current_head_name, 
                disabled=True
            )
            # Ensure the session variable tracks the locked ID
            st.session_state.pdi_active_head_id = current_head_id


        st.divider()
        st.info(f"Managing: **{current_head_name}**")
        if st.button("📊 View Daily Sales Report", type="primary", use_container_width=True):
            show_sales_report_popup(head_map=head_map)

        
        # --- Define variables for the rest of the dashboard (OUTSIDE the sidebar) ---
        # NOTE: We define these variables AFTER the sidebar to ensure they are available
        # to the rest of the app, regardless of the user type.
        current_head_id = st.session_state.pdi_active_head_id
        # Use the name corresponding to the ID, whether selected or locked
        current_head_name = next((name for name, hid in head_map.items() if hid == current_head_id), "N/A")
        
        with SessionLocal() as db:
            managed_branches = mgr.get_managed_branches(db, current_head_id)
        managed_map = {b.Branch_Name: b.Branch_ID for b in managed_branches}
        sub_branch_map = {k: v for k, v in managed_map.items() if v != current_head_id}

    is_akash_owner = (
        st.session_state.get('inventory_user_role') == 'Owner' and
        st.session_state.get('inventory_username') == 'akash'
    )


    # --- Tabbed Interface ---
    tab_list = [
        "📋 PDI Assignment",
        "🔧 In-Progress Tasks",
        "📊 Stock View",
        "📥 OEM Inward", 
        "📤 Branch Transfer", 
        "📤 Sub-Branch Sale"
    ]
    if is_akash_owner:
        tab_list.append("🛠️ Stock Correction")
    
    selected_tab = st.radio(
        "Select View", 
        tab_list, 
        horizontal=True, 
        label_visibility="collapsed"
    )

# Now, use if/elif blocks to render ONLY the active tab's content
    if selected_tab == "📋 PDI Assignment":
        render_pdi_assignment_view(branch_id=branch_id)

    elif selected_tab == "🔧 In-Progress Tasks":
        render_pdi_pending_tasks(branch_id=branch_id)
        st.header("Recently Allotted Vehicles (Last 48 Hours)")
        
        try:
            with SessionLocal() as db:
                completed_df = mgr.get_completed_sales_last_48h(db, branch_id=branch_id)
            
            if completed_df.empty:
                st.info("No vehicles have completed PDI in the last 48 hours.")
            else:
                # These are the specific columns you requested from the SalesRecord model
                columns_to_show = [
                    'DC_Number',
                    'Customer_Name',
                    'Model',
                    'Variant',
                    'Paint_Color',
                    'engine_no',
                    'chassis_no',
                    'pdi_assigned_to'
                ]
                
                # Filter the DataFrame to only these columns
                display_df = completed_df[columns_to_show]
                
                # Rename columns for a cleaner look
                display_df = display_df.rename(columns={
                    'Paint_Color': 'Color',
                    'engine_no': 'Engine Number',
                    'chassis_no': 'Chassis Number',
                    'pdi_assigned_to': 'PDI Completed By'
                })
                
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                
        except Exception as e:
            st.error(f"An error occurred loading completed tasks: {e}")
    
    elif selected_tab == "📊 Stock View":
        st.header("Operational Stock View")
        render_stock_view_interactive(initial_head_name=current_head_name, is_public=False, head_map_global=head_map)
        
        st.header("Vehicle Movement Reports")
        
        # Report Type Selector outside the date container
        report_type = st.selectbox(
            "Select Report Type:",
            # Options list is now simplified
            ["Summary: Outward (Head -> Branches)", "Summary: OEM Inward (HMSI)"] 
        )

        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            
            # --- Conditional Branch Input (c1) ---
            if report_type == "Summary: Outward (Head -> Branches)":
                # Use current_head_name directly for the source branch
                c1.text_input("Source Branch (Operating Context):", value=current_head_name, disabled=True)
                report_branch_id = current_head_id
                
            elif report_type == "Summary: OEM Inward (HMSI)":
                # Allow selecting any branch for OEM receipts
                branch_options = list(all_branch_map.keys())
                # Note: We must get the ID of the selected branch for the report
                selected_branch_name = c1.selectbox("Select Branch for HMSI Report:", branch_options, key="hmsi_branch_select")
                report_branch_id = all_branch_map[selected_branch_name]

            # Date Range (c2, c3)
            start_date = c2.date_input("Start Date", value=date.today().replace(day=1))
            end_date = c3.date_input("End Date", value=date.today())

        # --- Logic Router ---
        try:
            with SessionLocal() as db:
                
                # REPORT 1: Outward Summary (Head -> Branches)
                if report_type == "Summary: Outward (Head -> Branches)":
                    st.subheader(f"📤 Outward Summary: From {current_head_name}")
                    # Use current_head_id directly
                    summary_df = mgr.get_branch_transfer_summary(db, current_head_id, start_date, end_date)
                    
                    if summary_df.empty:
                        st.info(f"No vehicles sent from {current_head_name} in this period.")
                    else:
                        pivot_df = summary_df.pivot_table(
                            index=['Model', 'Variant', 'Color'], 
                            columns='Destination_Branch', 
                            values='Total_Quantity', 
                            aggfunc='sum',
                            fill_value=0
                        )
                        pivot_df['TOTAL'] = pivot_df.sum(axis=1)
                        
                        st.dataframe(pivot_df, use_container_width=True)
                        st.metric("Total Vehicles Sent", int(pivot_df['TOTAL'].sum()))
                        
                # REPORT 2: OEM Inward (HMSI -> Branch)
                elif report_type == "Summary: OEM Inward (HMSI)":
                    st.subheader(f"📥 OEM Inward Summary: {all_branch_map.get(report_branch_id)}")
                    oem_df = mgr.get_oem_inward_summary(db, report_branch_id, start_date, end_date)
                    
                    if oem_df.empty:
                        st.info(f"No HMSI stock received at {all_branch_map.get(report_branch_id)} in this period.")
                    else:
                        st.dataframe(oem_df, use_container_width=True, hide_index=True)
                        st.metric("Total New Stock Received", int(oem_df['Total_Received'].sum()))

        except Exception as e:
            st.error(f"Error generating report: {e}")
    
    elif selected_tab == "📥 OEM Inward":
        st.header(f"Stock Arrival at {current_head_name}")
        st.info("Upload a CSV file to add new vehicles to the VehicleMaster.")
        
        with st.container(border=True):
            c1, c2 = st.columns(2)
            source_options = ["HMSI (OEM)", "Other External"]
            source_in_name = c1.selectbox("Received From:", options=source_options)
            load_no_fallback = c2.text_input("Fallback Load / Invoice No (if not in CSV):")
            date_in_fallback = c1.date_input("Fallback Date Received (if not in CSV):", value=date.today())
            remarks_in = c2.text_input("Remarks for entire batch:")
        
        st.subheader("Upload Vehicle Batch CSV")
        uploaded_file = st.file_uploader("Choose a CSV file", type="csv")

        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file, dtype=str)
                df.columns = df.columns.str.lower().str.replace(' ', '_').str.replace('reference_number', 'no')
                
                required_cols = ['chassis_no', 'model', 'variant', 'color']
                if not all(col in df.columns for col in required_cols):
                    st.error(f"CSV is missing required columns: {', '.join(required_cols)}")
                else:
                    # --- IMPROVEMENT: COLOR MAPPING FROM DB ---
                    original_colors = df['color'].copy()
                    df['color_code'] = df['color'].str.strip().str.upper()
                    df['color'] = df['color_code'].map(COLOR_CODE_MAP)
                    unmapped_mask = df['color'].isna()
                    unmapped_codes = original_colors[unmapped_mask].str.strip().str.upper().unique()
                    df['color'] = df['color'].fillna(original_colors)
                    
                    if unmapped_codes.any():
                        st.warning(f"Unmapped color codes: {', '.join(unmapped_codes)}")
                        st.warning("Please update the 'color_code_map' table.")

                    # (Rest of your CSV processing logic...)
                    if 'date_received' not in df.columns: df['date_received'] = date_in_fallback
                    if 'load_no' not in df.columns: df['load_no'] = load_no_fallback
                    if 'engine_no' not in df.columns: df['engine_no'] = None
                    df['date_received'] = pd.to_datetime(df['date_received'])
                    final_batch_cols = ['chassis_no', 'engine_no','model', 'variant', 'color', 'date_received', 'load_no']
                    df_final = df[final_batch_cols]
                    vehicle_batch = df_final.to_dict('records')
                    
                    st.info(f"Ready to import {len(vehicle_batch)} vehicles.")
                    
                    if st.button("✅ Submit This Batch", type="primary", use_container_width=True):
                        # --- IMPROVEMENT: DUPLICATE CHASSIS CHECK ---
                        chassis_in_csv = [v['chassis_no'] for v in vehicle_batch]
                        try:
                            with SessionLocal() as db:
                                existing_vehicles = db.query(models.VehicleMaster.chassis_no).filter(
                                    models.VehicleMaster.chassis_no.in_(chassis_in_csv)
                                ).all()
                            existing_list = [v[0] for v in existing_vehicles]

                            if existing_list:
                                st.error(f"Import Failed: Duplicate Chassis: {', '.join(existing_list)}")
                            else:
                                # All good, submit the batch
                                with SessionLocal() as db:
                                    mgr.log_bulk_inward_master(db, current_head_id, source_in_name, load_no_fallback, date_in_fallback, remarks_in, vehicle_batch)
                                st.success(f"Successfully logged {len(vehicle_batch)} new vehicles!")
                                st.cache_data.clear() 
                                st.rerun()
                        except Exception as e:
                            st.error(f"Error during validation or submission: {e}")

            except Exception as e:
                st.error(f"An error occurred while processing the file: {e}")
    
    elif selected_tab == "📤 Branch Transfer":  
        st.header("Transfer to Sub-Dealer")
        if not sub_branch_map:
            st.warning(f"No sub-branches configured for {current_head_name}.")
        else:
            with st.container(border=True):
                c1, c2 = st.columns(2)
                dest_name = c1.selectbox("Destination Branch:", options=all_branch_map.keys())
                date_out = c2.date_input("Transfer Date:", value=date.today())
                remarks_out = st.text_input("Transfer DC Number:")
            
            st.subheader("Add Vehicle to Transfer Batch")
            chassis_scan_val = qrcode_scanner(key="transfer_scanner")
            if chassis_scan_val:
                st.session_state.scanned_chassis = chassis_scan_val

            chassis_val = st.text_input(
                "Chassis Number:", 
                value=st.session_state.get("scanned_chassis", ""), 
                placeholder="Click Scan button or type Chassis No."
            )

            
            if st.button("⬇️ Add to Transfer Batch"):
                if chassis_val:
                    st.session_state.transfer_batch.append(chassis_val)
                    st.session_state.scanned_chassis = ""
                    st.rerun()
                else:
                    st.warning("Chassis Number is required.")
            
            if st.session_state.transfer_batch:
                st.dataframe(pd.DataFrame(st.session_state.transfer_batch, columns=["Chassis Number"]), use_container_width=True)
                c1, c2 = st.columns([1, 5])
                if c1.button("🗑️ Clear", key="transfer_clear"):
                    st.session_state.transfer_batch = []
                    st.rerun()
                if c2.button("✅ Submit Batch", key="transfer_submit", type="primary", use_container_width=True):
                    try:
                        with SessionLocal() as db:
                            mgr.log_bulk_transfer_master(db, current_head_id, all_branch_map[dest_name], date_out, remarks_out, st.session_state.transfer_batch)
                        st.success(f"Transferred {len(st.session_state.transfer_batch)} vehicles!")
                        st.session_state.transfer_batch = []
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e: 
                        st.error(f"Error: {e}")

    elif selected_tab == "📤 Sub-Branch Sale":
        st.header("Log a Manual Sub-Branch Sale (Batch Mode)")

        # --- Session state for the batch ---
        if 'manual_sale_batch' not in st.session_state:
            st.session_state.manual_sale_batch = []
        if 'scanned_chassis_manual_sale' not in st.session_state:
            st.session_state.manual_sale_chassis = ""

        # --- Section 1: Add Vehicle to Batch ---
        st.subheader("Add Vehicle to Sale Batch")
        chassis_scan_val = qrcode_scanner(key="manual_sale_scanner")
        
        # 2. If a code is scanned, force-update the text_input's key directly
        if chassis_scan_val:
            st.session_state.manual_sale_chassis = chassis_scan_val

        # 3. Render the text input
        # Note: We don't need 'value=' anymore because we set the key above
        chassis_val = st.text_input(
                "Chassis Number:", 
                value=st.session_state.get("manual_sale_chassis", ""), 
                placeholder="Click Scan button or type Chassis No."
            )
        
        if st.button("⬇️ Add to Sale Batch"):
            if not chassis_val:
                st.warning("Chassis Number is required.")
            elif chassis_val in st.session_state.manual_sale_batch:
                st.warning(f"{chassis_val} is already in the batch.")
            else:
                st.session_state.manual_sale_batch.append(chassis_val)
                st.session_state.manual_sale_chassis = "" # Clear for next scan
                st.success(f"Added {chassis_val} to batch.")
                st.rerun()

        # --- Section 2: Display Current Batch ---
        if st.session_state.manual_sale_batch:
            st.markdown("##### 📋 Vehicles in Current Sale Batch")
            st.dataframe(
                pd.DataFrame(st.session_state.manual_sale_batch, columns=["Chassis Number"]),
                use_container_width=True, 
                hide_index=True
            )
            if st.button("🗑️ Clear Entire Batch", key="manual_sale_clear"):
                st.session_state.manual_sale_batch = []
                st.rerun()

        st.divider()

        # --- Section 3: Submit Batch ---
        st.subheader("Sale Details (for all vehicles in batch)")
        
        with st.container(border=True):
            c1, c2 = st.columns(2)
            sale_date = c1.date_input("Date of Sale (for all)", value=date.today())
            remarks = c2.text_input("Remarks Eg: Branch name(Sathupalli, Thallada, etc.)")
            
            if st.button("Submit Sale", type="primary", use_container_width=True):
                batch_to_submit = st.session_state.manual_sale_batch
                
                if not batch_to_submit:
                    st.warning("Batch is empty. Please add vehicles before submitting.")
                elif not remarks:
                    st.warning("Remarks are required (e.g., which sub-branch).")
                else:
                    try:
                        with SessionLocal() as db:
                            success, message = mgr.log_bulk_manual_sub_branch_sale(
                                db, 
                                chassis_list=batch_to_submit, 
                                sale_date=sale_date, 
                                remarks=remarks
                            )
                        
                        if success:
                            st.success(message)
                            st.balloons()
                            st.session_state.manual_sale_batch = [] # Clear batch on success
                            st.rerun()
                        else:
                            st.error(f"Batch Failed: {message}")
                    except Exception as e:
                        st.error(f"An application error occurred: {e}")
    
    elif selected_tab == "🛠️ Stock Correction":
        # This check is crucial for security and prevents rendering
        # if someone tries to access the URL query parameter manually.
        if is_akash_owner: 
            st.header("🛠️ Bulk Stock Correction")
            st.warning("""
                **WARNING:** This tool will overwrite Model, Variant, Color, and Branch ID in the VehicleMaster.
                **Critical Rule:** Any vehicle with a transfer logged after **November 19, 2025** will be **SKIPPED**.
            """)

            # Set the fixed cutoff date
            CUTOFF_DATE = date(2025, 11, 19)
            CORRECTION_DATE = date.today()

            with st.container(border=True):
                st.info(f"Correction Date will be logged as: **{CORRECTION_DATE}**. Transfers after **{CUTOFF_DATE}** will be ignored.")
                st.markdown("""
                    **CSV Required Columns:**
                    * `chassis_no` (mandatory for lookup)
                    * `model` (corrected value)
                    * `variant` (corrected value)
                    * `color` (corrected value)
                    * `current_branch_id` (corrected branch ID)
                """)
                
            st.subheader("Upload Correction CSV")
            uploaded_file = st.file_uploader(
                "Choose a CSV file containing the corrected data.", 
                type="csv", 
                key="correction_csv"
            )

            if uploaded_file is not None:
                # ... (rest of your bulk_correct_stock file reading and execution logic goes here) ...
                try:
                    df = pd.read_csv(uploaded_file, dtype=str)
                    print(df.columns)
                
                    required_cols = ['chassis_no', 'model', 'variant', 'color', 'current_branch_id']
                    missing_cols = [col for col in required_cols if col not in df.columns]
                    
                    if missing_cols:
                        st.error(f"CSV is missing required columns: {', '.join(missing_cols)}")
                    else:
                        st.dataframe(df[['chassis_no', 'model', 'variant', 'current_branch_id']].head())
                        update_batch = df[required_cols].to_dict('records')
                        st.info(f"Ready to process {len(update_batch)} records.")
                        
                        if st.button("EXECUTE BULK CORRECTION", type="primary", use_container_width=True):
                            with SessionLocal() as db:
                                success, message, error_log = mgr.bulk_correct_stock(
                                    db, 
                                    update_batch, 
                                    CORRECTION_DATE,
                                    CUTOFF_DATE
                                )
                            
                            if success:
                                st.success(message)
                                if error_log:
                                    st.warning("See details of skipped vehicles and errors below:")
                                    st.json(error_log)
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(message)
                                if error_log:
                                    st.json(error_log)
                
                except Exception as e:
                    st.error(f"An error occurred while processing the file: {e}")
        else:
             # This message should technically never be seen, but it's a good fallback
             st.error("Access Denied: This tool is restricted to specific users.")
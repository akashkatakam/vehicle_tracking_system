# ui/pdi_dashboard.py
import streamlit as st
import pandas as pd
from datetime import date
import time
from database import SessionLocal
from models import IST_TIMEZONE
from services import stock_service, sales_service, branch_service, report_service, email_import_service
import models
from streamlit_qrcode_scanner import qrcode_scanner
from ui.color_code import COLOR_CODE_MAP


# --- 1. CACHED CONFIGURATION ---
@st.cache_data(ttl=3600)
def load_config_data():
    """Loads static branch and master data. Cached for 1 hour."""
    with SessionLocal() as db:
        try:
            all_branches = branch_service.get_all_branches(db)
            head_branches = branch_service.get_head_branches(db)
            vehicle_master = stock_service.get_vehicle_master_data(db)
            return all_branches, head_branches, vehicle_master
        except Exception as e:
            st.error(f"Database connection error: {e}")
            return [], [], {}


# --- 2. REPORT DIALOG (POPUP) ---
@st.dialog("📊 Detailed Sales & Transfers", width="large")
def show_daily_report_dialog(start_d, end_d, head_map):
    """
    Renders the Sales and Transfer report in a modal popup.
    """
    st.write(f"**Period:** {start_d.strftime('%d-%b-%Y')} to {end_d.strftime('%d-%b-%Y')}")

    try:
        with SessionLocal() as db:
            # --- PART 1: SALES ---
            st.subheader("💰 Sales Summary")
            master_sales_df = report_service.get_sales_report(db, start_d, end_d)

            if master_sales_df.empty:
                st.info("No sales recorded for this period.")
            else:
                sales_found = False
                for head_name, head_id in head_map.items():
                    # Get branches managed by this head
                    t_branches = branch_service.get_managed_branches(db, head_id)
                    t_names = [b.Branch_Name for b in t_branches]

                    # Filter report for these branches
                    t_sales = master_sales_df[master_sales_df.index.isin(t_names)]

                    if not t_sales.empty:
                        sales_found = True
                        st.write(f"**{head_name} Territory**")
                        st.dataframe(t_sales, use_container_width=True)

                if not sales_found:
                    st.warning("No sales found for your territories.")

            st.divider()

            # --- PART 2: TRANSFERS ---
            st.subheader("🚚 Transfers (Outward)")
            has_transfers = False

            for head_name, head_id in head_map.items():
                transfer_df = report_service.get_branch_transfer_summary(db, head_id, start_d, end_d)

                if not transfer_df.empty:
                    has_transfers = True
                    st.write(f"**From {head_name}**")

                    # Pivot for readability
                    piv = transfer_df.pivot_table(
                        index='Destination_Branch', columns=['Model', 'Variant'],
                        values='Total_Quantity', aggfunc='sum', fill_value=0
                    )
                    piv['TOTAL'] = piv.sum(axis=1)
                    st.dataframe(piv, use_container_width=True)

            if not has_transfers:
                st.info("No stock transfers recorded.")

    except Exception as e:
        st.error(f"Error generating report: {e}")


# --- 3. UX HELPERS ---
def render_global_search(db, query_str, branch_ids):
    """
    Searches Sales and Inventory across the ENTIRE territory (list of branch_ids).
    """
    st.info(f"🔍 Searching for '{query_str}' in {len(branch_ids)} branches...")

    # 1. Search Sales (Customer, DC, Phone)
    sales = db.query(models.SalesRecord).filter(
        models.SalesRecord.Branch_ID.in_(branch_ids),
        (models.SalesRecord.Customer_Name.ilike(f"%{query_str}%")) |
        (models.SalesRecord.DC_Number.ilike(f"%{query_str}%")) |
        (models.SalesRecord.chassis_no.ilike(f"%{query_str}%"))
    ).all()

    if sales:
        st.subheader("Customer/Sales Results")
        data = [{
            "Customer": s.Customer_Name,
            "Model": f"{s.Model} {s.Variant}",
            "Status": s.fulfillment_status,
            "PDI By": s.pdi_assigned_to,
            "Branch": s.Branch_ID
        } for s in sales]
        st.dataframe(data, use_container_width=True)

    # 2. Search Inventory (Chassis)
    vehicles = db.query(models.VehicleMaster).filter(
        models.VehicleMaster.current_branch_id.in_(branch_ids),
        models.VehicleMaster.chassis_no.ilike(f"%{query_str}%")
    ).all()

    if vehicles:
        st.subheader("Inventory Results")
        v_data = [{
            "Chassis": v.chassis_no,
            "Model": v.model,
            "Color": v.color,
            "Status": v.status,
            "Location": v.current_branch_id
        } for v in vehicles]
        st.dataframe(v_data, use_container_width=True)

    if not sales and not vehicles:
        st.warning("No results found.")


# --- 4. MODULAR TAB FUNCTIONS ---

def render_tab_overview(managed_ids):
    """
    Overview Tab: Calculates counts for the ENTIRE TERRITORY (Head + Subs).
    """
    st.header("👋 Good Morning, Manager")

    with SessionLocal() as db:
        # FIX: Filter by the list of ALL managed IDs, not just the single head ID.
        pending_cnt = db.query(models.SalesRecord).filter(
            models.SalesRecord.Branch_ID.in_(managed_ids),
            models.SalesRecord.fulfillment_status == "PDI Pending"
        ).count()

        wip_cnt = db.query(models.SalesRecord).filter(
            models.SalesRecord.Branch_ID.in_(managed_ids),
            models.SalesRecord.fulfillment_status == "PDI In Progress"
        ).count()

        transit_cnt = db.query(models.VehicleMaster).filter(
            models.VehicleMaster.current_branch_id.in_(managed_ids),
            models.VehicleMaster.status == "In Transit"
        ).count()

        stock_cnt = db.query(models.VehicleMaster).filter(
            models.VehicleMaster.current_branch_id.in_(managed_ids),
            models.VehicleMaster.status == "In Stock"
        ).count()

    # KPI ROW
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚨 PDI Pending", pending_cnt)
    c2.metric("🔧 In Progress", wip_cnt)
    c3.metric("🚚 In Transit", transit_cnt)
    c4.metric("🏍️ Stock On Hand", stock_cnt, help="Total stock across Head Branch + Sub-branches")

    st.divider()

    # Global Search
    search_query = st.text_input("🔎 Universal Search", placeholder="Enter Chassis No, Customer Name, or DC Number...")
    if search_query:
        with SessionLocal() as db:
            render_global_search(db, search_query, managed_ids)


def render_tab_pdi_management(branch_id):
    """Combined Assignment & Progress Tracking"""
    c1, c2 = st.columns([1, 1])

    with SessionLocal() as db:
        pending_pdi = sales_service.get_sales_records_by_status(db, "PDI Pending", branch_id=branch_id)
        in_progress = sales_service.get_sales_records_by_status(db, "PDI In Progress", branch_id=branch_id)
        mechanics = branch_service.get_users_by_role(db, "Mechanic")

    with c1:
        st.subheader("📋 Assign Pending Tasks")
        if pending_pdi.empty:
            st.info("No pending tasks.")
        else:
            with st.form("quick_assign"):
                mechanic_names = [m.username for m in mechanics]
                target_mech = st.selectbox("Select Mechanic", mechanic_names)

                # Create a display string that helps identify the record
                pending_pdi['display'] = pending_pdi['DC_Number'] + " (" + pending_pdi['Customer_Name'] + ")"
                selected_display = st.selectbox("Select Sale", pending_pdi['display'])

                if st.form_submit_button("➡️ Assign"):
                    # Find the ID corresponding to the selection
                    sel_record = pending_pdi[pending_pdi['display'] == selected_display].iloc[0]
                    with SessionLocal() as db:
                        sales_service.assign_pdi_mechanic(db, int(sel_record['id']), target_mech)
                    st.toast(f"Assigned to {target_mech}!", icon="✅")
                    time.sleep(1)
                    st.rerun()

    with c2:
        st.subheader("👀 Monitoring")
        if in_progress.empty:
            st.caption("No active work.")
        else:
            st.dataframe(
                in_progress[['Customer_Name', 'Model', 'pdi_assigned_to']],
                use_container_width=True,
                column_config={
                    "pdi_assigned_to": st.column_config.TextColumn("Mechanic", help="Who is working on this?"),
                    "Model": st.column_config.TextColumn("Vehicle", max_chars=20)
                }
            )


# --- CONSOLIDATED TAB WRAPPERS ---

def render_tab_inventory(managed_map, vehicle_master_data):
    """Consolidated Inventory Tab: Locator + Stock Levels"""
    st.caption("Search, locate, and analyze stock across branches.")
    t1, t2 = st.tabs(["🔍 Locator", "📊 Stock Levels"])

    with t1:
        render_tab_locator(vehicle_master_data)

    with t2:
        render_tab_stock_interactive(managed_map)


def render_tab_logistics(current_head_id, current_head_name, all_branch_map):
    """Consolidated Logistics Tab: Inward + Transfers"""
    st.caption("Manage incoming shipments and outward transfers.")
    t1, t2 = st.tabs(["📥 Receive (Inward)", "📤 Transfer / Outward"])

    with t1:
        render_tab_inward_actions(current_head_id)

    with t2:
        render_tab_transfers(current_head_id, current_head_name, all_branch_map)


# --- INDIVIDUAL COMPONENTS (CALLED BY WRAPPERS) ---

def render_tab_stock_interactive(managed_map):
    st.subheader("📊 Inventory Drill-down")

    # Filters
    cols = st.columns([2, 1])
    selected_branches = cols[0].multiselect("Filter Branches", options=managed_map.keys(),
                                            default=list(managed_map.keys()))

    if st.button("🔄 Refresh Stock"):
        st.cache_data.clear()
        st.rerun()

    if selected_branches:
        sel_ids = [managed_map[n] for n in selected_branches]
        with SessionLocal() as db:
            df = stock_service.get_multi_branch_stock(db, sel_ids)

        if not df.empty:
            # 1. High Level Metrics
            total_stock = int(df['Stock'].sum())
            st.metric("Total Territory Stock", total_stock)

            st.divider()

            # 2. Interactive Selection
            c1, c2 = st.columns(2)

            # MODEL SELECTOR
            with c1:
                # Group by model to get counts
                model_summary = df.groupby('model')['Stock'].sum().sort_values(ascending=False)
                model_opts = ["All Models"] + model_summary.index.tolist()

                sel_model = st.selectbox("1. Select Model", model_opts)

            # VARIANT SELECTOR (Conditional)
            sel_variant = "All Variants"
            with c2:
                if sel_model != "All Models":
                    # Filter for variants of selected model
                    model_df = df[df['model'] == sel_model]
                    variant_summary = model_df.groupby('variant')['Stock'].sum().sort_values(ascending=False)
                    variant_opts = ["All Variants"] + variant_summary.index.tolist()

                    sel_variant = st.selectbox("2. Select Variant", variant_opts)
                else:
                    st.info("Select a model to filter variants.")

            # 3. Data Display Logic
            st.markdown("### 📦 Stock View")

            # Filter Data based on selections
            filtered_df = df.copy()
            if sel_model != "All Models":
                filtered_df = filtered_df[filtered_df['model'] == sel_model]

            if sel_variant != "All Variants":
                filtered_df = filtered_df[filtered_df['variant'] == sel_variant]

            # VIEW 1: If specific Variant is selected -> Show Color vs Branch Matrix (The requested view)
            if sel_model != "All Models" and sel_variant != "All Variants":
                st.success(f"Showing stock for **{sel_model} {sel_variant}**")

                # Pivot: Index=Branch, Col=Color
                pivot = filtered_df.pivot_table(
                    index='Branch_Name',
                    columns='color',
                    values='Stock',
                    aggfunc='sum',
                    fill_value=0
                )
                pivot['TOTAL'] = pivot.sum(axis=1)
                pivot = pivot.sort_values('TOTAL', ascending=False)

                st.dataframe(pivot, use_container_width=True)

            # VIEW 2: If only Model is selected -> Show Variant Summary
            elif sel_model != "All Models":
                st.caption(f"Breakdown of {sel_model} by Variant")
                var_group = filtered_df.groupby('variant')['Stock'].sum().reset_index().sort_values('Stock',
                                                                                                    ascending=False)
                st.dataframe(var_group, use_container_width=True)

            # VIEW 3: Overview (All Models) -> Show Model Summary
            else:
                st.caption("Overview by Model")
                mod_group = filtered_df.groupby('model')['Stock'].sum().reset_index().sort_values('Stock',
                                                                                                  ascending=False)
                st.dataframe(mod_group, use_container_width=True)

        else:
            st.warning("No stock found in selected branches.")


def render_tab_locator(vehicle_master_data):
    st.subheader("🔍 Vehicle Locator")

    # Toggle Search Mode
    search_mode = st.radio("Search Mode:", ["By Attributes", "By Chassis"], horizontal=True)

    found_vehicles = pd.DataFrame()

    with st.container(border=True):
        if search_mode == "By Attributes":
            c1, c2, c3 = st.columns(3)

            # Cascading Dropdowns
            sel_model = c1.selectbox("Model", options=[""] + list(vehicle_master_data.keys()))

            variants = []
            if sel_model and sel_model in vehicle_master_data:
                variants = list(vehicle_master_data[sel_model].keys())
            sel_variant = c2.selectbox("Variant", options=[""] + variants)

            colors = []
            if sel_model and sel_variant and sel_variant in vehicle_master_data[sel_model]:
                colors = vehicle_master_data[sel_model][sel_variant]
            sel_color = c3.selectbox("Color", options=[""] + colors)

            if st.button("Search Vehicles", type="primary"):
                if not sel_model:
                    st.warning("Please select at least a Model.")
                else:
                    with SessionLocal() as db:
                        found_vehicles = stock_service.search_vehicles(
                            db, model=sel_model, variant=sel_variant, color=sel_color
                        )

        else:
            # By Chassis
            chassis_input = st.text_input("Enter Chassis Number (Full or Partial):")
            if st.button("Search Chassis", type="primary"):
                if len(chassis_input) < 4:
                    st.warning("Please enter at least 4 characters.")
                else:
                    with SessionLocal() as db:
                        found_vehicles = stock_service.search_vehicles(db, chassis=chassis_input)

    # Display Results
    if not found_vehicles.empty:
        st.success(f"Found {len(found_vehicles)} vehicles.")
        st.dataframe(
            found_vehicles,
            use_container_width=True,
            column_config={
                "Current_Location": st.column_config.TextColumn("Branch", help="Where is the vehicle now?"),
                "status": st.column_config.TextColumn("Status", width="small")
            }
        )
    elif st.session_state.get("search_performed", False):
        # Optional: Add a state check to only show this if a search was actually attempted
        st.info("No vehicles found matching criteria.")


def render_tab_reports(current_head_id, current_head_name, all_branch_map):
    st.header("📈 Reports & Summaries")

    report_type = st.selectbox(
        "Select Report Type:",
        ["Summary: Outward (Head -> Branches)", "Summary: OEM Inward (HMSI)"]
    )

    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 1, 1])

        if report_type == "Summary: Outward (Head -> Branches)":
            c1.text_input("Source Branch:", value=current_head_name, disabled=True)
            report_branch_id = current_head_id
        else:
            # For OEM Inward, allow selecting ANY branch (sometimes sub-branches receive direct)
            selected_branch_name = c1.selectbox("Select Branch for HMSI Report:", list(all_branch_map.keys()))
            report_branch_id = all_branch_map[selected_branch_name]

        start_date = c2.date_input("Start Date", value=date.today().replace(day=1))
        end_date = c3.date_input("End Date", value=date.today())

    if st.button("Generate Report", type="primary"):
        with SessionLocal() as db:
            if "Outward" in report_type:
                st.subheader(f"📤 Outward Summary: From {current_head_name}")
                df = report_service.get_branch_transfer_summary(db, report_branch_id, start_date, end_date)

                if not df.empty:
                    # Pivot for readability: Model/Variant vs Destination Branch
                    piv = df.pivot_table(
                        index=['Model', 'Variant'],
                        columns='Destination_Branch',
                        values='Total_Quantity',
                        aggfunc='sum',
                        fill_value=0
                    )
                    piv['TOTAL'] = piv.sum(axis=1)
                    st.dataframe(piv, use_container_width=True)
                    st.metric("Total Transferred", int(piv['TOTAL'].sum()))
                else:
                    st.info("No transfers recorded for this period.")
            else:
                st.subheader(f"📥 OEM Inward Summary: {all_branch_map.get(report_branch_id, '')}")
                df = report_service.get_oem_inward_summary(db, report_branch_id, start_date, end_date)

                if not df.empty:
                    st.dataframe(df, use_container_width=True)
                    st.metric("Total Received", int(df['Total_Received'].sum()))
                else:
                    st.info("No inward stock found.")


def render_tab_inward_actions(head_id):
    st.subheader("📥 Receive Stock")

    with SessionLocal() as db:
        pending_loads = stock_service.get_pending_loads(db, head_id)

    if pending_loads:
        st.info(f"You have {len(pending_loads)} loads waiting to be received.")
        for load in pending_loads:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.write(f"🚛 **Load #{load}** (Status: In Transit)")
                if c2.button("Receive", key=f"btn_{load}"):
                    with SessionLocal() as db:
                        success, msg = stock_service.receive_load(db, head_id, load)
                    if success:
                        st.toast(msg, icon="🎉")
                        time.sleep(1)
                        st.rerun()
    else:
        st.success("All caught up! No transit loads pending.")

    with st.expander("📧 Fetch from Email (HMSI)"):
        if st.button("Scan Emails"):
            with st.status("Connecting to Gmail...", expanded=True) as status:
                with SessionLocal() as db:
                    batches, logs = email_import_service.fetch_and_process_emails(db, head_id)
                status.update(label="Scan Complete!", state="complete", expanded=False)

                if batches:
                    st.session_state['transit_import_data'] = pd.DataFrame(batches).to_dict('records')
                    st.rerun()
                else:
                    st.toast("No new S08 files found.", icon="ℹ️")

    if 'transit_import_data' in st.session_state:
        st.divider()
        st.write("📝 **Previewing Import**")
        df = pd.DataFrame(st.session_state['transit_import_data'])
        st.dataframe(df.head(), use_container_width=True)

        c1, c2 = st.columns([1, 4])
        if c1.button("Confirm & Save", type="primary"):
            with SessionLocal() as db:
                stock_service.log_bulk_inward_master(
                    db, head_id, "Auto-Import", "MULTI", date.today(),
                    "Batch Import", st.session_state['transit_import_data'], initial_status='In Transit'
                )
            st.toast("Saved successfully!", icon="💾")
            del st.session_state['transit_import_data']
            time.sleep(1)
            st.rerun()

        if c2.button("Cancel"):
            del st.session_state['transit_import_data']
            st.rerun()


def render_tab_transfers(current_head_id, current_head_name, all_branch_map):
    st.subheader("📤 Outward Operations")

    # Mode Selector to switch between Transfer and Sale
    action_mode = st.radio(
        "Select Action:",
        ["Transfer to Sub-Dealer", "Log Manual Sale"],
        horizontal=True,
        label_visibility="collapsed"
    )

    st.divider()

    # --- MODE 1: BRANCH TRANSFER ---
    if action_mode == "Transfer to Sub-Dealer":
        st.caption(f"📍 Moving Stock FROM: **{current_head_name}**")

        # 1. Transfer Configuration
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            # Filter distinct branches to avoid transferring to self
            dest_options = [name for name, bid in all_branch_map.items() if bid != current_head_id]
            dest_name = c1.selectbox("Destination Branch:", options=dest_options)
            date_out = c2.date_input("Transfer Date:", value=date.today())
            remarks_out = c3.text_input("DC Number / Remarks:")

        # 2. Add to Batch Logic
        _render_batch_builder(
            batch_key="transfer_batch",
            scanner_key="transfer_scanner",
            btn_label="Add to Transfer List"
        )

        # 3. Submit
        if st.session_state.get("transfer_batch"):
            st.warning(f"Ready to transfer {len(st.session_state.transfer_batch)} vehicles to {dest_name}.")

            if st.button("🚀 Confirm Transfer", type="primary", use_container_width=True):
                if not remarks_out:
                    st.error("Please enter a DC Number or Remark.")
                else:
                    try:
                        with SessionLocal() as db:
                            stock_service.log_bulk_transfer_master(
                                db,
                                current_head_id,
                                all_branch_map[dest_name],
                                date_out,
                                remarks_out,
                                st.session_state.transfer_batch
                            )
                        st.toast(f"Successfully transferred {len(st.session_state.transfer_batch)} vehicles!", icon="✅")
                        st.session_state.transfer_batch = []  # Clear batch
                        st.cache_data.clear()
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Transfer failed: {e}")

    # --- MODE 2: MANUAL SALE ---
    elif action_mode == "Log Manual Sale":
        st.caption("📝 Mark vehicles as 'Sold' manually (e.g. for Sub-branches without system access).")

        # 1. Sale Configuration
        with st.container(border=True):
            c1, c2 = st.columns(2)
            sale_date = c1.date_input("Date of Sale:", value=date.today())
            remarks_sale = c2.text_input("Remarks (e.g. Sub-branch Name):")

        # 2. Add to Batch Logic
        _render_batch_builder(
            batch_key="manual_sale_batch",
            scanner_key="manual_sale_scanner",
            btn_label="Add to Sale List"
        )

        # 3. Submit
        if st.session_state.get("manual_sale_batch"):
            st.warning(f"Ready to mark {len(st.session_state.manual_sale_batch)} vehicles as SOLD.")

            if st.button("💰 Confirm Sale", type="primary", use_container_width=True):
                if not remarks_sale:
                    st.error("Remarks are required.")
                else:
                    try:
                        with SessionLocal() as db:
                            success, msg = stock_service.log_bulk_manual_sub_branch_sale(
                                db,
                                st.session_state.manual_sale_batch,
                                sale_date,
                                remarks_sale
                            )
                        if success:
                            st.toast(msg, icon="🎉")
                            st.session_state.manual_sale_batch = []
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(msg)
                    except Exception as e:
                        st.error(f"Error: {e}")


def _render_batch_builder(batch_key, scanner_key, btn_label):
    """
    Helper to render the Scan/Type -> Add to Batch UI.
    """
    # Initialize batch if missing
    if batch_key not in st.session_state:
        st.session_state[batch_key] = []

    # Input Area
    c1, c2 = st.columns([3, 1])

    # QR Scanner
    scan_val = qrcode_scanner(key=scanner_key)
    if scan_val:
        # Auto-add if scanned
        if scan_val not in st.session_state[batch_key]:
            st.session_state[batch_key].append(scan_val)
            st.toast(f"Added {scan_val}", icon="📦")
            time.sleep(0.5)
            st.rerun()

    # Manual Input
    with c1:
        manual_val = st.text_input("Chassis Number", key=f"input_{batch_key}", placeholder="Type or Scan...")

    with c2:
        st.write("")  # Spacer
        st.write("")
        if st.button("⬇️ Add", key=f"btn_{batch_key}"):
            if manual_val and manual_val not in st.session_state[batch_key]:
                st.session_state[batch_key].append(manual_val)
                st.rerun()
            elif manual_val in st.session_state[batch_key]:
                st.warning("Already in batch.")

    # Batch Display
    if st.session_state[batch_key]:
        st.divider()
        st.markdown(f"**Current Batch ({len(st.session_state[batch_key])})**")

        # Show as a horizontal pill list or dataframe
        st.dataframe(
            pd.DataFrame(st.session_state[batch_key], columns=["Chassis Number"]),
            use_container_width=True,
            hide_index=True
        )

        if st.button("🗑️ Clear Batch", key=f"clear_{batch_key}"):
            st.session_state[batch_key] = []
            st.rerun()


# --- 4. MAIN LAYOUT ---
def render():
    st.title("🚀 PDI Command Center")

    # Context Loading
    all_branches, head_branches, vehicle_master = load_config_data()
    all_branch_map = {b.Branch_Name: b.Branch_ID for b in all_branches}  # For reports
    head_map = {b.Branch_Name: b.Branch_ID for b in head_branches}
    branch_id = st.session_state.inventory_branch_id
    user_role = st.session_state.get('inventory_user_role', '')

    # Sidebar Context
    with st.sidebar:
        st.caption("📍 Context")
        if branch_id == "All Branches" or branch_id is None:
            sel_name = st.selectbox("Territory", options=head_map.keys())
            current_head_id = head_map[sel_name]
            current_head_name = sel_name
        else:
            st.text_input("Territory", value=st.session_state.inventory_branch_name, disabled=True)
            current_head_id = branch_id
            current_head_name = st.session_state.inventory_branch_name

        st.divider()

        # --- RESTORED: Daily Sales & Transfer Report (IN POPUP) ---
        with st.expander("📊 Detailed Sales & Transfers", expanded=True):
            st.caption("Generate Report")
            today = date.today()
            date_range = st.date_input(
                "Date Range",
                value=(today, today)
            )

            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_d, end_d = date_range
                if st.button("Run Report", type="primary", use_container_width=True):
                    show_daily_report_dialog(start_d, end_d, head_map)

        st.divider()

        # --- NEW: Admin S08 Mappings (Owner Only) ---
        if user_role == "Owner":
            with st.expander("🛠️ Admin: S08 Mappings"):
                # 1. Add New
                with st.form("add_mapping_form"):
                    st.write("Add New Product Mapping")
                    mc = st.text_input("Model Code (e.g. ACT6G)")
                    vc = st.text_input("Variant Code (e.g. 5ID)")
                    rm = st.text_input("Real Model (e.g. Activa 6G)")
                    rv = st.text_input("Real Variant (e.g. STD)")

                    if st.form_submit_button("Add Mapping"):
                        if mc and vc and rm and rv:
                            with SessionLocal() as db:
                                success, msg = stock_service.add_product_mapping(db, mc, vc, rm, rv)
                            if success:
                                st.success(msg)
                            else:
                                st.error(msg)
                        else:
                            st.warning("All fields required.")

                # 2. View Existing (Restored Checkbox)
                if st.checkbox("Show Current Mappings"):
                    with SessionLocal() as db:
                        mappings_df = stock_service.get_all_product_mappings(db)

                    if not mappings_df.empty:
                        st.dataframe(mappings_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No mappings found.")

    # Get managed branches for this head
    with SessionLocal() as db:
        managed = branch_service.get_managed_branches(db, current_head_id)

    managed_map = {b.Branch_Name: b.Branch_ID for b in managed}
    managed_ids = list(managed_map.values())  # Helper list for queries

    # NAVIGATION
    tabs = st.tabs([
        "🏠 Overview",
        "📋 Task Manager",
        "🏍️ Inventory",
        "🚚 Logistics",
        "📈 Reports"
    ])

    with tabs[0]:
        render_tab_overview(managed_ids)

    with tabs[1]:
        render_tab_pdi_management(current_head_id)

    with tabs[2]:
        render_tab_inventory(managed_map, vehicle_master)

    with tabs[3]:
        render_tab_logistics(current_head_id, current_head_name, all_branch_map)

    with tabs[4]:
        render_tab_reports(current_head_id, current_head_name, all_branch_map)
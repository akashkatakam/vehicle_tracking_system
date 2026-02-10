# ui/pdi_dashboard.py
import streamlit as st
import pandas as pd
from datetime import date
import time
from database import SessionLocal
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
    Searches Sales and Inventory across the ENTIRE territory.
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

def render_tab_overview(managed_ids,current_head_id):
    st.header("👋 Good Morning, Manager")

    with SessionLocal() as db:
        pending_cnt = db.query(models.SalesRecord).filter(
            models.SalesRecord.Branch_ID == current_head_id,
            models.SalesRecord.fulfillment_status == "PDI Pending"
        ).count()

        wip_cnt = db.query(models.SalesRecord).filter(
            models.SalesRecord.Branch_ID == current_head_id,
            models.SalesRecord.fulfillment_status == "PDI In Progress"
        ).count()

        transit_cnt = db.query(models.VehicleMaster).filter(
            models.VehicleMaster.current_branch_id == current_head_id,
            models.VehicleMaster.status == "In Transit"
        ).count()

        stock_cnt = db.query(models.VehicleMaster).filter(
            models.VehicleMaster.current_branch_id == current_head_id,
            models.VehicleMaster.status == "In Stock"
        ).count()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚨 PDI Pending", pending_cnt)
    c2.metric("🔧 In Progress", wip_cnt)
    c3.metric("🚚 In Transit", transit_cnt)
    c4.metric("🏍️ Stock On Hand", stock_cnt)

    st.divider()

    search_query = st.text_input("🔎 Universal Search", placeholder="Enter Chassis No, Customer Name, or DC Number...")
    if search_query:
        with SessionLocal() as db:
            render_global_search(db, search_query, managed_ids)


def render_tab_pdi_management(branch_id):
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
                pending_pdi['display'] = pending_pdi['DC_Number'] + " (" + pending_pdi['Customer_Name'] + ")"
                selected_display = st.selectbox("Select Sale", pending_pdi['display'])

                if st.form_submit_button("➡️ Assign"):
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
                column_config={"pdi_assigned_to": "Mechanic", "Model": "Vehicle"}
            )


# --- CONSOLIDATED TAB WRAPPERS ---

def render_tab_inventory(managed_map, vehicle_master_data):
    st.caption("Search, locate, and analyze stock across branches.")
    t1, t2 = st.tabs(["🔍 Locator", "📊 Stock Levels"])

    with t1:
        render_tab_locator(vehicle_master_data)

    with t2:
        render_tab_stock_interactive(managed_map)


def render_tab_logistics(current_head_id, current_head_name, all_branch_map):
    st.caption("Manage incoming shipments and outward transfers.")
    t1, t2 = st.tabs(["📥 Receive (Inward)", "📤 Transfer / Outward"])

    with t1:
        render_tab_inward_actions(current_head_id)

    with t2:
        render_tab_transfers(current_head_id, current_head_name, all_branch_map)


# --- INDIVIDUAL COMPONENTS ---

def render_tab_stock_interactive(managed_map):
    """
    Mobile-First Stock View:
    - Filters: Branch + Universal Text Search
    - Display: List of Models (Accordions) -> Details Table
    """
    # 1. Filters Section
    with st.container(border=True):
        st.subheader("📊 Stock Overview")

        # Branch Filter (Collapsible to save space on mobile)
        with st.expander("🌍 Filter Branches", expanded=False):
            selected_branches = st.multiselect(
                "Select Branches",
                options=managed_map.keys(),
                default=list(managed_map.keys())
            )

        # Smart Text Search
        c1, c2 = st.columns([3, 1])
        search_term = c1.text_input("🔍 Quick Filter", placeholder="Type Model, Variant, Color or Branch...")
        if c2.button("Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # 2. Data Fetching
    if selected_branches:
        sel_ids = [managed_map[n] for n in selected_branches]
        with SessionLocal() as db:
            df = stock_service.get_multi_branch_stock(db, sel_ids)

        if not df.empty:
            # 3. Apply Text Search Filter (Case Insensitive)
            if search_term:
                term = search_term.lower()
                df = df[
                    df['model'].str.lower().str.contains(term) |
                    df['variant'].str.lower().str.contains(term) |
                    df['color'].str.lower().str.contains(term) |
                    df['Branch_Name'].str.lower().str.contains(term)
                    ]

            if df.empty:
                st.warning("No stock matches your search.")
                return

            # 4. Metrics
            total_stock = int(df['Stock'].sum())
            unique_models = df['model'].nunique()
            st.caption(f"Showing **{total_stock}** vehicles across **{unique_models}** models.")

            # 5. Render Accordions (Group by Model)
            # Sort models by stock count descending
            model_counts = df.groupby('model')['Stock'].sum().sort_values(ascending=False)

            for model_name, count in model_counts.items():
                # Card-like Expander
                with st.expander(f"🏍️ {model_name} ({count})"):
                    # Filter data for this model
                    model_df = df[df['model'] == model_name]

                    # Layout: Metrics + Table

                    # A. Quick Variant Stats (Pills)
                    var_stats = model_df.groupby('variant')['Stock'].sum().to_dict()
                    stats_text = " | ".join([f"**{k}:** {v}" for k, v in var_stats.items()])
                    st.markdown(stats_text)

                    st.divider()

                    # B. Detailed Table
                    # We want: Variant | Color | Branch | Stock
                    display_df = model_df[['variant', 'color', 'Branch_Name', 'Stock']].copy()
                    display_df = display_df.rename(columns={
                        'variant': 'Variant',
                        'color': 'Color',
                        'Branch_Name': 'Location',
                        'Stock': 'Qty'
                    }).sort_values(by=['Variant', 'Qty'], ascending=[True, False])

                    st.dataframe(
                        display_df,
                        use_container_width=True,
                        column_config={
                            "Qty": st.column_config.ProgressColumn(
                                "Qty",
                                min_value=0,
                                max_value=int(display_df['Qty'].max()),
                                format="%d"
                            ),
                            "Location": st.column_config.TextColumn("Location", width="medium")
                        },
                        hide_index=True
                    )

        else:
            st.info("Stock room is empty for the selected branches.")
    else:
        st.warning("Please select at least one branch.")


def render_tab_locator(vehicle_master_data):
    st.subheader("🔍 Vehicle Locator")

    search_mode = st.radio("Search Mode:", ["By Attributes", "By Chassis"], horizontal=True)

    found_vehicles = pd.DataFrame()

    with st.container(border=True):
        if search_mode == "By Attributes":
            c1, c2, c3 = st.columns(3)
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
            chassis_input = st.text_input("Enter Chassis Number (Full or Partial):")
            if st.button("Search Chassis", type="primary"):
                if len(chassis_input) < 4:
                    st.warning("Please enter at least 4 characters.")
                else:
                    with SessionLocal() as db:
                        found_vehicles = stock_service.search_vehicles(db, chassis=chassis_input)

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
            # --- MODIFIED: Show Details in Expander ---
            with st.expander(f"🚛 Load #{load} (In Transit)", expanded=False):
                with SessionLocal() as db:
                    # Fetch details
                    load_vehicles = stock_service.get_vehicles_in_load(db, head_id, load)

                # Show Table
                st.dataframe(
                    load_vehicles,
                    use_container_width=True,
                    column_config={
                        "Chassis No": st.column_config.TextColumn("Chassis", width="medium"),
                        "Color": st.column_config.TextColumn("Color", width="small")
                    }
                )

                # Receive Button (Inside the expander now, more context)
                if st.button(f"📥 Receive Entire Load ({len(load_vehicles)} Vehicles)", key=f"btn_{load}",
                             type="primary", use_container_width=True):
                    with SessionLocal() as db:
                        success, msg = stock_service.receive_load(db, head_id, load)
                    if success:
                        st.toast(msg, icon="🎉")
                        time.sleep(1)
                        st.rerun()
    else:
        st.success("All caught up! No transit loads pending.")

    # --- INTERACTIVE EMAIL SCANNER ---
    with st.expander("📧 Fetch from Email (HMSI)", expanded=False):
        st.caption("Scans recent emails for S08 attachments and decodes them.")

        if st.button("🚀 Start Email Scan"):
            with st.status("Connecting to Email Server...", expanded=True) as status:

                def status_update(msg):
                    status.write(msg)

                with SessionLocal() as db:
                    batches, logs = email_import_service.fetch_and_process_emails(
                        db,
                        head_id,
                        color_map=COLOR_CODE_MAP,
                        progress_callback=status_update
                    )

                if batches:
                    status.update(label="✅ Scan Complete! Found new vehicles.", state="complete", expanded=False)
                    st.session_state['transit_import_data'] = pd.DataFrame(batches).to_dict('records')
                    st.rerun()
                else:
                    status.update(label="ℹ️ Scan Complete. No new files found.", state="complete", expanded=False)
                    st.toast("No new S08 files found.", icon="ℹ️")

    # --- EDITABLE PREVIEW ---
    if 'transit_import_data' in st.session_state:
        st.divider()
        st.subheader("📝 Review & Edit Import")
        st.caption("You can edit the details below before saving to the database.")

        df = pd.DataFrame(st.session_state['transit_import_data'])

        edited_df = st.data_editor(
            df,
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "color": st.column_config.TextColumn("Color", help="Readable Color Name"),
                "model": st.column_config.TextColumn("Model"),
                "variant": st.column_config.TextColumn("Variant"),
                "chassis_no": st.column_config.TextColumn("Chassis No", disabled=True),
            }
        )

        c1, c2 = st.columns([1, 4])

        if c1.button("💾 Confirm & Save", type="primary"):
            final_data = edited_df.to_dict('records')

            with SessionLocal() as db:
                stock_service.log_bulk_inward_master(
                    db, head_id, "Auto-Import", "MULTI", date.today(),
                    "Batch Import", final_data, initial_status='In Transit'
                )
            st.toast(f"Successfully saved {len(final_data)} vehicles!", icon="💾")
            del st.session_state['transit_import_data']
            time.sleep(1)
            st.rerun()

        if c2.button("❌ Discard"):
            del st.session_state['transit_import_data']
            st.rerun()


def render_tab_transfers(current_head_id, current_head_name, all_branch_map):
    st.subheader("📤 Outward Operations")

    action_mode = st.radio(
        "Select Action:",
        ["Transfer to Sub-Dealer", "Log Manual Sale"],
        horizontal=True,
        label_visibility="collapsed"
    )

    st.divider()

    if action_mode == "Transfer to Sub-Dealer":
        st.caption(f"📍 Moving Stock FROM: **{current_head_name}**")
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            dest_options = [name for name, bid in all_branch_map.items() if bid != current_head_id]
            dest_name = c1.selectbox("Destination Branch:", options=dest_options)
            date_out = c2.date_input("Transfer Date:", value=date.today())
            remarks_out = c3.text_input("DC Number / Remarks:")

        _render_batch_builder(
            batch_key="transfer_batch",
            scanner_key="transfer_scanner",
            btn_label="Add to Transfer List"
        )

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
                        st.session_state.transfer_batch = []
                        st.cache_data.clear()
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Transfer failed: {e}")

    elif action_mode == "Log Manual Sale":
        st.caption("📝 Mark vehicles as 'Sold' manually.")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            sale_date = c1.date_input("Date of Sale:", value=date.today())
            remarks_sale = c2.text_input("Remarks (e.g. Sub-branch Name):")

        _render_batch_builder(
            batch_key="manual_sale_batch",
            scanner_key="manual_sale_scanner",
            btn_label="Add to Sale List"
        )

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
    if batch_key not in st.session_state:
        st.session_state[batch_key] = []

    c1, c2 = st.columns([3, 1])
    scan_val = qrcode_scanner(key=scanner_key)
    if scan_val:
        if scan_val not in st.session_state[batch_key]:
            st.session_state[batch_key].append(scan_val)
            st.toast(f"Added {scan_val}", icon="📦")
            time.sleep(0.5)
            st.rerun()

    with c1:
        manual_val = st.text_input("Chassis Number", key=f"input_{batch_key}", placeholder="Type or Scan...")

    with c2:
        st.write("")
        st.write("")
        if st.button("⬇️ Add", key=f"btn_{batch_key}"):
            if manual_val and manual_val not in st.session_state[batch_key]:
                st.session_state[batch_key].append(manual_val)
                st.rerun()
            elif manual_val in st.session_state[batch_key]:
                st.warning("Already in batch.")

    if st.session_state[batch_key]:
        st.divider()
        st.markdown(f"**Current Batch ({len(st.session_state[batch_key])})**")
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

    all_branches, head_branches, vehicle_master = load_config_data()
    all_branch_map = {b.Branch_Name: b.Branch_ID for b in all_branches}
    head_map = {b.Branch_Name: b.Branch_ID for b in head_branches}
    branch_id = st.session_state.inventory_branch_id
    user_role = st.session_state.get('inventory_user_role', '')

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

        with st.expander("📊 Detailed Sales & Transfers", expanded=True):
            st.caption("Generate Report")
            today = date.today()
            date_range = st.date_input("Date Range", value=(today, today), max_value=today)

            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_d, end_d = date_range
                if st.button("Run Report", type="primary", use_container_width=True):
                    show_daily_report_dialog(start_d, end_d, head_map)

        st.divider()

        if user_role == "Owner":
            with st.expander("🛠️ Admin: S08 Mappings"):
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

                if st.checkbox("Show Current Mappings"):
                    with SessionLocal() as db:
                        mappings_df = stock_service.get_all_product_mappings(db)

                    if not mappings_df.empty:
                        st.dataframe(mappings_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No mappings found.")

    with SessionLocal() as db:
        managed = branch_service.get_managed_branches(db, current_head_id)

    managed_map = {b.Branch_Name: b.Branch_ID for b in managed}
    managed_ids = list(managed_map.values())

    tabs = st.tabs([
        "🏠 Overview",
        "📋 Task Manager",
        "🏍️ Inventory",
        "🚚 Logistics",
        "📈 Reports"
    ])

    with tabs[0]:
        render_tab_overview(managed_ids, current_head_id)

    with tabs[1]:
        render_tab_pdi_management(current_head_id)

    with tabs[2]:
        render_tab_inventory(managed_map, vehicle_master)

    with tabs[3]:
        render_tab_logistics(current_head_id, current_head_name, all_branch_map)

    with tabs[4]:
        render_tab_reports(current_head_id, current_head_name, all_branch_map)
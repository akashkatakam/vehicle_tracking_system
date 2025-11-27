# ui/mechanic_dashboard.py
import streamlit as st
import pandas as pd
from database import SessionLocal
import inventory_manager as mgr
from streamlit_qrcode_scanner import qrcode_scanner

def render():
    """
    Renders the simple view for the Mechanic role.
    """
    st.title("🔧 My PDI Tasks")
    
    username = st.session_state.inventory_username
    branch_id = st.session_state.inventory_branch_id
    
    my_tasks = pd.DataFrame()
    try:
        with SessionLocal() as db:
            my_tasks = mgr.get_sales_records_for_mechanic(db, username, branch_id=branch_id)
        
        if my_tasks.empty:
            st.success("No pending tasks. Great job!")
            return

        my_tasks['display'] = my_tasks['DC_Number'] + " (" + my_tasks['Customer_Name'] + " - " + my_tasks['Model'] + ")"
        
        task_display_str = st.selectbox("Select Task to Complete:", my_tasks['display'])
        
        if task_display_str:
            selected_task = my_tasks[my_tasks['display'] == task_display_str].iloc[0]
            sale_id = int(selected_task['id'])
            dc_number = str(selected_task['DC_Number'])
            
            st.subheader(f"Complete Task: {selected_task['DC_Number']}")
            st.write(f"**Customer:** {selected_task['Customer_Name']}")
            st.write(f"**Vehicle Request:** {selected_task['Model']} / {selected_task['Variant']} / {selected_task['Paint_Color']}")
            
            st.warning("Camera Not Working? Ensure `https://` URL and browser permissions.")
            
            st.divider()
            st.subheader("Scan Vehicle Details")
            st.markdown(
            """
            <style>
            iframe[title="streamlit_qrcode_scanner.qrcode_scanner"] {
                min-height: 140px;
            }
            </style>
            """, 
            unsafe_allow_html=True
        )

            chassis_scan_val = qrcode_scanner(key="chassis_scanner")
            if chassis_scan_val:
                st.session_state.scanned_chassis = chassis_scan_val
            
            chassis_val = st.text_input("Chassis Number:", value=st.session_state.get("scanned_chassis", ""), placeholder="Scan or type Chassis No.")
            
            st.divider()

            if st.button("Mark PDI Complete", type="primary", use_container_width=True):
                if not chassis_val:
                    st.warning("Chassis Number is required.")
                else:
                    try:
                        with SessionLocal() as db:
                            success, message = mgr.complete_pdi(
                                db, 
                                sale_id, 
                                chassis_no=chassis_val.strip(), 
                                engine_no=None,
                                dc_number=dc_number
                            )
                        
                        if success:
                            st.success(message)
                            st.balloons()
                            st.session_state.scanned_chassis = ""
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(message)
                    except Exception as e:
                        st.error(f"An application error occurred: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
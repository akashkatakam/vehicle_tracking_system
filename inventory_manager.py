from typing import Any, Dict, List
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, case, and_, or_
import pandas as pd
from datetime import date, datetime, timedelta
import models
from models import IST_TIMEZONE, TransactionType

# --- READ FUNCTIONS (Existing) ---

def get_head_branches(db: Session):
    """Returns branches that are NOT sub-branches."""
    head_branch_ids = db.query(models.BranchHierarchy.Parent_Branch_ID)
    return db.query(models.Branch).filter(models.Branch.Branch_ID.in_(head_branch_ids)).all()

def get_managed_branches(db: Session, head_branch_id: str):
    """Returns all branches managed by this Head Branch."""
    sub_branches = db.query(models.Branch).join(
        models.BranchHierarchy, models.Branch.Branch_ID == models.BranchHierarchy.Sub_Branch_ID
    ).filter(models.BranchHierarchy.Parent_Branch_ID == head_branch_id).all()
    head_branch = db.query(models.Branch).filter(models.Branch.Branch_ID == head_branch_id).first()
    return [head_branch] + sub_branches if head_branch else sub_branches

def get_all_branches(db: Session):
    return db.query(models.Branch).order_by(models.Branch.Branch_ID).all()

def get_recent_transactions(db: Session, branch_id: str, limit: int = 50) -> pd.DataFrame:
    # This now just logs movements. The VehicleMaster is the source of truth for stock.
    query = (
        db.query(models.InventoryTransaction)
        .filter(or_(
            models.InventoryTransaction.Current_Branch_ID == branch_id,
            models.InventoryTransaction.From_Branch_ID == branch_id,
            models.InventoryTransaction.To_Branch_ID == branch_id
        ))
        .order_by(models.InventoryTransaction.Date.desc(), models.InventoryTransaction.id.desc())
        .limit(limit)
    )
    return pd.read_sql(query.statement, db.get_bind())

def get_current_stock_summary(db: Session, branch_id: str) -> pd.DataFrame:
    # --- UPDATED: This function should now read from VehicleMaster ---
    query = (
        db.query(
            models.VehicleMaster.model,
            models.VehicleMaster.variant,
            models.VehicleMaster.color,
            func.count(models.VehicleMaster.id).label("Stock_On_Hand")
        )
        .filter(models.VehicleMaster.current_branch_id == branch_id)
        .filter(models.VehicleMaster.status == 'In Stock')
        .group_by(models.VehicleMaster.model, models.VehicleMaster.variant, models.VehicleMaster.color)
    )
    return pd.read_sql(query.statement, db.get_bind())

def get_multi_branch_stock(db: Session, branch_ids: List[str]) -> pd.DataFrame:
    """
    Calculates combined current stock for a list of branches from VehicleMaster.
    """
    query = (
        db.query(
            models.Branch.Branch_Name,
            models.VehicleMaster.model,
            models.VehicleMaster.variant,
            models.VehicleMaster.color,
            func.count(models.VehicleMaster.id).label("Stock")
        )
        .join(models.Branch, models.VehicleMaster.current_branch_id == models.Branch.Branch_ID)
        .filter(models.VehicleMaster.current_branch_id.in_(branch_ids))
        .filter(models.VehicleMaster.status == 'In Stock')
        .group_by(models.Branch.Branch_Name, models.VehicleMaster.model, models.VehicleMaster.variant, models.VehicleMaster.color)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

def get_daily_transfer_summary(db: Session, limit: int = 100) -> pd.DataFrame:
    """
    Returns a day-by-day summary of ALL vehicle transfers between branches.
    """
    # Aliases for joining the Branch table twice (once for sender, once for receiver)
    FromBranch = aliased(models.Branch)
    ToBranch = aliased(models.Branch)

    query = (
        db.query(
            models.InventoryTransaction.Date,
            FromBranch.Branch_Name.label("From_Branch"),
            ToBranch.Branch_Name.label("To_Branch"),
            func.sum(models.InventoryTransaction.Quantity).label("Total_Qty"),
        )
        .join(FromBranch, models.InventoryTransaction.From_Branch_ID == FromBranch.Branch_ID)
        .join(ToBranch, models.InventoryTransaction.To_Branch_ID == ToBranch.Branch_ID)
        .filter(models.InventoryTransaction.Transaction_Type == TransactionType.OUTWARD_TRANSFER)
        .group_by(
            models.InventoryTransaction.Date,
            FromBranch.Branch_Name,
            ToBranch.Branch_Name
        )
        .order_by(models.InventoryTransaction.Date.desc())
        .limit(limit)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

def get_vehicle_master_data(db: Session) -> dict:
    """
    Fetches all vehicles and structures them for cascading dropdowns.
    """
    vehicles = db.query(models.VehiclePrice).all()
    master_data = {}
    for v in vehicles:
        if v.Model not in master_data:
            master_data[v.Model] = {}
        if v.Color_List:
             colors = sorted([c.strip() for c in v.Color_List.split(',') if c.strip()])
        else:
             colors = ["N/A"]
        master_data[v.Model][v.Variant] = colors
    return master_data

def get_transfer_history(db: Session, branch_id: str = None, start_date: date = None, end_date: date = None) -> pd.DataFrame:
    """
    Retrieves a detailed history of vehicle transfers (IN and OUT).
    Can be filtered by a specific branch and date range.
    """
    # Alias for joining Branch names
    FromBranch = aliased(models.Branch)
    ToBranch = aliased(models.Branch)
    
    query = (
        db.query(
            models.InventoryTransaction.Date,
            models.InventoryTransaction.Transaction_Type,
            FromBranch.Branch_Name.label("From_Branch"),
            ToBranch.Branch_Name.label("To_Branch"),
            models.InventoryTransaction.Model,
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color,
            models.InventoryTransaction.Quantity,
            models.InventoryTransaction.Remarks
        )
        .outerjoin(FromBranch, models.InventoryTransaction.From_Branch_ID == FromBranch.Branch_ID)
        .outerjoin(ToBranch, models.InventoryTransaction.To_Branch_ID == ToBranch.Branch_ID)
        .filter(models.InventoryTransaction.Transaction_Type.in_([
            TransactionType.OUTWARD_TRANSFER, 
            TransactionType.INWARD_TRANSFER
        ]))
        .order_by(models.InventoryTransaction.Date.desc(), models.InventoryTransaction.id.desc())
    )

    # Apply Filters
    if branch_id:
        query = query.filter(or_(
            models.InventoryTransaction.From_Branch_ID == branch_id,
            models.InventoryTransaction.To_Branch_ID == branch_id
        ))
    
    if start_date:
        query = query.filter(models.InventoryTransaction.Date >= start_date)
        
    if end_date:
        query = query.filter(models.InventoryTransaction.Date <= end_date)

    return pd.read_sql(query.statement, db.get_bind())

def get_branch_transfer_summary(db: Session, from_branch_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Summarizes transfers FROM a specific branch TO all other branches.
    Grouping: Destination Branch -> Model -> Variant.
    """
    ToBranch = aliased(models.Branch)
    
    query = (
        db.query(
            ToBranch.Branch_Name.label("Destination_Branch"),
            models.InventoryTransaction.Model,
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color,
            func.sum(models.InventoryTransaction.Quantity).label("Total_Quantity")
        )
        .join(ToBranch, models.InventoryTransaction.To_Branch_ID == ToBranch.Branch_ID)
        .filter(
            models.InventoryTransaction.Transaction_Type == TransactionType.OUTWARD_TRANSFER,
            models.InventoryTransaction.Current_Branch_ID == from_branch_id, 
            models.InventoryTransaction.Date >= start_date,
            models.InventoryTransaction.Date <= end_date
        )
        .group_by(
            ToBranch.Branch_Name, 
            models.InventoryTransaction.Model, 
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color
        )
        .order_by(ToBranch.Branch_Name, models.InventoryTransaction.Model)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

def get_oem_inward_summary(db: Session, branch_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Summarizes stock received from OEM (HMSI) for a specific branch.
    Grouping: Model -> Variant.
    """
    query = (
        db.query(
            models.InventoryTransaction.Model,
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color,
            func.sum(models.InventoryTransaction.Quantity).label("Total_Received")
        )
        .filter(
            models.InventoryTransaction.Transaction_Type == TransactionType.INWARD_OEM,
            models.InventoryTransaction.Current_Branch_ID == branch_id,
            models.InventoryTransaction.Date >= start_date,
            models.InventoryTransaction.Date <= end_date,
            models.InventoryTransaction.Source_External != 'OEM-BULK-IMPORT'
        )
        .group_by(
            models.InventoryTransaction.Model, 
            models.InventoryTransaction.Variant,
            models.InventoryTransaction.Color
        )
        .order_by(models.InventoryTransaction.Model)
    )
    
    return pd.read_sql(query.statement, db.get_bind())

def get_sales_report(db: Session, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Generates a matrix of Sales for a specific date range.
    Rows: Branch Names
    Columns: Models
    Values: Quantity Sold
    """
    BranchAlias = aliased(models.Branch)
    
    # Query TransactionType.SALE within the date range
    query = (
        db.query(
            BranchAlias.Branch_Name,
            models.InventoryTransaction.Model,
            func.sum(models.InventoryTransaction.Quantity).label("Total_Sold")
        )
        .join(BranchAlias, models.InventoryTransaction.Current_Branch_ID == BranchAlias.Branch_ID)
        .filter(
            models.InventoryTransaction.Transaction_Type == models.TransactionType.SALE,
            models.InventoryTransaction.Date >= start_date,
            models.InventoryTransaction.Date <= end_date
        )
        .group_by(BranchAlias.Branch_Name, models.InventoryTransaction.Model)
    )
    
    df = pd.read_sql(query.statement, db.get_bind())
    
    if df.empty:
        return pd.DataFrame()
        
    # Pivot the data: Branch (Rows) x Model (Columns)
    pivot_df = df.pivot_table(
        index='Branch_Name', 
        columns='Model', 
        values='Total_Sold', 
        aggfunc='sum', 
        fill_value=0
    )
    
    # Add a 'Total' column for each branch
    pivot_df['TOTAL'] = pivot_df.sum(axis=1)
    
    # Sort by Total descending
    pivot_df = pivot_df.sort_values(by='TOTAL', ascending=False)
    
    return pivot_df
# --- WRITE FUNCTIONS (Existing) ---

# Use log_bulk_inward_master instead.
def log_oem_inward(db: Session, branch_id: str, model: str, var: str, color: str, qty: int, load_no: str, dt: date, rem: str):
    # This function is now secondary.
    # The primary function should be to add to VehicleMaster.
    # For now, we'll just log the transaction.
    db.add(models.InventoryTransaction(
        Date=dt, Transaction_Type=TransactionType.INWARD_OEM,
        Current_Branch_ID=branch_id, Source_External="HMSI",
        Model=model, Variant=var, Color=color, Quantity=qty,
        Load_Number=load_no, Remarks=rem
    ))
    db.commit()

# --- MODIFIED: This function now updates VehicleMaster AND logs the transaction ---
def log_transfer(db: Session, from_id: str, to_id: str, chassis_no: str, dt: date, rem: str):
    """
    Transfers a single vehicle (by chassis_no) from one branch to another.
    """
    try:
        # 1. Find the vehicle
        vehicle = db.query(models.VehicleMaster).filter(models.VehicleMaster.chassis_no == chassis_no).first()
        if not vehicle:
            raise Exception(f"Vehicle {chassis_no} not found.")
        if vehicle.status != 'In Stock':
            raise Exception(f"Vehicle {chassis_no} is not In Stock (status is {vehicle.status}).")
        if vehicle.current_branch_id != from_id:
             raise Exception(f"Vehicle {chassis_no} is not at branch {from_id}.")
             
        # 2. Update the VehicleMaster
        vehicle.current_branch_id = to_id
        
        # 3. Log the transactions (double-entry)
        txn_out = models.InventoryTransaction(
            Date=dt, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
            Current_Branch_ID=from_id, To_Branch_ID=to_id,
            Model=vehicle.model, Variant=vehicle.variant, Color=vehicle.color, Quantity=1,
            Remarks=f"Transfer OUT to {to_id}. {rem}"
        )
        txn_in = models.InventoryTransaction(
            Date=dt, Transaction_Type=TransactionType.INWARD_TRANSFER,
            Current_Branch_ID=to_id, From_Branch_ID=from_id,
            Model=vehicle.model, Variant=vehicle.variant, Color=vehicle.color, Quantity=1,
            Remarks=f"Auto-transfer IN from {from_id}. {rem}"
        )
        db.add_all([txn_out, txn_in])
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

# --- MODIFIED: This function is now OBSOLETE.
def log_bulk_manual_sub_branch_sale(db: Session, chassis_list: List[str], sale_date: date, remarks: str):
    """
    Marks a list of vehicles as 'Sold' in the VehicleMaster table in a single 
    atomic transaction and logs an InventoryTransaction.
    """
    if not chassis_list:
        # It's good practice to raise an exception for an empty list
        raise Exception("No vehicles in the batch to process.")
        
    try:
        for chassis_no in chassis_list:
            # 1. Find the vehicle
            vehicle = db.query(models.VehicleMaster).filter(
                models.VehicleMaster.chassis_no == chassis_no
            ).first()

            if not vehicle:
                raise Exception(f"Vehicle {chassis_no} not found.")

            if vehicle.status == 'Sold':
                raise Exception(f"Vehicle {chassis_no} is already marked as 'Sold'.")

            # Get the branch_id from the vehicle itself
            branch_id = vehicle.current_branch_id

            # 2. Update the VehicleMaster status
            vehicle.status = 'Sold'
            
            # 3. Log the InventoryTransaction
            sale_log = models.InventoryTransaction(
                Date=sale_date,
                Transaction_Type=models.TransactionType.SALE,
                Current_Branch_ID=branch_id,
                Model=vehicle.model,
                Variant=vehicle.variant,
                Color=vehicle.color,
                Quantity=1,
                Remarks=f"Manual Sub-Branch Sale. {remarks}"
            )
            db.add(sale_log)
        
        # 4. Commit all changes at once
        db.commit()
        return True, f"Success: {len(chassis_list)} vehicles marked as 'Sold'."

    except Exception as e:
        # If any error occurs (like a chassis not found), roll back everything
        db.rollback()
        return False, str(e)
    
# --- NEW: Bulk Inward for VehicleMaster ---
def log_bulk_inward_master(db: Session, current_branch_id: str, source: str, load_no: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    """
    Logs a batch of NEW vehicles into the VehicleMaster.
    This is for OEM/External inward.
    'vehicle_batch' should be a list of dicts, each with:
    {'chassis_no', 'engine_no', 'model', 'variant', 'color'}
    """
    try:
        for item in vehicle_batch:
            # 1. Create the master vehicle record
            vehicle = models.VehicleMaster(
                chassis_no=item['chassis_no'],
                engine_no=item.get('engine_no'),
                load_reference_number=load_no,
                model=item['model'],
                variant=item['variant'],
                color=item['color'],
                status='In Stock',
                date_received=date_val,
                current_branch_id=current_branch_id
            )
            db.add(vehicle)
            
            # 2. Log the transaction
            db.add(models.InventoryTransaction(
                Date=date_val, Transaction_Type=TransactionType.INWARD_OEM,
                Current_Branch_ID=current_branch_id, Source_External=source,
                Load_Number=load_no, Remarks=remarks,
                Model=item['model'], Variant=item['variant'], Color=item['color'], Quantity=1
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    
# --- NEW: Bulk Transfer for VehicleMaster ---
def log_bulk_transfer_master(db: Session, from_branch_id: str, to_branch_id: str, date_val: date, remarks: str, chassis_list: List[str]):
    """Logs a batch transfer by updating VehicleMaster and logging transactions."""
    try:
        for chassis_no in chassis_list:
            # 1. Find and update the vehicle
            vehicle = db.query(models.VehicleMaster).filter(
                models.VehicleMaster.chassis_no == chassis_no,
                models.VehicleMaster.current_branch_id == from_branch_id,
                models.VehicleMaster.status == 'In Stock'
            ).first()
            
            if not vehicle:
                raise Exception(f"Vehicle {chassis_no} not found, not 'In Stock', or not at branch {from_branch_id}.")
            
            vehicle.current_branch_id = to_branch_id
            vehicle.dc_number = remarks
            
            # 2. Log the transactions (double-entry)
            db.add(models.InventoryTransaction(
                Date=date_val, Transaction_Type=TransactionType.OUTWARD_TRANSFER,
                Current_Branch_ID=from_branch_id, To_Branch_ID=to_branch_id,
                Remarks=f"Transfer OUT to {to_branch_id}. {remarks}",
                Model=vehicle.model, Variant=vehicle.variant, Color=vehicle.color, Quantity=1
            ))
            db.add(models.InventoryTransaction(
                Date=date_val, Transaction_Type=TransactionType.INWARD_TRANSFER,
                Current_Branch_ID=to_branch_id, From_Branch_ID=from_branch_id,
                Remarks=f"Transfer IN from {from_branch_id}. {remarks}",
                Model=vehicle.model, Variant=vehicle.variant, Color=vehicle.color, Quantity=1
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

def bulk_correct_stock(db: Session, update_batch: List[Dict], correction_date: date, cutoff_date: date):
    """
    Corrects Model/Variant/Branch in VehicleMaster based on CSV data.
    Skips correction if a transfer (INWARD/OUTWARD) has occurred after cutoff_date.
    """
    skip_count = 0
    update_count = 0
    error_log = []

    try:
        for item in update_batch:
            chassis_no = item.get('chassis_no')
            if not chassis_no:
                error_log.append(f"Skipped row: Missing chassis_no.")
                continue

            # 1. Find the vehicle master record
            vehicle = db.query(models.VehicleMaster).filter(
                models.VehicleMaster.chassis_no == chassis_no
            ).first()

            if not vehicle:
                error_log.append(f"Error: Chassis {chassis_no} not found in VehicleMaster.")
                continue

            # 2. Check for recent transfers (after cutoff_date)
            # We look for ANY INWARD or OUTWARD transfer activity involving this vehicle's model/variant/color 
            # and location after the cutoff date.
            recent_transfer = db.query(models.InventoryTransaction).filter(
                models.InventoryTransaction.Date >= cutoff_date,
                models.InventoryTransaction.Transaction_Type.in_([
                    models.TransactionType.INWARD_TRANSFER, 
                    models.TransactionType.OUTWARD_TRANSFER
                ]),
                models.InventoryTransaction.Current_Branch_ID == vehicle.current_branch_id,
                models.InventoryTransaction.Model == vehicle.model,
                models.InventoryTransaction.Variant == vehicle.variant,
            ).first()

            if recent_transfer:
                skip_count += 1
                error_log.append(f"Skipped Chassis {chassis_no}: Found recent transfer ({recent_transfer.Transaction_Type}) on {recent_transfer.Date}.")
                continue

            # 3. Apply corrections only if not sold/allotted
            if vehicle.status in ['Sold', 'Allotted']:
                error_log.append(f"Skipped Chassis {chassis_no}: Status is '{vehicle.status}'. Correction not allowed.")
                continue
                
            # --- Correction ---
            original_branch_id = vehicle.current_branch_id
            
            # Update fields from CSV (using existing values as default if CSV field is missing)
            # vehicle.model = item.get('model', vehicle.model)
            # vehicle.variant = item.get('variant', vehicle.variant)
            # vehicle.color = item.get('color', vehicle.color)
            vehicle.current_branch_id = item.get('current_branch_id', vehicle.current_branch_id)
            # vehicle.load_reference_number = item.get('load_reference_number',vehicle.load_reference_number)
            
            update_count += 1

            # 4. Log a correction transaction for auditing (using the correction date)
            db.add(models.InventoryTransaction(
                Date=correction_date,
                Transaction_Type="STOCK CORRECTION", # Custom transaction type for auditing
                Current_Branch_ID=vehicle.current_branch_id,
                From_Branch_ID=original_branch_id,
                Model=vehicle.model,
                Variant=vehicle.variant,
                Color=vehicle.color,
                Quantity=1,
                Remarks=f"Data Correction: Updated model/variant/branch from CSV. Original Branch: {original_branch_id}."
            ))

        db.commit()
        return True, f"Success: {update_count} vehicles corrected. {skip_count} skipped due to recent transfer/sale. {len(error_log)} errors logged.", error_log
    
    except Exception as e:
        db.rollback()
        return False, f"FATAL Database Error: {e}", error_log
# ---
# --- FUNCTIONS FOR SALES LIFECYCLE ---
# ---
def get_completed_sales_last_48h(db: Session, branch_id: str = None) -> pd.DataFrame:
    """
    Gets all sales records that completed PDI within the last 48 hours.
    """
    # Define the statuses that mean a vehicle is "allotted"
    allotted_statuses = ['PDI Complete', 'Insurance Done', 'TR Done']
    
    # Calculate the time 48 hours ago, using the timezone from models.py
    time_48h_ago = datetime.now(IST_TIMEZONE) - timedelta(days=2)
    
    query = (
        db.query(models.SalesRecord)
        .filter(
            # Check for the correct statuses
            models.SalesRecord.fulfillment_status.in_(allotted_statuses),
            # Check that the completion date is within the last 48 hours
            models.SalesRecord.pdi_completion_date >= time_48h_ago
        )
    )
    
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
        
    query = query.order_by(models.SalesRecord.pdi_completion_date.desc())
    
    return pd.read_sql(query.statement, db.get_bind())

def get_users_by_role(db: Session, role: str) -> List[models.User]:
    """Retrieves all users matching a specific role."""
    return db.query(models.User).filter(models.User.role == role).all()

def get_sales_records_by_status(db: Session, status: str, branch_id: str = None) -> pd.DataFrame:
    """Gets all sales records matching a fulfillment_status."""
    query = db.query(models.SalesRecord).filter(models.SalesRecord.fulfillment_status == status)
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())

def get_sales_records_by_statuses(db: Session, statuses: List[str], branch_id: str = None) -> pd.DataFrame:
    """Gets all sales records matching a list of fulfillment_statuses."""
    query = db.query(models.SalesRecord).filter(models.SalesRecord.fulfillment_status.in_(statuses))
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())

def get_sales_records_for_mechanic(db: Session, mechanic_username: str, branch_id: str = None) -> pd.DataFrame:
    """Gets tasks for a specific mechanic."""
    query = db.query(models.SalesRecord).filter(
        models.SalesRecord.pdi_assigned_to == mechanic_username,
        models.SalesRecord.fulfillment_status == 'PDI In Progress'
    )
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())

def assign_pdi_mechanic(db: Session, sale_id: int, mechanic_name: str):
    """Assigns a PDI task to a mechanic."""
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if record:
            record.pdi_assigned_to = mechanic_name
            record.fulfillment_status = "PDI In Progress"
            db.commit()
    except Exception as e:
        db.rollback()
        raise e

# --- UPDATED: This is the new CRITICAL function ---
def complete_pdi(db: Session, sale_id: int, chassis_no: str, engine_no: str = None,dc_number: str=None):
    """
    Links a scanned vehicle from VehicleMaster to a SalesRecord,
    validates it, and marks PDI as complete.
    This is the "source of truth" function.
    """
    try:
        # 1. Find the SalesRecord (the "request")
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if not record:
            return False, "Error: Sales Record not found."
        
        # 2. Find the Vehicle (the "fulfillment")
        vehicle = db.query(models.VehicleMaster).filter(models.VehicleMaster.chassis_no == chassis_no).first()
        
        # 3. Validation
        if not vehicle:
            return False, f"Error: Chassis No. '{chassis_no}' not found in Vehicle Master. Please scan a valid vehicle."
        
        if vehicle.status != 'In Stock':
            if vehicle.sale_id == sale_id:
                # Vehicle is already linked, just update status
                record.fulfillment_status = "PDI Complete"
                record.pdi_completion_date = datetime.now(IST_TIMEZONE)
                db.commit()
                return True, "This vehicle is already allotted to this sale. PDI marked complete."
            else:
                return False, f"Error: Vehicle is already '{vehicle.status}' and linked to another sale (ID: {vehicle.sale_id})."

        # 4. (Optional but Recommended) Validate vehicle model
        if vehicle.model.upper() != record.Model.upper() or vehicle.variant.upper() != record.Variant.upper() or vehicle.color != record.Paint_Color:
            return False, f"Mismatch: Customer wants '{record.Model}/{record.Variant}/{record.Paint_Color}', but you scanned a '{vehicle.model}/{vehicle.variant}/{vehicle.color}'."
        
        # 5. Link and Update (The Transaction)
        
        # Update VehicleMaster: Set status, link to sale
        vehicle.status = 'Allotted' # 'Allotted' is better than 'PDI Complete' for status
        vehicle.sale_id = sale_id
        
        # Update SalesRecord: Add the scanned details
        record.chassis_no = vehicle.chassis_no
        record.engine_no = vehicle.engine_no if engine_no else vehicle.engine_no # Use vehicle's engine_no if not provided
        record.fulfillment_status = "PDI Complete"
        record.pdi_completion_date = datetime.now(IST_TIMEZONE)
        
        db.commit()
        return True, "Success: Vehicle linked and PDI marked as complete!"

    except Exception as e:
        db.rollback()
        return False, f"A database error occurred: {e}"

def update_insurance_tr_status(db: Session, sale_id: int, updates: Dict[str, Any]):
    """Updates the Insurance, TR, Dues, and Tax flags."""
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if record:
            # Apply all updates from the dictionary
            for key, value in updates.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            
            # Update fulfillment status based on progression
            if record.is_tr_done:
                record.fulfillment_status = "TR Done"
                    
            elif record.is_insurance_done:
                record.fulfillment_status = "Insurance Done"
                vehicle = db.query(models.VehicleMaster).filter(models.VehicleMaster.sale_id == sale_id).first()
                if vehicle:
                    vehicle.status = "Sold"
                
            db.commit()
    except Exception as e:
        db.rollback()
        raise e
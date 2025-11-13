from typing import Any, Dict, List
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, case, and_, or_
import pandas as pd
from datetime import date, datetime
# --- UPDATED: Import all models from the new merged file ---
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

# --- WRITE FUNCTIONS (Existing) ---

# --- MODIFIED: This function is now OBSOLETE for bulk inward.
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
# Sales are logged by the mechanic's scan linking to a SalesRecord.
def log_sale(db: Session, branch_id: str, model: str, var: str, color: str, qty: int, dt: date, rem: str):
    # This is now a simple log. The VehicleMaster status change is the real event.
    db.add(models.InventoryTransaction(
        Date=dt, Transaction_Type=TransactionType.SALE,
        Current_Branch_ID=branch_id,
        Model=model, Variant=var, Color=color, Quantity=qty,
        Remarks=rem
    ))
    db.commit()
    
# --- OBSOLETE: Use log_bulk_transfer_master
def log_bulk_sales(db: Session, branch_id: str, date_val: date, remarks: str, vehicle_batch: List[Dict]):
    pass # This logic is now handled by the PDI/Mechanic scan

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
    
# --- OBSOLETE: Use log_bulk_inward_master ---
def log_inward_stock(db: Session, current_branch_id: str, source: str, 
                     model: str, variant: str, color: str, qty: int, 
                     load_no: str, date_val: date, remarks: str):
    pass # This logic is replaced by VehicleMaster entry

# ---
# --- FUNCTIONS FOR SALES LIFECYCLE ---
# ---

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
def complete_pdi(db: Session, sale_id: int, chassis_no: str, engine_no: str = None):
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
        if vehicle.model.upper() != record.Model.upper():
            return False, f"Mismatch: Customer wants '{record.Model}', but you scanned a '{vehicle.model}'."
        # Add more checks for variant/color if needed
        
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
                
                # --- NEW: Update VehicleMaster to 'Sold' ---
                vehicle = db.query(models.VehicleMaster).filter(models.VehicleMaster.sale_id == sale_id).first()
                if vehicle:
                    vehicle.status = "Sold"
                    
            elif record.is_insurance_done:
                record.fulfillment_status = "Insurance Done"
                
            db.commit()
    except Exception as e:
        db.rollback()
        raise e
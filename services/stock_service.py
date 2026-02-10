# services/stock_service.py
from typing import List, Dict, Any
from sqlalchemy.orm import Session
import models
from models import TransactionType
from datetime import date, datetime, timedelta
import pandas as pd


# --- READS ---
def get_current_stock_summary(db: Session, branch_id: str) -> pd.DataFrame:
    query = (
        db.query(
            models.VehicleMaster.model,
            models.VehicleMaster.variant,
            models.VehicleMaster.color,
            models.VehicleMaster.id  # Just to count
        )
        .filter(models.VehicleMaster.current_branch_id == branch_id)
        .filter(models.VehicleMaster.status == 'In Stock')
    )
    df = pd.read_sql(query.statement, db.get_bind())
    if df.empty: return pd.DataFrame()

    return df.groupby(['model', 'variant', 'color']).size().reset_index(name='Stock_On_Hand')


def get_multi_branch_stock(db: Session, branch_ids: List[str]) -> pd.DataFrame:
    query = (
        db.query(
            models.Branch.Branch_Name,
            models.VehicleMaster.model,
            models.VehicleMaster.variant,
            models.VehicleMaster.color,
            models.VehicleMaster.id
        )
        .join(models.Branch, models.VehicleMaster.current_branch_id == models.Branch.Branch_ID)
        .filter(models.VehicleMaster.current_branch_id.in_(branch_ids))
        .filter(models.VehicleMaster.status == 'In Stock')
    )
    df = pd.read_sql(query.statement, db.get_bind())
    if df.empty: return pd.DataFrame()

    return df.groupby(['Branch_Name', 'model', 'variant', 'color']).size().reset_index(name='Stock')


def get_vehicle_master_data(db: Session) -> dict:
    """Fetches all vehicles and structures them for cascading dropdowns."""
    vehicles = db.query(models.VehiclePrice).all()
    master_data = {}
    for v in vehicles:
        if v.Model not in master_data:
            master_data[v.Model] = {}
        colors = sorted([c.strip() for c in v.Color_List.split(',') if c.strip()]) if v.Color_List else ["N/A"]
        master_data[v.Model][v.Variant] = colors
    return master_data


def search_vehicles(db: Session, chassis: str = None, model: str = None, variant: str = None,
                    color: str = None) -> pd.DataFrame:
    """
    Locates vehicles by Chassis OR by Model/Variant/Color attributes.
    Returns Branch Location and Status.
    """
    query = db.query(
        models.VehicleMaster.chassis_no,
        models.VehicleMaster.model,
        models.VehicleMaster.variant,
        models.VehicleMaster.color,
        models.VehicleMaster.status,
        models.VehicleMaster.dc_number,
        models.Branch.Branch_Name.label("Current_Location")
    ).join(models.Branch, models.VehicleMaster.current_branch_id == models.Branch.Branch_ID)

    if chassis:
        query = query.filter(models.VehicleMaster.chassis_no.ilike(f"%{chassis}%"))
    else:
        # Attribute search
        if model:
            query = query.filter(models.VehicleMaster.model == model)
        if variant:
            query = query.filter(models.VehicleMaster.variant == variant)
        if color:
            query = query.filter(models.VehicleMaster.color == color)

    # Limit results to prevent massive dumps if filters are loose
    query = query.filter(models.VehicleMaster.status == 'In Stock')
    query = query.limit(500)

    return pd.read_sql(query.statement, db.get_bind())


def get_all_product_mappings(db: Session) -> pd.DataFrame:
    """Returns all S08 product mappings."""
    query = db.query(
        models.ProductMapping.model_code,
        models.ProductMapping.variant_code,
        models.ProductMapping.real_model,
        models.ProductMapping.real_variant
    )
    return pd.read_sql(query.statement, db.get_bind())


def get_vehicles_in_load(db: Session, branch_id: str, load_reference: str) -> pd.DataFrame:
    """
    Fetches details of all 'In Transit' vehicles for a specific load.
    """
    query = db.query(
        models.VehicleMaster.chassis_no.label("Chassis No"),
        models.VehicleMaster.model.label("Model"),
        models.VehicleMaster.variant.label("Variant"),
        models.VehicleMaster.color.label("Color"),
        models.VehicleMaster.engine_no.label("Engine No")
    ).filter(
        models.VehicleMaster.current_branch_id == branch_id,
        models.VehicleMaster.load_reference_number == load_reference,
        models.VehicleMaster.status == 'In Transit'
    )
    return pd.read_sql(query.statement, db.get_bind())


# --- WRITES ---

def add_product_mapping(db: Session, m_code: str, v_code: str, r_model: str, r_variant: str):
    """Adds a new mapping for S08 file decoding."""
    try:
        # Check if exists
        exists = db.query(models.ProductMapping).filter(
            models.ProductMapping.model_code == m_code,
            models.ProductMapping.variant_code == v_code
        ).first()

        if exists:
            return False, f"Mapping for {m_code}-{v_code} already exists."

        mapping = models.ProductMapping(
            model_code=m_code,
            variant_code=v_code,
            real_model=r_model,
            real_variant=r_variant
        )
        db.add(mapping)
        db.commit()
        return True, "Mapping added successfully."
    except Exception as e:
        db.rollback()
        return False, str(e)


def log_bulk_inward_master(db: Session, current_branch_id: str, source: str, load_no: str, date_val: date, remarks: str,
                           vehicle_batch: List[Dict], initial_status: str = 'In Stock'):
    """
    Logs a batch. 'initial_status' can be 'In Stock' (for CSV) or 'In Transit' (for S08).
    """
    try:
        for item in vehicle_batch:
            # Use the extracted load_reference if available, otherwise fallback to the manual one
            ref_no = item.get('load_reference', load_no)

            vehicle = models.VehicleMaster(
                chassis_no=item['chassis_no'],
                engine_no=item.get('engine_no'),
                load_reference_number=ref_no,  # Saved here
                model=item['model'],
                variant=item['variant'],
                color=item['color'],
                status=initial_status,  # Use the dynamic status
                date_received=date_val,
                current_branch_id=current_branch_id
            )
            db.add(vehicle)

            # Only log the InventoryTransaction if it is actually IN STOCK.
            # If it's In Transit, we don't count it as inventory yet.
            if initial_status == 'In Stock':
                db.add(models.InventoryTransaction(
                    Date=date_val, Transaction_Type=TransactionType.INWARD_OEM,
                    Current_Branch_ID=current_branch_id, Source_External=source,
                    Load_Number=ref_no, Remarks=remarks,
                    Model=item['model'], Variant=item['variant'], Color=item['color'], Quantity=1
                ))

        db.commit()
    except Exception as e:
        db.rollback()
        raise e


def log_bulk_transfer_master(db: Session, from_branch_id: str, to_branch_id: str, date_val: date, remarks: str,
                             chassis_list: List[str]):
    try:
        for chassis_no in chassis_list:
            vehicle = db.query(models.VehicleMaster).filter(
                models.VehicleMaster.chassis_no == chassis_no,
                models.VehicleMaster.current_branch_id == from_branch_id,
                models.VehicleMaster.status == 'In Stock'
            ).first()

            if not vehicle:
                raise Exception(f"Vehicle {chassis_no} not found/available at {from_branch_id}.")

            vehicle.current_branch_id = to_branch_id
            vehicle.dc_number = remarks

            # Double Entry Logging
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
                error_log.append(
                    f"Skipped Chassis {chassis_no}: Found recent transfer ({recent_transfer.Transaction_Type}) on {recent_transfer.Date}.")
                continue

            # 3. Apply corrections only if not sold/allotted
            if vehicle.status in ['Sold', 'Allotted']:
                error_log.append(f"Skipped Chassis {chassis_no}: Status is '{vehicle.status}'. Correction not allowed.")
                continue

            # --- Correction ---
            original_branch_id = vehicle.current_branch_id
            vehicle.current_branch_id = item.get('current_branch_id', vehicle.current_branch_id)

            update_count += 1

            # 4. Log a correction transaction for auditing (using the correction date)
            db.add(models.InventoryTransaction(
                Date=correction_date,
                Transaction_Type="STOCK CORRECTION",  # Custom transaction type for auditing
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


def get_pending_loads(db: Session, branch_id: str) -> List[str]:
    """Returns a list of unique Load Reference Numbers that are currently 'In Transit'."""
    results = db.query(models.VehicleMaster.load_reference_number).filter(
        models.VehicleMaster.current_branch_id == branch_id,
        models.VehicleMaster.status == 'In Transit'
    ).distinct().all()
    return [r[0] for r in results if r[0]]


def receive_load(db: Session, branch_id: str, load_reference: str):
    """
    Moves all vehicles in a specific load from 'In Transit' to 'In Stock'
    and logs the Inventory Transaction.
    """
    try:
        # Find all vehicles in this load
        vehicles = db.query(models.VehicleMaster).filter(
            models.VehicleMaster.current_branch_id == branch_id,
            models.VehicleMaster.load_reference_number == load_reference,
            models.VehicleMaster.status == 'In Transit'
        ).all()

        if not vehicles:
            return False, "No 'In Transit' vehicles found for this Load ID."

        today = date.today()

        for v in vehicles:
            # Update Status
            v.status = 'In Stock'
            v.date_received = today  # Update receipt date to TODAY (actual arrival)

            # Now we Log the Transaction (Stock Increase)
            db.add(models.InventoryTransaction(
                Date=today,
                Transaction_Type=TransactionType.INWARD_OEM,
                Current_Branch_ID=branch_id,
                Source_External="HMSI (Transit Received)",
                Load_Number=load_reference,
                Remarks=f"Received Load {load_reference}",
                Model=v.model,
                Variant=v.variant,
                Color=v.color,
                Quantity=1
            ))

        db.commit()
        return True, f"Successfully received {len(vehicles)} vehicles from Load {load_reference}."
    except Exception as e:
        db.rollback()
        return False, f"Error receiving load: {e}"


def log_bulk_manual_sub_branch_sale(db: Session, chassis_list: List[str], sale_date: date, remarks: str):
    """
    Marks a list of vehicles as 'Sold' in the VehicleMaster table in a single
    atomic transaction and logs an InventoryTransaction.
    """
    if not chassis_list:
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
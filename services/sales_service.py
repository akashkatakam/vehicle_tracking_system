# services/sales_service.py
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import models
from models import IST_TIMEZONE
import pandas as pd


def get_sales_records_by_status(db: Session, status: str, branch_id: str = None) -> pd.DataFrame:
    query = db.query(models.SalesRecord).filter(models.SalesRecord.fulfillment_status == status)
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())


def get_sales_records_for_mechanic(db: Session, mechanic_username: str, branch_id: str = None) -> pd.DataFrame:
    query = db.query(models.SalesRecord).filter(
        models.SalesRecord.pdi_assigned_to == mechanic_username,
        models.SalesRecord.fulfillment_status == 'PDI In Progress'
    )
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())


def get_completed_sales_last_48h(db: Session, branch_id: str = None) -> pd.DataFrame:
    time_48h_ago = datetime.now(IST_TIMEZONE) - timedelta(days=2)
    query = db.query(models.SalesRecord).filter(
        models.SalesRecord.fulfillment_status.in_(['PDI Complete', 'Insurance Done', 'TR Done']),
        models.SalesRecord.pdi_completion_date >= time_48h_ago
    )
    if branch_id:
        query = query.filter(models.SalesRecord.Branch_ID == branch_id)
    return pd.read_sql(query.statement, db.get_bind())


def assign_pdi_mechanic(db: Session, sale_id: int, mechanic_name: str):
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        if record:
            record.pdi_assigned_to = mechanic_name
            record.fulfillment_status = "PDI In Progress"
            db.commit()
    except Exception as e:
        db.rollback()
        raise e


def complete_pdi(db: Session, sale_id: int, chassis_no: str, engine_no: str = None, dc_number: str = None):
    try:
        record = db.query(models.SalesRecord).filter(models.SalesRecord.id == sale_id).first()
        vehicle = db.query(models.VehicleMaster).filter(models.VehicleMaster.chassis_no == chassis_no).first()

        if not record: return False, "Sales Record not found."
        if not vehicle: return False, f"Chassis '{chassis_no}' not found."

        if vehicle.status != 'In Stock':
            if vehicle.sale_id == sale_id:
                record.fulfillment_status = "PDI Complete"
                record.pdi_completion_date = datetime.now(IST_TIMEZONE)
                db.commit()
                return True, "Already allotted. PDI marked complete."
            return False, f"Vehicle is '{vehicle.status}' (Linked to ID: {vehicle.sale_id})."

        # Link and Update
        vehicle.status = 'Allotted'
        vehicle.sale_id = sale_id

        record.chassis_no = vehicle.chassis_no
        record.engine_no = vehicle.engine_no if not engine_no else engine_no
        record.fulfillment_status = "PDI Complete"
        record.pdi_completion_date = datetime.now(IST_TIMEZONE)

        db.commit()
        return True, "Success: PDI Complete and Vehicle Linked!"
    except Exception as e:
        db.rollback()
        return False, f"Database Error: {e}"
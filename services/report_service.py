# services/report_service.py
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func
import models
from models import IST_TIMEZONE, TransactionType
import pandas as pd
from datetime import date, datetime, timedelta


def get_stock_aging_report(db: Session, branch_id: str = None) -> pd.DataFrame:
    """Calculates stock age buckets."""
    query = db.query(
        models.VehicleMaster.chassis_no,
        models.VehicleMaster.model,
        models.VehicleMaster.variant,
        models.VehicleMaster.color,
        models.VehicleMaster.date_received,
        models.Branch.Branch_Name
    ).join(models.Branch, models.VehicleMaster.current_branch_id == models.Branch.Branch_ID) \
        .filter(models.VehicleMaster.status == 'In Stock')

    if branch_id:
        query = query.filter(models.VehicleMaster.current_branch_id == branch_id)

    df = pd.read_sql(query.statement, db.get_bind())

    if df.empty: return pd.DataFrame()

    now = datetime.now(IST_TIMEZONE)
    df['date_received'] = pd.to_datetime(df['date_received'])

    if df['date_received'].dt.tz is None:
        df['date_received'] = df['date_received'].dt.tz_localize(IST_TIMEZONE)
    else:
        df['date_received'] = df['date_received'].dt.tz_convert(IST_TIMEZONE)

    df['Days_Old'] = (now - df['date_received']).dt.days

    bins = [0, 30, 60, 90, 9999]
    labels = ['0-30 Days', '31-60 Days', '61-90 Days', '90+ Days (Critical)']
    df['Age_Bucket'] = pd.cut(df['Days_Old'], bins=bins, labels=labels, right=False)

    return df


def get_branch_transfer_summary(db: Session, from_branch_id: str, start_date: date, end_date: date) -> pd.DataFrame:
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
        .group_by(ToBranch.Branch_Name, models.InventoryTransaction.Model, models.InventoryTransaction.Variant,
                  models.InventoryTransaction.Color)
    )
    return pd.read_sql(query.statement, db.get_bind())


def get_oem_inward_summary(db: Session, branch_id: str, start_date: date, end_date: date) -> pd.DataFrame:
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
            models.InventoryTransaction.Date <= end_date
        )
        .group_by(models.InventoryTransaction.Model, models.InventoryTransaction.Variant,
                  models.InventoryTransaction.Color)
        .order_by(models.InventoryTransaction.Model, models.InventoryTransaction.Variant)
    )
    return pd.read_sql(query.statement, db.get_bind())


def get_sales_report(db: Session, start_date: date, end_date: date) -> pd.DataFrame:
    BranchAlias = aliased(models.Branch)
    query = (
        db.query(
            BranchAlias.Branch_Name,
            models.InventoryTransaction.Model,
            models.InventoryTransaction.Variant,
            func.sum(models.InventoryTransaction.Quantity).label("Total_Sold")
        )
        .join(BranchAlias, models.InventoryTransaction.Current_Branch_ID == BranchAlias.Branch_ID)
        .filter(
            models.InventoryTransaction.Transaction_Type == models.TransactionType.SALE,
            models.InventoryTransaction.Date >= start_date,
            models.InventoryTransaction.Date <= end_date
        )
        .group_by(BranchAlias.Branch_Name, models.InventoryTransaction.Model, models.InventoryTransaction.Variant)
    )

    df = pd.read_sql(query.statement, db.get_bind())
    if df.empty: return pd.DataFrame()

    pivot_df = df.pivot_table(index='Branch_Name', columns=['Model', 'Variant'], values='Total_Sold', aggfunc='sum',
                              fill_value=0)
    pivot_df['TOTAL'] = pivot_df.sum(axis=1)
    return pivot_df.sort_values(by='TOTAL', ascending=False)


def get_daily_summary(db: Session, date_val: date) -> pd.DataFrame:
    """Returns a summary of Sales and Transfers for the given date, grouped by Branch."""
    query = (
        db.query(
            models.Branch.Branch_Name,
            models.InventoryTransaction.Transaction_Type,
            func.count(models.InventoryTransaction.id).label("Count")
        )
        .join(models.Branch, models.InventoryTransaction.Current_Branch_ID == models.Branch.Branch_ID)
        .filter(
            models.InventoryTransaction.Date == date_val,
            models.InventoryTransaction.Transaction_Type.in_([
                models.TransactionType.SALE,
                models.TransactionType.OUTWARD_TRANSFER
            ])
        )
        .group_by(models.Branch.Branch_Name, models.InventoryTransaction.Transaction_Type)
    )

    return pd.read_sql(query.statement, db.get_bind())
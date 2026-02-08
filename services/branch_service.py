# services/branch_service.py
from sqlalchemy.orm import Session
import models


def get_all_branches(db: Session):
    return db.query(models.Branch).order_by(models.Branch.Branch_ID).all()


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


def get_users_by_role(db: Session, role: str):
    return db.query(models.User).filter(models.User.role == role).all()
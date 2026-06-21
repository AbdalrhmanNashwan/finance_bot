"""
Database models.

Design notes:
- Money is stored as INTEGER (whole units of the account's currency, e.g. IQD has
  no meaningful decimal, so we just store whole numbers). If you need USD with
  cents, store in minor units (cents) instead and divide by 100 when displaying.
  For simplicity in v1 we just store whole numbers and let you decide the unit
  per account.
- Account balances are NEVER stored directly. They are always computed as
  opening_balance + sum(transactions for that account). This is the "ledger"
  approach -- it keeps history trustworthy and makes audit/undo trivial.
- Transfers are represented as TWO transactions (one negative on the source
  account, one positive on the destination account) linked by transfer_group_id.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Boolean, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    type = Column(String, nullable=False, default="cash")  # cash/bank/wallet/savings/credit_card
    currency = Column(String, nullable=False, default="IQD")
    opening_balance = Column(Integer, nullable=False, default=0)
    is_archived = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="account")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    kind = Column(String, nullable=False, default="expense")  # expense/income

    transactions = relationship("Transaction", back_populates="category")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    # Signed amount: negative = money out, positive = money in, in the
    # account's own currency unit.
    amount = Column(Integer, nullable=False)

    type = Column(String, nullable=False)  # expense / income / transfer
    note = Column(String, nullable=True)

    transfer_group_id = Column(String, nullable=True)  # links the two legs of a transfer

    occurred_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    is_deleted = Column(Boolean, default=False)

    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")


class AuditLog(Base):
    """Records every edit/delete of a transaction for history purposes."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    action = Column(String, nullable=False)  # created / edited / deleted
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

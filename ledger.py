"""
Core ledger logic. All money math lives here so the bot handlers stay thin.
"""

import uuid
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import Account, Category, Transaction, AuditLog


async def get_account_balance(session: AsyncSession, account: Account) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.account_id == account.id, Transaction.is_deleted == False)  # noqa: E712
    )
    total = result.scalar_one()
    return account.opening_balance + total


async def get_all_balances(session: AsyncSession) -> list[tuple[Account, int]]:
    result = await session.execute(
        select(Account).where(Account.is_archived == False)  # noqa: E712
    )
    accounts = result.scalars().all()
    out = []
    for acc in accounts:
        bal = await get_account_balance(session, acc)
        out.append((acc, bal))
    return out


async def add_transaction(
    session: AsyncSession,
    account: Account,
    amount: int,
    type_: str,
    category: Category | None = None,
    note: str | None = None,
    occurred_at: datetime | None = None,
) -> Transaction:
    """
    amount should be signed already: negative for expense, positive for income.
    """
    txn = Transaction(
        account_id=account.id,
        category_id=category.id if category else None,
        amount=amount,
        type=type_,
        note=note,
        occurred_at=occurred_at or datetime.utcnow(),
    )
    session.add(txn)
    await session.flush()  # get txn.id

    session.add(AuditLog(
        transaction_id=txn.id,
        action="created",
        new_value=f"amount={amount}, type={type_}, note={note}",
    ))
    await session.commit()
    return txn


async def add_transfer(
    session: AsyncSession,
    from_account: Account,
    to_account: Account,
    amount: int,
    note: str | None = None,
) -> tuple[Transaction, Transaction]:
    """amount is positive; we create a -amount leg and a +amount leg."""
    group_id = str(uuid.uuid4())

    out_txn = Transaction(
        account_id=from_account.id,
        amount=-abs(amount),
        type="transfer",
        note=note or f"Transfer to {to_account.name}",
        transfer_group_id=group_id,
    )
    in_txn = Transaction(
        account_id=to_account.id,
        amount=abs(amount),
        type="transfer",
        note=note or f"Transfer from {from_account.name}",
        transfer_group_id=group_id,
    )
    session.add_all([out_txn, in_txn])
    await session.flush()

    session.add_all([
        AuditLog(transaction_id=out_txn.id, action="created", new_value=f"transfer out {amount}"),
        AuditLog(transaction_id=in_txn.id, action="created", new_value=f"transfer in {amount}"),
    ])
    await session.commit()
    return out_txn, in_txn


async def soft_delete_transaction(session: AsyncSession, txn: Transaction):
    old = f"amount={txn.amount}, type={txn.type}, note={txn.note}"
    txn.is_deleted = True
    session.add(AuditLog(
        transaction_id=txn.id,
        action="deleted",
        old_value=old,
    ))
    await session.commit()


async def get_recent_transactions(session: AsyncSession, limit: int = 10) -> list[Transaction]:
    result = await session.execute(
        select(Transaction)
        .where(Transaction.is_deleted == False)  # noqa: E712
        .order_by(Transaction.occurred_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_net_worth(session: AsyncSession) -> int:
    balances = await get_all_balances(session)
    return sum(b for _, b in balances)


async def monthly_report(session: AsyncSession, year: int, month: int) -> dict:
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.is_deleted == False,  # noqa: E712
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
    )
    txns = result.scalars().all()

    total_income = sum(t.amount for t in txns if t.type == "income")
    total_expense = sum(-t.amount for t in txns if t.type == "expense")

    by_category: dict[str, int] = {}
    for t in txns:
        if t.type != "expense":
            continue
        cat_name = "Uncategorized"
        if t.category_id:
            cat = await session.get(Category, t.category_id)
            if cat:
                cat_name = cat.name
        by_category[cat_name] = by_category.get(cat_name, 0) + (-t.amount)

    return {
        "income": total_income,
        "expense": total_expense,
        "net": total_income - total_expense,
        "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
        "txn_count": len(txns),
    }

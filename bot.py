"""
Personal Finance Telegram Bot - v1

Commands:
  /start              - intro
  /accounts           - list accounts and balances
  /newaccount         - create an account
  /expense            - log an expense (guided)
  /income             - log income (guided)
  /transfer           - move money between accounts
  /categories         - list categories
  /newcategory        - add a category
  /recent             - last 10 transactions
  /report             - this month's report
  /networth           - total net worth across accounts
  /undo                - delete the most recent transaction (soft delete)

Quick entry: just type things like "spent 25k on lunch" or "salary 1200000"
and the bot will parse it automatically (asks which account to use if you
have more than one).
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, BotCommand
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

import config
from db import init_db, async_session
from models import Account, Category, Transaction
import ledger
import nlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ---------- Access control ----------

@dp.message.middleware()
async def auth_middleware(handler, event: Message, data):
    if event.from_user.id not in config.ALLOWED_USER_IDS:
        await event.answer("This is a private bot. You are not authorized to use it.")
        return
    return await handler(event, data)


@dp.callback_query.middleware()
async def auth_callback_middleware(handler, event: CallbackQuery, data):
    if event.from_user.id not in config.ALLOWED_USER_IDS:
        await event.answer("Not authorized.", show_alert=True)
        return
    return await handler(event, data)


# ---------- FSM states ----------

class ExpenseFlow(StatesGroup):
    choosing_account = State()
    entering_amount = State()
    choosing_category = State()
    entering_note = State()


class IncomeFlow(StatesGroup):
    choosing_account = State()
    entering_amount = State()
    entering_note = State()


class TransferFlow(StatesGroup):
    choosing_from = State()
    choosing_to = State()
    entering_amount = State()


class NewAccountFlow(StatesGroup):
    entering_name = State()
    entering_type = State()
    entering_currency = State()
    entering_opening_balance = State()


class NewCategoryFlow(StatesGroup):
    entering_name = State()
    entering_kind = State()


class QuickEntryFlow(StatesGroup):
    choosing_account = State()


def fmt(amount: int) -> str:
    return f"{amount:,}"


def main_menu_keyboard():
    """Persistent reply keyboard shown at the bottom of the chat."""
    kb = ReplyKeyboardBuilder()
    kb.button(text="💸 Expense")
    kb.button(text="💰 Income")
    kb.button(text="🔁 Transfer")
    kb.button(text="🏦 Accounts")
    kb.button(text="📊 Report")
    kb.button(text="📈 Net worth")
    kb.button(text="🕒 Recent")
    kb.button(text="↩️ Undo")
    kb.adjust(3, 3, 2)
    return kb.as_markup(resize_keyboard=True)


async def set_bot_commands(bot: Bot):
    """Populates the '/' menu next to the text input box."""
    commands = [
        BotCommand(command="start", description="Show intro / main menu"),
        BotCommand(command="expense", description="Log an expense"),
        BotCommand(command="income", description="Log income"),
        BotCommand(command="transfer", description="Transfer between accounts"),
        BotCommand(command="accounts", description="List accounts & balances"),
        BotCommand(command="newaccount", description="Create a new account"),
        BotCommand(command="categories", description="List categories"),
        BotCommand(command="newcategory", description="Create a new category"),
        BotCommand(command="recent", description="Last 10 transactions"),
        BotCommand(command="report", description="This month's report"),
        BotCommand(command="networth", description="Total net worth"),
        BotCommand(command="undo", description="Delete most recent transaction"),
    ]
    await bot.set_my_commands(commands)


# ---------- Helpers ----------

async def account_keyboard(accounts, prefix: str):
    kb = InlineKeyboardBuilder()
    for acc in accounts:
        kb.button(text=f"{acc.name} ({acc.currency})", callback_data=f"{prefix}:{acc.id}")
    kb.adjust(1)
    return kb.as_markup()


async def category_keyboard(categories, prefix: str):
    kb = InlineKeyboardBuilder()
    for cat in categories:
        kb.button(text=cat.name, callback_data=f"{prefix}:{cat.id}")
    kb.button(text="Skip", callback_data=f"{prefix}:skip")
    kb.adjust(2)
    return kb.as_markup()


# ---------- Basic commands ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Personal Finance Bot\n\n"
        "Quick entry: just type things like:\n"
        "  spent 25k on lunch\n"
        "  salary 1200000\n\n"
        "Use the buttons below, the / menu, or just type naturally.",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(Command("accounts"))
async def cmd_accounts(message: Message):
    async with async_session() as session:
        balances = await ledger.get_all_balances(session)
        if not balances:
            await message.answer("No accounts yet. Use /newaccount to create one.")
            return
        lines = ["Your accounts:\n"]
        for acc, bal in balances:
            lines.append(f"• {acc.name} ({acc.type}, {acc.currency}): {fmt(bal)}")
        await message.answer("\n".join(lines))


@dp.message(Command("networth"))
async def cmd_networth(message: Message):
    async with async_session() as session:
        balances = await ledger.get_all_balances(session)
        if not balances:
            await message.answer("No accounts yet.")
            return
        # naive sum across currencies -- fine if you mostly use one currency.
        # if you mix currencies heavily, report per-currency instead.
        by_currency: dict[str, int] = {}
        for acc, bal in balances:
            by_currency[acc.currency] = by_currency.get(acc.currency, 0) + bal
        lines = ["Net worth:\n"]
        for cur, total in by_currency.items():
            lines.append(f"• {cur}: {fmt(total)}")
        await message.answer("\n".join(lines))


@dp.message(Command("recent"))
async def cmd_recent(message: Message):
    async with async_session() as session:
        txns = await ledger.get_recent_transactions(session, limit=10)
        if not txns:
            await message.answer("No transactions yet.")
            return
        lines = ["Last transactions:\n"]
        for t in txns:
            acc = await session.get(Account, t.account_id)
            sign = "+" if t.amount >= 0 else ""
            date_str = t.occurred_at.strftime("%Y-%m-%d")
            note = f" - {t.note}" if t.note else ""
            lines.append(f"#{t.id} [{date_str}] {acc.name}: {sign}{fmt(t.amount)}{note}")
        await message.answer("\n".join(lines))


@dp.message(Command("report"))
async def cmd_report(message: Message):
    now = datetime.utcnow()
    async with async_session() as session:
        data = await ledger.monthly_report(session, now.year, now.month)
        lines = [
            f"Report for {now.strftime('%B %Y')}\n",
            f"Income: {fmt(data['income'])}",
            f"Expenses: {fmt(data['expense'])}",
            f"Net: {fmt(data['net'])}",
            f"Transactions: {data['txn_count']}\n",
        ]
        if data["by_category"]:
            lines.append("By category:")
            for cat, amt in data["by_category"].items():
                lines.append(f"  • {cat}: {fmt(amt)}")
        await message.answer("\n".join(lines))


@dp.message(Command("undo"))
async def cmd_undo(message: Message):
    async with async_session() as session:
        txns = await ledger.get_recent_transactions(session, limit=1)
        if not txns:
            await message.answer("Nothing to undo.")
            return
        txn = txns[0]
        await ledger.soft_delete_transaction(session, txn)
        await message.answer(f"Deleted transaction #{txn.id} ({fmt(txn.amount)}).")


# ---------- New account flow ----------

@dp.message(Command("newaccount"))
async def cmd_newaccount(message: Message, state: FSMContext):
    await state.set_state(NewAccountFlow.entering_name)
    await message.answer("Account name? (e.g. 'Cash', 'Zain Cash', 'Bank - Rasheed')")


@dp.message(NewAccountFlow.entering_name)
async def na_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    kb = InlineKeyboardBuilder()
    for t in ["cash", "bank", "wallet", "savings", "credit_card"]:
        kb.button(text=t, callback_data=f"acctype:{t}")
    kb.adjust(2)
    await state.set_state(NewAccountFlow.entering_type)
    await message.answer("Account type?", reply_markup=kb.as_markup())


@dp.callback_query(NewAccountFlow.entering_type, F.data.startswith("acctype:"))
async def na_type(callback: CallbackQuery, state: FSMContext):
    acc_type = callback.data.split(":")[1]
    await state.update_data(type=acc_type)
    await state.set_state(NewAccountFlow.entering_currency)
    await callback.message.edit_text(f"Type: {acc_type}\n\nCurrency? (e.g. IQD, USD)")
    await callback.answer()


@dp.message(NewAccountFlow.entering_currency)
async def na_currency(message: Message, state: FSMContext):
    await state.update_data(currency=message.text.strip().upper())
    await state.set_state(NewAccountFlow.entering_opening_balance)
    await message.answer("Opening balance? (whole number, 0 if starting fresh)")


@dp.message(NewAccountFlow.entering_opening_balance)
async def na_balance(message: Message, state: FSMContext):
    try:
        balance = int(message.text.strip().replace(",", ""))
    except ValueError:
        await message.answer("Please send a whole number.")
        return
    data = await state.get_data()
    async with async_session() as session:
        acc = Account(
            name=data["name"],
            type=data["type"],
            currency=data["currency"],
            opening_balance=balance,
        )
        session.add(acc)
        await session.commit()
    await state.clear()
    await message.answer(f"Created account '{data['name']}' with balance {fmt(balance)} {data['currency']}.")


# ---------- New category flow ----------

@dp.message(Command("newcategory"))
async def cmd_newcategory(message: Message, state: FSMContext):
    await state.set_state(NewCategoryFlow.entering_name)
    await message.answer("Category name? (e.g. 'Groceries', 'Salary', 'Transport')")


@dp.message(NewCategoryFlow.entering_name)
async def nc_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    kb = InlineKeyboardBuilder()
    kb.button(text="Expense", callback_data="catkind:expense")
    kb.button(text="Income", callback_data="catkind:income")
    await state.set_state(NewCategoryFlow.entering_kind)
    await message.answer("Is this an expense or income category?", reply_markup=kb.as_markup())


@dp.callback_query(NewCategoryFlow.entering_kind, F.data.startswith("catkind:"))
async def nc_kind(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(":")[1]
    data = await state.get_data()
    async with async_session() as session:
        cat = Category(name=data["name"], kind=kind)
        session.add(cat)
        await session.commit()
    await state.clear()
    await callback.message.edit_text(f"Created category '{data['name']}' ({kind}).")
    await callback.answer()


@dp.message(Command("categories"))
async def cmd_categories(message: Message):
    async with async_session() as session:
        result = await session.execute(select(Category))
        cats = result.scalars().all()
        if not cats:
            await message.answer("No categories yet. Use /newcategory to add one.")
            return
        lines = ["Categories:\n"]
        for c in cats:
            lines.append(f"• {c.name} ({c.kind})")
        await message.answer("\n".join(lines))


# ---------- Expense flow ----------

@dp.message(Command("expense"))
async def cmd_expense(message: Message, state: FSMContext):
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.is_archived == False))  # noqa: E712
        accounts = result.scalars().all()
        if not accounts:
            await message.answer("Create an account first with /newaccount.")
            return
        await state.set_state(ExpenseFlow.choosing_account)
        kb = await account_keyboard(accounts, "expacc")
        await message.answer("Which account?", reply_markup=kb)


@dp.callback_query(ExpenseFlow.choosing_account, F.data.startswith("expacc:"))
async def exp_account(callback: CallbackQuery, state: FSMContext):
    acc_id = int(callback.data.split(":")[1])
    await state.update_data(account_id=acc_id)
    await state.set_state(ExpenseFlow.entering_amount)
    await callback.message.edit_text("How much did you spend?")
    await callback.answer()


@dp.message(ExpenseFlow.entering_amount)
async def exp_amount(message: Message, state: FSMContext):
    try:
        amount = nlp._parse_amount(message.text)
    except ValueError:
        await message.answer("Please send a number, like 25000 or 25k.")
        return
    await state.update_data(amount=amount)
    async with async_session() as session:
        result = await session.execute(select(Category).where(Category.kind == "expense"))
        cats = result.scalars().all()
        kb = await category_keyboard(cats, "expcat")
        await state.set_state(ExpenseFlow.choosing_category)
        await message.answer("Category?", reply_markup=kb)


@dp.callback_query(ExpenseFlow.choosing_category, F.data.startswith("expcat:"))
async def exp_category(callback: CallbackQuery, state: FSMContext):
    val = callback.data.split(":")[1]
    await state.update_data(category_id=None if val == "skip" else int(val))
    await state.set_state(ExpenseFlow.entering_note)
    await callback.message.edit_text("Note? (or send '-' to skip)")
    await callback.answer()


@dp.message(ExpenseFlow.entering_note)
async def exp_note(message: Message, state: FSMContext):
    note = None if message.text.strip() == "-" else message.text.strip()
    data = await state.get_data()
    async with async_session() as session:
        account = await session.get(Account, data["account_id"])
        category = await session.get(Category, data["category_id"]) if data.get("category_id") else None
        txn = await ledger.add_transaction(
            session, account, -abs(data["amount"]), "expense", category=category, note=note
        )
        balance = await ledger.get_account_balance(session, account)
    await state.clear()
    await message.answer(
        f"Logged expense: {fmt(data['amount'])} from {account.name}"
        f"{' - ' + note if note else ''}\n"
        f"New balance: {fmt(balance)} {account.currency}"
    )


# ---------- Income flow ----------

@dp.message(Command("income"))
async def cmd_income(message: Message, state: FSMContext):
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.is_archived == False))  # noqa: E712
        accounts = result.scalars().all()
        if not accounts:
            await message.answer("Create an account first with /newaccount.")
            return
        await state.set_state(IncomeFlow.choosing_account)
        kb = await account_keyboard(accounts, "incacc")
        await message.answer("Which account?", reply_markup=kb)


@dp.callback_query(IncomeFlow.choosing_account, F.data.startswith("incacc:"))
async def inc_account(callback: CallbackQuery, state: FSMContext):
    acc_id = int(callback.data.split(":")[1])
    await state.update_data(account_id=acc_id)
    await state.set_state(IncomeFlow.entering_amount)
    await callback.message.edit_text("How much did you receive?")
    await callback.answer()


@dp.message(IncomeFlow.entering_amount)
async def inc_amount(message: Message, state: FSMContext):
    try:
        amount = nlp._parse_amount(message.text)
    except ValueError:
        await message.answer("Please send a number, like 1200000 or 1200k.")
        return
    await state.update_data(amount=amount)
    await state.set_state(IncomeFlow.entering_note)
    await message.answer("Note? (e.g. 'salary', or send '-' to skip)")


@dp.message(IncomeFlow.entering_note)
async def inc_note(message: Message, state: FSMContext):
    note = None if message.text.strip() == "-" else message.text.strip()
    data = await state.get_data()
    async with async_session() as session:
        account = await session.get(Account, data["account_id"])
        txn = await ledger.add_transaction(
            session, account, abs(data["amount"]), "income", note=note
        )
        balance = await ledger.get_account_balance(session, account)
    await state.clear()
    await message.answer(
        f"Logged income: {fmt(data['amount'])} to {account.name}"
        f"{' - ' + note if note else ''}\n"
        f"New balance: {fmt(balance)} {account.currency}"
    )


# ---------- Transfer flow ----------

@dp.message(Command("transfer"))
async def cmd_transfer(message: Message, state: FSMContext):
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.is_archived == False))  # noqa: E712
        accounts = result.scalars().all()
        if len(accounts) < 2:
            await message.answer("You need at least 2 accounts to transfer between them.")
            return
        await state.set_state(TransferFlow.choosing_from)
        kb = await account_keyboard(accounts, "trfrom")
        await message.answer("Transfer FROM which account?", reply_markup=kb)


@dp.callback_query(TransferFlow.choosing_from, F.data.startswith("trfrom:"))
async def tr_from(callback: CallbackQuery, state: FSMContext):
    from_id = int(callback.data.split(":")[1])
    await state.update_data(from_id=from_id)
    async with async_session() as session:
        result = await session.execute(
            select(Account).where(Account.is_archived == False, Account.id != from_id)  # noqa: E712
        )
        accounts = result.scalars().all()
        kb = await account_keyboard(accounts, "trto")
    await state.set_state(TransferFlow.choosing_to)
    await callback.message.edit_text("Transfer TO which account?", reply_markup=kb)
    await callback.answer()


@dp.callback_query(TransferFlow.choosing_to, F.data.startswith("trto:"))
async def tr_to(callback: CallbackQuery, state: FSMContext):
    to_id = int(callback.data.split(":")[1])
    await state.update_data(to_id=to_id)
    await state.set_state(TransferFlow.entering_amount)
    await callback.message.edit_text("How much to transfer?")
    await callback.answer()


@dp.message(TransferFlow.entering_amount)
async def tr_amount(message: Message, state: FSMContext):
    try:
        amount = nlp._parse_amount(message.text)
    except ValueError:
        await message.answer("Please send a number, like 50000 or 50k.")
        return
    data = await state.get_data()
    async with async_session() as session:
        from_acc = await session.get(Account, data["from_id"])
        to_acc = await session.get(Account, data["to_id"])
        await ledger.add_transfer(session, from_acc, to_acc, amount)
        from_bal = await ledger.get_account_balance(session, from_acc)
        to_bal = await ledger.get_account_balance(session, to_acc)
    await state.clear()
    await message.answer(
        f"Transferred {fmt(amount)} from {from_acc.name} to {to_acc.name}\n"
        f"{from_acc.name}: {fmt(from_bal)} {from_acc.currency}\n"
        f"{to_acc.name}: {fmt(to_bal)} {to_acc.currency}"
    )


# ---------- Reply-keyboard button routing ----------
# These map the persistent bottom-keyboard buttons to the same handlers as
# their slash-command equivalents. Must be registered before the catch-all
# quick_entry handler below.

@dp.message(F.text == "💸 Expense")
async def kb_expense(message: Message, state: FSMContext):
    await cmd_expense(message, state)


@dp.message(F.text == "💰 Income")
async def kb_income(message: Message, state: FSMContext):
    await cmd_income(message, state)


@dp.message(F.text == "🔁 Transfer")
async def kb_transfer(message: Message, state: FSMContext):
    await cmd_transfer(message, state)


@dp.message(F.text == "🏦 Accounts")
async def kb_accounts(message: Message):
    await cmd_accounts(message)


@dp.message(F.text == "📊 Report")
async def kb_report(message: Message):
    await cmd_report(message)


@dp.message(F.text == "📈 Net worth")
async def kb_networth(message: Message):
    await cmd_networth(message)


@dp.message(F.text == "🕒 Recent")
async def kb_recent(message: Message):
    await cmd_recent(message)


@dp.message(F.text == "↩️ Undo")
async def kb_undo(message: Message):
    await cmd_undo(message)


# ---------- Natural language quick entry ----------
# This catches plain messages that aren't commands and don't match an
# active FSM state above (aiogram only routes here if no state is set).

@dp.message(F.text)
async def quick_entry(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return  # mid-flow, let the relevant handler deal with it

    parsed = nlp.parse_message(message.text)
    if not parsed:
        await message.answer(
            "Didn't recognize that. Try '/expense', '/income', or phrasing like "
            "'spent 25k on lunch'."
        )
        return

    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.is_archived == False))  # noqa: E712
        accounts = result.scalars().all()
        if not accounts:
            await message.answer("Create an account first with /newaccount.")
            return
        if len(accounts) == 1:
            account = accounts[0]
            signed = parsed.amount if parsed.type_ == "income" else -parsed.amount
            await ledger.add_transaction(
                session, account, signed, parsed.type_, note=parsed.description
            )
            balance = await ledger.get_account_balance(session, account)
            verb = "Logged income" if parsed.type_ == "income" else "Logged expense"
            await message.answer(
                f"{verb}: {fmt(parsed.amount)} ({account.name})"
                f"{' - ' + parsed.description if parsed.description else ''}\n"
                f"New balance: {fmt(balance)} {account.currency}"
            )
        else:
            # multiple accounts -- ask which one to use
            await state.update_data(
                pending_amount=parsed.amount,
                pending_type=parsed.type_,
                pending_note=parsed.description,
            )
            kb = await account_keyboard(accounts, "qeacc")
            await state.set_state(QuickEntryFlow.choosing_account)
            await message.answer("Which account?", reply_markup=kb)


@dp.callback_query(QuickEntryFlow.choosing_account, F.data.startswith("qeacc:"))
async def quick_entry_account(callback: CallbackQuery, state: FSMContext):
    acc_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    async with async_session() as session:
        account = await session.get(Account, acc_id)
        amount = data["pending_amount"]
        type_ = data["pending_type"]
        note = data["pending_note"]
        signed = amount if type_ == "income" else -amount
        await ledger.add_transaction(session, account, signed, type_, note=note)
        balance = await ledger.get_account_balance(session, account)
    await state.clear()
    verb = "Logged income" if type_ == "income" else "Logged expense"
    await callback.message.edit_text(
        f"{verb}: {fmt(amount)} ({account.name})"
        f"{' - ' + note if note else ''}\n"
        f"New balance: {fmt(balance)} {account.currency}"
    )
    await callback.answer()


# ---------- Entrypoint ----------

async def main():
    await init_db()
    await set_bot_commands(bot)
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
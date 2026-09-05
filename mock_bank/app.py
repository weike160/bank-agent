#!/usr/bin/env python3
import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_DB = Path(__file__).with_name("bank.db")


def now():
    return datetime.now(timezone.utc).isoformat()


def money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError("amount must be a number")
    if amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("amount must be positive with at most 2 decimal places")
    return int(amount * 100)


def account_json(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "balance": f'{Decimal(row["balance_cents"]) / 100:.2f}',
        "status": row["status"],
    }


def transfer_json(row):
    return {
        "id": row["id"],
        "source_account_id": row["source_account_id"],
        "target_account_id": row["target_account_id"],
        "amount": f'{Decimal(row["amount_cents"]) / 100:.2f}',
        "currency": "CNY",
        "note": row["note"],
        "status": row["status"],
        "created_at": row["created_at"],
        "executed_at": row["executed_at"],
    }


def card_json(row):
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "card_type": row["card_type"],
        "status": row["status"],
        "limit": f'{Decimal(row["limit_cents"]) / 100:.2f}',
    }


class Bank:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = str(db_path)
        self.setup()

    def connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def setup(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT 'U1' REFERENCES users(id),
                    name TEXT NOT NULL,
                    balance_cents INTEGER NOT NULL CHECK (balance_cents >= 0),
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'frozen'))
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL REFERENCES accounts(id),
                    kind TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    counterparty TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'unknown',
                    category TEXT NOT NULL DEFAULT 'other',
                    merchant TEXT
                );
                CREATE TABLE IF NOT EXISTS transfers (
                    id TEXT PRIMARY KEY,
                    source_account_id TEXT NOT NULL REFERENCES accounts(id),
                    target_account_id TEXT NOT NULL REFERENCES accounts(id),
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'prepared'
                        CHECK (status IN ('prepared', 'executed')),
                    created_at TEXT NOT NULL,
                    executed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(id),
                    card_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'frozen', 'reported_lost')),
                    limit_cents INTEGER NOT NULL CHECK (limit_cents > 0)
                );
                CREATE TABLE IF NOT EXISTS holdings (
                    account_id TEXT NOT NULL REFERENCES accounts(id),
                    product TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
                    PRIMARY KEY (account_id, product)
                );
                CREATE TABLE IF NOT EXISTS debits (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(id),
                    merchant TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'cancelled')),
                    amount_cents INTEGER NOT NULL DEFAULT 0,
                    frequency TEXT NOT NULL DEFAULT 'monthly',
                    next_charge_at TEXT,
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS payees (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT 'U1' REFERENCES users(id),
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES accounts(id),
                    alias TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS investment_products (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    annual_rate TEXT NOT NULL,
                    minimum_cents INTEGER NOT NULL
                );
            """)
            db.executemany(
                "INSERT OR IGNORE INTO users VALUES (?, ?)",
                [
                    ("U1", "Alice"),
                    ("U2", "Bob"),
                    ("U3", "Tujing He"),
                    ("U4", "Ke Wei"),
                ],
            )
            account_columns = {row["name"] for row in db.execute("PRAGMA table_info(accounts)")}
            if "user_id" not in account_columns:
                db.execute("ALTER TABLE accounts ADD COLUMN user_id TEXT NOT NULL DEFAULT 'U1'")
            db.execute("UPDATE accounts SET user_id = 'U1' WHERE id = 'A1001'")
            db.execute("UPDATE accounts SET user_id = 'U2' WHERE id = 'A1002'")
            transaction_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(transactions)")
            }
            for name, definition in {
                "direction": "TEXT NOT NULL DEFAULT 'unknown'",
                "category": "TEXT NOT NULL DEFAULT 'other'",
                "merchant": "TEXT",
            }.items():
                if name not in transaction_columns:
                    db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {definition}")
            debit_columns = {row["name"] for row in db.execute("PRAGMA table_info(debits)")}
            for name, definition in {
                "amount_cents": "INTEGER NOT NULL DEFAULT 0",
                "frequency": "TEXT NOT NULL DEFAULT 'monthly'",
                "next_charge_at": "TEXT",
                "created_at": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in debit_columns:
                    db.execute(f"ALTER TABLE debits ADD COLUMN {name} {definition}")
            payee_columns = {row["name"] for row in db.execute("PRAGMA table_info(payees)")}
            if "owner_user_id" not in payee_columns:
                db.execute(
                    "ALTER TABLE payees ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT 'U1'"
                )
            db.executemany(
                "INSERT OR IGNORE INTO accounts "
                "(id, user_id, name, balance_cents, status) VALUES (?, ?, ?, ?, 'active')",
                [
                    ("A1001", "U1", "Alice", 100000),
                    ("A1002", "U2", "Bob", 50000),
                    ("A1003", "U3", "Tujing He", 100000),
                    ("A1004", "U4", "Ke Wei", 100000),
                ],
            )
            db.execute(
                "INSERT OR IGNORE INTO debits "
                "(id, account_id, merchant, status, amount_cents, frequency, next_charge_at, created_at) "
                "VALUES ('D1001', 'A1001', 'Example Telecom', 'active', 9900, 'monthly', "
                "'2026-10-01T00:00:00+00:00', ?)",
                (now(),),
            )
            db.execute(
                "INSERT OR IGNORE INTO cards VALUES ('C1001', 'A1001', 'debit', 'active', 500000)"
            )
            db.execute(
                "INSERT OR IGNORE INTO payees "
                "(id, owner_user_id, name, phone, account_id, alias) VALUES "
                "('P1001', 'U1', 'Bob', '13800000000', 'A1002', '小明')"
            )
            db.executemany(
                "INSERT OR IGNORE INTO investment_products VALUES (?, ?, ?, ?, ?)",
                [
                    ("I1001", "稳健理财", "LOW", "0.0250", 10000),
                    ("I1002", "成长理财", "MEDIUM", "0.0450", 50000),
                ],
            )

    def accounts(self, user_id=None):
        with self.connect() as db:
            if user_id:
                rows = db.execute(
                    "SELECT * FROM accounts WHERE user_id = ? ORDER BY id", (user_id,)
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [account_json(row) for row in rows]

    def account(self, account_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise LookupError("account not found")
        return account_json(row)

    def transactions(self, account_id, start_date=None, end_date=None, limit=100):
        self.account(account_id)
        conditions = ["account_id = ?"]
        parameters = [account_id]
        if start_date:
            conditions.append("created_at >= ?")
            parameters.append(start_date)
        if end_date:
            conditions.append("created_at <= ?")
            parameters.append(end_date)
        try:
            limit = min(max(int(limit), 1), 500)
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer")
        parameters.append(limit)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM transactions WHERE " + " AND ".join(conditions) +
                " ORDER BY id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [{
            "id": row["id"],
            "kind": row["kind"],
            "amount": f'{Decimal(row["amount_cents"]) / 100:.2f}',
            "counterparty": row["counterparty"],
            "note": row["note"],
            "created_at": row["created_at"],
            "direction": row["direction"],
            "category": row["category"],
            "merchant": row["merchant"],
        } for row in rows]

    def payees(self, user_id=None):
        with self.connect() as db:
            if user_id:
                rows = db.execute(
                    "SELECT * FROM payees WHERE owner_user_id = ? ORDER BY name", (user_id,)
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM payees ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def transfer_record(self, transfer_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
        if not row:
            raise LookupError("transfer not found")
        return transfer_json(row)

    def transfer_accounts(self, db, source, target, cents):
        if source == target:
            raise ValueError("source and target must be different")
        accounts = {
            row["id"]: row for row in db.execute(
                "SELECT * FROM accounts WHERE id IN (?, ?)", (source, target)
            )
        }
        if source not in accounts or target not in accounts:
            raise LookupError("account not found")
        if accounts[source]["status"] == "frozen":
            raise ValueError("source account is frozen")
        if accounts[source]["balance_cents"] < cents:
            raise ValueError("insufficient balance")
        return accounts

    def prepare_transfer(self, source, target, amount, note=""):
        cents = money(amount)
        with self.connect() as db:
            self.transfer_accounts(db, source, target, cents)
            transfer_id = "T" + uuid.uuid4().hex[:12].upper()
            db.execute(
                "INSERT INTO transfers "
                "(id, source_account_id, target_account_id, amount_cents, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (transfer_id, source, target, cents, note, now()),
            )
            row = db.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
        return transfer_json(row)

    def execute_transfer(self, transfer_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            transfer = db.execute(
                "SELECT * FROM transfers WHERE id = ?", (transfer_id,)
            ).fetchone()
            if not transfer:
                raise LookupError("transfer not found")
            source = transfer["source_account_id"]
            target = transfer["target_account_id"]
            if transfer["status"] != "executed":
                cents = transfer["amount_cents"]
                self.transfer_accounts(db, source, target, cents)
                db.execute(
                    "UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
                    (cents, source),
                )
                db.execute(
                    "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                    (cents, target),
                )
                created_at = now()
                db.executemany(
                    "INSERT INTO transactions "
                    "(account_id, kind, amount_cents, counterparty, note, created_at, "
                    "direction, category, merchant) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (source, "transfer_out", -cents, target, transfer["note"], created_at,
                         "out", "transfer", None),
                        (target, "transfer_in", cents, source, transfer["note"], created_at,
                         "in", "transfer", None),
                    ],
                )
                db.execute(
                    "UPDATE transfers SET status = 'executed', executed_at = ? WHERE id = ?",
                    (created_at, transfer_id),
                )
        with self.connect() as db:
            transfer = db.execute(
                "SELECT * FROM transfers WHERE id = ?", (transfer_id,)
            ).fetchone()
        return {
            "transfer": transfer_json(transfer),
            "from": self.account(source),
            "to": self.account(target),
        }

    def cards(self, user_id=None):
        with self.connect() as db:
            if user_id:
                rows = db.execute(
                    "SELECT cards.* FROM cards JOIN accounts ON accounts.id = cards.account_id "
                    "WHERE accounts.user_id = ? ORDER BY cards.id",
                    (user_id,),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM cards ORDER BY id").fetchall()
        return [card_json(row) for row in rows]

    def update_card_status(self, card_id, status):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE cards SET status = ? WHERE id = ?", (status, card_id)
            ).rowcount
            row = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not changed:
            raise LookupError("card not found")
        return card_json(row)

    def set_card_limit(self, card_id, amount):
        cents = money(amount)
        with self.connect() as db:
            changed = db.execute(
                "UPDATE cards SET limit_cents = ? WHERE id = ?", (cents, card_id)
            ).rowcount
            row = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not changed:
            raise LookupError("card not found")
        return card_json(row)

    def freeze(self, account_id):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE accounts SET status = 'frozen' WHERE id = ?", (account_id,)
            ).rowcount
        if not changed:
            raise LookupError("account not found")
        return self.account(account_id)

    def invest(self, account_id, product, amount):
        if not isinstance(product, str) or not product.strip():
            raise ValueError("product is required")
        cents = money(amount)
        with self.connect() as db:
            investment_product = db.execute(
                "SELECT * FROM investment_products WHERE name = ?", (product.strip(),)
            ).fetchone()
            if not investment_product:
                raise LookupError("investment product not found")
            if cents < investment_product["minimum_cents"]:
                raise ValueError("amount is below product minimum")
            account = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if not account:
                raise LookupError("account not found")
            if account["status"] == "frozen":
                raise ValueError("account is frozen")
            if account["balance_cents"] < cents:
                raise ValueError("insufficient balance")
            db.execute(
                "UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
                (cents, account_id),
            )
            db.execute(
                "INSERT INTO holdings VALUES (?, ?, ?) "
                "ON CONFLICT(account_id, product) DO UPDATE "
                "SET amount_cents = amount_cents + excluded.amount_cents",
                (account_id, product.strip(), cents),
            )
            db.execute(
                "INSERT INTO transactions "
                "(account_id, kind, amount_cents, counterparty, note, created_at, "
                "direction, category, merchant) "
                "VALUES (?, 'investment', ?, ?, '', ?, 'out', 'investment', ?)",
                (account_id, -cents, product.strip(), now(), product.strip()),
            )
        return self.holdings(account_id)

    def investment_products(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM investment_products ORDER BY id").fetchall()
        return [{
            "id": row["id"],
            "name": row["name"],
            "risk_level": row["risk_level"],
            "annual_rate": row["annual_rate"],
            "minimum_amount": f'{Decimal(row["minimum_cents"]) / 100:.2f}',
        } for row in rows]

    def redeem(self, account_id, product, amount):
        if not isinstance(product, str) or not product.strip():
            raise ValueError("product is required")
        cents = money(amount)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            holding = db.execute(
                "SELECT amount_cents FROM holdings WHERE account_id = ? AND product = ?",
                (account_id, product.strip()),
            ).fetchone()
            if not holding or holding["amount_cents"] < cents:
                raise ValueError("insufficient holding")
            db.execute(
                "UPDATE holdings SET amount_cents = amount_cents - ? "
                "WHERE account_id = ? AND product = ?",
                (cents, account_id, product.strip()),
            )
            db.execute(
                "DELETE FROM holdings WHERE account_id = ? AND product = ? AND amount_cents = 0",
                (account_id, product.strip()),
            )
            db.execute(
                "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                (cents, account_id),
            )
            db.execute(
                "INSERT INTO transactions "
                "(account_id, kind, amount_cents, counterparty, note, created_at, "
                "direction, category, merchant) "
                "VALUES (?, 'investment_redeem', ?, ?, '', ?, 'in', 'investment', ?)",
                (account_id, cents, product.strip(), now(), product.strip()),
            )
        return self.holdings(account_id)

    def holdings(self, account_id):
        self.account(account_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT product, amount_cents FROM holdings WHERE account_id = ? ORDER BY product",
                (account_id,),
            ).fetchall()
        return [{"product": row["product"], "amount": f'{Decimal(row["amount_cents"]) / 100:.2f}'} for row in rows]

    def cancel_debit(self, debit_id):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE debits SET status = 'cancelled' WHERE id = ?", (debit_id,)
            ).rowcount
            row = db.execute("SELECT * FROM debits WHERE id = ?", (debit_id,)).fetchone()
        if not changed:
            raise LookupError("debit not found")
        return self.debit_json(row)

    def debits(self, user_id=None):
        with self.connect() as db:
            if user_id:
                rows = db.execute(
                    "SELECT debits.* FROM debits JOIN accounts ON accounts.id = debits.account_id "
                    "WHERE accounts.user_id = ? ORDER BY debits.id",
                    (user_id,),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM debits ORDER BY id").fetchall()
        return [self.debit_json(row) for row in rows]

    def debit(self, debit_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM debits WHERE id = ?", (debit_id,)).fetchone()
        if not row:
            raise LookupError("debit not found")
        return self.debit_json(row)

    @staticmethod
    def debit_json(row):
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "merchant": row["merchant"],
            "status": row["status"],
            "amount": f'{Decimal(row["amount_cents"]) / 100:.2f}',
            "frequency": row["frequency"],
            "next_charge_at": row["next_charge_at"],
            "created_at": row["created_at"],
        }


class Handler(BaseHTTPRequestHandler):
    bank = None

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise ValueError("invalid JSON")

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        query = parse_qs(parsed.query)
        try:
            if parts == ["accounts"]:
                result = self.bank.accounts()
            elif len(parts) == 3 and parts[0] == "users" and parts[2] == "accounts":
                result = self.bank.accounts(parts[1])
            elif len(parts) == 3 and parts[0] == "users" and parts[2] == "payees":
                result = self.bank.payees(parts[1])
            elif len(parts) == 3 and parts[0] == "users" and parts[2] == "cards":
                result = self.bank.cards(parts[1])
            elif len(parts) == 3 and parts[0] == "users" and parts[2] == "debits":
                result = self.bank.debits(parts[1])
            elif len(parts) == 2 and parts[0] == "accounts":
                result = self.bank.account(parts[1])
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "transactions":
                result = self.bank.transactions(
                    parts[1],
                    query.get("start_date", [None])[0],
                    query.get("end_date", [None])[0],
                    query.get("limit", [100])[0],
                )
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "holdings":
                result = self.bank.holdings(parts[1])
            elif parts == ["debits"]:
                result = self.bank.debits()
            elif len(parts) == 2 and parts[0] == "debits":
                result = self.bank.debit(parts[1])
            elif parts == ["cards"]:
                result = self.bank.cards()
            elif parts == ["payees"]:
                result = self.bank.payees()
            elif len(parts) == 2 and parts[0] == "transfers":
                result = self.bank.transfer_record(parts[1])
            elif parts == ["investments", "products"]:
                result = self.bank.investment_products()
            else:
                return self.send_json(404, {"error": "route not found"})
            self.send_json(200, result)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})

    def do_POST(self):
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        try:
            data = self.read_json()
            if parts == ["transfers", "prepare"]:
                result = self.bank.prepare_transfer(
                    data.get("from"), data.get("to"), data.get("amount"), data.get("note", "")
                )
            elif len(parts) == 3 and parts[0] == "transfers" and parts[2] == "execute":
                result = self.bank.execute_transfer(parts[1])
            elif len(parts) == 3 and parts[0] == "cards" and parts[2] == "freeze":
                result = self.bank.update_card_status(parts[1], "frozen")
            elif len(parts) == 3 and parts[0] == "cards" and parts[2] == "unfreeze":
                result = self.bank.update_card_status(parts[1], "active")
            elif len(parts) == 3 and parts[0] == "cards" and parts[2] == "report-lost":
                result = self.bank.update_card_status(parts[1], "reported_lost")
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "freeze":
                result = self.bank.freeze(parts[1])
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "investments":
                result = self.bank.invest(parts[1], data.get("product"), data.get("amount"))
            elif len(parts) == 4 and parts[0] == "accounts" and parts[2:] == ["investments", "redeem"]:
                result = self.bank.redeem(parts[1], data.get("product"), data.get("amount"))
            elif len(parts) == 3 and parts[0] == "debits" and parts[2] == "cancel":
                result = self.bank.cancel_debit(parts[1])
            else:
                return self.send_json(404, {"error": "route not found"})
            self.send_json(200, result)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
        except (ValueError, AttributeError) as error:
            self.send_json(400, {"error": str(error)})

    def do_PUT(self):
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        try:
            data = self.read_json()
            if len(parts) == 3 and parts[0] == "cards" and parts[2] == "limit":
                result = self.bank.set_card_limit(parts[1], data.get("amount"))
            else:
                return self.send_json(404, {"error": "route not found"})
            self.send_json(200, result)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
        except (ValueError, AttributeError) as error:
            self.send_json(400, {"error": str(error)})

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


def main():
    parser = argparse.ArgumentParser(description="Minimal persistent mock bank API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    Handler.bank = Bank(args.db)
    print(f"Mock bank listening on http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

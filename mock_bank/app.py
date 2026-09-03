#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


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
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
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
                    created_at TEXT NOT NULL
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
                        CHECK (status IN ('active', 'cancelled'))
                );
            """)
            if not db.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
                db.executemany(
                    "INSERT INTO accounts VALUES (?, ?, ?, 'active')",
                    [("A1001", "Alice", 100000), ("A1002", "Bob", 50000)],
                )
                db.execute(
                    "INSERT INTO debits VALUES ('D1001', 'A1001', 'Example Telecom', 'active')"
                )

    def account(self, account_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise LookupError("account not found")
        return account_json(row)

    def transactions(self, account_id):
        self.account(account_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM transactions WHERE account_id = ? ORDER BY id DESC",
                (account_id,),
            ).fetchall()
        return [{
            "id": row["id"],
            "kind": row["kind"],
            "amount": f'{Decimal(row["amount_cents"]) / 100:.2f}',
            "counterparty": row["counterparty"],
            "note": row["note"],
            "created_at": row["created_at"],
        } for row in rows]

    def transfer(self, source, target, amount, note=""):
        if source == target:
            raise ValueError("source and target must be different")
        cents = money(amount)
        with self.connect() as db:
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
                "(account_id, kind, amount_cents, counterparty, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (source, "transfer_out", -cents, target, note, created_at),
                    (target, "transfer_in", cents, source, note, created_at),
                ],
            )
        return {"from": self.account(source), "to": self.account(target)}

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
                "(account_id, kind, amount_cents, counterparty, note, created_at) "
                "VALUES (?, 'investment', ?, ?, '', ?)",
                (account_id, -cents, product.strip(), now()),
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
        return dict(row)

    def debit(self, debit_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM debits WHERE id = ?", (debit_id,)).fetchone()
        if not row:
            raise LookupError("debit not found")
        return dict(row)


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
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        try:
            if len(parts) == 2 and parts[0] == "accounts":
                result = self.bank.account(parts[1])
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "transactions":
                result = self.bank.transactions(parts[1])
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "holdings":
                result = self.bank.holdings(parts[1])
            elif len(parts) == 2 and parts[0] == "debits":
                result = self.bank.debit(parts[1])
            else:
                return self.send_json(404, {"error": "route not found"})
            self.send_json(200, result)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})

    def do_POST(self):
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        try:
            data = self.read_json()
            if parts == ["transfers"]:
                result = self.bank.transfer(data.get("from"), data.get("to"), data.get("amount"), data.get("note", ""))
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "freeze":
                result = self.bank.freeze(parts[1])
            elif len(parts) == 3 and parts[0] == "accounts" and parts[2] == "investments":
                result = self.bank.invest(parts[1], data.get("product"), data.get("amount"))
            elif len(parts) == 3 and parts[0] == "debits" and parts[2] == "cancel":
                result = self.bank.cancel_debit(parts[1])
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

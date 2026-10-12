from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from models.invoice import Invoice
from models.trip import TripContext


class ReimbursementDB:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site TEXT NOT NULL,
                    examiner TEXT,
                    root_path TEXT,
                    generated_excel_path TEXT,
                    exported_zip_path TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id INTEGER,
                    file_name TEXT,
                    invoice_number TEXT,
                    category TEXT,
                    verification_status TEXT,
                    payload_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(trip_id) REFERENCES trips(id)
                )
                """
            )

    def save_trip(self, trip: TripContext) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trips(site, examiner, root_path, generated_excel_path, exported_zip_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trip.site, trip.examiner, trip.root_path, trip.generated_excel_path, trip.exported_zip_path),
            )
            trip_id = int(cursor.lastrowid)
            for invoice in trip.invoices:
                self.save_invoice(conn, trip_id, invoice)
            return trip_id

    def save_invoice(self, conn: sqlite3.Connection, trip_id: int, invoice: Invoice) -> None:
        conn.execute(
            """
            INSERT INTO invoices(trip_id, file_name, invoice_number, category, verification_status, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trip_id,
                invoice.file_name,
                invoice.invoice_number,
                invoice.category,
                invoice.verification_status_label,
                json.dumps(invoice.to_json_dict(), ensure_ascii=False),
            ),
        )

    def list_trips(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trips ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

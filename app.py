import sqlite3
from datetime import datetime, timedelta, date
import pandas as pd
import streamlit as st
import math
from io import BytesIO
import os, hashlib
import locale
import psycopg2
try:
    import libsql
except ImportError:
    libsql = None

DB_NAME = "leave_tracker.db"

RECOVERABLE_TYPES = ["Învoire"]

LEAVE_TYPES = [
    "Concediu odihnă",
    "Concediu medical",
    "Concediu fără plată"
]

POSITION_OPTIONS = [
    "Project Manager",
    "Dezvoltator Software",
    "Tester Analist",
    "HR Admin",
]

# -----------------------------
# DATABASE HELPERS
# -----------------------------

def count_business_days(start_date, end_date):
    """
    Numără zilele lucrătoare dintre start_date și end_date.
    end_date este exclusivă, adică ziua întoarcerii nu se scade.
    Weekendul este ignorat.
    """
    days = 0
    current_date = start_date

    while current_date < end_date:
        # weekday(): luni = 0, duminică = 6
        if current_date.weekday() < 5:
            days += 1

        current_date += timedelta(days=1)

    return float(days)


def get_leave_end_date(entry):
    """
    Folosește end_date dacă există.
    Pentru intrări vechi fără end_date, calculează aproximativ pe baza leave_days.
    """
    start_date = datetime.fromisoformat(entry["entry_date"]).date()

    if entry["end_date"]:
        return datetime.fromisoformat(entry["end_date"]).date()

    return start_date + timedelta(days=math.ceil(float(entry["leave_days"])))

def hash_pin(pin, salt=None):
    if salt is None:
        salt = os.urandom(16)

    if isinstance(salt, str):
        salt = bytes.fromhex(salt)

    pin_hash = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        100_000
    )

    return pin_hash.hex(), salt.hex()


def verify_pin(pin, stored_hash, stored_salt):
    if not stored_hash or not stored_salt:
        return False

    new_hash, _ = hash_pin(pin, stored_salt)
    return new_hash == stored_hash

def update_user_pin(employee_id, new_pin):
    conn = get_connection()
    cursor = conn.cursor()

    pin_hash, pin_salt = hash_pin(new_pin)

    cursor.execute("""
        UPDATE employees
        SET pin_hash = ?,
            pin_salt = ?
        WHERE id = ?
    """, (
        pin_hash,
        pin_salt,
        employee_id
    ))

    rows_updated = cursor.rowcount

    conn.commit()
    conn.close()

    st.cache_data.clear()

    return rows_updated == 1

def has_overlapping_leave(employee_id, start_date, end_date, exclude_entry_id=None):
    conn = get_connection()
    cursor = conn.cursor()

    if exclude_entry_id is None:
        cursor.execute("""
            SELECT *
            FROM entries
            WHERE employee_id = ?
              AND deleted = 0
              AND entry_type IN ('Concediu odihnă', 'Concediu medical', 'Concediu fără plată')
        """, (employee_id,))
    else:
        cursor.execute("""
            SELECT *
            FROM entries
            WHERE employee_id = ?
              AND deleted = 0
              AND id != ?
              AND entry_type IN ('Concediu odihnă', 'Concediu medical', 'Concediu fără plată')
        """, (employee_id, exclude_entry_id))

    existing_entries = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    st.cache_data.clear()

    for existing in existing_entries:
        existing_start = datetime.fromisoformat(existing["entry_date"]).date()
        existing_end = get_leave_end_date(existing)

        overlaps = start_date < existing_end and end_date > existing_start

        if overlaps:
            return True, existing

    return False, None

def get_entry_interval(entry):
    start = datetime.fromisoformat(entry["entry_date"]).date()

    if entry["entry_type"] == "Învoire":
        end = start + timedelta(days=1)
    else:
        end = get_leave_end_date(entry)

    return start, end

def get_entry_interval(entry):
    start = datetime.fromisoformat(entry["entry_date"]).date()

    if entry["entry_type"] == "Învoire":
        end = start + timedelta(days=1)
    else:
        end = get_leave_end_date(entry)

    return start, end


def has_conflicting_entry(employee_id, new_start, new_end, exclude_entry_id=None):
    conn = get_connection()
    cursor = conn.cursor()

    if exclude_entry_id is None:
        cursor.execute("""
            SELECT *
            FROM entries
            WHERE employee_id = ?
              AND deleted = 0
        """, (employee_id,))
    else:
        cursor.execute("""
            SELECT *
            FROM entries
            WHERE employee_id = ?
              AND deleted = 0
              AND id != ?
        """, (employee_id, exclude_entry_id))

    existing_entries = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    for existing in existing_entries:
        existing_start, existing_end = get_entry_interval(existing)

        overlaps = new_start < existing_end and new_end > existing_start

        if overlaps:
            return True, existing, existing_start, existing_end

    return False, None, None, None

def format_month_ro(month_key):
    months = {
        "01": "Ianuarie",
        "02": "Februarie",
        "03": "Martie",
        "04": "Aprilie",
        "05": "Mai",
        "06": "Iunie",
        "07": "Iulie",
        "08": "August",
        "09": "Septembrie",
        "10": "Octombrie",
        "11": "Noiembrie",
        "12": "Decembrie",
    }

    year, month = month_key.split("-")
    return f"{months[month]} {year}"

# def get_connection():
#     return sqlite3.connect(DB_NAME)

def get_connection():
    turso_url = ""
    turso_token = ""

    try:
        if hasattr(st, "secrets"):
            turso_url = st.secrets["TURSO_DATABASE_URL"] if "TURSO_DATABASE_URL" in st.secrets else ""
            turso_token = st.secrets["TURSO_AUTH_TOKEN"] if "TURSO_AUTH_TOKEN" in st.secrets else ""
    except Exception:
        turso_url = ""
        turso_token = ""

    if turso_url and turso_token:
        if libsql is None:
            st.error("libsql is not installed. Locally, either install libsql or run without Turso secrets.")
            st.stop()

        return libsql.connect(
            turso_url,
            auth_token=turso_token
        )

    return sqlite3.connect(DB_NAME)

def rows_to_dicts(cursor, rows):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def row_to_dict(cursor, row):
    if row is None:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            name TEXT NOT NULL,
            position TEXT,
            role TEXT DEFAULT 'employee',
            pin_hash TEXT,
            pin_salt TEXT,
            deleted INTEGER DEFAULT 0
        )
    """)

    cursor.execute("PRAGMA table_info(entries)")

    cursor.execute("PRAGMA table_info(employees)")

    employee_columns = [col[1] for col in cursor.fetchall()]

    if "username" not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN username TEXT")

    if "position" not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN position TEXT")

    if "role" not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN role TEXT DEFAULT 'employee'")

    if "pin_hash" not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN pin_hash TEXT")

    if "pin_salt" not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN pin_salt TEXT")

    if "deleted" not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN deleted INTEGER DEFAULT 0")
      
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY ,
            employee_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            end_date TEXT,
            entry_type TEXT NOT NULL,
            hours INTEGER DEFAULT 0,
            leave_days REAL DEFAULT 0,
            description TEXT,
            deleted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recovery_hours (
            id INTEGER PRIMARY KEY ,
            entry_id INTEGER NOT NULL,
            hour_index INTEGER NOT NULL,
            is_recovered INTEGER DEFAULT 0,
            recovered_at TEXT,
            FOREIGN KEY (entry_id) REFERENCES entries(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leave_balances (
            id INTEGER PRIMARY KEY ,
            employee_id INTEGER NOT NULL UNIQUE,
            annual_leave_days REAL DEFAULT 21,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
    """)

    # Pentru baze deja create anterior, adaugă coloanele noi dacă lipsesc.
    cursor.execute("PRAGMA table_info(entries)")
    columns = [col[1] for col in cursor.fetchall()]

    if "leave_days" not in columns:
        cursor.execute("ALTER TABLE entries ADD COLUMN leave_days INTEGER DEFAULT 0")

    if "end_date" not in columns:
        cursor.execute("ALTER TABLE entries ADD COLUMN end_date TEXT")

    cursor.execute("PRAGMA table_info(leave_balances)")
    balance_columns = [col[1] for col in cursor.fetchall()]

    if "manual_used_adjustment" not in balance_columns:
        cursor.execute("""
            ALTER TABLE leave_balances
            ADD COLUMN manual_used_adjustment REAL DEFAULT 0.0
        """)

    if "manual_used_leave_days" not in balance_columns:
        cursor.execute("""
            ALTER TABLE leave_balances 
            ADD COLUMN manual_used_leave_days REAL DEFAULT NULL
        """)

    if len(balance_columns) == 0:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leave_balances (
                id INTEGER PRIMARY KEY ,
                employee_id INTEGER NOT NULL UNIQUE,
                annual_leave_days REAL DEFAULT 21.0,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
            )
        """)

    default_admins = [
        ("mmitrica", "Marius Mitrica", "Project Manager", "Admin", "1234"),
        ("rbejan", "Roxana Bejan", "HR Admin", "Admin", "1234"),
    ]

    for username, name, position, role, pin in default_admins:
        pin_hash, pin_salt = hash_pin(pin)

        cursor.execute("""
            INSERT OR IGNORE INTO employees (
                username, name, position, role, pin_hash, pin_salt
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            username,
            name,
            position,
            role,
            pin_hash,
            pin_salt
        ))

        cursor.execute("""
        SELECT id
        FROM employees
        WHERE username = ?
    """, (username,))

        row = cursor.fetchone()

        if row:
            employee_id = row[0]

            cursor.execute("""
                INSERT OR IGNORE INTO leave_balances (
                    employee_id, annual_leave_days
                )
                VALUES (?, 21.0)
            """, (employee_id,))
            
    cursor.execute("""
        INSERT OR IGNORE INTO leave_balances (
            employee_id, annual_leave_days
        )
        SELECT id, 21.0
        FROM employees
    """)

    cursor.execute("""
        UPDATE leave_balances
        SET annual_leave_days = 21.0
        WHERE annual_leave_days = 0
    """)

    conn.commit()
    conn.close()

    st.cache_data.clear()

@st.cache_data(ttl=30)
def get_employee_by_username(username):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM employees
        WHERE lower(username) = lower(?)
            AND COALESCE(deleted, 0) = 0
    """, (username.strip(),))

    row = cursor.fetchone()
    employee = row_to_dict(cursor, row)

    conn.close()
    st.cache_data.clear()
    return employee

@st.cache_data(ttl=30)
def get_people_off_on_date(target_date):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            emp.name,
            emp.position,
            e.entry_type,
            e.entry_date,
            e.end_date,
            e.hours,
            e.leave_days
        FROM entries e
        JOIN employees emp ON emp.id = e.employee_id
        WHERE e.deleted = 0
          AND (
                (
                    e.entry_type IN ('Concediu odihnă', 'Concediu medical', 'Concediu fără plată')
                    AND e.entry_date <= ?
                    AND e.end_date > ?
                )
                OR
                (
                    e.entry_type = 'Învoire'
                    AND e.entry_date = ?
                )
          )
        ORDER BY emp.name
    """, (
        str(target_date),
        str(target_date),
        str(target_date)
    ))

    rows = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    st.cache_data.clear()
    return rows

def create_employee(username, full_name, position, role, pin):
    conn = get_connection()
    cursor = conn.cursor()

    pin_hash, pin_salt = hash_pin(pin)

    cursor.execute("""
        INSERT INTO employees (
            username, name, position, role, pin_hash, pin_salt
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        username.strip().lower(),
        full_name.strip(),
        position,
        role,
        pin_hash,
        pin_salt
    ))

    employee_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO leave_balances (employee_id, annual_leave_days)
        VALUES (?, 21.0)
    """, (employee_id,))

    conn.commit()
    conn.close()

    st.cache_data.clear()

def update_employee(employee_id, username, full_name, position, role):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE employees
            SET username = ?,
                name = ?,
                position = ?,
                role = ?
            WHERE id = ?
              AND COALESCE(deleted, 0) = 0
        """, (
            username.strip().lower(),
            full_name.strip(),
            position,
            role,
            employee_id
        ))

        rows_updated = cursor.rowcount

        conn.commit()
        conn.close()

        st.cache_data.clear()

        return True, rows_updated == 1

    except Exception as e:
        conn.rollback()
        conn.close()

        error_text = str(e).lower()

        if "unique" in error_text and "username" in error_text:
            return False, "Există deja un utilizator cu acest username."

        return False, f"Eroare la modificarea utilizatorului: {e}"


def soft_delete_employee(employee_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE employees
        SET deleted = 1
        WHERE id = ?
    """, (employee_id,))

    rows_updated = cursor.rowcount

    conn.commit()
    conn.close()

    st.cache_data.clear()

    return rows_updated == 1


def add_entry(employee_id, entry_date, end_date, entry_type, hours, leave_days, description):
    conn = get_connection()
    cursor = conn.cursor()

    if entry_type == "Învoire":
        final_hours = int(hours)
        final_leave_days = 0.0
        final_end_date = None
    else:
        final_hours = 0
        final_leave_days = float(leave_days)
        final_end_date = str(end_date)

    cursor.execute("""
        INSERT INTO entries (
            employee_id,
            entry_date,
            end_date,
            entry_type,
            hours,
            leave_days,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        employee_id,
        str(entry_date),
        final_end_date,
        entry_type,
        final_hours,
        final_leave_days,
        description,
        datetime.now().isoformat(timespec="seconds")
    ))

    entry_id = cursor.lastrowid

    if entry_type == "Învoire":
        for i in range(1, final_hours + 1):
            cursor.execute("""
                INSERT INTO recovery_hours (entry_id, hour_index, is_recovered)
                VALUES (?, ?, 0)
            """, (entry_id, i))

    conn.commit()
    conn.close()

    st.cache_data.clear()

@st.cache_data(ttl=30)
def get_entries_for_employee(employee_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM entries
        WHERE employee_id = ?
          AND deleted = 0
        ORDER BY entry_date DESC, id DESC
    """, (employee_id,))

    entries = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    st.cache_data.clear()
    return entries

@st.cache_data(ttl=30)
def get_all_entries_for_admin():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            e.*,
            emp.name AS employee_name,
            emp.username AS employee_username,
            emp.position AS employee_position
        FROM entries e
        JOIN employees emp ON emp.id = e.employee_id
        WHERE e.deleted = 0
        ORDER BY e.entry_date DESC, e.id DESC
    """)

    entries = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    st.cache_data.clear()
    return entries

@st.cache_data(ttl=30)
def get_hours_for_entry(entry_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM recovery_hours
        WHERE entry_id = ?
        ORDER BY hour_index
    """, (entry_id,))

    hours = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    st.cache_data.clear()
    return hours


def mark_hour_recovered(hour_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE recovery_hours
        SET is_recovered = 1,
            recovered_at = ?
        WHERE id = ?
    """, (
        datetime.now().isoformat(timespec="seconds"),
        hour_id
    ))

    conn.commit()
    conn.close()

    st.cache_data.clear()


def unmark_hour_recovered(hour_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE recovery_hours
        SET is_recovered = 0,
            recovered_at = NULL
        WHERE id = ?
    """, (hour_id,))

    conn.commit()
    conn.close()

    st.cache_data.clear()


def soft_delete_entry(entry_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE entries
        SET deleted = 1
        WHERE id = ?
    """, (entry_id,))

    conn.commit()
    conn.close()

    st.cache_data.clear()

@st.cache_data(ttl=30)
def get_leave_balance(employee_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM leave_balances
        WHERE employee_id = ?
    """, (employee_id,))

    row = cursor.fetchone()
    balance = row_to_dict(cursor, row)

    conn.close()

    st.cache_data.clear()
    return balance


def update_annual_leave_days(employee_id, annual_leave_days):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO leave_balances (employee_id, annual_leave_days)
        VALUES (?, ?)
        ON CONFLICT(employee_id)
        DO UPDATE SET annual_leave_days = excluded.annual_leave_days
    """, (employee_id, float(annual_leave_days)))

    conn.commit()
    conn.close()

    st.cache_data.clear()

@st.cache_data(ttl=30)
def get_leave_summary(employee_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            entry_type,
            substr(entry_date, 1, 7) AS month,
            SUM(leave_days) AS total_days
        FROM entries
        WHERE employee_id = ?
          AND deleted = 0
          AND entry_type IN ('Concediu odihnă', 'Concediu medical', 'Concediu fără plată')
        GROUP BY entry_type, substr(entry_date, 1, 7)
        ORDER BY month DESC, entry_type
    """, (employee_id,))

    rows = rows_to_dicts(cursor, cursor.fetchall())
    conn.close()

    st.cache_data.clear()
    return rows

@st.cache_data(ttl=30)
def get_used_annual_leave_days(employee_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(leave_days), 0.0)
        FROM entries
        WHERE employee_id = ?
          AND deleted = 0
          AND entry_type = 'Concediu odihnă'
    """, (employee_id,))

    used_days = float(cursor.fetchone()[0])
    conn.close()

    st.cache_data.clear()
    return used_days


def update_leave_entry(entry_id, entry_date, end_date, entry_type, leave_days, description):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE entries
        SET entry_date = ?,
            end_date = ?,
            entry_type = ?,
            leave_days = ?,
            hours = 0,
            description = ?
        WHERE id = ?
    """, (
        str(entry_date),
        str(end_date),
        entry_type,
        float(leave_days),
        description,
        entry_id
    ))

    conn.commit()
    conn.close()

    st.cache_data.clear()

@st.cache_data(ttl=30)
def get_export_data(employee_id):
    conn = get_connection()

    df = pd.read_sql_query("""
        SELECT
            e.id AS entry_id,
            emp.name AS employee_name,
            e.entry_date,
            e.end_date,
            substr(e.entry_date, 1, 7) AS month,
            e.entry_type,
            e.hours AS recovery_hours_total,
            e.leave_days,
            e.description,
            rh.hour_index,
            rh.is_recovered,
            rh.recovered_at
        FROM entries e
        JOIN employees emp ON emp.id = e.employee_id
        LEFT JOIN recovery_hours rh ON rh.entry_id = e.id
        WHERE e.employee_id = ?
          AND e.deleted = 0
        ORDER BY e.entry_date DESC, e.id DESC, rh.hour_index
    """, conn, params=(employee_id,))

    conn.close()

    st.cache_data.clear()
    return df

@st.cache_data(ttl=30)
def get_all_users_table():
    conn = get_connection()

    df = pd.read_sql_query("""
    SELECT
        emp.id AS employee_id,
        emp.username,
        emp.name AS nume_complet,
        emp.position AS pozitie,
        emp.role AS rol,

        COALESCE(lb.annual_leave_days, 21.0) AS zile_co_disponibile,

        COALESCE(used.used_days, 0.0)
        + COALESCE(lb.manual_used_adjustment, 0.0) AS zile_co_folosite,

        COALESCE(lb.annual_leave_days, 21.0)
        - (
            COALESCE(used.used_days, 0.0)
            + COALESCE(lb.manual_used_adjustment, 0.0)
          ) AS zile_co_ramase

    FROM employees emp
    LEFT JOIN leave_balances lb ON lb.employee_id = emp.id
    LEFT JOIN (
        SELECT
            employee_id,
            SUM(leave_days) AS used_days
        FROM entries
        WHERE deleted = 0
          AND entry_type = 'Concediu odihnă'
        GROUP BY employee_id
    ) used ON used.employee_id = emp.id
    WHERE COALESCE(emp.deleted, 0) = 0
    ORDER BY emp.name
""", conn)

    conn.close()

    st.cache_data.clear()

    df["zile_co_disponibile"] = df["zile_co_disponibile"].astype(float)
    df["zile_co_folosite"] = df["zile_co_folosite"].astype(float)
    df["zile_co_ramase"] = df["zile_co_ramase"].astype(float)

    return df

def update_leave_balances_from_table(edited_df):
    conn = get_connection()
    cursor = conn.cursor()

    for _, row in edited_df.iterrows():
        employee_id = int(row["employee_id"])
        edited_used_days = float(row["zile_co_folosite"])

        cursor.execute("""
            SELECT COALESCE(SUM(leave_days), 0.0)
            FROM entries
            WHERE employee_id = ?
              AND deleted = 0
              AND entry_type = 'Concediu odihnă'
        """, (employee_id,))

        used_from_entries = float(cursor.fetchone()[0])

        manual_adjustment = edited_used_days - used_from_entries

        cursor.execute("""
            INSERT INTO leave_balances (
                employee_id,
                annual_leave_days,
                manual_used_adjustment
            )
            VALUES (?, 21.0, ?)
            ON CONFLICT(employee_id)
            DO UPDATE SET manual_used_adjustment = excluded.manual_used_adjustment
        """, (
            employee_id,
            manual_adjustment
        ))

    conn.commit()
    conn.close()

    st.cache_data.clear()
    
@st.cache_data(ttl=30)
def get_full_report_data(start_date, end_date):
    conn = get_connection()

    df = pd.read_sql_query("""
        SELECT
            emp.username,
            emp.name AS nume_complet,
            emp.position AS pozitie,
            emp.role AS rol,
            e.entry_date AS data_inceput,
            e.end_date AS data_intoarcere,
            e.entry_type AS tip_intrare,
            e.hours AS ore_invoire,
            e.leave_days AS zile_concediu,
            e.description AS observatii,
            rh.hour_index AS ora_index,
            rh.is_recovered AS ora_recuperata,
            rh.recovered_at AS recuperata_la
        FROM employees emp
        LEFT JOIN entries e ON e.employee_id = emp.id
            AND e.deleted = 0
            AND (
                (
                    e.entry_type = 'Învoire'
                    AND e.entry_date BETWEEN ? AND ?
                )
                OR
                (
                    e.entry_type IN ('Concediu odihnă', 'Concediu medical', 'Concediu fără plată')
                    AND e.entry_date < ?
                    AND e.end_date > ?
                )
            )
        LEFT JOIN recovery_hours rh ON rh.entry_id = e.id
        ORDER BY emp.name, e.entry_date, e.id, rh.hour_index
    """, conn, params=(
        str(start_date),
        str(end_date),
        str(end_date),
        str(start_date)
    ))

    conn.close()

    st.cache_data.clear()
    return df

# -----------------------------
# APP
# -----------------------------

st.set_page_config(
    page_title="Administrare Concedii si Invoiri",
    page_icon="📋",
    layout="wide"
)

st.markdown(
    """
    <style>
    div[data-testid="InputInstructions"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    div[data-testid="stTextInput"] input {
        height: 35px !important;
        line-height: 48px !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        text-align: center !important;
    }

    div[data-testid="stTextInput"] input::placeholder {
        line-height: 48px !important;
        text-align: center !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown("""
<style>
input[aria-label="Username"] {
    padding-left: 35px !important;
    padding-right: 25px !important;
}

input[aria-label="PIN"] {
    padding-left: 100px !important;
    padding-right: 44px !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown(
    """
    <style>
    .card {
        background-color: var(--background-color);
        color: var(--text-color);
        padding: 18px;
        border-radius: 12px;
        border: 1px solid rgba(128, 128, 128, 0.25);
        margin-bottom: 16px;
    }

    .card-title {
        font-size: 20px;
        font-weight: 700;
        margin-bottom: 10px;
        color: var(--text-color);
    }

    .muted {
        color: rgba(128, 128, 128, 0.9);
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown("""
<style>
div[data-baseweb="select"] input {
    caret-color: transparent !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown(
    """
    <style>
    .login-wrapper {
        max-width: 480px;
        margin: 80px auto 0 auto;
        padding: 32px;
        border-radius: 16px;
        border: 1px solid rgba(128, 128, 128, 0.25);
        background-color: rgba(128, 128, 128, 0.08);
        text-align: center;
    }

    .login-title {
        font-size: 34px;
        font-weight: 800;
        margin-bottom: 8px;
    }

    .login-subtitle {
        font-size: 16px;
        opacity: 0.75;
        margin-bottom: 24px;
    }

    div[data-testid="stTextInput"] input {
        font-size: 18px;
        height: 46px;
    }
    </style>
    """,
    unsafe_allow_html=True
)


@st.cache_resource
def init_db_once():
    init_db()
    return True

init_db_once()

# if "TURSO_DATABASE_URL" in st.secrets:
#     st.sidebar.success("DB: Turso")
# else:
#     st.sidebar.error("DB: SQLite local / Turso lipsă")

try:
    locale.setlocale(locale.LC_TIME, "ro_RO.UTF-8")
except locale.Error:
    pass

if "logged_username" not in st.session_state:
    st.session_state.logged_username = ""

if not st.session_state.logged_username:
    st.markdown(
        """
        <style>
        .login-title {
            font-size: 38px;
            font-weight: 800;
            text-align: center;
            margin-bottom: 8px;
        }

        .login-subtitle {
            font-size: 16px;
            text-align: center;
            opacity: 0.75;
            margin-bottom: 26px;
        }

        .login-box {
            padding: 34px 38px 28px 38px;
            border-radius: 18px;
            border: 1px solid rgba(128, 128, 128, 0.25);
            background-color: rgba(128, 128, 128, 0.08);
            margin-bottom: 22px;
            margin-top: 90px;
        }

        div[data-testid="stTextInput"] input {
            font-size: 18px;
            height: 48px;
            border-radius: 10px;
            text-align: center;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    left, login_col, right = st.columns([1.4, 1, 1.4])

    with login_col:
        st.markdown(
            """
            <div class="login-box">
                <div class="login-title">STATUS32</div>
                <div class="login-subtitle">Administrare concedii și învoiri</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        username_input = st.text_input(
        "Username",
        placeholder="Username",
        label_visibility="collapsed",
        key="login_username",
        autocomplete="off"
    )

        pin_input = st.text_input(
            "PIN",
            placeholder="Password",
            type="password",
            label_visibility="collapsed",
            key="login_pin",
            autocomplete="off"
        )

    if username_input.strip() and pin_input:
        employee = get_employee_by_username(username_input)

        if employee is None:
            st.warning("Username inexistent. Cere unui admin să creeze utilizatorul.")
            st.stop()

        pin_ok = verify_pin(
            pin_input,
            employee["pin_hash"],
            employee["pin_salt"]
        )

        if pin_ok:
            st.session_state.logged_username = employee["username"]
            st.rerun()
        else:
            st.error("PIN greșit.")

    st.stop()

current_user = get_employee_by_username(st.session_state.logged_username)

if current_user is None:
    st.session_state.logged_username = ""
    st.rerun()

employee_id = current_user["id"]
employee_name = current_user["name"]
employee_position = current_user["position"]
user_role = current_user["role"]

page_left, page_col, page_right = st.columns([0.25, 2.5, 0.25])

with page_col:
    st.subheader(employee_name)

    if employee_position:
        st.caption(employee_position)

st.sidebar.image("logo.png", use_container_width=True)

st.sidebar.markdown("### Administrare")

admin_mode = user_role == "Admin"

if admin_mode:
    st.sidebar.success("Mod admin activ")
else:
    st.sidebar.info("Mod utilizator")

today = date.today()
people_off_today = get_people_off_on_date(today)

with st.sidebar.expander("Cine are liber azi", expanded=True):
    st.caption(today.strftime("%d.%m.%Y"))

    if not people_off_today:
        st.write("Nimeni nu are liber azi.")
    else:
        for person in people_off_today:
            start_date = datetime.fromisoformat(person["entry_date"]).date()

            if person["entry_type"] == "Învoire":
                st.markdown(
                    f"""
                    <div style="
                        padding: 8px 0;
                        border-bottom: 1px solid rgba(128,128,128,0.25);
                    ">
                        <b>{person['name']}</b><br>
                        <span style="font-size: 13px; opacity: 0.75;">
                            {person['position'] or 'Fără poziție'}<br>
                            Învoire - {person['hours']} ore<br>
                            {start_date.strftime('%d.%m.%Y')}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            else:
                end_date = datetime.fromisoformat(person["end_date"]).date()

                st.markdown(
                    f"""
                    <div style="
                        padding: 8px 0;
                        border-bottom: 1px solid rgba(128,128,128,0.25);
                    ">
                        <b>{person['name']}</b><br>
                        <span style="font-size: 13px; opacity: 0.75;">
                            {person['position'] or 'Fără poziție'}<br>
                            {person['entry_type']}<br>
                            {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

with st.sidebar.expander("Schimbă PIN"):
    current_pin = st.text_input(
        "PIN actual",
        type="password",
        key="change_current_pin",
        autocomplete="off"
    )

    new_pin = st.text_input(
        "PIN nou",
        type="password",
        key="change_new_pin",
        autocomplete="off"
    )

    confirm_new_pin = st.text_input(
        "Confirmă PIN nou",
        type="password",
        key="change_confirm_pin",
        autocomplete="off"
    )

    if st.button("Actualizează PIN", use_container_width=True):
        if not verify_pin(
            current_pin,
            current_user["pin_hash"],
            current_user["pin_salt"]
        ):
            st.error("Parola actuală este greșită.")
        elif not new_pin:
            st.error("Parola nouă este obligatorie.")
        elif new_pin != confirm_new_pin:
            st.error("Parolele noi nu coincid.")
        else:
            pin_updated = update_user_pin(employee_id, new_pin)

            if not pin_updated:
                st.error("Parola nu a fost actualizată. ID-ul utilizatorului nu a fost găsit în baza de date.")
                st.stop()

            st.session_state.logged_username = ""
            st.success("Parola a fost schimbată. Autentifică-te din nou.")
            st.rerun()

if "user_created_message" not in st.session_state:
    st.session_state.user_created_message = ""

if admin_mode:
    if st.session_state.user_created_message:
        st.sidebar.success(st.session_state.user_created_message)

if st.session_state.get("clear_new_user_fields", False):
    st.session_state.new_user_username = ""
    st.session_state.new_user_full_name = ""
    st.session_state.new_user_pin = ""
    st.session_state.clear_new_user_fields = False

if admin_mode:
    with st.sidebar.expander("Adaugă utilizator nou"):
        new_username = st.text_input(
            "Username",
            key="new_user_username",
            autocomplete="off"
        )

        new_full_name = st.text_input(
            "Nume complet",
            key="new_user_full_name",
            autocomplete="off"
        )

        new_position = st.selectbox(
            "Poziție",
            POSITION_OPTIONS,
            key="new_user_position"
        )

        new_pin = st.text_input(
            "PIN inițial",
            type="password",
            key="new_user_pin",
            autocomplete="off"
        )

        if new_position in ["Project Manager", "HR Admin"]:
            new_role = "Admin"
        else:
            new_role = "Employee"

        if st.button(
            "Creează utilizator",
            use_container_width=True,
            key="btn_create_new_user"
        ):
            st.session_state.user_created_message = ""

            if not new_username.strip():
                st.error("Username obligatoriu.")
            elif not new_full_name.strip():
                st.error("Numele complet este obligatoriu.")
            elif not new_pin.strip():
                st.error("Parola inițială este obligatorie.")
            elif get_employee_by_username(new_username):
                st.error("Există deja un utilizator cu acest username.")
            else:
                create_employee(
                    new_username,
                    new_full_name,
                    new_position,
                    new_role,
                    new_pin
                )

                st.session_state.user_created_message = "Utilizatorul a fost creat."
                st.session_state.clear_new_user_fields = True
                st.rerun()

if admin_mode:
    page_left, page_col, page_right = st.columns([0.25, 2.5, 0.25])

    with page_col:
        st.markdown(
            "<div style='font-size: 28px; font-weight: 700; margin-top: 24px;'>Utilizatori</div>",
            unsafe_allow_html=True
        )

        users_df = get_all_users_table()

        st.dataframe(
            users_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "employee_id": st.column_config.NumberColumn("ID", width="small"),
                "username": st.column_config.TextColumn("Username", width="medium"),
                "nume_complet": st.column_config.TextColumn("Nume complet", width="medium"),
                "pozitie": st.column_config.TextColumn("Poziție", width="medium"),
                "rol": st.column_config.TextColumn("Rol", width="small"),
                "zile_co_disponibile": st.column_config.NumberColumn("Zile CO disponibile", format="%.2f", width="small"),
                "zile_co_folosite": st.column_config.NumberColumn("Zile CO folosite", format="%.2f", width="small"),
                "zile_co_ramase": st.column_config.NumberColumn("Zile CO rămase", format="%.2f", width="small"),
            }
        )

        selected_user_label = st.selectbox(
            "Alege utilizator",
            users_df["nume_complet"].tolist(),
            key="admin_selected_user"
        )

        selected_user_row = users_df[users_df["nume_complet"] == selected_user_label].iloc[0]
        selected_employee_id = int(selected_user_row["employee_id"])


    page_left, page_col, page_right = st.columns([0.25, 2.5, 0.25])

    with page_col:
        with st.expander("Administrare cont utilizator selectat", expanded=False):

            edit_username = st.text_input(
                "Username utilizator",
                value=str(selected_user_row["username"]),
                key=f"edit_username_{selected_employee_id}",
                autocomplete="off"
            )

            edit_full_name = st.text_input(
                "Nume complet utilizator",
                value=str(selected_user_row["nume_complet"]),
                key=f"edit_full_name_{selected_employee_id}",
                autocomplete="off"
            )

            current_position = selected_user_row["pozitie"]

            if current_position in POSITION_OPTIONS:
                position_index = POSITION_OPTIONS.index(current_position)
            else:
                position_index = 0

            edit_position = st.selectbox(
                "Poziție utilizator",
                POSITION_OPTIONS,
                index=position_index,
                key=f"edit_position_{selected_employee_id}"
            )

            if edit_position in ["Project Manager", "HR Admin"]:
                edit_role = "Admin"
            else:
                edit_role = "Employee"

                st.markdown("")

            if st.button(
                "Salvează modificările utilizatorului",
                use_container_width=True,
                key=f"btn_save_user_changes_{selected_employee_id}"
            ):
                if not edit_username.strip():
                    st.error("Username obligatoriu.")
                elif not edit_full_name.strip():
                    st.error("Numele complet este obligatoriu.")
                else:
                    success, result = update_employee(
                    selected_employee_id,
                    edit_username,
                    edit_full_name,
                    edit_position,
                    edit_role
                )

                if success and result:
                    st.success("Utilizatorul a fost modificat.")
                    st.rerun()
                elif success and not result:
                    st.error("Utilizatorul nu a fost găsit sau nu a putut fi modificat.")
                else:
                    st.error(result)

            st.markdown("")

            if selected_employee_id == employee_id:
                st.button(
                    "Șterge utilizatorul",
                    disabled=True,
                    use_container_width=True,
                    help="Nu poți șterge utilizatorul cu care ești logat.",
                    key=f"btn_delete_user_disabled_{selected_employee_id}"
                )
            else:
                confirm_delete_user = st.checkbox(
                    "Confirm ștergerea utilizatorului",
                    key=f"confirm_delete_user_{selected_employee_id}"
                )

                if st.button(
                    "Șterge utilizatorul",
                    use_container_width=True,
                    key=f"btn_delete_user_{selected_employee_id}"
                ):
                    if not confirm_delete_user:
                        st.error("Bifează confirmarea înainte de ștergere.")
                    else:
                        deleted = soft_delete_employee(selected_employee_id)

                        if deleted:
                            st.success("Utilizatorul a fost șters.")
                            st.rerun()
                        else:
                            st.error("Utilizatorul nu a fost găsit sau nu a putut fi șters.")

    st.markdown("#### Sold CO")

    new_balance = st.number_input(
        "Total zile CO disponibile",
        min_value=-365.0,
        max_value=365.0,
        value=float(selected_user_row["zile_co_disponibile"]),
        step=0.25,
        format="%.2f",
        key=f"admin_balance_{selected_employee_id}"
    )

    if st.button("Actualizează soldul utilizatorului", use_container_width=True):
        update_annual_leave_days(selected_employee_id, float(new_balance))
        st.success("Soldul a fost actualizat.")
        st.rerun()

    st.markdown("#### Administrare concedii utilizator selectat")

    selected_entries = get_entries_for_employee(selected_employee_id)

    selected_leave_entries = [
        entry for entry in selected_entries
        if entry["entry_type"] in ["Concediu odihnă", "Concediu medical", "Concediu fără plată"]
    ]

    if not selected_leave_entries:
        st.info("Utilizatorul nu are concedii înregistrate.")
    else:
        for entry in selected_leave_entries:
            start_date = datetime.fromisoformat(entry["entry_date"]).date()
            end_date = get_leave_end_date(entry)

            col1, col2 = st.columns([5, 1])

            with col1:
                st.write(
                    f"{entry['entry_type']} | "
                    f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')} | "
                    f"{float(entry['leave_days']):.2f} zile"
                )

            with col2:
                if st.button("Șterge", key=f"admin_delete_leave_{entry['id']}"):
                    soft_delete_entry(entry["id"])
                    st.success("Concediul a fost șters.")
                    st.rerun()



def logout_employee():
    st.session_state.logged_username = ""

st.sidebar.button(
    "Deconectare",
    on_click=logout_employee,
    use_container_width=True,
    type="primary"
)

if admin_mode:
    st.sidebar.markdown("### Raport general concedii")

    report_start_date = st.sidebar.date_input(
        "De la",
        key="report_start_date"
    )

    report_end_date = st.sidebar.date_input(
        "Până la",
        key="report_end_date"
    )

    if report_end_date >= report_start_date:
        if st.sidebar.button(
            "Generează raport general",
            use_container_width=True,
            key="btn_generate_general_report"
        ):
            report_df = get_full_report_data(
                report_start_date,
                report_end_date
            )

            users_df = get_all_users_table()

            if not report_df.empty:
                output = BytesIO()

                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    users_df.to_excel(
                        writer,
                        index=False,
                        sheet_name="Utilizatori"
                    )

                    report_df.to_excel(
                        writer,
                        index=False,
                        sheet_name="Raport interval"
                    )

                st.sidebar.download_button(
                    label="Descarcă raport general",
                    data=output.getvalue(),
                    file_name=f"raport_general_{report_start_date}_{report_end_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="download_general_report"
                )
            else:
                st.sidebar.info("Nu există date pentru interval.")
    else:
        st.sidebar.error("Interval invalid.")

# -----------------------------
# ADD NEW ENTRY
# -----------------------------

# -----------------------------
# ADD NEW ENTRY
# -----------------------------
if admin_mode and "admin_selected_user" in st.session_state:
    users_df_for_entry = get_all_users_table()
    selected_user_name = st.session_state.admin_selected_user
    selected_user_row = users_df_for_entry[
        users_df_for_entry["nume_complet"] == selected_user_name
    ].iloc[0]
    target_employee_id = int(selected_user_row["employee_id"])
else:
    target_employee_id = employee_id

page_left, page_col, page_right = st.columns([0.25, 2.5, 0.25])

with page_col:
    with st.expander("Adaugă intrare nouă", expanded=False):
        entry_type = st.selectbox(
            "Tip intrare",
            ["Învoire", "Concediu odihnă", "Concediu medical", "Concediu fără plată"],
            key="entry_type_select",
            accept_new_options=False
        )

        entry_date = st.date_input(
            "Data plecării",
            key="entry_date_input"
        )

        hours = 0
        leave_days = 0.0
        end_date = None

        if entry_type == "Învoire":
            hours = st.number_input(
                "Ore de recuperat",
                min_value=1,
                max_value=12,
                step=1,
                key="hours_input"
            )
        else:
            end_date = st.date_input(
                "Data întoarcerii",
                value=entry_date + timedelta(days=1),
                key="leave_end_date_input"
            )

            calculated_leave_days = count_business_days(entry_date, end_date)

            st.info(
                f"Zile de concediu calculate, fără weekend: {calculated_leave_days:.2f}"
            )

            manual_override = st.checkbox(
                "Suprascrie manual zilele scăzute",
                key="manual_override_input"
            )

            if manual_override:
                leave_days = st.number_input(
                    "Zile de concediu scăzute manual",
                    min_value=0.0,
                    max_value=31.0,
                    value=float(calculated_leave_days),
                    step=0.25,
                    format="%.2f",
                    key=f"leave_days_manual_{entry_date}_{end_date}"
                )
            else:
                leave_days = float(calculated_leave_days)

        description = st.text_area(
            "Observații",
            key="description_input"
        )

        if "confirm_overdraw" not in st.session_state:
            st.session_state.confirm_overdraw = False

        if st.button("Salvează intrarea", key="save_entry_button"):
            overdraw = False

            if entry_type == "Învoire":
                new_start = entry_date
                new_end = entry_date + timedelta(days=1)
            else:
                if end_date <= entry_date:
                    st.error("Data întoarcerii trebuie să fie după data de început.")
                    st.stop()

                new_start = entry_date
                new_end = end_date

            conflict, existing, existing_start, existing_end = has_conflicting_entry(
                target_employee_id,
                new_start,
                new_end
            )

            if conflict:
                st.error(
                    f"Interval invalid: se suprapune cu o intrare existentă "
                    f"({existing['entry_type']} {existing_start.strftime('%d.%m.%Y')} - "
                    f"{existing_end.strftime('%d.%m.%Y')})."
                )
                st.stop()

            if entry_type == "Concediu odihnă":
                balance = get_leave_balance(target_employee_id)
                annual_days = float(balance["annual_leave_days"]) if balance else 21.0
                used_days = float(get_used_annual_leave_days(target_employee_id))
                remaining_days = annual_days - used_days

                if float(leave_days) > remaining_days:
                    overdraw = True
                    st.session_state.confirm_overdraw = True
                    st.session_state.pending_entry = {
                        "employee_id": target_employee_id,
                        "entry_date": entry_date,
                        "end_date": end_date,
                        "entry_type": entry_type,
                        "hours": int(hours),
                        "leave_days": float(leave_days),
                        "description": description,
                        "remaining_days": remaining_days
                    }

            if not overdraw:
                add_entry(
                    target_employee_id,
                    entry_date,
                    end_date,
                    entry_type,
                    int(hours),
                    float(leave_days),
                    description
                )

                st.success("Intrarea a fost salvată.")
                st.rerun()

        if st.session_state.confirm_overdraw:
            pending = st.session_state.pending_entry

            st.warning(
                f"Atenție: încerci să iei {pending['leave_days']:.2f} zile CO, "
                f"dar mai ai disponibile doar {pending['remaining_days']:.2f}. "
                f"Dacă salvezi, vei ajunge la "
                f"{pending['remaining_days'] - pending['leave_days']:.2f} zile."
            )

            col1, col2 = st.columns(2)

            with col1:
                if st.button("Confirmă salvarea pe minus", key="confirm_overdraw_button"):
                    add_entry(
                        pending["employee_id"],
                        pending["entry_date"],
                        pending["end_date"],
                        pending["entry_type"],
                        pending["hours"],
                        pending["leave_days"],
                        pending["description"]
                    )

                    st.session_state.confirm_overdraw = False
                    st.session_state.pending_entry = None

                    st.success("Intrarea a fost salvată cu sold negativ.")
                    st.rerun()

            with col2:
                if st.button("Renunță", key="cancel_overdraw_button"):
                    st.session_state.confirm_overdraw = False
                    st.session_state.pending_entry = None
                    st.rerun()



# -----------------------------
# DISPLAY ENTRIES
# -----------------------------

balance = get_leave_balance(employee_id)
annual_days = float(balance["annual_leave_days"]) if balance else 21.0
used_annual_days = float(get_used_annual_leave_days(employee_id))
remaining_annual_days = annual_days - used_annual_days

page_left, page_col, page_right = st.columns([0.25, 2.5, 0.25])

with page_col:
    st.markdown(
        "<div style='font-size: 28px; font-weight: 700;'>Situație angajat</div>",
        unsafe_allow_html=True
    )

    if admin_mode:
        entries = get_all_entries_for_admin()
    else:
        entries = get_entries_for_employee(employee_id)

    if not entries:
        st.write("Nu există intrări.")

    # Grupăm intrările pe lună
    entries_by_month = {}

    for entry in entries:
        month_key = entry["entry_date"][:7]  # exemplu: 2026-06

        if month_key not in entries_by_month:
            entries_by_month[month_key] = []

        entries_by_month[month_key].append(entry)


    for month_key, month_entries in entries_by_month.items():
        month_label = format_month_ro(month_key)

        st.subheader(month_label)

        # -----------------------------
        # ÎNVOIRI
        # -----------------------------

        invoiri = [
            entry for entry in month_entries
            if entry["entry_type"] == "Învoire"
        ]

        if invoiri:
            st.markdown(
                f"<div style='font-size: 22px; font-weight: 700; margin-top: 16px; margin-bottom: 8px;'>Învoire - {month_label}</div>",
                unsafe_allow_html=True
            )

        for entry in invoiri:
            if admin_mode:
                st.write(f"Angajat: {entry['employee_name']} ({entry['employee_username']})")

            st.write(f"Data plecării: {entry['entry_date']}")

            st.write(f"Ore de recuperat: {entry['hours']}")

            if entry["description"]:
                st.write(f"Observații: {entry['description']}")

            recovery_hours = get_hours_for_entry(entry["id"])

            entry_date_display = datetime.fromisoformat(entry["entry_date"]).date()
            entry_date_label = entry_date_display.strftime("%d.%m.%Y")

            recovered_hours = [h for h in recovery_hours if h["is_recovered"] == 1]
            unrecovered_hours = [h for h in recovery_hours if h["is_recovered"] == 0]

            recovered_count = len(recovered_hours)
            total_count = len(recovery_hours)

            col_minus, col_display, col_plus = st.columns([0.7, 5, 0.7])

            with col_minus:
                st.write("")
                if st.button(
                    "−",
                    key=f"minus_recovery_{entry['id']}",
                    disabled=(recovered_count == 0),
                    use_container_width=True
                ):
                    last_recovered_hour = recovered_hours[-1]
                    unmark_hour_recovered(last_recovered_hour["id"])
                    st.rerun()

            with col_display:
                if recovered_count == total_count:
                    st.success(
                        f"{entry_date_label} - {recovered_count}/{total_count} recuperate"
                    )
                elif recovered_count == 0:
                    st.warning(
                        f"{entry_date_label} - {recovered_count}/{total_count} recuperate"
                    )
                else:
                    st.info(
                        f"{entry_date_label} - {recovered_count}/{total_count} recuperate"
                    )

            with col_plus:
                st.write("")
                if st.button(
                    "+",
                    key=f"plus_recovery_{entry['id']}",
                    disabled=(recovered_count == total_count),
                    use_container_width=True
                ):
                    next_unrecovered_hour = unrecovered_hours[0]
                    mark_hour_recovered(next_unrecovered_hour["id"])
                    st.rerun()

            if admin_mode:
                if st.button("Șterge învoirea", key=f"delete_invoire_{entry['id']}"):
                    soft_delete_entry(entry["id"])
                    st.rerun()

            st.divider()

        # -----------------------------
        # CONCEDII GRUPATE PE TIP
        # -----------------------------

        leave_types_in_month = [
            "Concediu odihnă",
            "Concediu medical",
            "Concediu fără plată"
        ]

        for leave_type in leave_types_in_month:
            leave_entries = [
                entry for entry in month_entries
                if entry["entry_type"] == leave_type
            ]

            if not leave_entries:
                continue

            total_days = sum(float(entry["leave_days"]) for entry in leave_entries)

            st.markdown(
                f"<div style='font-size: 22px; font-weight: 700; margin-top: 16px; margin-bottom: 8px;'>{leave_type} - {month_label}</div>",
                unsafe_allow_html=True
            )

            st.write(f"Total zile: {total_days:.2f}")

            for entry in leave_entries:
                if admin_mode:
                    st.write(f"Angajat: {entry['employee_name']} ({entry['employee_username']})")

                start_date = datetime.fromisoformat(entry["entry_date"]).date()

                if entry["end_date"]:
                    end_date = datetime.fromisoformat(entry["end_date"]).date()
                    interval_text = f"Interval: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
                else:
                    end_date = start_date + timedelta(days=1)
                    interval_text = f"Data: {start_date.strftime('%d.%m.%Y')}"

                col1, col2, col3 = st.columns([4, 2, 2])

                with col1:
                    st.write(interval_text)

                with col2:
                    st.write(f"{float(entry['leave_days']):.2f} zile")

                with col3:
                    if admin_mode:
                        if st.button("Șterge", key=f"delete_leave_{entry['id']}"):
                            soft_delete_entry(entry["id"])
                            st.rerun()
                    else:
                        st.write("")

                if entry["description"]:
                    st.caption(f"Observații: {entry['description']}")

                if admin_mode:
                    with st.expander(f"Admin - modifică intervalul {start_date.strftime('%d.%m.%Y')}"):
                        new_date = st.date_input(
                            "Data început",
                            value=start_date,
                            key=f"edit_date_{entry['id']}"
                        )

                        new_end_date = st.date_input(
                            "Data întoarcerii",
                            value=end_date,
                            key=f"edit_end_date_{entry['id']}"
                        )

                        new_type = st.selectbox(
                            "Tip concediu",
                            ["Concediu odihnă", "Concediu medical", "Concediu fără plată"],
                            index=["Concediu odihnă", "Concediu medical", "Concediu fără plată"].index(entry["entry_type"]),
                            key=f"edit_type_{entry['id']}"
                        )

                        calculated_new_days = count_business_days(new_date, new_end_date)

                        st.info(
                            f"Zile calculate automat, fără weekend: {calculated_new_days:.2f}"
                        )

                        new_days = st.number_input(
                            "Zile scăzute",
                            min_value=0.0,
                            max_value=31.0,
                            value=float(calculated_new_days),
                            step=0.25,
                            format="%.2f",
                            key=f"edit_days_{entry['id']}_{new_date}_{new_end_date}"
                        )

                        new_description = st.text_area(
                            "Observații",
                            value=entry["description"] or "",
                            key=f"edit_description_{entry['id']}"
                        )

                        if st.button("Salvează modificarea", key=f"save_override_{entry['id']}"):
                            update_leave_entry(
                                entry["id"],
                                new_date,
                                new_end_date,
                                new_type,
                                float(new_days),
                                new_description
                            )
                            st.success("Concediul a fost modificat.")
                            st.rerun()

#            st.divider()

# -----------------------------
# EXPORT
# -----------------------------

# st.sidebar.markdown("### Export")

# df_export = get_export_data(employee_id)

# if not df_export.empty:
#     excel_file = "export_angajat.xlsx"
#     df_export.to_excel(excel_file, index=False)

#     with open(excel_file, "rb") as file:
#         st.sidebar.download_button(
#             label="Descarcă Excel",
#             data=file,
#             file_name=f"situatie_{employee_name.strip()}.xlsx",
#             mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#             use_container_width=True
#         )
# else:
#     st.sidebar.write("Nu există date pentru export.")

st.sidebar.markdown("### Export")

if st.sidebar.button("Generează raport utilizator curent", use_container_width=True):
    df_export = get_export_data(employee_id)

    if not df_export.empty:
        output = BytesIO()

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_export.to_excel(writer, index=False, sheet_name="Situatie")

        st.sidebar.download_button(
            label="Descarcă raport utilizator curent",
            data=output.getvalue(),
            file_name=f"situatie_{employee_name.strip()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    else:
        st.sidebar.write("Nu există date pentru export.")
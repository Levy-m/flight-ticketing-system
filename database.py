"""
Database layer for the SkyLink Airlines Flight Ticketing System.

Responsibilities:
    * open / close SQLite connections (one per request, stored on flask.g)
    * create the schema (tables, keys, constraints, indexes)
    * populate the database with sample flights, an administrator and demo bookings
    * provide a small transaction helper used for bookings and cancellations

Run directly to (re)create the database:
    python database.py            # create tables + sample data if the DB is empty
    python database.py --reset    # delete the DB file and rebuild it from scratch
"""

import os
import random
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from flask import current_app, g
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "flight_ticketing.db")

TRAVEL_CLASSES = ["Economy", "Business", "First Class"]
BOOKING_STATUSES = ["Pending", "Confirmed", "Cancelled"]

SCHEMA = """
PRAGMA foreign_keys = ON;

-- One row per scheduled flight. The flight number is unique.
CREATE TABLE IF NOT EXISTS flights (
    flight_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number       TEXT    NOT NULL UNIQUE,
    airline_name        TEXT    NOT NULL DEFAULT 'SkyLink Airlines',
    departure_city      TEXT    NOT NULL,
    departure_airport   TEXT    NOT NULL,
    destination_city    TEXT    NOT NULL,
    destination_airport TEXT    NOT NULL,
    departure_date      TEXT    NOT NULL,              -- YYYY-MM-DD
    departure_time      TEXT    NOT NULL,              -- HH:MM (24h)
    arrival_time        TEXT    NOT NULL,              -- HH:MM (24h)
    aircraft            TEXT,
    created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (departure_city <> destination_city)
);

-- Design improvement: travel class, price and seat capacity are stored per class,
-- so one flight can offer Economy, Business and First Class at different prices.
CREATE TABLE IF NOT EXISTS flight_fares (
    fare_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(flight_id) ON DELETE CASCADE,
    travel_class    TEXT    NOT NULL CHECK (travel_class IN ('Economy', 'Business', 'First Class')),
    ticket_price    INTEGER NOT NULL CHECK (ticket_price > 0),          -- KSh per passenger
    total_seats     INTEGER NOT NULL CHECK (total_seats >= 0),
    available_seats INTEGER NOT NULL CHECK (available_seats >= 0 AND available_seats <= total_seats),
    UNIQUE (flight_id, travel_class)
);

CREATE TABLE IF NOT EXISTS passengers (
    passenger_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name             TEXT NOT NULL,
    email                 TEXT NOT NULL,
    phone                 TEXT NOT NULL,
    identification_number TEXT NOT NULL,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (email, identification_number)
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_reference    TEXT    NOT NULL UNIQUE,
    passenger_id         INTEGER NOT NULL REFERENCES passengers(passenger_id) ON DELETE RESTRICT,
    flight_id            INTEGER NOT NULL REFERENCES flights(flight_id) ON DELETE RESTRICT,
    number_of_passengers INTEGER NOT NULL CHECK (number_of_passengers > 0),
    travel_class         TEXT    NOT NULL CHECK (travel_class IN ('Economy', 'Business', 'First Class')),
    price_per_ticket     INTEGER NOT NULL CHECK (price_per_ticket > 0),
    total_cost           INTEGER NOT NULL CHECK (total_cost > 0),
    payment_method       TEXT    NOT NULL DEFAULT 'Simulated M-Pesa',
    booking_date         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    booking_status       TEXT    NOT NULL DEFAULT 'Pending'
                         CHECK (booking_status IN ('Pending', 'Confirmed', 'Cancelled')),
    cancelled_at         TEXT,
    customer_id          INTEGER REFERENCES customers(customer_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS administrators (
    admin_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'agent')),
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contact_messages (
    message_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name   TEXT NOT NULL,
    email           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    message         TEXT NOT NULL,
    submission_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_flights_route ON flights (departure_city, destination_city, departure_date);
CREATE INDEX IF NOT EXISTS idx_bookings_flight ON bookings (flight_id);
CREATE INDEX IF NOT EXISTS idx_bookings_passenger ON bookings (passenger_id);
"""

# --------------------------------------------------------------------------- #
# Sample data
# --------------------------------------------------------------------------- #

AIRPORTS = {
    "Nairobi": "Jomo Kenyatta International Airport (NBO)",
    "Mombasa": "Moi International Airport (MBA)",
    "Kisumu": "Kisumu International Airport (KIS)",
    "Eldoret": "Eldoret International Airport (EDL)",
    "Malindi": "Malindi Airport (MYD)",
    "Lamu": "Manda Airport (LAU)",
    "Zanzibar": "Abeid Amani Karume International Airport (ZNZ)",
    "Dar es Salaam": "Julius Nyerere International Airport (DAR)",
    "Kigali": "Kigali International Airport (KGL)",
    "Entebbe": "Entebbe International Airport (EBB)",
    "Dubai": "Dubai International Airport (DXB)",
}

# (from, to, departure time, duration in minutes, economy fare KSh, international?)
ROUTES = [
    ("Nairobi", "Mombasa", "08:00", 75, 8500, False),
    ("Nairobi", "Kisumu", "10:30", 55, 6500, False),
    ("Mombasa", "Nairobi", "14:00", 75, 9000, False),
    ("Kisumu", "Nairobi", "13:00", 55, 6800, False),
    ("Nairobi", "Eldoret", "07:15", 50, 5900, False),
    ("Eldoret", "Nairobi", "09:00", 50, 5900, False),
    ("Nairobi", "Malindi", "11:45", 80, 9800, False),
    ("Malindi", "Nairobi", "15:00", 80, 9800, False),
    ("Nairobi", "Lamu", "12:00", 95, 11500, False),
    ("Lamu", "Nairobi", "15:30", 95, 11500, False),
    ("Nairobi", "Zanzibar", "06:40", 105, 18500, True),
    ("Zanzibar", "Nairobi", "10:20", 105, 18500, True),
    ("Nairobi", "Kigali", "09:30", 120, 24000, True),
    ("Kigali", "Nairobi", "13:45", 105, 24000, True),
    ("Nairobi", "Entebbe", "16:30", 70, 16500, True),
    ("Entebbe", "Nairobi", "19:00", 70, 16500, True),
    ("Nairobi", "Dar es Salaam", "18:00", 80, 15500, True),
    ("Dar es Salaam", "Nairobi", "08:30", 80, 15500, True),
    ("Nairobi", "Dubai", "05:00", 320, 52000, True),
    ("Dubai", "Nairobi", "12:30", 330, 52000, True),
]

SCHEDULE_DAYS = 14  # sample flights are generated for the next 14 days
HISTORY_WEEKS = 52  # mock past flights (one day a week) so the reports have a year of data

# Mock traffic: how popular each destination is, and busier months (holidays).
DESTINATION_DEMAND = {"Mombasa": 1.0, "Zanzibar": 0.95, "Dubai": 0.85, "Nairobi": 0.8, "Malindi": 0.7,
                      "Kisumu": 0.65, "Lamu": 0.6, "Kigali": 0.55, "Entebbe": 0.5,
                      "Dar es Salaam": 0.5, "Eldoret": 0.4}
MONTH_DEMAND = {1: 0.8, 2: 0.6, 3: 0.65, 4: 0.85, 5: 0.6, 6: 0.7, 7: 0.85, 8: 1.0, 9: 0.7,
                10: 0.75, 11: 0.8, 12: 1.0}
MOCK_FIRST_NAMES = ["Wanjiku", "Kamau", "Achieng", "Otieno", "Njeri", "Mwangi", "Fatuma", "Omondi",
                    "Chebet", "Kiprono", "Akinyi", "Mutua", "Halima", "Baraka", "Zawadi", "Juma",
                    "Nafula", "Wekesa", "Amani", "Imani", "Grace", "David", "Sarah", "Peter"]
MOCK_LAST_NAMES = ["Kariuki", "Odhiambo", "Wambui", "Kiptoo", "Mohamed", "Nyambura", "Ochieng",
                   "Mutiso", "Cherono", "Njoroge", "Abdi", "Wafula", "Kilonzo", "Atieno", "Rotich"]


def _round100(value):
    return int(round(value / 100.0) * 100)


def _add_minutes(hhmm, minutes):
    start = datetime.strptime(hhmm, "%H:%M")
    return (start + timedelta(minutes=minutes)).strftime("%H:%M")


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #

def connect(db_path):
    """Open a connection in autocommit mode; transactions are started explicitly."""
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Return the connection for the current request, opening it if needed."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db):
    """Run a block of statements atomically.

    BEGIN IMMEDIATE takes the write lock straight away, so two customers cannot
    both read "1 seat left" and both book it.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


# --------------------------------------------------------------------------- #
# Initialisation and seeding
# --------------------------------------------------------------------------- #

def create_schema(db):
    db.executescript(SCHEMA)
    # Databases created before staff roles existed: add the column; existing accounts stay admins.
    columns = [r["name"] for r in db.execute("PRAGMA table_info(administrators)")]
    if "role" not in columns:
        db.execute("ALTER TABLE administrators ADD COLUMN role TEXT NOT NULL DEFAULT 'admin' "
                   "CHECK (role IN ('admin', 'agent'))")
    # ...and before customer accounts existed: bookings made then simply have no account.
    columns = [r["name"] for r in db.execute("PRAGMA table_info(bookings)")]
    if "customer_id" not in columns:
        db.execute("ALTER TABLE bookings ADD COLUMN customer_id INTEGER "
                   "REFERENCES customers(customer_id) ON DELETE SET NULL")


def seed_admin(db, username, password, role="admin"):
    exists = db.execute("SELECT 1 FROM administrators WHERE username = ?", (username,)).fetchone()
    if not exists:
        db.execute(
            "INSERT INTO administrators (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role),
        )


def seed_flights(db, start=None):
    """Insert sample flights for the next SCHEDULE_DAYS days (relative to today)."""
    start = start or date.today()
    number = 101
    for day in range(1, SCHEDULE_DAYS + 1):
        flight_date = (start + timedelta(days=day)).isoformat()
        for origin, dest, dep_time, minutes, economy, international in ROUTES:
            # International routes operate every other day.
            if international and day % 2 == 0:
                continue
            cur = db.execute(
                """INSERT INTO flights (flight_number, airline_name, departure_city, departure_airport,
                                        destination_city, destination_airport, departure_date,
                                        departure_time, arrival_time, aircraft)
                   VALUES (?, 'SkyLink Airlines', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"SL{number}", origin, AIRPORTS[origin], dest, AIRPORTS[dest], flight_date,
                    dep_time, _add_minutes(dep_time, minutes),
                    "Boeing 787-8 Dreamliner" if minutes > 200 else
                    ("Embraer E190" if international else "Bombardier Q400"),
                ),
            )
            flight_id = cur.lastrowid
            fares = [("Economy", economy, 120), ("Business", _round100(economy * 2.6), 16)]
            if international:
                fares.append(("First Class", _round100(economy * 4.2), 8))
            for travel_class, price, seats in fares:
                db.execute(
                    """INSERT INTO flight_fares (flight_id, travel_class, ticket_price, total_seats, available_seats)
                       VALUES (?, ?, ?, ?, ?)""",
                    (flight_id, travel_class, price, seats, seats),
                )
            number += 1


def _find_flight(db, origin, dest, day_offset):
    flight_date = (date.today() + timedelta(days=day_offset)).isoformat()
    return db.execute(
        "SELECT * FROM flights WHERE departure_city = ? AND destination_city = ? AND departure_date = ?",
        (origin, dest, flight_date),
    ).fetchone()


def seed_demo_bookings(db):
    """Sample passengers and bookings so the admin pages and My Bookings have data."""
    demo = [
        # reference, name, email, phone, id, (from, to, day), class, pax, status, payment
        ("SLDEMO01", "Jane Wanjiru", "jane.wanjiru@example.com", "+254 712 345 678", "28456123",
         ("Nairobi", "Mombasa", 5), "Economy", 2, "Confirmed", "Simulated M-Pesa"),
        ("SLDEMO02", "Brian Otieno", "brian.otieno@example.com", "+254 722 111 222", "A1234567",
         ("Nairobi", "Zanzibar", 7), "Business", 1, "Pending", "Pay at airline office"),
        ("SLDEMO03", "Amina Hassan", "amina.hassan@example.com", "+254 733 999 000", "31234987",
         ("Mombasa", "Nairobi", 6), "Economy", 3, "Cancelled", "Simulated M-Pesa"),
    ]
    for ref, name, email, phone, id_no, (origin, dest, day), travel_class, pax, status, payment in demo:
        flight = _find_flight(db, origin, dest, day)
        if flight is None:
            continue
        fare = db.execute(
            "SELECT * FROM flight_fares WHERE flight_id = ? AND travel_class = ?",
            (flight["flight_id"], travel_class),
        ).fetchone()
        passenger_id = db.execute(
            """INSERT INTO passengers (full_name, email, phone, identification_number)
               VALUES (?, ?, ?, ?)""",
            (name, email, phone, id_no),
        ).lastrowid
        db.execute(
            """INSERT INTO bookings (booking_reference, passenger_id, flight_id, number_of_passengers,
                                     travel_class, price_per_ticket, total_cost, payment_method,
                                     booking_status, cancelled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ref, passenger_id, flight["flight_id"], pax, travel_class, fare["ticket_price"],
             fare["ticket_price"] * pax, payment, status,
             datetime.now().isoformat(timespec="seconds") if status == "Cancelled" else None),
        )
        if status != "Cancelled":
            db.execute(
                "UPDATE flight_fares SET available_seats = available_seats - ? WHERE fare_id = ?",
                (pax, fare["fare_id"]),
            )

    # Demo customer account: Jane can sign in and see SLDEMO01 straight away.
    jane = db.execute(
        "INSERT INTO customers (full_name, email, phone, password_hash) VALUES (?, ?, ?, ?)",
        ("Jane Wanjiru", "jane.wanjiru@example.com", "+254 712 345 678", generate_password_hash("Jane@2026")),
    ).lastrowid
    db.execute("UPDATE bookings SET customer_id = ? WHERE booking_reference = 'SLDEMO01'", (jane,))

    # A nearly full flight for demonstrating the seat-capacity check:
    # Nairobi -> Kisumu, 3 days from today, has only 3 Economy seats left.
    nearly_full = _find_flight(db, "Nairobi", "Kisumu", 3)
    if nearly_full:
        db.execute(
            "UPDATE flight_fares SET available_seats = 3 WHERE flight_id = ? AND travel_class = 'Economy'",
            (nearly_full["flight_id"],),
        )

    db.execute(
        """INSERT INTO contact_messages (customer_name, email, subject, message)
           VALUES ('Peter Mwangi', 'peter.mwangi@example.com', 'Baggage allowance',
                   'Hello, what is the checked baggage allowance on Economy flights to Zanzibar?')"""
    )


def _insert_flight(db, number, origin, dest, dep_time, minutes, economy, international, flight_date,
                   total_seats=None):
    flight_id = db.execute(
        """INSERT INTO flights (flight_number, airline_name, departure_city, departure_airport,
                                destination_city, destination_airport, departure_date,
                                departure_time, arrival_time, aircraft)
           VALUES (?, 'SkyLink Airlines', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"SL{number}", origin, AIRPORTS[origin], dest, AIRPORTS[dest], flight_date,
         dep_time, _add_minutes(dep_time, minutes),
         "Boeing 787-8 Dreamliner" if minutes > 200 else
         ("Embraer E190" if international else "Bombardier Q400")),
    ).lastrowid
    fares = [("Economy", economy, 120), ("Business", _round100(economy * 2.6), 16)]
    if international:
        fares.append(("First Class", _round100(economy * 4.2), 8))
    for travel_class, price, seats in fares:
        db.execute(
            """INSERT INTO flight_fares (flight_id, travel_class, ticket_price, total_seats, available_seats)
               VALUES (?, ?, ?, ?, ?)""",
            (flight_id, travel_class, price, seats, seats),
        )
    return flight_id


def seed_mock_traffic(db, today=None):
    """Fictional bookings so seat counts and reports look like a working airline.

    * a year of past flights (one day a week) with completed and cancelled bookings
    * upcoming flights partly sold: fuller the sooner they depart
    Uses a fixed random seed, so every fresh database gets the same data.
    """
    rng = random.Random(2026)
    today = today or date.today()
    now = datetime.now()

    # A pool of repeat customers.
    pool = []
    for i in range(160):
        first, last = rng.choice(MOCK_FIRST_NAMES), rng.choice(MOCK_LAST_NAMES)
        email = f"{first}.{last}{i}@example.com".lower()
        pool.append(db.execute(
            "INSERT INTO passengers (full_name, email, phone, identification_number) VALUES (?, ?, ?, ?)",
            (f"{first} {last}", email, f"+254 7{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(100, 999)}",
             str(rng.randint(20000000, 39999999))),
        ).lastrowid)

    # Past flights: same timetable, one day a week, numbered from SL1001.
    number = 1001
    for week in range(HISTORY_WEEKS, 0, -1):
        flight_date = today - timedelta(weeks=week)
        for origin, dest, dep_time, minutes, economy, international in ROUTES:
            if international and week % 2:
                continue
            _insert_flight(db, number, origin, dest, dep_time, minutes, economy, international,
                           flight_date.isoformat())
            number += 1

    used_refs = {r[0] for r in db.execute("SELECT booking_reference FROM bookings")}
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    def new_ref():
        while True:
            ref = "SL" + "".join(rng.choice(alphabet) for _ in range(6))
            if ref not in used_refs:
                used_refs.add(ref)
                return ref

    # Fill every flight that has no bookings yet (leaves the hand-made demo flights alone).
    fares = db.execute(
        """SELECT fa.*, f.departure_date, f.departure_time, f.destination_city
           FROM flight_fares fa JOIN flights f ON f.flight_id = fa.flight_id
           WHERE fa.available_seats = fa.total_seats
             AND f.flight_id NOT IN (SELECT flight_id FROM bookings)
             AND f.flight_id NOT IN (SELECT flight_id FROM flight_fares WHERE available_seats < total_seats)"""
    ).fetchall()
    rows, seat_updates = [], []
    for fare in fares:
        dep_day = date.fromisoformat(fare["departure_date"])
        days_ahead = (dep_day - today).days
        demand = DESTINATION_DEMAND.get(fare["destination_city"], 0.5) * MONTH_DEMAND[dep_day.month]
        if days_ahead > 0:  # still on sale: fewer seats sold the further away it is
            demand *= max(0.2, 1 - days_ahead / 16)
        load = min(0.95, demand * rng.uniform(0.55, 0.95))
        if fare["travel_class"] != "Economy":
            load *= rng.uniform(0.4, 0.9)
        target, sold = int(fare["total_seats"] * load), 0
        while sold < target:
            pax = min(rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6]), target - sold)
            status = rng.choices(["Confirmed", "Cancelled", "Pending"],
                                 weights=[85, 10, 5 if days_ahead > 0 else 0])[0]
            booked_on = datetime.combine(dep_day, datetime.min.time()) - timedelta(
                days=rng.randint(1, 40), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
            booked_on = min(booked_on, now - timedelta(hours=rng.randint(1, 72)))
            discount = 10 if pax >= 6 else 5 if pax >= 4 else 0  # mirrors app.GROUP_DISCOUNTS
            rows.append((new_ref(), rng.choice(pool), fare["flight_id"], pax, fare["travel_class"],
                         fare["ticket_price"], round(fare["ticket_price"] * pax * (100 - discount) / 100),
                         rng.choice(["Simulated M-Pesa"] * 3 + ["Pay at airline office"]) if status != "Pending"
                         else "Pay at airline office", booked_on.isoformat(sep=" ", timespec="seconds"),
                         status, (booked_on + timedelta(days=1)).isoformat(timespec="seconds")
                         if status == "Cancelled" else None))
            if status != "Cancelled":
                sold += pax
        seat_updates.append((sold, fare["fare_id"]))
    db.executemany(
        """INSERT INTO bookings (booking_reference, passenger_id, flight_id, number_of_passengers,
                                 travel_class, price_per_ticket, total_cost, payment_method,
                                 booking_date, booking_status, cancelled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    db.executemany("UPDATE flight_fares SET available_seats = available_seats - ? WHERE fare_id = ?",
                   seat_updates)


def init_db(db_path=None, admin_username="admin", admin_password="SkyLink@2026", seed=True, mock_traffic=True):
    """Create the schema and, if the database is empty, load the sample data."""
    db_path = db_path or DEFAULT_DB_PATH
    db = connect(db_path)
    try:
        create_schema(db)
        with transaction(db):
            seed_admin(db, admin_username, admin_password)
            has_flights = db.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
            if seed and not has_flights:
                seed_flights(db)
                seed_demo_bookings(db)
                if mock_traffic:
                    seed_mock_traffic(db)
                seed_admin(db, "agent", "Agent@2026", role="agent")  # demo booking agent
    finally:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


if __name__ == "__main__":
    if "--reset" in sys.argv and os.path.exists(DEFAULT_DB_PATH):
        os.remove(DEFAULT_DB_PATH)
        print("Removed old database.")
    init_db(
        admin_username=os.environ.get("ADMIN_USERNAME", "admin"),
        admin_password=os.environ.get("ADMIN_PASSWORD", "SkyLink@2026"),
    )
    print(f"Database ready: {DEFAULT_DB_PATH}")

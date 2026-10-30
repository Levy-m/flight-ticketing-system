"""
SkyLink Airlines - Flight Ticketing System
Flask application: routes, validation, security and business rules.

Run:  python app.py      then open http://127.0.0.1:5000
"""

import hmac
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import database
from database import (AIRPORTS, BOOKING_STATUSES, TRAVEL_CLASSES, get_db,
                      transaction)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

MAX_PASSENGERS_PER_BOOKING = 9
CANCELLATION_CUTOFF_HOURS = 24
# Staff roles: admins manage everything; agents handle bookings and messages only.
STAFF_ROLES = {"admin": "Administrator", "agent": "Booking agent"}
# Group discounts as (minimum passengers, percent off), largest group first.
GROUP_DISCOUNTS = [(6, 10), (4, 5)]
PAYMENT_METHODS = {
    "office": ("Pay at airline office", "Pending"),
}
# Shown on the booking form but not selectable yet (awaiting the M-Pesa Daraja integration).
UNAVAILABLE_PAYMENT_METHODS = {"mpesa": "M-Pesa payments are not available yet. Please choose Pay at airline office."}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,98}[A-Za-z.]$")
PHONE_RE = re.compile(r"^\+?[0-9 ]{9,16}$")
ID_RE = re.compile(r"^[A-Za-z0-9]{5,20}$")
FLIGHT_NO_RE = re.compile(r"^[A-Z]{2}[0-9]{2,4}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,10}$")
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I confusion

FEATURED_DESTINATIONS = [
    ("Mombasa", "mombasa.svg", "White-sand beaches, Fort Jesus and Swahili cuisine on the Indian Ocean coast."),
    ("Zanzibar", "zanzibar.svg", "Stone Town's winding alleys, spice farms and turquoise lagoons."),
    ("Kigali", "kigali.svg", "Africa's cleanest capital and the gateway to Rwanda's mountain gorillas."),
    ("Kisumu", "kisumu.svg", "Lakeside sunsets on the shores of Lake Victoria."),
    ("Lamu", "lamu.svg", "A UNESCO World Heritage island where dhows still rule the harbour."),
    ("Dubai", "dubai.svg", "Skyscrapers, desert safaris and world-class shopping."),
]


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #

def _load_secret_key(instance_path):
    """Use SECRET_KEY from the environment, otherwise a random key kept in instance/.

    The key is never written into the source code.
    """
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    os.makedirs(instance_path, exist_ok=True)
    key_file = os.path.join(instance_path, "secret_key")
    if not os.path.exists(key_file):
        with open(key_file, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(key_file) as fh:
        return fh.read().strip()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        DATABASE=os.environ.get("DATABASE_PATH", database.DEFAULT_DB_PATH),
        ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", "SkyLink@2026"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app.instance_path)

    database.init_app(app)
    if not app.config.get("SKIP_DB_INIT"):
        database.init_db(app.config["DATABASE"], app.config["ADMIN_USERNAME"],
                         app.config["ADMIN_PASSWORD"], mock_traffic=app.config.get("SEED_MOCK_TRAFFIC", True))

    register_template_helpers(app)
    register_security(app)
    register_public_routes(app)
    register_admin_routes(app)
    register_error_handlers(app)

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables and sample data."""
        database.init_db(app.config["DATABASE"], app.config["ADMIN_USERNAME"],
                         app.config["ADMIN_PASSWORD"])
        print("Database initialised.")

    return app


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def today():
    return date.today()


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_time(value):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        return None


def parse_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def departure_datetime(flight):
    return datetime.strptime(f"{flight['departure_date']} {flight['departure_time']}", "%Y-%m-%d %H:%M")


def has_departed(flight):
    return departure_datetime(flight) <= datetime.now()


def group_discount_percent(passengers):
    return next((pct for min_pax, pct in GROUP_DISCOUNTS if passengers >= min_pax), 0)


def booking_total(price, passengers):
    """Total cost after any group discount, rounded to the nearest shilling."""
    return round(price * passengers * (100 - group_discount_percent(passengers)) / 100)


def can_cancel(booking):
    """Cancellation rule: active bookings may be cancelled up to 24 hours before departure."""
    if booking["booking_status"] == "Cancelled":
        return False
    return departure_datetime(booking) - datetime.now() >= timedelta(hours=CANCELLATION_CUTOFF_HOURS)


def generate_booking_reference(db):
    while True:
        ref = "SL" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not db.execute("SELECT 1 FROM bookings WHERE booking_reference = ?", (ref,)).fetchone():
            return ref


def all_cities():
    rows = get_db().execute(
        "SELECT departure_city AS city FROM flights UNION SELECT destination_city FROM flights ORDER BY city"
    ).fetchall()
    return [r["city"] for r in rows] or sorted(AIRPORTS)


BOOKING_SELECT = """
    SELECT b.*, p.full_name, p.email, p.phone, p.identification_number,
           f.flight_number, f.airline_name, f.departure_city, f.departure_airport,
           f.destination_city, f.destination_airport, f.departure_date,
           f.departure_time, f.arrival_time, f.aircraft
    FROM bookings b
    JOIN passengers p ON p.passenger_id = b.passenger_id
    JOIN flights f ON f.flight_id = b.flight_id
"""


def get_booking_by_reference(reference):
    return get_db().execute(BOOKING_SELECT + " WHERE b.booking_reference = ?", (reference,)).fetchone()


def get_my_bookings():
    """Bookings on the signed-in customer's account, newest first."""
    return get_db().execute(BOOKING_SELECT + " WHERE b.customer_id = ? ORDER BY b.booking_date DESC",
                            (session.get("customer_id"),)).fetchall()


def owns_booking(booking):
    return session.get("customer_id") is not None and booking["customer_id"] == session["customer_id"]


def safe_next(default):
    """Where to go after signing in: only local paths, never another site."""
    target = request.args.get("next", "")
    return target if target.startswith("/") and not target.startswith("//") else default


def start_customer_session(customer):
    # New session on sign-in (prevents session fixation), keeping only the CSRF token.
    token = session.get("_csrf_token")
    session.clear()
    if token:
        session["_csrf_token"] = token
    session.permanent = True
    session["customer_id"] = customer["customer_id"]
    session["customer_name"] = customer["full_name"]


def cancel_booking(db, booking):
    """Mark a booking Cancelled and release its seats - never deletes the row."""
    with transaction(db):
        cur = db.execute(
            """UPDATE bookings SET booking_status = 'Cancelled', cancelled_at = ?
               WHERE booking_id = ? AND booking_status != 'Cancelled'""",
            (datetime.now().isoformat(timespec="seconds"), booking["booking_id"]),
        )
        if cur.rowcount == 1:
            db.execute(
                """UPDATE flight_fares
                   SET available_seats = MIN(total_seats, available_seats + ?)
                   WHERE flight_id = ? AND travel_class = ?""",
                (booking["number_of_passengers"], booking["flight_id"], booking["travel_class"]),
            )
        return cur.rowcount == 1


# --------------------------------------------------------------------------- #
# Template filters / globals
# --------------------------------------------------------------------------- #

def register_template_helpers(app):
    @app.template_filter("ksh")
    def ksh(amount):
        return f"KSh {amount:,.0f}" if amount is not None else "-"

    @app.template_filter("time12")
    def time12(hhmm):
        t = parse_time(hhmm)
        return t.strftime("%I:%M %p") if t else hhmm

    @app.template_filter("nicedate")
    def nicedate(value, fmt="%a, %d %b %Y"):
        d = parse_date(str(value)[:10])
        return d.strftime(fmt) if d else value

    @app.template_filter("code")
    def airport_code(airport_name):
        m = re.search(r"\(([A-Z]{3})\)", airport_name or "")
        return m.group(1) if m else (airport_name or "")[:3].upper()

    @app.template_filter("duration")
    def duration(flight):
        dep, arr = parse_time(flight["departure_time"]), parse_time(flight["arrival_time"])
        if not dep or not arr:
            return ""
        mins = (datetime.combine(date.today(), arr) - datetime.combine(date.today(), dep)).seconds // 60
        return f"{mins // 60}h {mins % 60:02d}m"

    @app.context_processor
    def inject_globals():
        return {
            "current_year": date.today().year,
            "today_iso": date.today().isoformat(),
            "travel_classes": TRAVEL_CLASSES,
            "is_admin": bool(session.get("admin_id")),
            "staff_role": g.get("staff_role"),
            "customer_name": session.get("customer_name"),
            "group_discounts": GROUP_DISCOUNTS,
            "staff_roles": STAFF_ROLES,
        }


# --------------------------------------------------------------------------- #
# Security: CSRF protection and admin authentication
# --------------------------------------------------------------------------- #

def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def register_security(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def csrf_protect():
        # Every state-changing request must carry the token stored in the session.
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not sent or not expected or not hmac.compare_digest(sent, expected):
                abort(400, description="Your form session has expired. Please reload the page and try again.")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


def admin_required(view):
    """Any signed-in staff member (admin or agent)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        # The role is re-read on every request, so a changed role or a removed
        # account takes effect immediately rather than at next login.
        staff = None
        if session.get("admin_id"):
            staff = get_db().execute("SELECT admin_id, username, role FROM administrators WHERE admin_id = ?",
                                     (session["admin_id"],)).fetchone()
        if staff is None:
            session.pop("admin_id", None)
            session.pop("admin_username", None)
            flash("Please log in as an administrator to continue.", "error")
            return redirect(url_for("admin_login", next=request.path))
        g.admin_username = staff["username"]
        g.staff_role = staff["role"]
        return view(*args, **kwargs)
    return wrapped


def customer_required(view):
    """Booking and viewing bookings hold personal data, so customers must sign in."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("customer_id"):
            flash("Please sign in or create an account to continue.", "error")
            back = request.full_path if request.method == "GET" else url_for("my_bookings")
            return redirect(url_for("customer_login", next=back.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def role_required(role):
    """Restrict a view to one staff role. Apply below @admin_required."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.get("staff_role") != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# --------------------------------------------------------------------------- #
# Public routes
# --------------------------------------------------------------------------- #

def validate_search(args):
    """Validate the flight search form. Returns (criteria, errors)."""
    errors = []
    origin = args.get("from", "").strip()
    dest = args.get("to", "").strip()
    dep_date = parse_date(args.get("departure_date", ""))
    ret_raw = args.get("return_date", "").strip()
    ret_date = parse_date(ret_raw) if ret_raw else None
    passengers = parse_int(args.get("passengers", "1"))
    travel_class = args.get("travel_class", "Economy")

    if not origin:
        errors.append("Please choose a departure city or airport.")
    if not dest:
        errors.append("Please choose a destination city or airport.")
    if origin and dest and origin.lower() == dest.lower():
        errors.append("Departure and destination cannot be the same.")
    if dep_date is None:
        errors.append("Please choose a valid departure date.")
    elif dep_date < today():
        errors.append("The departure date cannot be in the past.")
    if ret_raw and ret_date is None:
        errors.append("The return date is not a valid date.")
    elif ret_date and dep_date and ret_date < dep_date:
        errors.append("The return date cannot be before the departure date.")
    if passengers is None or not 1 <= passengers <= MAX_PASSENGERS_PER_BOOKING:
        errors.append(f"Number of passengers must be between 1 and {MAX_PASSENGERS_PER_BOOKING}.")
    if travel_class not in TRAVEL_CLASSES:
        errors.append("Please choose a valid travel class.")

    criteria = {"from": origin, "to": dest, "departure_date": dep_date, "return_date": ret_date,
                "passengers": passengers or 1, "travel_class": travel_class}
    return criteria, errors


def find_flights(origin, dest, on_date, travel_class, date_to=None):
    """Flights on a route for a class. Matches city name or airport name/code."""
    sql = """
        SELECT f.*, fa.fare_id, fa.travel_class, fa.ticket_price, fa.available_seats, fa.total_seats
        FROM flights f
        JOIN flight_fares fa ON fa.flight_id = f.flight_id
        WHERE (lower(f.departure_city) = lower(:o) OR lower(f.departure_airport) LIKE '%' || lower(:o) || '%')
          AND (lower(f.destination_city) = lower(:d) OR lower(f.destination_airport) LIKE '%' || lower(:d) || '%')
          AND fa.travel_class = :c
          AND f.departure_date BETWEEN :start AND :end
        ORDER BY f.departure_date, f.departure_time
    """
    rows = get_db().execute(sql, {
        "o": origin, "d": dest, "c": travel_class,
        "start": on_date.isoformat(), "end": (date_to or on_date).isoformat(),
    }).fetchall()
    return [r for r in rows if not has_departed(r)]


def register_public_routes(app):

    @app.route("/")
    def index():
        db = get_db()
        destinations = []
        for city, image, blurb in FEATURED_DESTINATIONS:
            row = db.execute(
                """SELECT MIN(fa.ticket_price) AS price FROM flights f
                   JOIN flight_fares fa ON fa.flight_id = f.flight_id
                   WHERE f.destination_city = ? AND fa.travel_class = 'Economy'
                     AND f.departure_date > ? AND fa.available_seats > 0""",
                (city, today().isoformat()),
            ).fetchone()
            destinations.append({"city": city, "image": image, "blurb": blurb, "price": row["price"]})

        # Promotional offers: the cheapest upcoming Economy fare on each route (10% web discount shown).
        deals = db.execute(
            """SELECT f.flight_id, f.departure_city, f.destination_city, MIN(fa.ticket_price) AS price,
                      MIN(f.departure_date) AS first_date
               FROM flights f JOIN flight_fares fa ON fa.flight_id = f.flight_id
               WHERE fa.travel_class = 'Economy' AND f.departure_date > ? AND fa.available_seats > 0
               GROUP BY f.departure_city, f.destination_city
               ORDER BY price LIMIT 4""",
            (today().isoformat(),),
        ).fetchall()
        return render_template("index.html", cities=all_cities(), destinations=destinations, deals=deals)

    @app.route("/search")
    def search_flights():
        searched = "from" in request.args
        criteria, errors = validate_search(request.args) if searched else ({}, [])
        outbound, inbound, alternatives = [], [], []
        if searched and not errors:
            outbound = find_flights(criteria["from"], criteria["to"], criteria["departure_date"],
                                    criteria["travel_class"])
            if criteria["return_date"]:
                inbound = find_flights(criteria["to"], criteria["from"], criteria["return_date"],
                                       criteria["travel_class"])
            if not outbound:
                # Suggest the same route on nearby dates.
                start = max(today(), criteria["departure_date"] - timedelta(days=3))
                alternatives = find_flights(criteria["from"], criteria["to"], start,
                                            criteria["travel_class"],
                                            date_to=criteria["departure_date"] + timedelta(days=7))
        for message in errors:
            flash(message, "error")
        return render_template("search_flights.html", cities=all_cities(), searched=searched,
                               valid=searched and not errors, criteria=criteria, args=request.args,
                               outbound=outbound, inbound=inbound, alternatives=alternatives[:6])

    @app.route("/flights/<int:flight_id>")
    def flight_details(flight_id):
        db = get_db()
        flight = db.execute("SELECT * FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
        if flight is None:
            abort(404)
        fares = db.execute(
            "SELECT * FROM flight_fares WHERE flight_id = ? ORDER BY ticket_price", (flight_id,)
        ).fetchall()
        return render_template("flight_details.html", flight=flight, fares=fares,
                               departed=has_departed(flight),
                               passengers=parse_int(request.args.get("passengers")) or 1)

    @app.route("/book")
    def book_flight():
        """'Book a Flight' menu item: browse upcoming flights and pick one."""
        dest = request.args.get("to", "").strip()
        sql = """SELECT f.*, MIN(fa.ticket_price) AS from_price, SUM(fa.available_seats) AS seats_left
                 FROM flights f JOIN flight_fares fa ON fa.flight_id = f.flight_id
                 WHERE f.departure_date >= ?"""
        params = [today().isoformat()]
        if dest:
            sql += " AND f.destination_city = ?"
            params.append(dest)
        sql += " GROUP BY f.flight_id ORDER BY f.departure_date, f.departure_time LIMIT 60"
        flights = [f for f in get_db().execute(sql, params).fetchall() if not has_departed(f)]
        return render_template("book_flight.html", flights=flights, cities=all_cities(), dest=dest)

    @app.route("/book/<int:flight_id>", methods=["GET", "POST"])
    @customer_required
    def booking(flight_id):
        db = get_db()
        flight = db.execute("SELECT * FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
        if flight is None:
            flash("The selected flight does not exist.", "error")
            return redirect(url_for("search_flights"))
        fares = {f["travel_class"]: f for f in db.execute(
            "SELECT * FROM flight_fares WHERE flight_id = ? ORDER BY ticket_price", (flight_id,)).fetchall()}

        source = request.form if request.method == "POST" else request.args
        form = {
            "full_name": source.get("full_name", "").strip(),
            "email": source.get("email", "").strip(),
            "phone": source.get("phone", "").strip(),
            "identification_number": source.get("identification_number", "").strip().upper(),
            "travel_class": source.get("travel_class", next(iter(fares), "Economy")),
            "passengers": source.get("passengers", "1"),
            "payment": source.get("payment", "office"),
            "terms": source.get("terms"),
        }
        if request.method == "GET" and session.get("customer_id"):
            customer = db.execute("SELECT * FROM customers WHERE customer_id = ?",
                                  (session["customer_id"],)).fetchone()
            if customer:
                for key in ("full_name", "email", "phone"):
                    form[key] = form[key] or customer[key]
        errors = {}

        if has_departed(flight):
            flash("This flight has already departed and can no longer be booked.", "error")
            return redirect(url_for("search_flights"))

        if request.method == "POST":
            # ---- server-side validation ----
            if not form["full_name"]:
                errors["full_name"] = "Full name is required."
            elif not NAME_RE.match(form["full_name"]):
                errors["full_name"] = "Enter a valid name using letters only (3-100 characters)."
            if not form["email"]:
                errors["email"] = "Email address is required."
            elif not EMAIL_RE.match(form["email"]) or len(form["email"]) > 120:
                errors["email"] = "Enter a valid email address, e.g. name@example.com."
            if not form["phone"]:
                errors["phone"] = "Phone number is required."
            elif not PHONE_RE.match(form["phone"]):
                errors["phone"] = "Enter a valid phone number, e.g. +254 712 345 678."
            if not form["identification_number"]:
                errors["identification_number"] = "ID or passport number is required."
            elif not ID_RE.match(form["identification_number"]):
                errors["identification_number"] = "ID/passport number must be 5-20 letters or digits."
            fare = fares.get(form["travel_class"])
            if fare is None:
                errors["travel_class"] = "The selected travel class is not offered on this flight."
            passengers = parse_int(form["passengers"])
            if passengers is None or passengers < 1:
                errors["passengers"] = "Number of passengers must be a positive whole number."
            elif passengers > MAX_PASSENGERS_PER_BOOKING:
                errors["passengers"] = f"A single booking can have at most {MAX_PASSENGERS_PER_BOOKING} passengers."
            elif fare is not None and passengers > fare["available_seats"]:
                errors["passengers"] = (f"Only {fare['available_seats']} {fare['travel_class']} seat(s) "
                                        "are available on this flight.")
            if form["payment"] in UNAVAILABLE_PAYMENT_METHODS:
                errors["payment"] = UNAVAILABLE_PAYMENT_METHODS[form["payment"]]
            elif form["payment"] not in PAYMENT_METHODS:
                errors["payment"] = "Please choose a payment option."
            if not form["terms"]:
                errors["terms"] = "You must accept the terms and conditions."

            if errors:
                flash("Please correct the errors highlighted below.", "error")
            else:
                payment_label, status = PAYMENT_METHODS[form["payment"]]
                try:
                    with transaction(db):
                        # Re-check and reserve seats atomically: the UPDATE only succeeds
                        # if enough seats are still available at this exact moment.
                        cur = db.execute(
                            """UPDATE flight_fares SET available_seats = available_seats - ?
                               WHERE fare_id = ? AND available_seats >= ?""",
                            (passengers, fare["fare_id"], passengers),
                        )
                        if cur.rowcount != 1:
                            raise ValueError("Not enough seats")
                        price = db.execute("SELECT ticket_price FROM flight_fares WHERE fare_id = ?",
                                           (fare["fare_id"],)).fetchone()["ticket_price"]
                        existing = db.execute(
                            """SELECT passenger_id FROM passengers
                               WHERE lower(email) = lower(?) AND identification_number = ?""",
                            (form["email"], form["identification_number"]),
                        ).fetchone()
                        if existing:
                            passenger_id = existing["passenger_id"]
                            db.execute("UPDATE passengers SET full_name = ?, phone = ? WHERE passenger_id = ?",
                                       (form["full_name"], form["phone"], passenger_id))
                        else:
                            passenger_id = db.execute(
                                """INSERT INTO passengers (full_name, email, phone, identification_number)
                                   VALUES (?, ?, ?, ?)""",
                                (form["full_name"], form["email"].lower(), form["phone"],
                                 form["identification_number"]),
                            ).lastrowid
                        reference = generate_booking_reference(db)
                        db.execute(
                            """INSERT INTO bookings (booking_reference, passenger_id, flight_id,
                                   number_of_passengers, travel_class, price_per_ticket, total_cost,
                                   payment_method, booking_status, booking_date, customer_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (reference, passenger_id, flight_id, passengers, fare["travel_class"], price,
                             booking_total(price, passengers), payment_label, status,
                             datetime.now().isoformat(sep=" ", timespec="seconds"), session.get("customer_id")),
                        )
                except ValueError:
                    flash("Sorry, those seats were just taken. Please choose fewer passengers or another flight.",
                          "error")
                    return redirect(url_for("booking", flight_id=flight_id,
                                            travel_class=form["travel_class"]))
                flash(f"Booking successful! Your booking reference is {reference}.", "success")
                return redirect(url_for("booking_confirmation", reference=reference))

        return render_template("booking.html", flight=flight, fares=fares, form=form, errors=errors,
                               max_passengers=MAX_PASSENGERS_PER_BOOKING)

    @app.route("/booking/<reference>")
    @customer_required
    def booking_confirmation(reference):
        booking = get_booking_by_reference(reference.upper())
        # Same response whether the booking does not exist or belongs to someone else.
        if booking is None or not owns_booking(booking):
            abort(404)
        return render_template("booking_confirmation.html", booking=booking, cancellable=can_cancel(booking),
                               cutoff=CANCELLATION_CUTOFF_HOURS)

    @app.route("/my-bookings", methods=["GET", "POST"])
    @customer_required
    def my_bookings():
        reference = email = ""
        if request.method == "POST":
            # "Add an existing booking": for bookings made without an account (e.g. at our office).
            reference = request.form.get("reference", "").strip().upper()
            email = request.form.get("email", "").strip()
            found = get_booking_by_reference(reference) if REFERENCE_RE.match(reference) else None
            # Same generic message whatever is wrong, so the form does not reveal which bookings exist.
            if (found is None or not EMAIL_RE.match(email)
                    or not hmac.compare_digest(found["email"].lower(), email.lower())
                    or found["customer_id"] not in (None, session["customer_id"])):
                flash("No booking matches those details. Please check and try again.", "error")
            else:
                get_db().execute("UPDATE bookings SET customer_id = ? WHERE booking_id = ?",
                                 (session["customer_id"], found["booking_id"]))
                flash(f"Booking {reference} is now on your account.", "success")
                return redirect(url_for("booking_confirmation", reference=reference))
        return render_template("my_bookings.html", my_bookings=get_my_bookings(), reference=reference,
                               email=email, cutoff=CANCELLATION_CUTOFF_HOURS)

    @app.route("/my-bookings/cancel", methods=["POST"])
    @customer_required
    def cancel_my_booking():
        reference = request.form.get("reference", "").strip().upper()
        booking = get_booking_by_reference(reference) if reference else None
        if booking is None or not owns_booking(booking):
            abort(404)
        if not can_cancel(booking):
            if booking["booking_status"] == "Cancelled":
                flash("This booking has already been cancelled.", "error")
            else:
                flash(f"Bookings can only be cancelled at least {CANCELLATION_CUTOFF_HOURS} hours "
                      "before departure. Please contact our office.", "error")
        elif cancel_booking(get_db(), booking):
            flash(f"Booking {reference} has been cancelled and the seats have been released.", "success")
        return redirect(url_for("booking_confirmation", reference=reference))

    @app.route("/account/register", methods=["GET", "POST"])
    def customer_register():
        if session.get("customer_id"):
            return redirect(safe_next(url_for("my_bookings")))
        form = {k: request.form.get(k, "").strip() for k in ("full_name", "email", "phone")}
        errors = {}
        if request.method == "POST":
            password = request.form.get("password", "")
            if not NAME_RE.match(form["full_name"]):
                errors["full_name"] = "Enter a valid name using letters only (3-100 characters)."
            if not EMAIL_RE.match(form["email"]) or len(form["email"]) > 120:
                errors["email"] = "Enter a valid email address, e.g. name@example.com."
            if not PHONE_RE.match(form["phone"]):
                errors["phone"] = "Enter a valid phone number, e.g. +254 712 345 678."
            if len(password) < 8:
                errors["password"] = "Password must be at least 8 characters."
            elif password != request.form.get("confirm_password", ""):
                errors["confirm_password"] = "Passwords do not match."
            if not errors:
                db = get_db()
                try:
                    customer_id = db.execute(
                        "INSERT INTO customers (full_name, email, phone, password_hash) VALUES (?, ?, ?, ?)",
                        (form["full_name"], form["email"].lower(), form["phone"], generate_password_hash(password)),
                    ).lastrowid
                except sqlite3.IntegrityError:
                    errors["email"] = "An account with this email already exists. Please sign in."
                else:
                    start_customer_session({"customer_id": customer_id, "full_name": form["full_name"]})
                    flash(f"Welcome, {form['full_name']}! Your account has been created.", "success")
                    return redirect(safe_next(url_for("my_bookings")))
            flash("Please correct the errors highlighted below.", "error")
        return render_template("account/register.html", form=form, errors=errors)

    @app.route("/account/login", methods=["GET", "POST"])
    def customer_login():
        if session.get("customer_id"):
            return redirect(safe_next(url_for("my_bookings")))
        email = request.form.get("email", "").strip()
        if request.method == "POST":
            customer = get_db().execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
            if customer and check_password_hash(customer["password_hash"], request.form.get("password", "")):
                start_customer_session(customer)
                flash(f"Welcome back, {customer['full_name']}.", "success")
                return redirect(safe_next(url_for("my_bookings")))
            flash("Invalid email or password.", "error")
        return render_template("account/login.html", email=email)

    @app.route("/account/logout", methods=["POST"])
    def customer_logout():
        for key in ("customer_id", "customer_name"):
            session.pop(key, None)
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.route("/about")
    def about():
        return render_template("about.html", airports=AIRPORTS)

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        form = {k: request.form.get(k, "").strip() for k in ("customer_name", "email", "subject", "message")}
        errors = {}
        if request.method == "POST":
            if not form["customer_name"]:
                errors["customer_name"] = "Your name is required."
            elif len(form["customer_name"]) > 100:
                errors["customer_name"] = "Name must be 100 characters or fewer."
            if not form["email"]:
                errors["email"] = "Email address is required."
            elif not EMAIL_RE.match(form["email"]):
                errors["email"] = "Enter a valid email address."
            if not form["subject"]:
                errors["subject"] = "Subject is required."
            elif len(form["subject"]) > 150:
                errors["subject"] = "Subject must be 150 characters or fewer."
            if len(form["message"]) < 10:
                errors["message"] = "Message must be at least 10 characters."
            elif len(form["message"]) > 2000:
                errors["message"] = "Message must be 2000 characters or fewer."
            if not errors:
                get_db().execute(
                    """INSERT INTO contact_messages (customer_name, email, subject, message, submission_date)
                       VALUES (?, ?, ?, ?, ?)""",
                    (form["customer_name"], form["email"], form["subject"], form["message"],
                     datetime.now().isoformat(sep=" ", timespec="seconds")),
                )
                flash("Thank you! Your message has been received. Our team will reply within 24 hours.",
                      "success")
                return redirect(url_for("contact"))
            flash("Please correct the errors highlighted below.", "error")
        return render_template("contact.html", form=form, errors=errors)


# --------------------------------------------------------------------------- #
# Administrator routes
# --------------------------------------------------------------------------- #

def read_flight_form(form, existing_fares=None, booked_by_class=None, is_new=True, original_date=None):
    """Validate the add/edit flight form. Returns (data, fares, errors)."""
    existing_fares = existing_fares or {}
    booked_by_class = booked_by_class or {}
    data = {
        "flight_number": form.get("flight_number", "").strip().upper(),
        "airline_name": form.get("airline_name", "").strip() or "SkyLink Airlines",
        "departure_city": form.get("departure_city", "").strip(),
        "departure_airport": form.get("departure_airport", "").strip(),
        "destination_city": form.get("destination_city", "").strip(),
        "destination_airport": form.get("destination_airport", "").strip(),
        "departure_date": form.get("departure_date", "").strip(),
        "departure_time": form.get("departure_time", "").strip(),
        "arrival_time": form.get("arrival_time", "").strip(),
        "aircraft": form.get("aircraft", "").strip(),
    }
    errors = {}
    if not FLIGHT_NO_RE.match(data["flight_number"]):
        errors["flight_number"] = "Flight number must be 2 letters followed by 2-4 digits, e.g. SL101."
    for field, label in [("departure_city", "Departure city"), ("departure_airport", "Departure airport"),
                         ("destination_city", "Destination city"), ("destination_airport", "Destination airport")]:
        if not data[field]:
            errors[field] = f"{label} is required."
        elif len(data[field]) > 100:
            errors[field] = f"{label} must be 100 characters or fewer."
    if (data["departure_city"] and data["destination_city"]
            and data["departure_city"].lower() == data["destination_city"].lower()):
        errors["destination_city"] = "Destination must be different from the departure city."
    dep_date = parse_date(data["departure_date"])
    if dep_date is None:
        errors["departure_date"] = "Enter a valid departure date."
    elif dep_date < today() and (is_new or data["departure_date"] != original_date):
        errors["departure_date"] = "The departure date cannot be in the past."
    dep_t, arr_t = parse_time(data["departure_time"]), parse_time(data["arrival_time"])
    if dep_t is None:
        errors["departure_time"] = "Enter a valid departure time."
    if arr_t is None:
        errors["arrival_time"] = "Enter a valid arrival time."
    elif dep_t and arr_t <= dep_t:
        errors["arrival_time"] = "Arrival time must be after the departure time."

    fares = {}
    for travel_class in TRAVEL_CLASSES:
        key = travel_class.split()[0].lower()  # economy / business / first
        price_raw = form.get(f"price_{key}", "").strip()
        total_raw = form.get(f"total_{key}", "").strip()
        avail_raw = form.get(f"available_{key}", "").strip()
        booked = booked_by_class.get(travel_class, 0)
        if not price_raw and not total_raw:
            if booked:
                errors[f"price_{key}"] = f"{travel_class} has active bookings and cannot be removed."
            continue
        price, total = parse_int(price_raw), parse_int(total_raw)
        available = parse_int(avail_raw) if avail_raw else (
            total - booked if total is not None else None)
        if price is None or price <= 0:
            errors[f"price_{key}"] = f"{travel_class} price must be a positive whole number."
        if total is None or total < 1:
            errors[f"total_{key}"] = f"{travel_class} total seats must be at least 1."
        elif available is None or available < 0:
            errors[f"available_{key}"] = "Available seats must be zero or more."
        elif available > total - booked:
            errors[f"available_{key}"] = (f"Available seats cannot exceed {total - booked} "
                                          f"(total {total} minus {booked} already booked).")
        fares[travel_class] = {"ticket_price": price, "total_seats": total, "available_seats": available}
    if not fares and not any(k.startswith("price_") for k in errors):
        errors["price_economy"] = "Offer at least one travel class (enter a price and seat count)."
    return data, fares, errors


def register_admin_routes(app):

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_id"):
            return redirect(url_for("admin_dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = get_db().execute("SELECT * FROM administrators WHERE username = ?", (username,)).fetchone()
            if admin and check_password_hash(admin["password_hash"], password):
                next_url = request.args.get("next", "")
                token = session.get("_csrf_token")
                session.clear()  # prevent session fixation
                session["_csrf_token"] = token or secrets.token_urlsafe(32)
                session.permanent = True
                session["admin_id"] = admin["admin_id"]
                session["admin_username"] = admin["username"]
                flash(f"Welcome back, {admin['username']}.", "success")
                # Only allow redirects to local admin pages.
                if next_url.startswith("/admin") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect(url_for("admin_dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("admin/login.html")

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.pop("admin_id", None)
        session.pop("admin_username", None)
        flash("You have been logged out.", "success")
        return redirect(url_for("index"))

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        t = today().isoformat()
        stats = {
            "flights": db.execute("SELECT COUNT(*) FROM flights WHERE departure_date >= ?", (t,)).fetchone()[0],
            "bookings": db.execute("SELECT COUNT(*) FROM bookings WHERE booking_status != 'Cancelled'").fetchone()[0],
            "pending": db.execute("SELECT COUNT(*) FROM bookings WHERE booking_status = 'Pending'").fetchone()[0],
            "revenue": db.execute(
                "SELECT COALESCE(SUM(total_cost), 0) FROM bookings WHERE booking_status = 'Confirmed'").fetchone()[0],
            "messages": db.execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0],
        }
        schedule = [f for f in db.execute(
            """SELECT f.*, SUM(fa.total_seats) AS total_seats, SUM(fa.available_seats) AS available_seats
               FROM flights f JOIN flight_fares fa ON fa.flight_id = f.flight_id
               WHERE f.departure_date BETWEEN ? AND ?
               GROUP BY f.flight_id ORDER BY f.departure_date, f.departure_time""",
            (t, (today() + timedelta(days=2)).isoformat()),
        ).fetchall() if not has_departed(f)][:12]
        recent = db.execute(BOOKING_SELECT + " ORDER BY b.booking_date DESC LIMIT 5").fetchall()
        messages = db.execute("SELECT * FROM contact_messages ORDER BY submission_date DESC LIMIT 5").fetchall()
        return render_template("admin/dashboard.html", stats=stats, schedule=schedule, recent=recent,
                               messages=messages)

    @app.route("/admin/flights")
    @admin_required
    def admin_flights():
        q = request.args.get("q", "").strip()
        on_date = request.args.get("date", "").strip()
        show = request.args.get("show", "upcoming")
        page = max(parse_int(request.args.get("page")) or 1, 1)
        per_page = 20
        where, params = [], []
        if q:
            where.append("(f.flight_number LIKE ? OR f.departure_city LIKE ? OR f.destination_city LIKE ?)")
            params += [f"%{q}%"] * 3
        if parse_date(on_date):
            where.append("f.departure_date = ?")
            params.append(on_date)
        if show == "upcoming":
            where.append("f.departure_date >= ?")
            params.append(today().isoformat())
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        db = get_db()
        total = db.execute(f"SELECT COUNT(*) FROM flights f {clause}", params).fetchone()[0]
        flights = db.execute(
            f"""SELECT f.*,
                   (SELECT COUNT(*) FROM bookings b WHERE b.flight_id = f.flight_id) AS booking_count
                FROM flights f {clause}
                ORDER BY f.departure_date, f.departure_time LIMIT ? OFFSET ?""",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        fares = {}
        if flights:
            ids = [f["flight_id"] for f in flights]
            marks = ",".join("?" * len(ids))
            for fare in db.execute(f"SELECT * FROM flight_fares WHERE flight_id IN ({marks})", ids):
                fares.setdefault(fare["flight_id"], {})[fare["travel_class"]] = fare
        pages = max((total + per_page - 1) // per_page, 1)
        return render_template("admin/flights.html", flights=flights, fares=fares, q=q, on_date=on_date,
                               show=show, page=page, pages=pages, total=total)

    @app.route("/admin/flights/add", methods=["GET", "POST"])
    @admin_required
    @role_required("admin")
    def admin_add_flight():
        form, errors = request.form, {}
        if request.method == "POST":
            data, fares, errors = read_flight_form(request.form)
            db = get_db()
            if not errors and db.execute("SELECT 1 FROM flights WHERE flight_number = ?",
                                         (data["flight_number"],)).fetchone():
                errors["flight_number"] = "This flight number already exists. Flight numbers must be unique."
            if not errors:
                with transaction(db):
                    flight_id = db.execute(
                        """INSERT INTO flights (flight_number, airline_name, departure_city, departure_airport,
                               destination_city, destination_airport, departure_date, departure_time,
                               arrival_time, aircraft)
                           VALUES (:flight_number, :airline_name, :departure_city, :departure_airport,
                               :destination_city, :destination_airport, :departure_date, :departure_time,
                               :arrival_time, :aircraft)""", data).lastrowid
                    for travel_class, fare in fares.items():
                        db.execute(
                            """INSERT INTO flight_fares (flight_id, travel_class, ticket_price, total_seats,
                                   available_seats) VALUES (?, ?, ?, ?, ?)""",
                            (flight_id, travel_class, fare["ticket_price"], fare["total_seats"],
                             fare["available_seats"]))
                flash(f"Flight {data['flight_number']} has been added.", "success")
                return redirect(url_for("admin_flights"))
            flash("Please correct the errors highlighted below.", "error")
        return render_template("admin/add_flight.html", form=form, errors=errors, airports=AIRPORTS)

    def booked_seats_by_class(flight_id):
        rows = get_db().execute(
            """SELECT travel_class, SUM(number_of_passengers) AS seats FROM bookings
               WHERE flight_id = ? AND booking_status != 'Cancelled' GROUP BY travel_class""",
            (flight_id,)).fetchall()
        return {r["travel_class"]: r["seats"] for r in rows}

    @app.route("/admin/flights/<int:flight_id>/edit", methods=["GET", "POST"])
    @admin_required
    @role_required("admin")
    def admin_edit_flight(flight_id):
        db = get_db()
        flight = db.execute("SELECT * FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
        if flight is None:
            abort(404)
        existing = {f["travel_class"]: f for f in db.execute(
            "SELECT * FROM flight_fares WHERE flight_id = ?", (flight_id,)).fetchall()}
        booked = booked_seats_by_class(flight_id)
        errors = {}
        if request.method == "POST":
            form = request.form
            data, fares, errors = read_flight_form(form, existing, booked, is_new=False,
                                                   original_date=flight["departure_date"])
            if not errors and db.execute("SELECT 1 FROM flights WHERE flight_number = ? AND flight_id != ?",
                                         (data["flight_number"], flight_id)).fetchone():
                errors["flight_number"] = "Another flight already uses this flight number."
            if not errors:
                with transaction(db):
                    db.execute(
                        """UPDATE flights SET flight_number = :flight_number, airline_name = :airline_name,
                               departure_city = :departure_city, departure_airport = :departure_airport,
                               destination_city = :destination_city, destination_airport = :destination_airport,
                               departure_date = :departure_date, departure_time = :departure_time,
                               arrival_time = :arrival_time, aircraft = :aircraft
                           WHERE flight_id = :flight_id""", {**data, "flight_id": flight_id})
                    for travel_class in TRAVEL_CLASSES:
                        fare = fares.get(travel_class)
                        if fare is None:
                            db.execute("DELETE FROM flight_fares WHERE flight_id = ? AND travel_class = ?",
                                       (flight_id, travel_class))
                        elif travel_class in existing:
                            db.execute(
                                """UPDATE flight_fares SET ticket_price = ?, total_seats = ?, available_seats = ?
                                   WHERE flight_id = ? AND travel_class = ?""",
                                (fare["ticket_price"], fare["total_seats"], fare["available_seats"],
                                 flight_id, travel_class))
                        else:
                            db.execute(
                                """INSERT INTO flight_fares (flight_id, travel_class, ticket_price, total_seats,
                                       available_seats) VALUES (?, ?, ?, ?, ?)""",
                                (flight_id, travel_class, fare["ticket_price"], fare["total_seats"],
                                 fare["available_seats"]))
                flash(f"Flight {data['flight_number']} has been updated.", "success")
                return redirect(url_for("admin_flights"))
            flash("Please correct the errors highlighted below.", "error")
        else:
            form = dict(flight)
            for travel_class, fare in existing.items():
                key = travel_class.split()[0].lower()
                form[f"price_{key}"] = fare["ticket_price"]
                form[f"total_{key}"] = fare["total_seats"]
                form[f"available_{key}"] = fare["available_seats"]
        return render_template("admin/edit_flight.html", flight=flight, form=form, errors=errors,
                               booked=booked, airports=AIRPORTS)

    @app.route("/admin/flights/<int:flight_id>/delete", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_delete_flight(flight_id):
        db = get_db()
        flight = db.execute("SELECT * FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
        if flight is None:
            abort(404)
        # Rule: a flight that has ever been booked is kept for the booking history.
        count = db.execute("SELECT COUNT(*) FROM bookings WHERE flight_id = ?", (flight_id,)).fetchone()[0]
        if count:
            flash(f"Flight {flight['flight_number']} cannot be deleted because it has {count} booking(s). "
                  "Edit it instead (for example, set its available seats to 0).", "error")
        else:
            with transaction(db):
                db.execute("DELETE FROM flight_fares WHERE flight_id = ?", (flight_id,))
                db.execute("DELETE FROM flights WHERE flight_id = ?", (flight_id,))
            flash(f"Flight {flight['flight_number']} has been deleted.", "success")
        return redirect(request.referrer if request.referrer and "/admin/flights" in request.referrer
                        else url_for("admin_flights"))

    @app.route("/admin/bookings")
    @admin_required
    def admin_bookings():
        filters = {k: request.args.get(k, "").strip() for k in ("reference", "flight_number", "date", "status")}
        where, params = [], []
        if filters["reference"]:
            where.append("b.booking_reference LIKE ?")
            params.append(f"%{filters['reference'].upper()}%")
        if filters["flight_number"]:
            where.append("f.flight_number LIKE ?")
            params.append(f"%{filters['flight_number'].upper()}%")
        if parse_date(filters["date"]):
            where.append("f.departure_date = ?")
            params.append(filters["date"])
        if filters["status"] in BOOKING_STATUSES:
            where.append("b.booking_status = ?")
            params.append(filters["status"])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        bookings = get_db().execute(BOOKING_SELECT + clause + " ORDER BY b.booking_date DESC", params).fetchall()
        return render_template("admin/bookings.html", bookings=bookings, filters=filters,
                               statuses=BOOKING_STATUSES)

    @app.route("/admin/bookings/<int:booking_id>/status", methods=["POST"])
    @admin_required
    def admin_update_booking(booking_id):
        db = get_db()
        booking = db.execute(BOOKING_SELECT + " WHERE b.booking_id = ?", (booking_id,)).fetchone()
        if booking is None:
            abort(404)
        action = request.form.get("action")
        if action == "confirm" and booking["booking_status"] == "Pending":
            db.execute("UPDATE bookings SET booking_status = 'Confirmed' WHERE booking_id = ?", (booking_id,))
            flash(f"Booking {booking['booking_reference']} confirmed.", "success")
        elif action == "cancel" and booking["booking_status"] != "Cancelled":
            cancel_booking(db, booking)
            flash(f"Booking {booking['booking_reference']} cancelled and seats released.", "success")
        else:
            flash("That action is not allowed for this booking.", "error")
        return redirect(request.referrer if request.referrer and "/admin/bookings" in request.referrer
                        else url_for("admin_bookings"))

    @app.route("/admin/reports")
    @admin_required
    def admin_reports():
        db = get_db()
        t = today()
        periods = {
            "year": (f"This year to date ({t.year})", date(t.year, 1, 1), t, "%Y-%m"),
            "12m": ("Last 12 months", t - timedelta(days=365), t, "%Y-%m"),
            "90d": ("Last 90 days", t - timedelta(days=90), t, "%Y-W%W"),
            "30d": ("Last 30 days", t - timedelta(days=30), t, "%Y-%m-%d"),
        }
        period = request.args.get("period", "year")
        if period not in periods:
            period = "year"
        _label, start, end, bucket = periods[period]
        # Everything is measured by departure date: what actually flew (or will fly) in the period.
        scope = """FROM bookings b JOIN flights f ON f.flight_id = b.flight_id
                   WHERE f.departure_date BETWEEN ? AND ?"""
        params = (start.isoformat(), end.isoformat())

        def rows(sql):
            return [dict(r) for r in db.execute(sql, params).fetchall()]

        totals = db.execute(f"""
            SELECT COUNT(*) AS bookings,
                   COALESCE(SUM(CASE WHEN b.booking_status != 'Cancelled' THEN b.number_of_passengers END), 0) AS passengers,
                   COALESCE(SUM(CASE WHEN b.booking_status = 'Confirmed' THEN b.total_cost END), 0) AS revenue,
                   COUNT(CASE WHEN b.booking_status = 'Confirmed' THEN 1 END) AS confirmed,
                   COUNT(CASE WHEN b.booking_status = 'Cancelled' THEN 1 END) AS cancelled
            {scope}""", params).fetchone()
        report = {
            "trend": rows(f"""
                SELECT strftime('{bucket}', f.departure_date) AS bucket,
                       COALESCE(SUM(CASE WHEN b.booking_status = 'Confirmed' THEN b.total_cost END), 0) AS revenue,
                       COALESCE(SUM(CASE WHEN b.booking_status != 'Cancelled' THEN b.number_of_passengers END), 0) AS passengers
                {scope} GROUP BY bucket ORDER BY bucket"""),
            "destinations": rows(f"""
                SELECT f.destination_city AS city, SUM(b.number_of_passengers) AS passengers,
                       COUNT(*) AS bookings
                {scope} AND b.booking_status != 'Cancelled' AND f.destination_city != 'Nairobi'
                GROUP BY city ORDER BY passengers DESC LIMIT 10"""),
            "status": rows(f"""
                SELECT b.booking_status AS status, COUNT(*) AS bookings
                {scope} GROUP BY status"""),
            "classes": rows(f"""
                SELECT b.travel_class AS travel_class, SUM(b.number_of_passengers) AS passengers
                {scope} AND b.booking_status != 'Cancelled' GROUP BY travel_class"""),
            "occupancy": [dict(r) for r in db.execute("""
                SELECT f.destination_city AS city,
                       ROUND(100.0 * SUM(fa.total_seats - fa.available_seats) / SUM(fa.total_seats), 1) AS percent
                FROM flight_fares fa JOIN flights f ON f.flight_id = fa.flight_id
                WHERE f.departure_date BETWEEN ? AND ?
                GROUP BY city HAVING SUM(fa.total_seats) > 0 ORDER BY percent DESC""", params).fetchall()],
        }
        routes = rows(f"""
            SELECT f.departure_city || ' → ' || f.destination_city AS route, COUNT(*) AS bookings,
                   SUM(b.number_of_passengers) AS passengers,
                   COALESCE(SUM(CASE WHEN b.booking_status = 'Confirmed' THEN b.total_cost END), 0) AS revenue
            {scope} AND b.booking_status != 'Cancelled'
            GROUP BY route ORDER BY passengers DESC LIMIT 10""")
        return render_template("admin/reports.html", periods=periods, period=period, totals=totals,
                               report=report, routes=routes)

    @app.route("/admin/staff", methods=["GET", "POST"])
    @admin_required
    @role_required("admin")
    def admin_staff():
        db = get_db()
        form = {k: request.form.get(k, "").strip() for k in ("username", "role")}
        errors = {}
        if request.method == "POST":
            password = request.form.get("password", "")
            if not USERNAME_RE.match(form["username"]):
                errors["username"] = "Use 3-30 letters, numbers, dots, dashes or underscores."
            elif db.execute("SELECT 1 FROM administrators WHERE username = ?", (form["username"],)).fetchone():
                errors["username"] = "That username is already taken."
            if len(password) < 8:
                errors["password"] = "Password must be at least 8 characters."
            if form["role"] not in STAFF_ROLES:
                errors["role"] = "Choose a role."
            if errors:
                flash("Please correct the errors highlighted below.", "error")
            else:
                db.execute("INSERT INTO administrators (username, password_hash, role) VALUES (?, ?, ?)",
                           (form["username"], generate_password_hash(password), form["role"]))
                flash(f"{STAFF_ROLES[form['role']]} account '{form['username']}' created.", "success")
                return redirect(url_for("admin_staff"))
        staff = db.execute("SELECT admin_id, username, role, created_at FROM administrators "
                           "ORDER BY role, username").fetchall()
        return render_template("admin/staff.html", staff=staff, form=form, errors=errors)

    @app.route("/admin/staff/<int:admin_id>", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_update_staff(admin_id):
        db = get_db()
        member = db.execute("SELECT * FROM administrators WHERE admin_id = ?", (admin_id,)).fetchone()
        if member is None:
            abort(404)
        action, role = request.form.get("action"), request.form.get("role")
        # Count and change inside one transaction so two admins cannot demote each other at once.
        with transaction(db):
            admins = db.execute("SELECT COUNT(*) FROM administrators WHERE role = 'admin'").fetchone()[0]
            if admin_id == session.get("admin_id"):
                flash("You cannot change or remove your own account.", "error")
            elif member["role"] == "admin" and admins <= 1 and (action == "delete" or role != "admin"):
                flash("At least one administrator account must remain.", "error")
            elif action == "delete":
                db.execute("DELETE FROM administrators WHERE admin_id = ?", (admin_id,))
                flash(f"Account '{member['username']}' removed.", "success")
            elif action == "role" and role in STAFF_ROLES:
                db.execute("UPDATE administrators SET role = ? WHERE admin_id = ?", (role, admin_id))
                flash(f"'{member['username']}' is now a {STAFF_ROLES[role].lower()}.", "success")
            else:
                flash("That action is not allowed.", "error")
        return redirect(url_for("admin_staff"))

    @app.route("/admin/messages")
    @admin_required
    def admin_messages():
        messages = get_db().execute("SELECT * FROM contact_messages ORDER BY submission_date DESC").fetchall()
        return render_template("admin/messages.html", messages=messages)


# --------------------------------------------------------------------------- #
# Error pages
# --------------------------------------------------------------------------- #

def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(err):
        return render_template("error.html", code=400, title="Bad request",
                               message=getattr(err, "description", "")), 400

    @app.errorhandler(403)
    def forbidden(_err):
        return render_template("error.html", code=403, title="Access denied",
                               message="Your staff role does not allow this action."), 403

    @app.errorhandler(404)
    def not_found(_err):
        return render_template("error.html", code=404, title="Page not found",
                               message="The page you are looking for does not exist."), 404

    @app.errorhandler(500)
    def server_error(_err):
        return render_template("error.html", code=500, title="Something went wrong",
                               message="An unexpected error occurred. Please try again later."), 500


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)

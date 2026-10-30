# SkyLink Airlines – Flight Ticketing System

A database-driven flight reservation and ticketing web application built with **Python Flask**, **HTML5**, **CSS3** and **SQLite**.
Customers can search flights, book tickets, print an e-ticket, and retrieve or cancel bookings. Administrators can manage flights (full CRUD), bookings and contact messages.

> Academic project: bookings and payments are **simulated**. No real payment or card details are collected.

---

## 1. Requirements

| Item | Version |
|------|---------|
| Python | 3.10 or newer (developed and tested with **Python 3.14**) |
| Flask | 3.x (installs Werkzeug and Jinja2 automatically) |
| Browser | Any modern browser (Chrome, Firefox, Edge) |

## 2. Installation

```bash
# 1. Open a terminal in the project folder
cd flight_ticketing_system

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install the dependencies
pip install -r requirements.txt
```

## 3. Initialise the database

The database `flight_ticketing.db` is **created automatically** the first time the app starts.
You can also create it manually:

```bash
python database.py           # create tables + sample data (only if the database is empty)
python database.py --reset   # delete the database and rebuild it with fresh sample data
```

Sample flights are generated for the **next 14 days from the day the database is created**, together with fictional mock traffic: a year of past flights with bookings (for the reports) and partly sold upcoming flights. An existing database is upgraded automatically, but the mock data is only added to a new one, so run `python database.py --reset` once to get it. If you demonstrate the project later, run `python database.py --reset` first so the flights are in the future.

## 4. Run the application

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

(Alternatively: `flask --app app run`.)

## 5. Sample credentials and demo data (fictional)

| Purpose | Value |
|---------|-------|
| Admin login page | http://127.0.0.1:5000/admin/login |
| Admin username | `admin` |
| Admin password | `SkyLink@2026` |
| Booking agent login (limited role) | username `agent`, password `Agent@2026` – can manage bookings and read messages, but cannot change flights or staff |
| Demo booking (Confirmed) | Reference `SLDEMO01`, email `jane.wanjiru@example.com` |
| Demo booking (Pending) | Reference `SLDEMO02`, email `brian.otieno@example.com` |
| Demo booking (Cancelled) | Reference `SLDEMO03`, email `amina.hassan@example.com` |
| Nearly full flight | Nairobi → Kisumu, 3 days from today: only 3 Economy seats left (use it to show the seat-capacity check) |

The admin password is stored only as a Werkzeug hash. To use different credentials, set environment variables **before the database is first created**:

```bash
export ADMIN_USERNAME=myadmin
export ADMIN_PASSWORD='a-strong-password'
```

Other optional settings: `SECRET_KEY` (otherwise a random key is generated and stored in `instance/secret_key`, which is not committed), `DATABASE_PATH`, `FLASK_DEBUG=1`.

## 6. Features

| Module | What it does |
|--------|--------------|
| Home page | Logo, navigation, aircraft background, flight search form, featured destinations, promotions, footer |
| Flight search | Search by city/airport, date, return date, passengers and class; past dates blocked; "no flights" message with nearby-date suggestions |
| Booking | Passenger, flight and booking sections; live total-cost calculation (price × passengers, minus any group discount); full server-side validation; seat check inside a database transaction; unique booking reference |
| E-ticket | Airline-styled confirmation page with **Print Ticket** button (print stylesheet prints only the ticket) |
| My Bookings | Lists the visitor's bookings automatically (made this visit, or on their account); retrieve others by booking reference **and** email; view status; cancel (status becomes *Cancelled*, seats released, row kept) |
| Customer accounts | Register / sign in / sign out; booking form prefilled from the account; bookings stay with the account on any device |
| Admin – flights | Login, dashboard with 3-day schedule, add / view / edit / delete flights, update prices and seats per class |
| Admin – bookings | View all bookings; filter by reference, flight number, departure date, status; confirm pending or cancel bookings |
| Admin – reports | KPIs plus charts (Chart.js, bundled for offline use): revenue over time, most travelled destinations, seat occupancy by destination, bookings by status, passengers by class, top routes; filter by period |
| Staff roles | **Administrator** (everything, including flights and the Staff page) and **Booking agent** (dashboard, view flights, bookings, messages, reports) |
| About / Contact | Company background, mission, vision, services, destinations; validated contact form saved to the database |

### Business rules
- **Cancellation:** allowed online up to **24 hours before departure**. Cancelling never deletes a booking.
- **Deleting flights:** only flights that have **never been booked** can be deleted (keeps booking history intact). Booked flights can be edited instead.
- **Seat editing:** an admin cannot set available seats higher than *total seats − seats already booked*.
- **Booking status:** *Pay at airline office* → **Pending** until staff confirm it. M-Pesa is shown on the form but disabled ("Coming soon") until the Daraja integration is added.
- **Group discount:** 5% off for 4–5 passengers, 10% off for 6 or more (`GROUP_DISCOUNTS` in `app.py`).
- Max 9 passengers per booking.

## 7. Database design

Database file: `flight_ticketing.db` (SQLite). Schema is defined in `database.py`.

```
administrators (admin_id PK, username UNIQUE, password_hash, role 'admin'|'agent')

customers (customer_id PK, full_name, email UNIQUE, phone, password_hash)   1 ──< bookings.customer_id (optional)

flights (flight_id PK, flight_number UNIQUE, airline_name, departure_city, departure_airport,
         destination_city, destination_airport, departure_date, departure_time, arrival_time, aircraft)
   │ 1
   │
   ├──< flight_fares (fare_id PK, flight_id FK, travel_class, ticket_price, total_seats, available_seats)
   │        UNIQUE(flight_id, travel_class)
   │
   └──< bookings (booking_id PK, booking_reference UNIQUE, passenger_id FK, flight_id FK,
                  number_of_passengers, travel_class, price_per_ticket, total_cost,
                  payment_method, booking_date, booking_status, cancelled_at, customer_id FK NULL)
                       >──┐
passengers (passenger_id PK, full_name, email, phone, identification_number) ──┘ 1
         UNIQUE(email, identification_number)

contact_messages (message_id PK, customer_name, email, subject, message, submission_date)
```

**Design improvement:** travel class, price and seat counts are in a separate `flight_fares` table, so one flight can offer Economy, Business and First Class with different prices and capacities (allowed by section 5 of the brief).

Constraints used: primary keys, foreign keys (`PRAGMA foreign_keys = ON`), `UNIQUE` on flight number and booking reference, `CHECK` constraints (positive prices, `0 ≤ available_seats ≤ total_seats`, valid class and status values, departure ≠ destination).

## 8. Security measures

- All SQL uses **parameterised queries** (`?` / `:name` placeholders).
- **Server-side validation** of every form (the JavaScript validation is only a convenience).
- Jinja2 **auto-escaping** of all user content (XSS protection).
- **Session-based admin authentication** (`@admin_required`), session cleared on login, 2-hour session lifetime, HttpOnly + SameSite cookies.
- **Role-based access** (`@role_required("admin")`): the role is re-read from the database on every request, so demoting or removing staff takes effect immediately; the last administrator cannot be removed.
- **Customer accounts** only see bookings made while signed in or proven with reference + email, so registering with someone else's email reveals nothing.
- Passwords hashed with **Werkzeug** `generate_password_hash` / `check_password_hash`.
- **CSRF protection**: every POST form carries a random per-session token checked with `hmac.compare_digest`.
- Secret key is read from the environment or a generated file in `instance/` — never hard-coded.
- Booking lookup requires the reference **and** matching email; failures show the same generic message. Confirmation pages are only shown to the session that made or verified the booking, or to the account that owns it.
- Seat reservation uses `BEGIN IMMEDIATE` transactions and a conditional `UPDATE … WHERE available_seats >= ?`, so seats can never be oversold.
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`.

## 9. Testing

Automated tests (Python `unittest`, no extra packages needed) use a temporary database:

```bash
python -m unittest discover -s tests -v
```

| # | Test case | Expected result | Automated test |
|---|-----------|-----------------|----------------|
| 1 | Search for an existing flight | Matching flights displayed | `test_search_existing_flight_shows_results` |
| 2 | Search for a nonexistent flight | No-results message | `test_search_nonexistent_flight_shows_message` |
| 3 | Submit an incomplete booking form | Validation errors displayed | `test_incomplete_booking_form_shows_errors` |
| 4 | Enter an invalid email | Booking rejected | `test_invalid_email_is_rejected` |
| 5 | Book an available flight | Booking reference generated | `test_book_available_flight_generates_reference` |
| 6 | Exceed seat capacity | Booking rejected | `test_exceeding_seat_capacity_is_rejected` |
| 7 | Retrieve booking with valid details | Correct booking shown | `test_retrieve_booking_with_valid_details` |
| 8 | Retrieve booking with incorrect details | Nothing disclosed | `test_retrieve_booking_with_wrong_email_discloses_nothing` |
| 9 | Cancel an eligible booking | Status → Cancelled, seats released | `test_cancel_eligible_booking` |
| 10 | Add a new flight (admin) | Flight saved | `test_admin_add_flight` |
| 11 | Edit a flight | Changes saved | `test_admin_edit_flight` |
| 12 | Delete an eligible flight | Flight removed; booked flights protected | `test_admin_delete_eligible_flight`, `test_admin_cannot_delete_flight_with_bookings` |
| 13 | Access admin without login | Redirected to login | `test_admin_pages_require_login` |
| 14 | Submit a contact message | Message saved | `test_submit_contact_message` |

Extra tests cover past-date rejection, duplicate flight numbers, overselling via admin edit, CSRF rejection, SQL-injection input, HTML escaping, password hashing, booking filters, and that every public page loads.

## 10. Project structure

```
flight_ticketing_system/
├── app.py                 # Flask app: routes, validation, security, business rules
├── database.py            # schema, connection helpers, transactions, sample data
├── requirements.txt
├── README.md
├── flight_ticketing.db    # created automatically (not committed)
├── templates/
│   ├── base.html, _macros.html, _ticket.html, error.html
│   ├── index.html, search_flights.html, flight_details.html, book_flight.html
│   ├── booking.html, booking_confirmation.html, my_bookings.html
│   ├── about.html, contact.html
│   └── admin/ login, dashboard, flights, add_flight, edit_flight, _flight_form, bookings, messages
├── static/
│   ├── css/style.css
│   ├── js/script.js       # nav toggle, live total cost, client-side validation, print
│   └── images/            # airline_logo.svg, aircraft.svg, destinations/*.svg
├── tests/test_app.py
└── instance/              # secret key (git-ignored)
```

## 11. Acknowledgements

- [Flask](https://flask.palletsprojects.com/), [Werkzeug](https://werkzeug.palletsprojects.com/) and [Jinja2](https://jinja.palletsprojects.com/) (BSD-3-Clause).
- [Poppins](https://fonts.google.com/specimen/Poppins) font via Google Fonts (SIL Open Font License); falls back to system fonts offline.
- All images (logo, aircraft, destinations) are original SVG illustrations created for this project.
- Airport names are real; the airline, flights, prices, people and contact details are fictional.

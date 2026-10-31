# SkyLink Airlines – Flight Ticketing System

A database-driven flight reservation and ticketing web application built with **Python Flask**, **HTML5**, **CSS3**, **JavaScript** and **SQLite**.

- **Visitors** browse flights, fares, promotions and group discounts.
- **Customers** (signed in) book flights, print e-tickets, and view or cancel their bookings.
- **Booking agents** manage bookings, read contact messages and view reports.
- **Administrators** do everything agents can, plus manage flights and staff accounts.

> Academic project: bookings and payments are **simulated**. No real payment or card details are collected.

---

## 1. Requirements

| Item | Version |
|------|---------|
| Python | 3.10 or newer (developed and tested with **Python 3.14**) |
| Flask | 3.x (installs Werkzeug and Jinja2 automatically) |
| Git | Any recent version (only needed to clone the project) |
| Browser | Any modern browser (Chrome, Firefox, Edge) |
| IDE (optional) | PyCharm (Community or Professional), VS Code, or just a terminal |

## 2. Get the code and set it up

Choose **one** of the two ways below. Both end with the app running at **http://127.0.0.1:5000**.

### Option A – PyCharm

1. **Clone the repository**
   - On the PyCharm welcome screen click **Clone Repository**
     (or, with a project open: **File → New → Project from Version Control…**; in the new UI: main menu **Git → Clone…**).
   - **URL:** `https://github.com/Levy-m/flight-ticketing-system.git`
   - **Directory:** wherever you keep your projects, e.g. `…/PycharmProjects/flight-ticketing-system`
   - Click **Clone**. If the repository is private, PyCharm asks you to log in to GitHub first.
   - When asked, click **Trust Project**.
2. **Create the virtual environment**
   - **File → Settings → Project: flight-ticketing-system → Python Interpreter** (macOS: **PyCharm → Settings**).
   - **Add Interpreter → Add Local Interpreter → Virtualenv Environment → New**.
   - **Location:** `<project folder>/venv`; **Base interpreter:** Python 3.10 or newer. Click **OK**.
3. **Install the dependencies**
   - Click **Install requirements** on the yellow banner that appears over `requirements.txt`, **or**
   - open PyCharm's **Terminal** tab (it activates `venv` automatically) and run:
     ```bash
     pip install -r requirements.txt
     ```
4. **Create the database with the sample data** – in the **Terminal** tab:
   ```bash
   python database.py --reset
   ```
5. **Run the app** – right-click `app.py` → **Run 'app'**.
   Open the link printed in the **Run** window: **http://127.0.0.1:5000**.
6. **Run the tests (optional)** – right-click the `tests` folder → **Run 'Python tests in tests'**,
   or in the Terminal: `python -m unittest discover -s tests -v`.

> Tip: to turn on Flask debug mode (auto-reload and error pages), edit the **app** run configuration
> (**Run → Edit Configurations…**) and add `FLASK_DEBUG=1` under **Environment variables**.

### Option B – Terminal

```bash
# 1. Clone the project and enter the folder
git clone https://github.com/Levy-m/flight-ticketing-system.git
cd flight-ticketing-system

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install the dependencies
pip install -r requirements.txt

# 4. Create the database with the sample data
python database.py --reset

# 5. Run the app
python app.py                     # or: flask --app app run
```

Then open **http://127.0.0.1:5000** in your browser.

## 3. Database and sample data (seeding)

The database file `flight_ticketing.db` is **created automatically** the first time the app starts. You can also manage it yourself:

```bash
python database.py           # create tables + sample data (only if the database is empty)
python database.py --reset   # DELETE the database and rebuild it with fresh sample data
```

A new database is seeded with:

| Data | Details |
|------|---------|
| Staff accounts | `admin` (Administrator) and `agent` (Booking agent) – see section 4 |
| Demo customer | Jane Wanjiru, who owns booking `SLDEMO01` |
| Upcoming flights | 20 routes over the **next 14 days** (domestic daily, international every other day), with Economy, Business and – on international routes – First Class fares |
| Demo bookings | `SLDEMO01` (Confirmed, on Jane's account), `SLDEMO02` (Pending, no account), `SLDEMO03` (Cancelled) |
| Nearly full flight | Nairobi → Kisumu, 3 days from today, with only 3 Economy seats left (for showing the seat-capacity check) |
| Mock traffic | **Fictional**, generated with a fixed seed so it is the same every time: 160 repeat passengers; a **year of past flights** (one day a week) with about 21,000 bookings and busier holiday months, which feeds the Reports page; upcoming flights **partly sold** (fuller the sooner they depart) |
| Contact message | One sample message |

Important notes:

- Flights are generated **relative to the day the database is created**. If you demonstrate the project on a later day, run `python database.py --reset` first so the flights are in the future.
- An older database is **upgraded automatically** (new tables and columns are added and existing data is kept), but the demo accounts and mock data are **only added to a new database**. If a login from section 4 does not work, run `python database.py --reset`.
- `--reset` deletes everything in the database, including bookings you made yourself.

## 4. Login credentials (fictional, for demonstration)

| Role | Login page | Username / email | Password | What they can do |
|------|------------|------------------|----------|------------------|
| **Administrator** | http://127.0.0.1:5000/admin/login | `admin` | `SkyLink@2026` | Everything: dashboard, flights (add / edit / delete), bookings, messages, reports, **Staff** accounts |
| **Booking agent** | http://127.0.0.1:5000/admin/login | `agent` | `Agent@2026` | Dashboard, view flights, confirm / cancel bookings, messages, reports. Cannot change flights or staff |
| **Customer** | http://127.0.0.1:5000/account/login | `jane.wanjiru@example.com` | `Jane@2026` | Book flights; view, print and cancel own bookings |
| **Visitor** | – | – | – | Browse flights, fares, promotions and discounts; register at http://127.0.0.1:5000/account/register |

Demo bookings for trying out **My Bookings → Add an existing booking** (signed in as any customer):

| Reference | Email | Status |
|-----------|-------|--------|
| `SLDEMO01` | `jane.wanjiru@example.com` | Confirmed – already on Jane's account, so it cannot be added to another account |
| `SLDEMO02` | `brian.otieno@example.com` | Pending – no account yet, can be added |
| `SLDEMO03` | `amina.hassan@example.com` | Cancelled – no account yet, can be added |

Administrators can create more staff accounts (admin or agent) on the **Staff** page. Passwords are stored only as Werkzeug hashes.

To use a different administrator login, set these environment variables **before the database is first created** (in PyCharm: the run configuration's **Environment variables**):

```bash
export ADMIN_USERNAME=myadmin                # Windows (cmd): set ADMIN_USERNAME=myadmin
export ADMIN_PASSWORD='a-strong-password'
```

Other optional settings: `SECRET_KEY` (otherwise a random key is generated and stored in `instance/secret_key`, which is not committed), `DATABASE_PATH`, `FLASK_DEBUG=1`.

## 5. Features

| Module | What it does |
|--------|--------------|
| Home page | Logo, navigation, aircraft background, flight search form, featured destinations, promotions, footer |
| Flight search | Search by city/airport, date, return date, passengers and class; past dates blocked; "no flights" message with nearby-date suggestions |
| Flight details | Fares and seats left per class, baggage and perks, group-discount offer; visitors see **Sign in to book** |
| Customer accounts | Register, sign in, sign out. **Sign-in is required to book and to view bookings** because they hold personal data. After signing in you return to the flight you picked |
| Booking | Passenger, flight and payment sections, prefilled from the account; live total (price × passengers − group discount); full server-side validation; seat check inside a database transaction; unique booking reference |
| E-ticket | Airline-styled confirmation page with **Print Ticket** (the print stylesheet prints only the ticket) and **Cancel booking** |
| My Bookings | Every booking on the account; **Add an existing booking** attaches one made without an account (reference **and** email must match); view, print, cancel |
| Staff – dashboard | Key figures, the next 12 departures, recent bookings and messages |
| Staff – flights | List and search flights; administrators can add, edit and delete flights and update prices and seats per class |
| Staff – bookings | View all bookings; filter by reference, flight number, departure date and status; confirm pending or cancel bookings |
| Staff – reports | Key figures plus charts (Chart.js, bundled so it works offline): revenue over time, **most travelled destinations**, seat occupancy by destination, bookings by status, passengers by class, and a top-routes table. Filter by period: this year to date, last 12 months, 90 days or 30 days |
| Staff – accounts | Administrators create staff accounts, switch them between administrator and agent, or remove them |
| About / Contact | Company background, mission, vision, services, destinations; validated contact form saved to the database |

### Roles

| Page / action | Visitor | Customer | Booking agent | Administrator |
|---------------|:-------:|:--------:|:-------------:|:-------------:|
| Browse and search flights, fares, discounts | ✅ | ✅ | ✅ | ✅ |
| Book a flight, My Bookings, e-ticket, cancel own booking | – | ✅ | – | – |
| Staff dashboard, bookings, messages, reports | – | – | ✅ | ✅ |
| View flights list | – | – | ✅ | ✅ |
| Add / edit / delete flights | – | – | – | ✅ |
| Manage staff accounts | – | – | – | ✅ |

### Business rules

- **Cancellation:** allowed online up to **24 hours before departure**. Cancelling never deletes a booking; its status becomes *Cancelled* and the seats are released.
- **Payment:** *Pay at airline office* → the booking is **Pending** until staff confirm it. M-Pesa is shown on the form but disabled ("Coming soon") until the Daraja integration is added.
- **Group discount:** 5% off for 4–5 passengers and 10% off for 6 or more on one booking (`GROUP_DISCOUNTS` in `app.py`).
- **Deleting flights:** only flights that have **never been booked** can be deleted, which keeps booking history intact. Booked flights can be edited instead.
- **Seat editing:** staff cannot set available seats higher than *total seats − seats already booked*.
- **Staff accounts:** you cannot change or remove your own account, and the last administrator cannot be removed or demoted.
- Max 9 passengers per booking.

## 6. Database design

Database file: `flight_ticketing.db` (SQLite). The schema is defined in `database.py`.

```
administrators (admin_id PK, username UNIQUE, password_hash, role 'admin'|'agent')

customers (customer_id PK, full_name, email UNIQUE, phone, password_hash)
         1 ──< bookings.customer_id   (optional: NULL for bookings made without an account)

flights (flight_id PK, flight_number UNIQUE, airline_name, departure_city, departure_airport,
         destination_city, destination_airport, departure_date, departure_time, arrival_time, aircraft)
   │ 1
   │
   ├──< flight_fares (fare_id PK, flight_id FK, travel_class, ticket_price, total_seats, available_seats)
   │        UNIQUE(flight_id, travel_class)
   │
   └──< bookings (booking_id PK, booking_reference UNIQUE, passenger_id FK, flight_id FK, customer_id FK NULL,
                  number_of_passengers, travel_class, price_per_ticket, total_cost,
                  payment_method, booking_date, booking_status, cancelled_at)
                       >──┐
passengers (passenger_id PK, full_name, email, phone, identification_number) ──┘ 1
         UNIQUE(email, identification_number)

contact_messages (message_id PK, customer_name, email, subject, message, submission_date)
```

- **Design improvement:** travel class, price and seat counts are in a separate `flight_fares` table, so one flight can offer Economy, Business and First Class with different prices and capacities (allowed by section 5 of the brief).
- **Customers vs passengers:** a *customer* is the account that makes and manages the booking; a *passenger* is the person travelling. A booking made at the office has no customer until someone adds it to their account.
- **Constraints:** primary keys, foreign keys (`PRAGMA foreign_keys = ON`), `UNIQUE` on flight number, booking reference, staff username and customer email, and `CHECK` constraints (positive prices, `0 ≤ available_seats ≤ total_seats`, valid class, status and role values, departure ≠ destination).

## 7. Security measures

- All SQL uses **parameterised queries** (`?` placeholders).
- **Server-side validation** of every form (the JavaScript validation is only a convenience).
- Jinja2 **auto-escaping** of all user content (XSS protection).
- Passwords (staff and customers) are hashed with **Werkzeug** `generate_password_hash` / `check_password_hash`.
- **CSRF protection:** every POST form carries a random per-session token checked with `hmac.compare_digest`.
- **Sessions:** cleared on every sign-in (prevents session fixation), 2-hour lifetime, HttpOnly + SameSite cookies. The secret key comes from the environment or a generated file in `instance/`, never from the source code.
- **Staff access** (`@admin_required`, `@role_required("admin")`): the role is re-read from the database on every request, so demoting or removing a staff member takes effect immediately.
- **Customer access** (`@customer_required`): booking, My Bookings, e-tickets and cancelling require sign-in. Asking for an e-ticket or cancellation of a booking on another account returns 404, the same as for one that does not exist.
- **Adding an existing booking** requires the reference **and** matching email, and never moves a booking off another account; every failure shows the same generic message. Registering with someone else's email therefore reveals none of their bookings.
- Sign-in redirects (`?next=`) only go to pages on this site.
- Seat reservation uses `BEGIN IMMEDIATE` transactions and a conditional `UPDATE … WHERE available_seats >= ?`, so seats can never be oversold.
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`.

## 8. Testing

Automated tests (Python `unittest`, no extra packages needed) each use their own temporary database, so your real `flight_ticketing.db` is never touched:

```bash
python -m unittest discover -s tests -v
```

In PyCharm: right-click the `tests` folder → **Run 'Python tests in tests'**.

| # | Test case | Expected result | Automated test |
|---|-----------|-----------------|----------------|
| 1 | Search for an existing flight | Matching flights displayed | `test_search_existing_flight_shows_results` |
| 2 | Search for a nonexistent flight | No-results message | `test_search_nonexistent_flight_shows_message` |
| 3 | Submit an incomplete booking form | Validation errors displayed | `test_incomplete_booking_form_shows_errors` |
| 4 | Enter an invalid email | Booking rejected | `test_invalid_email_is_rejected` |
| 5 | Book an available flight | Booking reference generated | `test_book_available_flight_generates_reference` |
| 6 | Exceed seat capacity | Booking rejected | `test_exceeding_seat_capacity_is_rejected` |
| 7 | Book or view bookings without signing in | Redirected to sign-in, nothing booked | `test_booking_and_my_bookings_require_sign_in` |
| 8 | Customer views their booking | Correct booking shown | `test_demo_customer_sees_their_booking` |
| 9 | Open or cancel someone else's booking | Not found, nothing changed | `test_customer_cannot_open_or_cancel_someone_elses_booking` |
| 10 | Add an existing booking with wrong details | Nothing disclosed | `test_add_existing_booking_needs_matching_email` |
| 11 | Cancel an eligible booking | Status → Cancelled, seats released | `test_cancel_eligible_booking` |
| 12 | Group booking | Discount applied to the total | `test_group_booking_gets_discount` |
| 13 | Add a new flight (admin) | Flight saved | `test_admin_add_flight` |
| 14 | Edit a flight | Changes saved | `test_admin_edit_flight` |
| 15 | Delete an eligible flight | Flight removed; booked flights protected | `test_admin_delete_eligible_flight`, `test_admin_cannot_delete_flight_with_bookings` |
| 16 | Access staff pages without login | Redirected to login | `test_admin_pages_require_login` |
| 17 | Agent tries admin-only actions | Access denied (403) | `test_agent_can_manage_bookings_but_not_flights_or_staff` |
| 18 | Manage staff accounts | Created, role changed, removed | `test_admin_creates_and_manages_staff` |
| 19 | Reports page | Loads for staff with correct figures | `test_reports_page_for_staff_only` |
| 20 | Submit a contact message | Message saved | `test_submit_contact_message` |

Extra tests cover past-date rejection, the disabled M-Pesa option, returning to the chosen flight after sign-in, safe redirects, accounts across sessions, duplicate registration, demoted staff losing access immediately, consistent mock data, duplicate flight numbers, overselling via admin edit, CSRF rejection, SQL-injection input, HTML escaping, password hashing, booking filters, and that every public page loads.

## 9. Project structure

```
flight-ticketing-system/
├── app.py                 # Flask app: routes, validation, security, roles, reports, business rules
├── database.py            # schema, upgrades, connection helpers, transactions, sample and mock data
├── requirements.txt
├── README.md
├── flight_ticketing.db    # created automatically (not committed)
├── templates/
│   ├── base.html, _macros.html, _ticket.html, error.html
│   ├── index.html, search_flights.html, flight_details.html, book_flight.html
│   ├── booking.html, booking_confirmation.html, my_bookings.html
│   ├── about.html, contact.html
│   ├── account/           # login, register (customers)
│   └── admin/             # login, _nav, dashboard, flights, add_flight, edit_flight, _flight_form,
│                          # bookings, messages, reports, staff
├── static/
│   ├── css/style.css
│   ├── js/script.js       # nav toggle, live total and discount, client-side validation, print
│   ├── js/reports.js      # draws the report charts
│   ├── js/vendor/chart.umd.min.js   # Chart.js 4.4.1, bundled for offline use
│   └── images/            # airline_logo.svg, aircraft.svg, destinations/*.svg
├── tests/test_app.py
└── instance/              # secret key (git-ignored)
```

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| "Invalid username or password" for `agent` or Jane | The database was created before those accounts existed. Run `python database.py --reset`. |
| No upcoming flights, or the Reports page is empty | The sample data is older than 14 days. Run `python database.py --reset`. |
| `ModuleNotFoundError: No module named 'flask'` | The virtual environment is not active or not selected. Activate `venv` (Terminal), or select it as the project interpreter in PyCharm, then run `pip install -r requirements.txt`. |
| `Address already in use` / port 5000 busy | The app is already running (check PyCharm's **Run** window) – stop it first, or close the other program using port 5000. |

## 11. Acknowledgements

- [Flask](https://flask.palletsprojects.com/), [Werkzeug](https://werkzeug.palletsprojects.com/) and [Jinja2](https://jinja.palletsprojects.com/) (BSD-3-Clause).
- [Chart.js](https://www.chartjs.org/) 4.4.1 for the report charts (MIT License), included in `static/js/vendor/`.
- [Poppins](https://fonts.google.com/specimen/Poppins) font via Google Fonts (SIL Open Font License); falls back to system fonts offline.
- All images (logo, aircraft, destinations) are original SVG illustrations created for this project.
- Airport names are real; the airline, flights, prices, people and contact details are fictional.

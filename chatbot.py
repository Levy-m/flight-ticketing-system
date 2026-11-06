"""
SkyLink Assistant - the website's built-in chatbot.

A rule-based assistant written in plain Python: no external AI service, no API key,
and it works offline. Each message goes through three steps:

    1. understand   - spot booking references, cities (even misspelt), dates,
                      travel class and number of passengers in the text
    2. decide       - pick the intent: booking status, my bookings, flight search,
                      list of destinations, a help topic (FAQ), greeting or fallback
    3. answer       - build the reply from live database data and the site's rules

Replies are plain data (text, list items, links, suggestion chips); the browser
renders them as text, never as HTML, so nothing the user types can inject markup.
"""

import re
from datetime import date, datetime, timedelta
from difflib import get_close_matches

from flask import url_for

from database import AIRPORTS, TRAVEL_CLASSES

MAX_MESSAGE_LENGTH = 300
MAX_FLIGHTS_SHOWN = 5
SEARCH_WINDOW_DAYS = 14

WELCOME_SUGGESTIONS = ["Flights from Nairobi to Mombasa tomorrow", "Cheapest flight to Zanzibar",
                       "Baggage allowance", "How do I cancel a booking?"]

REFERENCE_PATTERN = re.compile(r"\bSL[A-Z0-9]{6}\b", re.IGNORECASE)

# Extra names people use for our airports, on top of city names and IATA codes.
CITY_ALIASES = {"dar": "Dar es Salaam", "jkia": "Nairobi", "nbi": "Nairobi", "zanzibar city": "Zanzibar",
                "stone town": "Zanzibar", "manda": "Lamu", "msa": "Mombasa", "kampala": "Entebbe"}

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "a couple of": 2, "couple": 2}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], start=1)}


# --------------------------------------------------------------------------- #
# Reply helpers
# --------------------------------------------------------------------------- #

def make_reply(text, items=None, links=None, suggestions=None):
    return {"text": text, "items": items or [], "links": links or [], "suggestions": suggestions or []}


def link(label, url):
    return {"label": label, "url": url}


def ksh(amount):
    return f"KSh {amount:,.0f}"


def time12(hhmm):
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p")


def nice_date(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %d %b")


def has_words(text, *words):
    """True if any of the words/phrases appears as a whole word in text."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


# --------------------------------------------------------------------------- #
# Step 1: understand the message
# --------------------------------------------------------------------------- #

def known_cities(db):
    """Canonical city names (sample airports plus any city an admin has added)."""
    rows = db.execute("SELECT departure_city AS c FROM flights UNION SELECT destination_city FROM flights").fetchall()
    return sorted(set(AIRPORTS) | {r["c"] for r in rows})


def city_lookup(cities):
    """Map every lowercase name, code and alias to its canonical city."""
    lookup = {c.lower(): c for c in cities}
    for city, airport in AIRPORTS.items():
        code = re.search(r"\(([A-Z]{3})\)", airport)
        if code:
            lookup[code.group(1).lower()] = city
    for alias, city in CITY_ALIASES.items():
        if city in cities:
            lookup[alias] = city
    return lookup


def find_cities(text, cities):
    """Return [(position, city, role)] for every city mentioned, in order.

    role is 'from', 'to' or None, read from the word just before the city.
    Misspelt city names (e.g. 'mombsa') are matched with difflib.
    """
    lookup = city_lookup(cities)
    found = []
    taken = [False] * len(text)
    # Longest names first, so "dar es salaam" wins over "dar".
    for name in sorted(lookup, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(name)}\b", text):
            if not any(taken[m.start():m.end()]):
                found.append((m.start(), lookup[name]))
                for i in range(m.start(), m.end()):
                    taken[i] = True
    # Fuzzy match the remaining longer words against full city names.
    full_names = [c.lower() for c in cities]
    for m in re.finditer(r"[a-z]{5,}", text):
        if any(taken[m.start():m.end()]):
            continue
        close = get_close_matches(m.group(), full_names, n=1, cutoff=0.8)
        if close:
            found.append((m.start(), lookup[close[0]]))
    found.sort()
    result = []
    for pos, city in found:
        before = text[max(0, pos - 14):pos]
        role = "from" if re.search(r"\bfrom\s*$|\bleaving\s*$|\bdeparting\s*$", before) else (
            "to" if re.search(r"\b(to|for|into|towards|visit|visiting)\s*$", before) else None)
        result.append((pos, city, role))
    return result


def route_from(mentions):
    """Work out (origin, destination) from the cities mentioned."""
    if len(mentions) >= 2 and len({c for _, c, _ in mentions}) == 1:
        return mentions[0][1], mentions[0][1]  # same city twice, e.g. "Nairobi to Nairobi"
    origin = next((c for _, c, r in mentions if r == "from"), None)
    dest = next((c for _, c, r in mentions if r == "to"), None)
    for _, city, role in mentions:
        if role is None and city not in (origin, dest):
            if len(mentions) >= 2 and origin is None:
                origin = city
            elif dest is None:
                dest = city
    return origin, dest


def parse_when(text, today):
    """Find a travel date in free text. Returns a date or None."""
    if has_words(text, "day after tomorrow"):
        return today + timedelta(days=2)
    if has_words(text, "today", "tonight"):
        return today
    if has_words(text, "tomorrow", "tmrw", "tmr"):
        return today + timedelta(days=1)
    m = re.search(r"\bin (\d{1,2}|a|one|two|three|four|five|six|seven) (day|days|week|weeks)\b", text)
    if m:
        n = 1 if m.group(1) in ("a", "one") else NUMBER_WORDS.get(m.group(1)) or int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2).startswith("week") else 1))
    if has_words(text, "next week"):
        return today + timedelta(days=7)
    if has_words(text, "this weekend", "weekend"):
        return today + timedelta(days=(5 - today.weekday()) % 7)
    for i, day in enumerate(WEEKDAYS):
        if has_words(text, day, day[:3]):
            ahead = (i - today.weekday()) % 7
            if has_words(text, f"next {day}") and ahead == 0:
                ahead = 7
            return today + timedelta(days=ahead)
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)  # day/month[/year], as used in Kenya
    if m:
        return _with_year(today, int(m.group(1)), int(m.group(2)), m.group(3))
    month_names = "|".join(MONTHS)
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_names})[a-z]*\b", text) or \
        re.search(rf"\b({month_names})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text)
    if m:
        a, b = m.groups()
        day, month = (int(a), MONTHS[b[:3]]) if a.isdigit() else (int(b), MONTHS[a[:3]])
        return _with_year(today, day, month, None)
    return None


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _with_year(today, day, month, year):
    if year:
        year = int(year) + (2000 if len(year) == 2 else 0)
        return _safe_date(year, month, day)
    found = _safe_date(today.year, month, day)
    if found and found < today:  # "5 Jan" in November means next January
        found = _safe_date(today.year + 1, month, day)
    return found


def find_reference(message):
    """A booking reference such as SLDEMO01 or slw3wv9b.

    Ordinary 8-letter words starting with "sl" (e.g. "sleeping") are ignored unless they
    contain a digit or are typed in capitals, as references usually are.
    """
    for m in REFERENCE_PATTERN.finditer(message):
        candidate = m.group()
        if any(ch.isdigit() for ch in candidate) or candidate.isupper():
            return candidate.upper()
    return None


def parse_passengers(text):
    m = re.search(r"\b(\d{1,2})\s*(?:people|persons|passengers|pax|adults|travell?ers|tickets|seats|of us)\b", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\bfor (\d{1,2})\b(?!\s*(?:am|pm|:|/|-|st|nd|rd|th))", text)
    if m:
        return int(m.group(1))
    for word, n in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s+(?:people|persons|passengers|adults|travell?ers|tickets|seats|of us)\b", text):
            return n
    if has_words(text, "family of four"):
        return 4
    return None


def parse_class(text):
    if has_words(text, "first class", "1st class"):
        return "First Class"
    if has_words(text, "business"):
        return "Business"
    if has_words(text, "economy", "coach"):
        return "Economy"
    return None


# --------------------------------------------------------------------------- #
# Step 3: answers that need the database
# --------------------------------------------------------------------------- #

def departed(row, now):
    return datetime.strptime(f"{row['departure_date']} {row['departure_time']}", "%Y-%m-%d %H:%M") <= now


def search_flights(db, origin, dest, start, end, travel_class, passengers, cheapest, now):
    sql = """SELECT f.flight_id, f.flight_number, f.departure_city, f.destination_city, f.departure_date,
                    f.departure_time, f.arrival_time, fa.travel_class, fa.ticket_price, fa.available_seats
             FROM flights f JOIN flight_fares fa ON fa.flight_id = f.flight_id
             WHERE fa.travel_class = ? AND fa.available_seats >= ? AND f.departure_date BETWEEN ? AND ?"""
    params = [travel_class, passengers, start.isoformat(), end.isoformat()]
    if origin:
        sql += " AND f.departure_city = ?"
        params.append(origin)
    if dest:
        sql += " AND f.destination_city = ?"
        params.append(dest)
    sql += (" ORDER BY fa.ticket_price, f.departure_date, f.departure_time" if cheapest
            else " ORDER BY f.departure_date, f.departure_time")
    rows = db.execute(sql + " LIMIT 40", params).fetchall()
    return [r for r in rows if not departed(r, now)][:MAX_FLIGHTS_SHOWN]


def route_exists(db, origin, dest):
    sql, params = "SELECT 1 FROM flights WHERE departure_date >= ?", [date.today().isoformat()]
    if origin:
        sql += " AND departure_city = ?"
        params.append(origin)
    if dest:
        sql += " AND destination_city = ?"
        params.append(dest)
    return db.execute(sql + " LIMIT 1", params).fetchone() is not None


def last_scheduled_date(db, origin, dest):
    sql, params = "SELECT MAX(departure_date) AS last FROM flights WHERE 1 = 1", []
    if origin:
        sql += " AND departure_city = ?"
        params.append(origin)
    if dest:
        sql += " AND destination_city = ?"
        params.append(dest)
    return db.execute(sql, params).fetchone()["last"]


def destinations_from(db, origin):
    rows = db.execute("SELECT DISTINCT destination_city FROM flights WHERE departure_city = ? AND departure_date >= ? "
                      "ORDER BY destination_city", (origin, date.today().isoformat())).fetchall()
    return [r["destination_city"] for r in rows]


def flight_answer(db, policy, origin, dest, when, travel_class, passengers, cheapest, now):
    travel_class = travel_class or "Economy"
    passengers = passengers or 1
    today = now.date()

    if passengers > policy["max_passengers"]:
        return make_reply(f"A single booking can have up to {policy['max_passengers']} passengers. "
                          "For a bigger group, please make two bookings or contact our office.",
                          links=[link("Contact us", url_for("contact"))])
    if origin and dest and origin == dest:
        return make_reply("The departure and destination cities are the same. Where would you like to fly to?",
                          suggestions=[f"Flights from {origin} to {c}" for c in destinations_from(db, origin)[:3]])
    if not origin and not dest and not cheapest:
        return make_reply("Sure! Where would you like to fly from and to? For example: "
                          "\"Nairobi to Kisumu on Friday for 2 people\".",
                          suggestions=["Nairobi to Mombasa tomorrow", "Flights to Kigali", "Where do you fly?"])
    if when and when < today:
        return make_reply("That date is in the past. Which upcoming date would you like to travel?",
                          suggestions=["Tomorrow", "This weekend", "Next week"])
    if not route_exists(db, origin, dest):
        if origin and dest:
            options = destinations_from(db, origin)
            text = f"We don't have flights from {origin} to {dest}."
            if options:
                text += f" From {origin} we fly to: {', '.join(options)}."
            return make_reply(text, suggestions=[f"Flights from {origin} to {c}" for c in options[:3]])
        city = origin or dest
        return make_reply(f"We don't currently have upcoming flights {'from' if origin else 'to'} {city}.",
                          suggestions=["Where do you fly?"])

    start, end = (when, when) if when else (today, today + timedelta(days=SEARCH_WINDOW_DAYS))
    flights = search_flights(db, origin, dest, start, end, travel_class, passengers, cheapest, now)
    note = ""
    if not flights and when:
        # Nothing on that day: look at the week around it.
        flights = search_flights(db, origin, dest, max(today, when - timedelta(days=3)), when + timedelta(days=7),
                                 travel_class, passengers, cheapest, now)
        note = f"There are no {travel_class} flights with {passengers} free seat(s) on {nice_date(when.isoformat())}. "
        if flights:
            note += "Here are the nearest alternatives:"
    route = f"{origin or 'any city'} to {dest or 'any destination'}" if origin or dest else "all our routes"
    if not flights:
        last = last_scheduled_date(db, origin, dest)
        if when and last and when.isoformat() > last:
            text = (f"Our published schedule for {route} currently runs until {nice_date(last)}, so I can't "
                    f"show flights for {nice_date(when.isoformat())} yet. Please check again closer to the date.")
        else:
            text = (f"I couldn't find {travel_class} flights from {route} with {passengers} free seat(s) "
                    + (f"around {nice_date(when.isoformat())}." if when else f"in the next {SEARCH_WINDOW_DAYS} days."))
        return make_reply(text, suggestions=(["Economy instead"] if travel_class != "Economy" else [])
                          + ["Other dates", "Contact us"])

    if not note:
        what = "Cheapest" if cheapest else "Available"
        when_text = f"on {nice_date(when.isoformat())}" if when else f"in the next {SEARCH_WINDOW_DAYS} days"
        note = f"{what} {travel_class} flights {'on' if route == 'all our routes' else 'from'} {route} {when_text}:"
    items, links = [], []
    for f in flights:
        items.append(f"{f['flight_number']} · {f['departure_city']} → {f['destination_city']} · "
                     f"{nice_date(f['departure_date'])}, {time12(f['departure_time'])}–{time12(f['arrival_time'])} · "
                     f"{ksh(f['ticket_price'])} per person · {f['available_seats']} seats left")
        links.append(link(f"Book {f['flight_number']}", url_for("booking", flight_id=f["flight_id"],
                                                               travel_class=travel_class, passengers=passengers)))
    text = note
    pct = next((p for n, p in policy["group_discounts"] if passengers >= n), 0)
    if pct:
        text += f"\nGood news: groups of {passengers} get {pct}% off the total, applied automatically."
    elif passengers > 1:
        best = flights[0]["ticket_price"] * passengers
        text += f"\nFor {passengers} passengers the first option costs {ksh(best)} in total."
    first = flights[0]
    links.append(link("Open full search", url_for(
        "search_flights", **{"from": first["departure_city"], "to": first["destination_city"],
                             "departure_date": first["departure_date"], "passengers": passengers,
                             "travel_class": travel_class})))
    return make_reply(text, items=items, links=links,
                      suggestions=["How do I book?", "Baggage allowance", "Cancellation policy"])


def booking_status_answer(db, policy, reference, customer_id):
    reference = reference.upper()
    if not customer_id:
        return make_reply(f"To protect your personal details, please sign in to see booking {reference}. "
                          "Once signed in, ask me again or open My Bookings.",
                          links=[link("Sign in", url_for("customer_login", next=url_for("my_bookings")))])
    row = db.execute(
        """SELECT b.booking_reference, b.booking_status, b.number_of_passengers, b.travel_class, b.total_cost,
                  f.flight_number, f.departure_city, f.destination_city, f.departure_date, f.departure_time
           FROM bookings b JOIN flights f ON f.flight_id = b.flight_id
           WHERE b.booking_reference = ? AND b.customer_id = ?""", (reference, customer_id)).fetchone()
    if row is None:
        # Same answer whether the booking does not exist or belongs to someone else.
        return make_reply(f"I couldn't find booking {reference} on your account. If you booked at our office "
                          "or before you had an account, add it on My Bookings using the reference and the "
                          "email used for the booking.", links=[link("My Bookings", url_for("my_bookings"))])
    status_help = {
        "Confirmed": "Your seats are confirmed. Remember to bring your ID or passport.",
        "Pending": "Your seats are reserved. Please pay at any SkyLink Airlines office to confirm the booking.",
        "Cancelled": "This booking was cancelled and its seats were released.",
    }[row["booking_status"]]
    return make_reply(
        f"Booking {row['booking_reference']} is {row['booking_status']}. {status_help}",
        items=[f"Flight {row['flight_number']}: {row['departure_city']} → {row['destination_city']}",
               f"Departs {nice_date(row['departure_date'])} at {time12(row['departure_time'])}",
               f"{row['number_of_passengers']} passenger(s), {row['travel_class']}, total {ksh(row['total_cost'])}"],
        links=[link("View / print e-ticket", url_for("booking_confirmation", reference=row["booking_reference"]))],
        suggestions=["Cancellation policy", "Check-in times"])


def my_bookings_answer(db, customer_id, now):
    if not customer_id:
        return make_reply("Please sign in to see your bookings. Bookings are linked to your SkyLink account.",
                          links=[link("Sign in", url_for("customer_login", next=url_for("my_bookings"))),
                                 link("Create an account", url_for("customer_register"))])
    rows = db.execute(
        """SELECT b.booking_reference, b.booking_status, f.departure_city, f.destination_city,
                  f.departure_date, f.departure_time
           FROM bookings b JOIN flights f ON f.flight_id = b.flight_id
           WHERE b.customer_id = ? AND f.departure_date >= ?
           ORDER BY f.departure_date, f.departure_time""", (customer_id, now.date().isoformat())).fetchall()
    upcoming = [r for r in rows if not departed(r, now)]
    if not upcoming:
        return make_reply("You have no upcoming bookings. Would you like to search for a flight?",
                          links=[link("My Bookings", url_for("my_bookings"))],
                          suggestions=["Cheapest flight to Mombasa", "Where do you fly?"])
    items = [f"{r['booking_reference']} · {r['departure_city']} → {r['destination_city']} · "
             f"{nice_date(r['departure_date'])} {time12(r['departure_time'])} · {r['booking_status']}"
             for r in upcoming[:MAX_FLIGHTS_SHOWN]]
    return make_reply(f"You have {len(upcoming)} upcoming booking(s):", items=items,
                      links=[link("My Bookings", url_for("my_bookings"))],
                      suggestions=[f"Status of {upcoming[0]['booking_reference']}", "Cancellation policy"])


def destinations_answer(db):
    today = date.today().isoformat()
    rows = db.execute("SELECT DISTINCT destination_city, destination_airport FROM flights WHERE departure_date >= ? "
                      "ORDER BY destination_city", (today,)).fetchall()
    domestic = {"Nairobi", "Mombasa", "Kisumu", "Eldoret", "Malindi", "Lamu"}
    local = [r["destination_city"] for r in rows if r["destination_city"] in domestic]
    abroad = [r["destination_city"] for r in rows if r["destination_city"] not in domestic]
    items = []
    if local:
        items.append("Within Kenya: " + ", ".join(local))
    if abroad:
        items.append("International: " + ", ".join(abroad))
    return make_reply("Here's where SkyLink flies:", items=items,
                      links=[link("Browse upcoming flights", url_for("book_flight"))],
                      suggestions=["Cheapest flight to Zanzibar", "Flights to Dubai next week"])


# --------------------------------------------------------------------------- #
# Help topics (FAQ). Each topic: trigger words and a function building the reply.
# --------------------------------------------------------------------------- #

def _faq_topics(policy):
    hours = policy["cancellation_hours"]
    discounts = " and ".join(f"{p}% off for {n}+ passengers" for n, p in reversed(policy["group_discounts"]))
    return [
        ("baggage", ["baggage", "luggage", "bag", "bags", "suitcase", "kg", "weight", "allowance"],
         lambda: make_reply("Checked baggage allowance by travel class:",
                            items=["Economy: 23 kg", "Business: 2 × 32 kg", "First Class: 3 × 32 kg"],
                            suggestions=["Travel classes", "Check-in times"])),
        ("cancel", ["cancel", "cancellation", "cancelling", "refund", "money back"],
         lambda: make_reply(f"You can cancel a booking online up to {hours} hours before departure: open My "
                            "Bookings, choose the booking and press Cancel booking. The seats are released and the "
                            "booking stays on record as Cancelled. Closer to departure, or for refunds, please "
                            "contact our office.",
                            links=[link("My Bookings", url_for("my_bookings")), link("Contact us", url_for("contact"))])),
        ("change", ["change", "reschedule", "modify", "amend", "move my", "different date", "rebook"],
         lambda: make_reply("Bookings can't be changed online yet. You can cancel the booking (up to "
                            f"{hours} hours before departure) and make a new one, or contact our office "
                            "and an agent will help you.",
                            links=[link("My Bookings", url_for("my_bookings")), link("Contact us", url_for("contact"))])),
        ("payment", ["pay", "payment", "paying", "mpesa", "m-pesa", "card", "visa", "cash", "price paid"],
         lambda: make_reply("At the moment you pay at any SkyLink Airlines office. Your seats are reserved "
                            "straight away and the booking shows as Pending until it is paid, then Confirmed. "
                            "M-Pesa payment is coming soon. We never ask for card details on this website.",
                            suggestions=["Booking status meaning", "How do I book?"])),
        ("status", ["pending", "confirmed", "status mean", "booking status"],
         lambda: make_reply("What the booking statuses mean:",
                            items=["Pending: seats reserved, waiting for payment at our office",
                                   "Confirmed: paid and confirmed",
                                   "Cancelled: cancelled, seats released"],
                            suggestions=["How do I pay?", "My bookings"])),
        ("book", ["how do i book", "how to book", "make a booking", "booking process", "reserve", "buy a ticket",
                  "book a flight", "how can i book"],
         lambda: make_reply("Booking takes a couple of minutes:",
                            items=["Sign in, or create a free account",
                                   "Search for your route and date",
                                   "Press Book Now on the flight you want",
                                   "Enter the lead passenger's details and number of passengers",
                                   "Confirm, then view or print your e-ticket"],
                            links=[link("Search flights", url_for("search_flights")),
                                   link("Create an account", url_for("customer_register"))])),
        ("account", ["account", "sign in", "signin", "log in", "login", "register", "sign up", "password"],
         lambda: make_reply("You need a free SkyLink account to book and to manage your bookings. "
                            "If you forgot your password, please contact our office.",
                            links=[link("Sign in", url_for("customer_login")),
                                   link("Create an account", url_for("customer_register"))])),
        ("group", ["group", "discount", "offer", "deal", "promo", "promotion", "family", "cheaper"],
         lambda: make_reply(f"Group discounts are applied automatically: {discounts} on a single booking. "
                            f"One booking can include up to {policy['max_passengers']} passengers.",
                            suggestions=["Cheapest flight to Mombasa", "Nairobi to Kisumu for 4 people"])),
        ("checkin", ["check in", "check-in", "checkin", "arrive", "arrival time at the airport", "how early",
                     "airport time", "boarding"],
         lambda: make_reply("Please arrive at the airport at least 2 hours before departure for domestic "
                            "flights and 3 hours before for international flights, with a valid ID or passport.",
                            suggestions=["Baggage allowance", "Documents needed"])),
        ("documents", ["id", "passport", "document", "documents", "visa requirement", "identification"],
         lambda: make_reply("You need the lead passenger's national ID or passport number to book, and every "
                            "passenger must carry a valid ID (domestic) or passport (international) when travelling. "
                            "Check the entry requirements of your destination country before you fly.",
                            suggestions=["Check-in times"])),
        ("classes", ["class", "classes", "business", "first class", "economy", "lounge", "seat type"],
         lambda: make_reply("Our travel classes:",
                            items=["Economy: 23 kg baggage, complimentary snack, standard seat",
                                   "Business: 2 × 32 kg baggage, lounge access, priority boarding",
                                   "First Class (international routes): 3 × 32 kg baggage, lie-flat seat, "
                                   "chauffeur service"],
                            suggestions=["Business class to Dubai", "Baggage allowance"])),
        ("ticket", ["e-ticket", "eticket", "print", "ticket copy", "receipt", "confirmation"],
         lambda: make_reply("Your e-ticket is in My Bookings: open the booking and press Print Ticket. "
                            "It shows your booking reference, flight details and status.",
                            links=[link("My Bookings", url_for("my_bookings"))])),
        ("contact", ["contact", "phone", "call", "email", "office", "human", "agent", "customer care",
                     "speak", "support", "complain", "complaint"],
         lambda: make_reply("Our customer care team is available 24 hours a day, 7 days a week:",
                            items=["Phone: +254 700 000 000", "Email: support@skylink.example",
                                   "Office: SkyLink House, Airport North Road, Nairobi"],
                            links=[link("Send us a message", url_for("contact"))])),
        ("about", ["who are you", "about skylink", "about you", "company", "airline history"],
         lambda: make_reply("SkyLink Airlines is a Nairobi-based airline flying across Kenya, East Africa and "
                            "the Middle East. I'm the SkyLink Assistant: I can find flights and fares, check your "
                            "bookings and answer questions about baggage, payment and cancellations.",
                            links=[link("About us", url_for("about"))])),
    ]


def faq_answer(text, policy):
    """Pick the help topic whose trigger words match best."""
    best, best_score = None, 0
    for _name, words, build in _faq_topics(policy):
        score = sum(len(w) for w in words if has_words(text, w))
        if score > best_score:
            best, best_score = build, score
    return best() if best else None


# --------------------------------------------------------------------------- #
# Step 2: decide what the user wants
# --------------------------------------------------------------------------- #

def welcome():
    return make_reply("Hi! I'm the SkyLink Assistant. I can find flights and fares, check your bookings, "
                      "and answer questions about baggage, payment and cancellations. How can I help?",
                      suggestions=WELCOME_SUGGESTIONS)


def respond(message, db, policy, customer_id=None, memory=None, now=None):
    """Answer one chat message.

    policy: dict with cancellation_hours, group_discounts [(min_pax, pct)], max_passengers.
    memory: small dict from the previous turn ({"origin", "dest", "when", "class", "passengers"}) so that
            follow-ups like "what about tomorrow?" keep the same route.
    Returns (reply dict, new memory dict).
    """
    now = now or datetime.now()
    memory = dict(memory or {})
    text = " ".join((message or "").lower().split())[:MAX_MESSAGE_LENGTH]
    if not text:
        return welcome(), memory

    ref = find_reference(message or "")
    if ref:
        return booking_status_answer(db, policy, ref, customer_id), memory
    asks_how_to = has_words(text, "cancel", "change", "reschedule", "refund", "print", "pay", "payment")
    if not asks_how_to and has_words(text, "my booking", "my bookings", "my trips", "my trip", "my tickets",
                                     "my flights", "my reservation", "my reservations"):
        return my_bookings_answer(db, customer_id, now), memory
    if has_words(text, "where do you fly", "destinations", "which cities", "where can i fly", "routes",
                 "where do you go", "places you fly"):
        return destinations_answer(db), memory

    mentions = find_cities(text, known_cities(db))
    when = parse_when(text, now.date())
    travel_class = parse_class(text)
    passengers = parse_passengers(text)
    cheapest = has_words(text, "cheapest", "cheap", "lowest", "best price", "budget", "affordable")
    flight_words = has_words(text, "flight", "flights", "fly", "flying", "travel", "trip", "go to", "ticket",
                             "tickets", "fare", "fares", "price", "prices", "cost", "how much", "available",
                             "schedule", "seats")

    if mentions:
        origin, dest = route_from(mentions)
        # A new route starts a fresh search; only follow-ups reuse the previous class and group size.
        memory = {"origin": origin, "dest": dest, "class": travel_class, "passengers": passengers,
                  "when": when.isoformat() if when else None}
        return flight_answer(db, policy, origin, dest, when, memory["class"], memory["passengers"],
                             cheapest, now), memory

    # A follow-up that changes only the date, class or group size of the last search.
    is_follow_up = (memory.get("origin") or memory.get("dest")) and (when or passengers or (
        travel_class and not has_words(text, "what is", "difference", "lounge", "classes")) or
        has_words(text, "what about", "how about", "instead", "other dates", "later"))
    if is_follow_up:
        if not when and memory.get("when") and not has_words(text, "other dates", "any date", "any day", "later"):
            when = date.fromisoformat(memory["when"])  # keep the date of the previous search
        memory.update({"class": travel_class or memory.get("class"), "passengers": passengers or memory.get("passengers"),
                       "when": when.isoformat() if when else None})
        return flight_answer(db, policy, memory.get("origin"), memory.get("dest"), when, memory["class"],
                             memory["passengers"], cheapest, now), memory

    faq = faq_answer(text, policy)
    if faq:
        return faq, memory
    if flight_words or cheapest:
        return flight_answer(db, policy, None, None, when, travel_class, passengers, cheapest, now), memory
    if has_words(text, "hi", "hello", "hey", "habari", "jambo", "good morning", "good afternoon", "good evening",
                 "start", "help", "menu"):
        return welcome(), memory
    if has_words(text, "thanks", "thank you", "asante", "great", "perfect", "ok", "okay", "cool"):
        return make_reply("You're welcome! Is there anything else I can help you with?",
                          suggestions=WELCOME_SUGGESTIONS[:2]), memory
    if has_words(text, "bye", "goodbye", "see you", "kwaheri"):
        return make_reply("Goodbye, and have a pleasant flight with SkyLink!"), memory
    return make_reply("Sorry, I didn't quite get that. I can help with flight searches (e.g. \"Nairobi to "
                      "Mombasa on Friday\"), your bookings, baggage, payment, cancellations and check-in. "
                      "You can also reach our team directly.",
                      links=[link("Contact us", url_for("contact"))],
                      suggestions=WELCOME_SUGGESTIONS), memory

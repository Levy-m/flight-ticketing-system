"""
Automated tests for the SkyLink Flight Ticketing System.

Each test uses a fresh temporary database, so the real flight_ticketing.db is never touched.
Run from the project folder:   python -m unittest discover -s tests -v
"""

import os
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "skylink_import_check.db"))
from app import create_app  # noqa: E402
import database  # noqa: E402


class FlightTicketingTestCase(unittest.TestCase):

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.app = create_app({
            "TESTING": True,
            "DATABASE": self.db_path,
            "SECRET_KEY": "test-secret-key",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "TestPass123",
            "SEED_MOCK_TRAFFIC": False,  # keeps each test's fresh database small and fast
        })
        self.client = self.app.test_client()
        # Prime a CSRF token in the session (as the browser would after loading any form).
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        self._conns = []

    def tearDown(self):
        for conn in self._conns:
            conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    # ---------------- helpers ----------------
    def db(self):
        conn = database.connect(self.db_path)
        self._conns.append(conn)
        return conn

    def post(self, url, data=None, **kwargs):
        data = dict(data or {})
        data.setdefault("csrf_token", "test-token")
        return self.client.post(url, data=data, **kwargs)

    def flight(self, origin="Nairobi", dest="Mombasa", days=5):
        flight_date = (date.today() + timedelta(days=days)).isoformat()
        return self.db().execute(
            "SELECT * FROM flights WHERE departure_city=? AND destination_city=? AND departure_date=?",
            (origin, dest, flight_date)).fetchone()

    def seats(self, flight_id, travel_class="Economy"):
        return self.db().execute(
            "SELECT available_seats FROM flight_fares WHERE flight_id=? AND travel_class=?",
            (flight_id, travel_class)).fetchone()[0]

    def booking_data(self, **overrides):
        data = {"full_name": "Test Passenger", "email": "test.passenger@example.com",
                "phone": "+254 700 123 456", "identification_number": "12345678",
                "travel_class": "Economy", "passengers": "2", "payment": "office", "terms": "1"}
        data.update(overrides)
        return data

    def login(self):
        return self.post("/admin/login", {"username": "admin", "password": "TestPass123"})

    # ---------------- 1. search ----------------
    def test_search_existing_flight_shows_results(self):
        d = (date.today() + timedelta(days=5)).isoformat()
        r = self.client.get(f"/search?from=Nairobi&to=Mombasa&departure_date={d}&passengers=1&travel_class=Economy")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"flight found", r.data)
        self.assertIn(b"Book Now", r.data)

    def test_search_nonexistent_flight_shows_message(self):
        d = (date.today() + timedelta(days=5)).isoformat()
        r = self.client.get(f"/search?from=Nairobi&to=Atlantis&departure_date={d}&passengers=1&travel_class=Economy")
        self.assertIn(b"No flights available", r.data)

    def test_search_rejects_past_date(self):
        d = (date.today() - timedelta(days=1)).isoformat()
        r = self.client.get(f"/search?from=Nairobi&to=Mombasa&departure_date={d}&passengers=1&travel_class=Economy")
        self.assertIn(b"cannot be in the past", r.data)

    # ---------------- 2. booking validation ----------------
    def test_incomplete_booking_form_shows_errors(self):
        f = self.flight()
        r = self.post(f"/book/{f['flight_id']}", {"travel_class": "Economy", "passengers": "1"})
        self.assertIn(b"Full name is required", r.data)
        self.assertIn(b"Email address is required", r.data)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 3)  # only demo data

    def test_invalid_email_is_rejected(self):
        f = self.flight()
        before = self.seats(f["flight_id"])
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(email="not-an-email"))
        self.assertIn(b"Enter a valid email address", r.data)
        self.assertEqual(self.seats(f["flight_id"]), before)

    def test_book_available_flight_generates_reference(self):
        f = self.flight()
        before = self.seats(f["flight_id"])
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(), follow_redirects=True)
        self.assertIn(b"Booking received", r.data)
        ref = re.search(rb"booking reference is (SL[A-Z0-9]{6})", r.data).group(1).decode()
        row = self.db().execute("SELECT * FROM bookings WHERE booking_reference=?", (ref,)).fetchone()
        self.assertEqual(row["number_of_passengers"], 2)
        self.assertEqual(row["total_cost"], 8500 * 2)          # total = price x passengers
        self.assertEqual(row["booking_status"], "Pending")      # pay at office until M-Pesa is live
        self.assertEqual(self.seats(f["flight_id"]), before - 2)

    def test_mpesa_payment_is_not_selectable_yet(self):
        f = self.flight()
        before = self.seats(f["flight_id"])
        r = self.client.get(f"/book/{f['flight_id']}")
        self.assertIn(b'value="mpesa" disabled', r.data)
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(payment="mpesa"))
        self.assertIn(b"M-Pesa payments are not available yet", r.data)
        self.assertEqual(self.seats(f["flight_id"]), before)

    def test_group_booking_gets_discount(self):
        f = self.flight()
        for pax, expected in [("3", 8500 * 3), ("4", 8500 * 4 * 95 // 100), ("6", 8500 * 6 * 90 // 100)]:
            r = self.post(f"/book/{f['flight_id']}", self.booking_data(passengers=pax), follow_redirects=True)
            ref = re.search(rb"booking reference is (SL[A-Z0-9]{6})", r.data).group(1).decode()
            row = self.db().execute("SELECT * FROM bookings WHERE booking_reference=?", (ref,)).fetchone()
            self.assertEqual(row["total_cost"], expected)
            self.assertEqual(row["price_per_ticket"], 8500)

    def test_exceeding_seat_capacity_is_rejected(self):
        f = self.flight("Nairobi", "Kisumu", 3)  # seeded with only 3 Economy seats
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(passengers="4"))
        self.assertIn(b"Only 3 Economy seat(s)", r.data)
        self.assertEqual(self.seats(f["flight_id"]), 3)

    def test_booking_nonexistent_flight_redirects(self):
        r = self.post("/book/999999", self.booking_data(), follow_redirects=True)
        self.assertIn(b"does not exist", r.data)

    # ---------------- 3. my bookings ----------------
    def test_retrieve_booking_with_valid_details(self):
        r = self.post("/my-bookings", {"reference": "SLDEMO01", "email": "jane.wanjiru@example.com"})
        self.assertIn(b"JANE WANJIRU", r.data)
        self.assertIn(b"Confirmed", r.data)

    def test_retrieve_booking_with_wrong_email_discloses_nothing(self):
        r = self.post("/my-bookings", {"reference": "SLDEMO01", "email": "attacker@example.com"})
        self.assertIn(b"No booking matches", r.data)
        self.assertNotIn(b"JANE WANJIRU", r.data)

    def test_confirmation_page_requires_verification(self):
        r = self.client.get("/booking/SLDEMO01")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/my-bookings", r.headers["Location"])

    def test_cancel_eligible_booking(self):
        f = self.flight()
        before = self.seats(f["flight_id"])
        r = self.post("/my-bookings/cancel", {"reference": "SLDEMO01", "email": "jane.wanjiru@example.com"},
                      follow_redirects=True)
        self.assertIn(b"has been cancelled", r.data)
        row = self.db().execute("SELECT booking_status FROM bookings WHERE booking_reference='SLDEMO01'").fetchone()
        self.assertEqual(row["booking_status"], "Cancelled")        # status updated, row not deleted
        self.assertEqual(self.seats(f["flight_id"]), before + 2)     # seats released

    def test_my_bookings_lists_bookings_made_this_session(self):
        r = self.client.get("/my-bookings")
        self.assertNotIn(b"Your bookings", r.data)
        f = self.flight()
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(), follow_redirects=True)
        ref = re.search(rb"booking reference is (SL[A-Z0-9]{6})", r.data).group(1)
        r = self.client.get("/my-bookings")
        self.assertIn(b"Your bookings", r.data)
        self.assertIn(ref, r.data)
        self.assertNotIn(b"/booking/SLDEMO01", r.data)  # other people's bookings are never listed

    def test_cancel_without_email_works_only_for_own_session_booking(self):
        r = self.post("/my-bookings/cancel", {"reference": "SLDEMO01"}, follow_redirects=True)
        self.assertIn(b"No booking matches", r.data)
        f = self.flight()
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(), follow_redirects=True)
        ref = re.search(rb"booking reference is (SL[A-Z0-9]{6})", r.data).group(1).decode()
        r = self.post("/my-bookings/cancel", {"reference": ref}, follow_redirects=True)
        self.assertIn(b"has been cancelled", r.data)

    # ---------------- 3b. customer accounts ----------------
    def register(self, email="mary.atieno@example.com"):
        return self.post("/account/register", {"full_name": "Mary Atieno", "email": email,
                                               "phone": "+254 711 222 333", "password": "Secret123",
                                               "confirm_password": "Secret123"}, follow_redirects=True)

    def test_customer_bookings_follow_the_account_across_sessions(self):
        self.assertIn(b"Welcome, Mary Atieno", self.register().data)
        f = self.flight()
        r = self.client.get(f"/book/{f['flight_id']}")
        self.assertIn(b'value="mary.atieno@example.com"', r.data)  # booking form is prefilled
        r = self.post(f"/book/{f['flight_id']}", self.booking_data(email="mary.atieno@example.com"),
                      follow_redirects=True)
        ref = re.search(rb"booking reference is (SL[A-Z0-9]{6})", r.data).group(1)
        self.post("/account/logout")
        self.assertNotIn(ref, self.client.get("/my-bookings").data)
        r = self.post("/account/login", {"email": "MARY.ATIENO@example.com", "password": "Secret123"},
                      follow_redirects=True)
        self.assertIn(b"Welcome back", r.data)
        self.assertIn(ref, r.data)
        self.assertEqual(self.client.get(f"/booking/{ref.decode()}").status_code, 200)

    def test_registering_with_someone_elses_email_does_not_expose_their_bookings(self):
        r = self.register(email="jane.wanjiru@example.com")  # owner of SLDEMO01
        self.assertNotIn(b"/booking/SLDEMO01", r.data)
        self.assertEqual(self.client.get("/booking/SLDEMO01").status_code, 302)

    def test_duplicate_registration_and_wrong_password_rejected(self):
        self.register()
        self.post("/account/logout")
        self.assertIn(b"already exists", self.register().data)
        r = self.post("/account/login", {"email": "mary.atieno@example.com", "password": "nope"})
        self.assertIn(b"Invalid email or password", r.data)

    # ---------------- 4. admin ----------------
    def test_admin_pages_require_login(self):
        for url in ["/admin", "/admin/flights", "/admin/flights/add", "/admin/bookings"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/admin/login", r.headers["Location"])
        r = self.post("/admin/flights/1/delete")
        self.assertIn("/admin/login", r.headers["Location"])

    def login_agent(self):
        return self.post("/admin/login", {"username": "agent", "password": "Agent@2026"})

    def test_agent_can_manage_bookings_but_not_flights_or_staff(self):
        self.login_agent()
        for url in ["/admin", "/admin/flights", "/admin/bookings", "/admin/messages"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in ["/admin/flights/add", "/admin/staff"]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        f = self.flight()
        self.assertEqual(self.post(f"/admin/flights/{f['flight_id']}/delete").status_code, 403)
        self.assertEqual(self.post("/admin/flights/add", self.flight_form()).status_code, 403)
        self.assertNotIn(b"Add Flight", self.client.get("/admin").data)
        booking = self.db().execute("SELECT booking_id FROM bookings WHERE booking_reference='SLDEMO02'").fetchone()
        r = self.post(f"/admin/bookings/{booking['booking_id']}/status", {"action": "confirm"}, follow_redirects=True)
        self.assertIn(b"SLDEMO02 confirmed", r.data)

    def test_admin_creates_and_manages_staff(self):
        self.login()
        r = self.post("/admin/staff", {"username": "wanjiku", "password": "short", "role": "agent"})
        self.assertIn(b"at least 8 characters", r.data)
        r = self.post("/admin/staff", {"username": "wanjiku", "password": "LongEnough1", "role": "agent"},
                      follow_redirects=True)
        self.assertIn(b"account &#39;wanjiku&#39; created", r.data)
        row = self.db().execute("SELECT * FROM administrators WHERE username='wanjiku'").fetchone()
        self.assertEqual(row["role"], "agent")
        self.post(f"/admin/staff/{row['admin_id']}", {"action": "role", "role": "admin"})
        self.assertEqual(self.db().execute("SELECT role FROM administrators WHERE username='wanjiku'").fetchone()[0],
                         "admin")
        self.post(f"/admin/staff/{row['admin_id']}", {"action": "delete"})
        self.assertIsNone(self.db().execute("SELECT 1 FROM administrators WHERE username='wanjiku'").fetchone())

    def test_admin_cannot_demote_self_and_demoted_agent_loses_access_immediately(self):
        self.login()
        me = self.db().execute("SELECT admin_id FROM administrators WHERE username='admin'").fetchone()[0]
        r = self.post(f"/admin/staff/{me}", {"action": "role", "role": "agent"}, follow_redirects=True)
        self.assertIn(b"cannot change or remove your own account", r.data)
        self.db().execute("UPDATE administrators SET role='agent' WHERE admin_id=?", (me,))
        self.assertEqual(self.client.get("/admin/staff").status_code, 403)

    def test_reports_page_for_staff_only(self):
        self.assertEqual(self.client.get("/admin/reports").status_code, 302)
        self.login_agent()
        for period in ["year", "12m", "90d", "30d", "bogus"]:
            r = self.client.get(f"/admin/reports?period={period}")
            self.assertEqual(r.status_code, 200, period)
        self.assertIn(b"No bookings for flights in this period", self.client.get("/admin/reports?period=30d").data)
        # Move the demo Nairobi -> Mombasa flight (SLDEMO01, 2 passengers) into the past.
        self.db().execute("UPDATE flights SET departure_date = ? WHERE flight_id = ?",
                          ((date.today() - timedelta(days=3)).isoformat(), self.flight()["flight_id"]))
        r = self.client.get("/admin/reports?period=30d")
        self.assertIn(b"Most travelled destinations", r.data)
        self.assertIn(b'"city": "Mombasa", "passengers": 2', r.data)

    def test_mock_traffic_fills_seats_consistently(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            database.init_db(path)
            db = database.connect(path)
            # Seats sold must match the active bookings on every fare.
            mismatch = db.execute("""
                SELECT COUNT(*) FROM flight_fares fa WHERE fa.total_seats - fa.available_seats !=
                    (SELECT COALESCE(SUM(number_of_passengers), 0) FROM bookings b
                     WHERE b.flight_id = fa.flight_id AND b.travel_class = fa.travel_class
                       AND b.booking_status != 'Cancelled')
                AND fa.available_seats != 3""").fetchone()[0]
            self.assertEqual(mismatch, 0)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1000)
            db.close()
        finally:
            os.unlink(path)

    def test_admin_wrong_password_rejected(self):
        r = self.post("/admin/login", {"username": "admin", "password": "wrong"})
        self.assertIn(b"Invalid username or password", r.data)

    def test_admin_password_is_hashed(self):
        row = self.db().execute("SELECT password_hash FROM administrators").fetchone()
        self.assertNotEqual(row["password_hash"], "TestPass123")
        self.assertTrue(row["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def flight_form(self, **overrides):
        data = {"flight_number": "SL999", "airline_name": "SkyLink Airlines",
                "departure_city": "Nairobi", "departure_airport": "Jomo Kenyatta International Airport (NBO)",
                "destination_city": "Mombasa", "destination_airport": "Moi International Airport (MBA)",
                "departure_date": (date.today() + timedelta(days=20)).isoformat(),
                "departure_time": "09:00", "arrival_time": "10:15",
                "price_economy": "7000", "total_economy": "100", "available_economy": ""}
        data.update(overrides)
        return data

    def test_admin_add_flight(self):
        self.login()
        r = self.post("/admin/flights/add", self.flight_form(), follow_redirects=True)
        self.assertIn(b"Flight SL999 has been added", r.data)
        row = self.db().execute("SELECT * FROM flights WHERE flight_number='SL999'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(self.seats(row["flight_id"]), 100)

    def test_admin_duplicate_flight_number_rejected(self):
        self.login()
        r = self.post("/admin/flights/add", self.flight_form(flight_number="SL101"))
        self.assertIn(b"already exists", r.data)

    def test_admin_edit_flight(self):
        self.login()
        f = self.flight()
        r = self.post(f"/admin/flights/{f['flight_id']}/edit", self.flight_form(
            flight_number=f["flight_number"], departure_date=f["departure_date"], departure_time="08:30",
            arrival_time="09:45", price_economy="9500", total_economy="120", available_economy="100",
            price_business="22100", total_business="16", available_business="16"), follow_redirects=True)
        self.assertIn(b"has been updated", r.data)
        db = self.db()
        self.assertEqual(db.execute("SELECT departure_time FROM flights WHERE flight_id=?",
                                    (f["flight_id"],)).fetchone()[0], "08:30")
        self.assertEqual(db.execute("SELECT ticket_price FROM flight_fares WHERE flight_id=? AND travel_class='Economy'",
                                    (f["flight_id"],)).fetchone()[0], 9500)

    def test_admin_cannot_oversell_when_editing_seats(self):
        self.login()
        f = self.flight()  # has a 2-seat demo booking in Economy
        r = self.post(f"/admin/flights/{f['flight_id']}/edit", self.flight_form(
            flight_number=f["flight_number"], departure_date=f["departure_date"],
            total_economy="120", available_economy="120"))
        self.assertIn(b"cannot exceed 118", r.data)

    def test_admin_delete_eligible_flight(self):
        self.login()
        f = self.flight("Nairobi", "Eldoret", 9)
        r = self.post(f"/admin/flights/{f['flight_id']}/delete", follow_redirects=True)
        self.assertIn(b"has been deleted", r.data)
        self.assertIsNone(self.db().execute("SELECT 1 FROM flights WHERE flight_id=?", (f["flight_id"],)).fetchone())

    def test_admin_cannot_delete_flight_with_bookings(self):
        self.login()
        f = self.flight()
        r = self.post(f"/admin/flights/{f['flight_id']}/delete", follow_redirects=True)
        self.assertIn(b"cannot be deleted", r.data)
        self.assertIsNotNone(self.db().execute("SELECT 1 FROM flights WHERE flight_id=?", (f["flight_id"],)).fetchone())

    def test_admin_filter_bookings_by_status(self):
        self.login()
        r = self.client.get("/admin/bookings?status=Pending")
        self.assertIn(b"SLDEMO02", r.data)
        self.assertNotIn(b"SLDEMO01", r.data)

    # ---------------- 5. contact & security ----------------
    def test_submit_contact_message(self):
        r = self.post("/contact", {"customer_name": "Mary Achieng", "email": "mary@example.com",
                                   "subject": "Refund", "message": "How long does a refund take?"},
                      follow_redirects=True)
        self.assertIn(b"Your message has been received", r.data)
        row = self.db().execute("SELECT * FROM contact_messages WHERE email='mary@example.com'").fetchone()
        self.assertEqual(row["subject"], "Refund")

    def test_contact_validation(self):
        r = self.post("/contact", {"customer_name": "", "email": "bad", "subject": "", "message": "hi"})
        self.assertIn(b"Your name is required", r.data)
        self.assertIn(b"Enter a valid email address", r.data)

    def test_post_without_csrf_token_is_rejected(self):
        r = self.client.post("/contact", data={"customer_name": "X", "email": "x@example.com",
                                               "subject": "S", "message": "A message body"})
        self.assertEqual(r.status_code, 400)

    def test_sql_injection_in_search_is_harmless(self):
        d = (date.today() + timedelta(days=5)).isoformat()
        r = self.client.get(f"/search?from=Nairobi'%20OR%201=1--&to=Mombasa&departure_date={d}"
                            "&passengers=1&travel_class=Economy")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"No flights available", r.data)

    def test_user_input_is_escaped(self):
        self.post("/contact", {"customer_name": "<script>alert(1)</script>", "email": "x@example.com",
                               "subject": "XSS", "message": "<b>bold</b> message here"})
        self.login()
        r = self.client.get("/admin/messages")
        self.assertNotIn(b"<script>alert(1)</script>", r.data)
        self.assertIn(b"&lt;script&gt;", r.data)

    def test_all_public_pages_load(self):
        for url in ["/", "/search", "/book", "/my-bookings", "/about", "/contact", "/admin/login"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/does-not-exist").status_code, 404)


if __name__ == "__main__":
    unittest.main()

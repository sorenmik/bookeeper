import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlsplit

from bookeeper import create_app
from bookeeper.catalog import BookMetadata, LookupUnavailable, lookup_isbn, normalize_isbn
from bookeeper.dashboard import activity
from bookeeper.quotes import (
    Quote, Unavailable, _display_name, _title_variants, fallback_quote, gutenberg_sentences, needs_refresh,
    refresh_quote, wikiquote_lines,
)
from bookeeper.service import ALL_INTERFACES, HOST, Service, main
from bookeeper.version import build_version
from bookeeper.storage import Store


PASSWORD = "test-only password"


class ServiceTests(unittest.TestCase):
    def test_restart_waits_for_successful_stop(self):
        with TemporaryDirectory() as directory:
            service = Service(Path(directory))
            for result, expected in ((0, [call.stop(), call.start(HOST)]), (1, [call.stop()])):
                with self.subTest(stop_status=result):
                    actions = Mock()
                    actions.stop.return_value = result
                    actions.start.return_value = 0
                    with patch.object(service, "stop", actions.stop), patch.object(service, "start", actions.start):
                        self.assertEqual(service.restart(), result)
                    self.assertEqual(actions.mock_calls, expected)

    def test_restart_preserves_all_interfaces_listener(self):
        with TemporaryDirectory() as directory:
            service = Service(Path(directory))
            service.listen_host.write_text(ALL_INTERFACES, encoding="ascii")
            with patch.object(service, "stop", return_value=0), patch.object(service, "start", return_value=0) as start:
                self.assertEqual(service.restart(), 0)
            start.assert_called_once_with(ALL_INTERFACES)

    def test_menu_starts_on_all_interfaces(self):
        with TemporaryDirectory() as directory:
            with (
                patch("bookeeper.service.Service") as service_type,
                patch("builtins.input", return_value="4"),
                patch.dict("os.environ", {"BOOKEEPER_DATA_DIR": directory}),
                patch("sys.argv", ["service"]),
            ):
                service_type.return_value.status.return_value = "Stopped"
                service_type.return_value.start.return_value = 0
                self.assertEqual(main(), 0)
            service_type.return_value.start.assert_called_once_with(ALL_INTERFACES)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))
        self.app = create_app({"TESTING": True, "DATA_DIR": self.directory.name})
        self.client = self.app.test_client()
        self.add_user("Alice")

    def add_user(self, username):
        result = self.app.test_cli_runner().invoke(
            args=["add-user", username], input=f"{PASSWORD}\n{PASSWORD}\n"
        )
        self.assertEqual(result.exit_code, 0, result.output)

    def post(self, path, **data):
        with self.client.session_transaction() as session:
            csrf = session["csrf"]
        return self.client.post(path, data={"csrf": csrf, **data})

    def login(self, username="alice"):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.post("/login", username=username, password=PASSWORD).status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def add_storage(self, name):
        self.assertEqual(self.post("/storages", name=name).status_code, 302)
        return next(key for key, item in self.store.read()["storages"].items() if item["name"] == name)

    def add_unit(self, storage_id, kind, name, cols=1, rows=1):
        self.assertEqual(
            self.post("/units", storage_id=storage_id, kind=kind, name=name, cols=cols, rows=rows).status_code, 302
        )
        return next(key for key, item in self.store.read()["units"].items() if item["name"] == name)

    def test_household_sign_in_and_csrf(self):
        self.assertEqual(self.client.get("/").location, "/login")
        self.assertEqual(self.client.get("/locations").location, "/login")
        self.login()
        self.assertNotEqual(self.store.read()["users"]["alice"], PASSWORD)
        denied = self.client.post("/copies/new", data={"title": "Should not be saved"})
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(self.post("/copies/new", title="Shared catalog").status_code, 302)
        self.assertEqual(self.post("/logout").location, "/login")
        self.assertEqual(self.client.get("/").location, "/login")
        self.add_user("Bob")
        self.login("bob")
        self.assertIn(b"Shared catalog", self.client.get("/").data)

    def test_copies_can_share_an_isbn_and_move_independently(self):
        self.login()
        study = self.add_storage("Study")
        case = self.add_unit(study, "cabinet", "Oak case", 3, 2)
        pile = self.add_unit(study, "pile", "Reading pile")
        shelf, other_slot = f"{case}:1:1", f"{pile}:1:1"
        self.assertIn(b"Oak case", self.client.get("/locations").data)
        first = self.post("/copies/new", title="Pride and Prejudice", isbn="0-14-143951-3", location_id=shelf)
        self.assertEqual(first.status_code, 302)
        other = dict(title="Pride and Prejudice", isbn="9780141439518", location_id=other_slot)
        warning = self.post("/copies/new", **other)
        self.assertEqual(warning.status_code, 409)
        self.assertIn(b"Add another copy anyway", warning.data)
        self.assertEqual(len(self.store.read()["copies"]), 1)
        self.assertEqual(self.post("/copies/new", allow_duplicate="yes", **other).status_code, 302)
        copies = self.store.read()["copies"]
        self.assertEqual({item["location_id"] for item in copies.values()}, {shelf, other_slot})
        self.assertIn(b"2 copies", self.client.get(f"/?location={study}").data)
        result = self.client.get(f"/?location={shelf}&q=0-14-143951-3")
        self.assertIn("Study › Oak case › R1C1".encode(), result.data)
        self.assertNotIn("Study › Reading pile</a>".encode(), result.data)
        self.assertEqual(self.post("/copies/new", title="Bad", location_id=f"{case}:3:1").status_code, 400)
        first_id = next(key for key, item in copies.items() if item["location_id"] == shelf)
        self.assertEqual(self.post(f"/copies/{first_id}/edit", title=other["title"]).status_code, 302)
        self.assertEqual(self.post(f"/copies/{first_id}/delete").status_code, 302)
        self.assertEqual(len(self.store.read()["copies"]), 1)

    def test_shelf_map_search_and_bulk_move(self):
        self.login()
        kitchen = self.add_storage("Kitchen")
        cabinet = self.add_unit(kitchen, "cabinet", "Cabinet A", 5, 5)
        shelf = self.add_unit(kitchen, "shelf", "Wall shelf", 4, 3)
        self.assertEqual(self.store.read()["units"][shelf]["rows"], 1)
        self.assertEqual(self.post("/units", storage_id=kitchen, kind="shelf", name="Wall shelf").status_code, 409)
        top_left, wall = f"{cabinet}:1:1", f"{shelf}:1:2"
        self.assertEqual(self.post(f"/slots/{top_left}", name="top-left").status_code, 302)
        for title in ("Dune", "Emma", "Ulysses"):
            self.post("/copies/new", title=title, location_id=top_left)
        self.post("/copies/new", title="Loose book")
        slot_page = self.client.get(f"/slots/{top_left}")
        self.assertIn("Kitchen › Cabinet A › top-left".encode(), slot_page.data)
        self.assertIn(b"Ulysses", slot_page.data)
        highlighted = self.client.get("/locations", query_string={"slot": top_left})
        self.assertIn(b"is-target", highlighted.data)
        self.assertIn(b"1 unplaced book", highlighted.data)
        self.assertEqual(self.client.get("/locations?slot=missing").status_code, 404)
        self.assertIn(b"Dune", self.client.get("/", query_string={"q": "dnue"}).data)
        self.assertNotIn(b"Emma", self.client.get("/", query_string={"q": "dnue"}).data)
        self.assertIn(b"Loose book", self.client.get("/", query_string={"location": "unplaced"}).data)
        self.assertNotIn(b"Dune", self.client.get("/", query_string={"location": "unplaced"}).data)
        copies = self.store.read()["copies"]
        ids = {item["title"]: key for key, item in copies.items()}
        self.assertEqual(self.post("/books/move", target=wall).status_code, 400)
        self.assertEqual(self.post("/books/move", target="bad", book=ids["Dune"]).status_code, 400)
        moved = self.post("/books/move", target=wall, book=[ids["Dune"], ids["Emma"]], next=f"/slots/{top_left}")
        self.assertEqual((moved.status_code, moved.location), (302, f"/slots/{top_left}"))
        self.assertEqual(self.store.read()["copies"][ids["Dune"]]["location_id"], wall)
        self.assertEqual(self.post("/books/undo-move").status_code, 302)
        self.assertEqual(self.store.read()["copies"][ids["Dune"]]["location_id"], top_left)
        sneaky = self.post("/books/move", target=wall, book=ids["Dune"], next="/\t/evil.example")
        self.assertNotIn("evil.example", sneaky.location)
        self.assertEqual(self.post("/books/undo-move").status_code, 302)
        self.assertEqual(self.post("/books/move", target=wall, source=cabinet).status_code, 302)
        self.assertEqual({book["location_id"] for book in self.store.read()["copies"].values()}, {wall, ""})
        # Shrinking and removing units never deletes books.
        self.assertEqual(self.post(f"/units/{shelf}", name="Wall shelf", cols=1, target=top_left).status_code, 302)
        self.assertEqual(self.post(f"/units/{shelf}", name="Wall shelf", cols=1, color="#336699").status_code, 302)
        self.assertEqual(self.store.read()["units"][shelf]["color"], "#336699")
        self.assertEqual(self.post(f"/units/{shelf}", name="Wall shelf", cols=1, color="red").status_code, 400)
        self.assertEqual(self.post(f"/units/{shelf}", name="Wall shelf", cols=1, color="#336699", default_color="1").status_code, 302)
        self.assertNotIn("color", self.store.read()["units"][shelf])
        self.assertEqual(self.store.read()["copies"][ids["Dune"]]["location_id"], top_left)
        self.assertEqual(self.post(f"/units/{cabinet}/delete").status_code, 302)
        self.assertEqual({book["location_id"] for book in self.store.read()["copies"].values()}, {""})
        self.assertEqual(len(self.store.read()["copies"]), 4)

    def test_old_floor_plan_snapshot_restores_books_unplaced(self):
        self.login()
        self.assertEqual(self.post("/copies/new", title="Kept").status_code, 302)
        key = next(iter(self.store.read()["copies"]))
        old = {
            "version": 1, "reading": {}, "loans": {}, "copies": {key: {**self.store.read()["copies"][key], "location_id": "c" * 32}},
            "locations": {"c" * 32: {"id": "c" * 32, "kind": "shelf", "name": "Old", "parent_id": ""}},
        }
        restore = self.post("/backup/restore", confirm="yes", snapshot=(io.BytesIO(json.dumps(old).encode()), "old.json"))
        self.assertEqual(restore.status_code, 302)
        self.assertEqual(self.store.read()["copies"][key]["location_id"], "")

    def test_manual_entry_search_and_validation(self):
        self.login()
        details = dict(title="The Moon", author="Marta", series="Atlas", tags="space, maps")
        self.assertEqual(self.post("/copies/new", isbn="9780141439519", **details).status_code, 400)
        self.assertEqual(self.post("/copies/new", location_id="missing", **details).status_code, 400)
        self.assertFalse(self.store.read()["copies"])
        self.assertEqual(self.post("/copies/new", **details).status_code, 302)
        self.assertEqual(self.post("/copies/new", title="the  moon", author="MARTA").status_code, 409)
        for query in ("MOON", "Marta", "Atlas space", "maps"):
            with self.subTest(query=query):
                results = self.client.get("/", query_string={"q": query})
                self.assertIn(b"The Moon", results.data)

    def test_isbn_lookup_is_a_preview_and_failure_allows_manual_entry(self):
        self.login()
        details = BookMetadata("Remote title", "Remote author", genre="Classics", format="Paperback",
                               pages=336, publisher="Example Press", publication_date="2020", language="English", cover_id=123)
        with patch("bookeeper.lookup_isbn", return_value=details) as fetch:
            result = self.post("/copies/new", action="lookup", isbn="0-14-143951-3", title="My title", genre="My genre")
        fetch.assert_called_once_with("9780141439518")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'value="My title"', result.data)
        self.assertIn(b'value="Remote author"', result.data)
        self.assertIn(b'value="My genre"', result.data)
        for value in (b"Paperback", b"336", b"Example Press", b"2020", b"English", b"123"):
            self.assertIn(b'value="' + value + b'"', result.data)
        self.assertFalse(self.store.read()["copies"])
        with patch("bookeeper.lookup_isbn", side_effect=LookupUnavailable):
            unavailable = self.post("/copies/new", action="lookup", isbn="9780141439518")
        self.assertIn(b"enter the details manually", unavailable.data)
        self.assertEqual(
            self.post("/copies/new", isbn="9780141439518", title="Manual title", pages="336", language="English").status_code, 302
        )
        copy = next(iter(self.store.read()["copies"].values()))
        self.assertEqual((copy["pages"], copy["language"]), (336, "English"))
        catalog = self.client.get("/catalog")
        self.assertNotIn(b"336 pages", catalog.data)
        self.assertIn(b'class="reading-badge status-unread"', catalog.data)

    def test_catalog_synopsis_is_short_and_title_links_to_details(self):
        self.login()
        synopsis = "A sample synopsis with useful details. " + "long description " * 12 + "THE VERY END"
        self.assertEqual(self.post(
            "/copies/new", title="Example book", author="Example author", summary=synopsis,
            series="Example series",
        ).status_code, 302)
        catalog = self.client.get("/catalog")
        self.assertIn(b"A sample synopsis with useful details.", catalog.data)
        self.assertIn(b"Example author", catalog.data)
        self.assertNotIn(b"THE VERY END", catalog.data)
        self.assertNotIn(b"Example series", catalog.data)
        key = next(iter(self.store.read()["copies"]))
        self.assertIn(b"THE VERY END", self.client.get(f"/copies/{key}/edit").data)


class SetupTests(unittest.TestCase):
    def test_password_length_rules(self):
        for password, accepted in (("x" * 11, False), ("x" * 12, True), ("x" * 257, True)):
            with self.subTest(length=len(password)), TemporaryDirectory() as directory:
                app = create_app({"TESTING": True, "DATA_DIR": directory})
                result = app.test_cli_runner().invoke(
                    args=["create-first-user"], input=f"Owner\n{password}\n{password}\n"
                )
                if not accepted:
                    self.assertNotEqual(result.exit_code, 0)
                    self.assertIn("at least 12 characters", result.output)
                    self.assertEqual(Store(Path(directory)).read()["users"], {})
                    continue
                self.assertEqual(result.exit_code, 0, result.output)

                client = app.test_client()
                client.get("/login")
                with client.session_transaction() as session:
                    csrf = session["csrf"]
                login = client.post("/login", data={"csrf": csrf, "username": "owner", "password": password})
                self.assertEqual(login.location, "/")

    def test_first_account_can_sign_in_and_setup_cannot_be_repeated(self):
        with TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "DATA_DIR": directory})
            runner = app.test_cli_runner()
            result = runner.invoke(args=["create-first-user"], input=f"Owner\n{PASSWORD}\n{PASSWORD}\n")
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("Created account: owner", result.output)
            self.assertNotIn(PASSWORD, result.output)

            client = app.test_client()
            client.get("/login")
            with client.session_transaction() as session:
                csrf = session["csrf"]
            login = client.post("/login", data={"csrf": csrf, "username": "owner", "password": PASSWORD})
            self.assertEqual(login.location, "/")

            repeated = runner.invoke(args=["create-first-user"], input=f"Other\n{PASSWORD}\n{PASSWORD}\n")
            self.assertNotEqual(repeated.exit_code, 0)
            self.assertIn("An account already exists.", repeated.output)
            self.assertNotIn("Username:", repeated.output)
            self.assertEqual(set(Store(Path(directory)).read()["users"]), {"owner"})


class ScannerAssetTests(unittest.TestCase):
    def test_zxing_wasm_assets_are_served_to_signed_in_users(self):
        with TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "DATA_DIR": directory})
            result = app.test_cli_runner().invoke(args=["add-user", "owner"], input=f"{PASSWORD}\n{PASSWORD}\n")
            self.assertEqual(result.exit_code, 0, result.output)
            client = app.test_client()
            client.get("/login")
            with client.session_transaction() as session:
                csrf = session["csrf"]
            login = client.post("/login", data={"csrf": csrf, "username": "owner", "password": PASSWORD})
            self.assertEqual(login.location, "/")
            reader = client.get("/scanner-wasm.js")
            wasm = client.get("/scanner-wasm.wasm")
            reader_data, wasm_mimetype = reader.data, wasm.mimetype
            reader.close()
            wasm.close()

        self.assertEqual(reader.status_code, 200)
        self.assertIn(b"ZXingWASM", reader_data)
        self.assertEqual(wasm.status_code, 200)
        self.assertEqual(wasm_mimetype, "application/wasm")


class AuthTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app({"TESTING": True, "DATA_DIR": directory.name, "CLIENT_IP_HEADER": "CF-Connecting-IP"})
        for username in ("owner", "member"):
            result = self.app.test_cli_runner().invoke(args=["add-user", username], input=f"{PASSWORD}\n{PASSWORD}\n")
            self.assertEqual(result.exit_code, 0, result.output)

    def attempt(self, username, password=PASSWORD, address="", client=None):
        client = client or self.app.test_client()
        headers = {"CF-Connecting-IP": address} if address else {}
        client.get("/login", headers=headers)
        with client.session_transaction() as session:
            csrf = session["csrf"]
        data = {"csrf": csrf, "username": username, "password": password}
        return client.post("/login", data=data, headers=headers)

    def signed_in(self, username):
        client = self.app.test_client()
        self.assertEqual(self.attempt(username, client=client).location, "/")
        client.get("/")
        return client

    def test_repeated_failures_lock_the_username_and_address(self):
        for username in ("owner", "ghost"):
            for _ in range(10):
                self.assertEqual(self.attempt(username, "wrong password").status_code, 200)
            blocked = self.attempt(username)
            self.assertEqual(blocked.status_code, 429)
            self.assertIn(b"Too many sign-in attempts", blocked.data)
        self.assertEqual(self.attempt("member").location, "/")

        for index in range(10):
            self.attempt(f"guess{index}", "wrong password", address="203.0.113.5")
        self.assertEqual(self.attempt("member", address="203.0.113.5").status_code, 429)
        self.assertEqual(self.attempt("member", address="198.51.100.7").location, "/")

    def test_sign_out_and_password_change_end_sessions_everywhere(self):
        phone, laptop = self.signed_in("owner"), self.signed_in("owner")
        member = self.signed_in("member")
        self.assertEqual(laptop.get("/").status_code, 200)
        with phone.session_transaction() as session:
            csrf = session["csrf"]
        self.assertEqual(phone.post("/logout", data={"csrf": csrf}).location, "/login")
        self.assertEqual(laptop.get("/").location, "/login")
        self.assertEqual(member.get("/").status_code, 200)

        result = self.app.test_cli_runner().invoke(
            args=["set-password", "member"], input=f"{PASSWORD}2\n{PASSWORD}2\n"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(member.get("/").location, "/login")
        self.assertEqual(self.attempt("member", f"{PASSWORD}2").location, "/")

    def test_private_pages_are_not_cached_and_send_security_headers(self):
        client = self.signed_in("owner")
        page = client.get("/catalog")
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", page.headers["Content-Security-Policy"])
        self.assertEqual(page.headers["X-Content-Type-Options"], "nosniff")
        stylesheet = client.get("/static/style.css")
        self.assertNotIn("no-store", stylesheet.headers.get("Cache-Control", ""))
        stylesheet.close()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name))
        self.app = create_app({"TESTING": True, "DATA_DIR": directory.name, "PUBLIC_URL": "https://books.home.arpa"})
        for username in ("owner", "member"):
            result = self.app.test_cli_runner().invoke(
                args=["add-user", username], input=f"{PASSWORD}\n{PASSWORD}\n"
            )
            self.assertEqual(result.exit_code, 0, result.output)
        self.owner, self.member = (self.sign_in(username) for username in ("owner", "member"))

    def submit(self, client, path, **data):
        with client.session_transaction() as session:
            csrf = session["csrf"]
        return client.post(path, data={**data, "csrf": csrf})

    def sign_in(self, username):
        client = self.app.test_client()
        client.get("/login")
        response = self.submit(client, "/login", username=username, password=PASSWORD)
        self.assertEqual(response.location, "/")
        client.get("/")
        return client

    def add_copy(self, title="River Atlas"):
        self.assertEqual(self.submit(self.owner, "/copies/new", title=title, genre="Travel").status_code, 302)
        return next(key for key, copy in self.store.read()["copies"].items() if copy["title"] == title)

    def test_wishlist_and_notes_are_private_until_purchase(self):
        self.assertEqual(self.submit(self.member, "/wishlist", title="Secret book").status_code, 302)
        key = next(iter(self.store.read()["wishlists"]["member"]))
        self.assertNotIn(b"Secret book", self.owner.get("/wishlist").data)
        self.assertNotIn(b"Secret book", self.owner.get("/").data)
        private_home = self.member.get("/")
        self.assertIn(b"Secret book", private_home.data)
        self.assertIn("no-store", private_home.headers["Cache-Control"])
        self.assertEqual(self.submit(self.owner, f"/wishlist/{key}/bought").status_code, 404)
        self.assertEqual(self.submit(self.member, f"/wishlist/{key}/bought").status_code, 302)
        self.assertEqual(self.submit(self.member, f"/wishlist/{key}/bought").status_code, 404)
        self.assertIn(b"Secret book", self.owner.get("/").data)
        copy_id = next(iter(self.store.read()["copies"]))
        self.assertEqual(self.store.read()["copies"][copy_id]["added_on"], date.today().isoformat())
        self.submit(self.member, f"/copies/{copy_id}/note", note="Member-only note")
        self.assertNotIn(b"Member-only note", self.owner.get(f"/copies/{copy_id}/edit").data)
        self.assertIn(b"Member-only note", self.member.get(f"/copies/{copy_id}/edit").data)
        self.submit(self.member, "/wishlist", title="Secret book")
        wishlist = self.member.get("/wishlist")
        self.assertIn(b"Marking it bought adds another copy.", wishlist.data)
        self.assertIn("no-store", wishlist.headers["Cache-Control"])

    def test_storages_and_wishlist_show_add_forms_only_when_requested(self):
        self.submit(self.owner, "/storages", name="Study")
        self.submit(self.owner, "/wishlist", title="Wanted title")
        for path, item, field in (("/locations", b"Study", b'name="name"'), ("/wishlist", b"Wanted title", b'name="title"')):
            with self.subTest(path=path):
                listing = self.owner.get(path)
                self.assertIn(item, listing.data)
                self.assertNotIn(field, listing.data)
                self.assertIn(field, self.owner.get(path, query_string={"add": "1"}).data)

    def test_home_tracks_additions_without_inventing_legacy_dates(self):
        old_id = self.add_copy("Undated book")
        with self.store.edit() as library:
            del library["copies"][old_id]["added_on"]
        recent_id = self.add_copy("New book")
        stamp = date.today().isoformat()
        self.assertEqual(self.store.read()["copies"][recent_id]["added_on"], stamp)
        edit = self.submit(self.owner, f"/copies/{recent_id}/edit", title="New edition")
        self.assertEqual(edit.status_code, 302)
        self.assertEqual(self.store.read()["copies"][recent_id]["added_on"], stamp)
        self.assertEqual(self.submit(
            self.owner, f"/copies/{recent_id}/reading", status="finished", finished=stamp
        ).status_code, 302)
        home = self.owner.get("/")
        self.assertIn(b'action="/catalog"', home.data)
        self.assertIn(b"New edition", home.data)
        self.assertIn(b"1 copy has no recorded addition date", home.data)
        self.assertIn(b"1 added, 1 finished", home.data)
        search = self.owner.get("/catalog", query_string={"q": "Undated book"})
        self.assertIn(b"Undated book", search.data)
        self.assertNotIn(b"New edition", search.data)

    def test_shared_reading_rereads_loans_and_filters(self):
        copy_id = self.add_copy()
        today = date.today().isoformat()
        path = f"/copies/{copy_id}/reading"
        self.assertEqual(self.submit(self.owner, path, status="finished").status_code, 400)
        for client in (self.member, self.owner):
            self.assertEqual(self.submit(client, path, status="finished", finished=today).status_code, 302)
        stats = self.member.get("/stats").data
        self.assertIn(b'class="metric">2</p>', stats)
        self.assertIn(b"1 added, 2 finished", stats)
        self.assertIn(b"Travel", stats)
        self.assertIn(b'<dt>Finished</dt><dd>1</dd>', stats)
        lent = dict(direction="lent", copy_id=copy_id, person="A friend", started=today)
        self.assertEqual(self.submit(self.member, "/loans", **lent).status_code, 302)
        self.assertEqual(self.submit(self.owner, "/loans", **lent).status_code, 409)
        self.assertEqual(self.submit(self.owner, f"/copies/{copy_id}/delete").status_code, 409)
        self.assertIn(b"River Atlas", self.owner.get("/?reading=finished&loan=lent").data)
        self.assertNotIn(b"River Atlas", self.owner.get("/?reading=finished&loan=available").data)
        key = next(iter(self.store.read()["loans"]))
        self.assertEqual(self.submit(self.owner, f"/loans/{key}/return").status_code, 302)
        self.assertIn(b"River Atlas", self.owner.get("/?reading=finished&loan=available").data)
        self.assertEqual(self.submit(self.member, "/loans", direction="borrowed", title="A borrowed book", person="A friend").status_code, 302)
        with self.store.edit() as library:
            library["loans"][key]["due"] = today
        loans = self.owner.get("/loans").data
        self.assertIn(b"A borrowed book", loans)
        self.assertNotIn(b'Due date', loans)
        self.assertNotIn(b"Due " + today.encode(), loans)
        self.assertEqual(self.store.read()["loans"][key]["due"], today)

    def test_owner_snapshot_restore_keeps_private_data_and_exports_csv(self):
        copy_id = self.add_copy("=SUM(1,1)")
        self.submit(self.member, f"/copies/{copy_id}/note", note="Private note")
        self.submit(self.member, "/wishlist", title="Wishlist secret")
        self.assertEqual(self.member.get("/backup").status_code, 403)
        self.assertEqual(self.submit(self.member, "/backup/snapshot").status_code, 403)
        self.assertEqual(self.submit(self.owner, "/backup/snapshot").status_code, 302)
        filename = next((self.store.path.parent / "snapshots").glob("*.json")).name
        download = self.owner.get(f"/backup/snapshot/{filename}")
        snapshot = download.data
        download.close()
        self.assertEqual(set(json.loads(snapshot)), {"version", "copies", "storages", "units", "reading", "loans"})
        self.assertNotIn(b"Private note", snapshot)
        self.assertNotIn(b"Wishlist secret", snapshot)
        export = self.owner.get("/backup/catalog.csv")
        self.assertIn(b"\"'=SUM(1,1)\"", export.data)
        export.close()
        self.add_copy("Newer title")
        invalid = self.submit(self.owner, "/backup/restore", confirm="yes", snapshot=(io.BytesIO(b'{}'), "bad.json"))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"Newer title", self.owner.get("/").data)
        valid = self.submit(self.owner, "/backup/restore", confirm="yes", snapshot=(io.BytesIO(snapshot), "saved.json"))
        self.assertEqual(valid.status_code, 302, valid.data)
        self.assertNotIn(b"Newer title", self.owner.get("/").data)
        self.assertIn(b"Private note", self.member.get(f"/copies/{copy_id}/edit").data)
        self.assertIn(b"Wishlist secret", self.member.get("/wishlist").data)
        self.assertEqual(len(list((self.store.path.parent / "snapshots").glob("*.json"))), 2)

    def test_qr_and_sorting_pages(self):
        self.add_copy("River Atlas")
        self.assertIn(b"https://books.home.arpa", self.owner.get("/connect").data)
        qr = self.owner.get("/connect/qr.svg")
        self.assertEqual(qr.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", qr.data)
        self.assertIn(b"River Atlas", self.owner.get("/sorting?by=genre&by=").data)


class QuoteTests(unittest.TestCase):
    WIKITEXT = (
        "== Quotes ==\n* It is a truth universally acknowledged, that a single man in possession of a good fortune.\n"
        "** ''Chapter 1''\n* Short.\n== Misattributed ==\n"
        "* This misattributed line is long enough to be considered a quote by length alone, but must be skipped.\n"
        "== Quotes about [[Pride and Prejudice|the book]] ==\n"
        "* Somebody else said something rather long about the book, which is not a quote from the book itself.\n"
    )

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name))
        self.app = create_app({"TESTING": True, "DATA_DIR": directory.name, "QUOTES": True})
        self.app.test_cli_runner().invoke(args=["add-user", "alice"], input=f"{PASSWORD}\n{PASSWORD}\n")
        with self.store.edit() as library:
            library["copies"]["a" * 32] = {
                "id": "a" * 32, "title": "Pride and Prejudice", "author": "Jane Austen", "series": "",
                "isbn": "", "tags": [], "location_id": "",
            }

    def test_wikiquote_parsing_skips_doubtful_sections(self):
        self.assertEqual(wikiquote_lines(self.WIKITEXT), [
            "It is a truth universally acknowledged, that a single man in possession of a good fortune.",
        ])
        author_page = "== Works ==\n=== Emma ===\n" + self.WIKITEXT.replace("== Quotes ==", "==== Quotes ====")
        author_page += "== Pride and Prejudice (1813) ==\n* A line that is only found in the section for the requested novel.\n"
        self.assertEqual(
            wikiquote_lines(author_page, "Pride and Prejudice"),
            ["A line that is only found in the section for the requested novel."],
        )

    def test_gutenberg_sentences_avoid_fragments(self):
        text = (
            "*** START OF THE PROJECT GUTENBERG EBOOK ***\n\n" + "Filler paragraph here. " * 40 + "\n\n"
            "Mr. Darcy walked across the quiet room and looked out at the long grey road beyond the garden wall.\n\n"
            "\"Come here,\" said she, and so it went on for quite some considerable while after that.\n\n"
            + "More filler text goes here. " * 40 + "\n*** END OF THE PROJECT GUTENBERG EBOOK ***\n"
        )
        self.assertEqual(gutenberg_sentences(text), [
            "Mr. Darcy walked across the quiet room and looked out at the long grey road beyond the garden wall.",
        ])

    def test_refresh_falls_back_to_gutenberg_and_remembers_misses(self):
        today = date(2026, 5, 1)
        miss, hit = Mock(return_value=None), Mock(return_value=Quote("A real sentence.", "Project Gutenberg", "https://www.gutenberg.org/ebooks/1342"))
        sources = (("wikiquote", miss), ("gutenberg", hit))
        self.assertTrue(refresh_quote(self.store, today, sources=sources))
        library = self.store.read()
        self.assertEqual(library["quote"]["title"], "Pride and Prejudice")
        self.assertIn("wikiquote:pride and prejudice|jane austen", library["quote_state"]["misses"])
        self.assertFalse(needs_refresh(library, today, datetime.now(timezone.utc)))
        self.assertFalse(needs_refresh(library, today, datetime.now(timezone.utc) + timedelta(hours=3)))
        self.assertFalse(refresh_quote(self.store, today + timedelta(days=1), sources=sources[:1]))
        self.assertEqual(miss.call_count, 1)
        self.assertEqual(self.store.read()["quote"], fallback_quote(today + timedelta(days=1)))

    def test_failures_fall_back_to_a_famous_quote_once_per_day(self):
        today = date(2026, 5, 1)
        offline = Mock(side_effect=Unavailable)
        self.assertFalse(refresh_quote(self.store, today, sources=(("wikiquote", offline),)))
        library = self.store.read()
        self.assertEqual(library["quote"], fallback_quote(today))
        self.assertEqual(library["quote"]["title"], "")
        self.assertFalse(needs_refresh(library, today, datetime.now(timezone.utc) + timedelta(hours=2)))
        self.assertTrue(needs_refresh(library, today + timedelta(days=1), datetime.now(timezone.utc) + timedelta(hours=2)))
        self.assertEqual(len({fallback_quote(today + timedelta(days=n))["text"] for n in range(200)}), 200)

    def test_titles_and_authors_are_normalised_for_lookups(self):
        self.assertEqual(_display_name("Ernest Becker, Ernest Becker"), "Ernest Becker")
        self.assertEqual(_display_name("Becker, Ernest"), "Ernest Becker")
        self.assertEqual(_title_variants("Denial of Death"), ["Denial of Death", "The Denial of Death"])
        self.assertEqual(_title_variants("The Denial of Death"), ["The Denial of Death", "Denial of Death"])

    def test_home_shows_title_and_author_only_for_books_still_in_the_library(self):
        refresh_quote(self.store, date.today(), sources=(("wikiquote", Mock(return_value=Quote("Truth acknowledged.", "Wikiquote", "https://en.wikiquote.org/wiki/Pride_and_Prejudice"))),))
        client = self.app.test_client()
        client.get("/login")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        client.post("/login", data={"csrf": csrf, "username": "alice", "password": PASSWORD})
        page = client.get("/").data
        self.assertIn(b"Truth acknowledged.", page)
        self.assertIn(f'— <a href="/copies/{"a" * 32}/edit">Pride and Prejudice</a> - Jane Austen'.encode(), page)
        with self.store.edit() as library:
            library["copies"].clear()
        self.assertNotIn(b"Truth acknowledged.", client.get("/").data)

    def test_home_starts_a_background_lookup_for_a_new_day(self):
        done = Event()
        with patch("bookeeper.refresh_quote", side_effect=lambda *_: done.set()) as refresh:
            client = self.app.test_client()
            client.get("/login")
            with client.session_transaction() as session:
                csrf = session["csrf"]
            client.post("/login", data={"csrf": csrf, "username": "alice", "password": PASSWORD})
            client.get("/")
            self.assertTrue(done.wait(5))
        refresh.assert_called_once()


class VersionTests(unittest.TestCase):
    def test_version_shows_short_commit_time_and_dirty_marker(self):
        outputs = {"log": "3fa9c1e\t2026-11-23T12:10:00+00:00", "status": " M file.py"}
        with patch("bookeeper.version._git", side_effect=lambda command, *_: outputs[command]):
            version = build_version()
        self.assertTrue(version.startswith("3fa9c1e+ · 2026-11-23 "))
        with patch("bookeeper.version._git", side_effect=FileNotFoundError):
            self.assertEqual(build_version(), "")

    def test_footer_shows_the_version(self):
        with TemporaryDirectory() as directory, patch("bookeeper.build_version", return_value="3fa9c1e · 2026-11-23 12:10"):
            page = create_app({"TESTING": True, "DATA_DIR": directory}).test_client().get("/login")
        self.assertIn(b"Version 3fa9c1e", page.data)


class DataTests(unittest.TestCase):
    def test_activity_window_crosses_year_and_counts_rereads(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory))
            with store.edit() as library:
                for key, stamp in (("old", "2024-01-10"), ("recent", "2024-12-10"), ("unknown", "")):
                    book = dict(id=key, title=key, author="", series="", isbn="", tags=[], location_id="")
                    if stamp:
                        book["added_on"] = stamp
                    library["copies"][key] = book
                library["reading"] = {"recent": {"status": "finished", "dates": ["2024-12-20", "2025-01-10"]}}
            result = activity(store.read(), date(2025, 1, 15))
            self.assertEqual((result.copies, result.copies_read, result.finished_this_year, result.undated_copies), (3, 1, 1, 1))
            self.assertEqual((result.months[0].label, result.months[-1].label), ("Feb 2024", "Jan 2025"))
            self.assertEqual([(month.added, month.finished) for month in result.months[-2:]], [(1, 1), (0, 1)])
            self.assertEqual(sum(month.added for month in result.months), 1)

    def test_concurrent_updates_and_rollback(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)

            def add_storage(index):
                key = str(index)
                with Store(path).edit() as library:
                    library["storages"][key] = {"id": key, "name": key}

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(add_storage, range(20)))
            store = Store(path)
            self.assertEqual(len(store.read()["storages"]), 20)
            with self.assertRaises(ValueError), store.edit() as library:
                library["storages"].clear()
                raise ValueError("Cancel the edit")
            self.assertEqual(len(store.read()["storages"]), 20)

    def test_old_floor_plans_are_discarded_on_read(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            old = {"session_key": "", "users": {}, "locations": {"a" * 32: {"id": "a" * 32, "kind": "room", "name": "Study", "parent_id": ""}},
                   "copies": {"b" * 32: dict(id="b" * 32, title="T", author="", series="", isbn="", tags=[], location_id="a" * 32)}}
            (path / "library.json").write_text(json.dumps(old), encoding="utf-8")
            library = Store(path).read()
            self.assertNotIn("locations", library)
            self.assertEqual((library["storages"], library["units"]), ({}, {}))
            self.assertEqual(library["copies"]["b" * 32]["location_id"], "")

    def test_open_library_query_and_response(self):
        key = "ISBN:9780141439518"
        payload = {key: {"title": "Pride and Prejudice", "authors": [{"name": "Jane Austen"}]}}
        responses = [io.BytesIO(json.dumps(payload).encode()), io.BytesIO(b"{}")]
        with patch("bookeeper.catalog.urlopen", side_effect=responses) as fetch:
            self.assertEqual(lookup_isbn("9780141439518"), BookMetadata("Pride and Prejudice", "Jane Austen"))
        url = urlsplit(fetch.call_args_list[0].args[0].full_url)
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "openlibrary.org", "/api/books"))
        self.assertEqual(parse_qs(url.query), {"bibkeys": [key], "format": ["json"], "jscmd": ["data"]})
        with patch("bookeeper.catalog.urlopen", side_effect=[io.BytesIO(b"{}"), io.BytesIO(b"{}")]):
            self.assertIsNone(lookup_isbn("9780141439518"))
        self.assertEqual(normalize_isbn("080442957X"), "9780804429573")

    def test_open_library_reads_a_work_summary(self):
        key = "ISBN:9780141439518"
        payloads = [
            {key: {"title": "Pride and Prejudice", "url": "https://openlibrary.org/books/OL1M/Pride_and_Prejudice"}},
            {"works": [{"key": "/works/OL2W"}], "physical_format": "Paperback", "publishers": ["Example Press"],
             "publish_date": "2020", "pagination": "336 p.", "covers": [-1, 123]},
            {"description": {"value": "A work summary"}, "subjects": ["Literature", "Classic fiction"]},
        ]
        responses = [io.BytesIO(json.dumps(payload).encode()) for payload in payloads]
        with patch("bookeeper.catalog.urlopen", side_effect=responses) as fetch:
            self.assertEqual(lookup_isbn("9780141439518"), BookMetadata(
                "Pride and Prejudice", summary="A work summary", genre="Literature, Classic fiction",
                format="Paperback", publisher="Example Press", publication_date="2020", pages=336, cover_id=123,
            ))
        self.assertTrue(fetch.call_args.args[0].full_url.endswith("/works/OL2W.json"))

    def test_open_library_edition_fallback_resolves_author_and_language(self):
        edition = {
            "title": "The Denial of Death", "type": {"key": "/type/edition"},
            "pagination": "336", "subjects": ["Humanism", "Consolation"],
            "authors": [{"key": "/authors/OL2002958A"}], "languages": [{"key": "/languages/eng"}],
        }
        with patch("bookeeper.catalog._open_library", side_effect=[
            LookupUnavailable, edition, {"name": "Ernest Becker"}, {"name": "English"},
        ]):
            result = lookup_isbn("9781788164269")
        self.assertEqual(result, BookMetadata(
            "The Denial of Death", "Ernest Becker", genre="Humanism, Consolation", pages=336, language="English",
        ))


if __name__ == "__main__":
    unittest.main()

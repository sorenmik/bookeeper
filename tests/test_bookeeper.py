import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlsplit

from bookeeper import create_app
from bookeeper.catalog import LookupUnavailable, lookup_isbn, normalize_isbn
from bookeeper.service import Service
from bookeeper.storage import Store


PASSWORD = "test-only password"


class ServiceTests(unittest.TestCase):
    def test_restart_waits_for_successful_stop(self):
        with TemporaryDirectory() as directory:
            service = Service(Path(directory))
            for result, expected in ((0, [call.stop(), call.start()]), (1, [call.stop()])):
                with self.subTest(stop_status=result):
                    actions = Mock()
                    actions.stop.return_value = result
                    actions.start.return_value = 0
                    with patch.object(service, "stop", actions.stop), patch.object(service, "start", actions.start):
                        self.assertEqual(service.restart(), result)
                    self.assertEqual(actions.mock_calls, expected)


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

    def add_location(self, kind, name, parent_id=""):
        self.assertEqual(self.post("/locations", kind=kind, name=name, parent_id=parent_id).status_code, 302)
        return next(
            key for key, item in self.store.read()["locations"].items()
            if item["name"] == name and item["parent_id"] == parent_id
        )

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
        room = self.add_location("room", "Study")
        case = self.add_location("bookcase", "Oak case", room)
        shelf = self.add_location("shelf", "Top shelf", case)
        pile = self.add_location("pile", "Reading pile", room)
        self.assertIn(b"Oak case", self.client.get("/locations").data)
        first = self.post("/copies/new", title="Pride and Prejudice", isbn="0-14-143951-3", location_id=shelf)
        self.assertEqual(first.status_code, 302)
        other = dict(title="Pride and Prejudice", isbn="9780141439518", location_id=pile)
        warning = self.post("/copies/new", **other)
        self.assertEqual(warning.status_code, 409)
        self.assertIn(b"Add another copy anyway", warning.data)
        self.assertEqual(len(self.store.read()["copies"]), 1)
        self.assertEqual(self.post("/copies/new", allow_duplicate="yes", **other).status_code, 302)
        copies = self.store.read()["copies"]
        self.assertEqual({item["location_id"] for item in copies.values()}, {shelf, pile})
        self.assertIn(b"2 copies", self.client.get(f"/?location={room}").data)
        result = self.client.get(f"/?location={shelf}&q=0-14-143951-3")
        self.assertIn(b"Study / Oak case / Top shelf", result.data)
        self.assertNotIn(b"Reading pile</p>", result.data)
        self.assertEqual(self.post(f"/locations/{shelf}/delete").status_code, 409)
        first_id = next(key for key, item in copies.items() if item["location_id"] == shelf)
        self.assertEqual(self.post(f"/copies/{first_id}/edit", title=other["title"]).status_code, 302)
        self.assertEqual(self.post(f"/locations/{shelf}/delete").status_code, 302)
        self.assertEqual(self.post(f"/copies/{first_id}/delete").status_code, 302)
        self.assertEqual(len(self.store.read()["copies"]), 1)

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
        with patch("bookeeper.lookup_isbn", return_value=("Remote title", "Remote author")) as fetch:
            result = self.post("/copies/new", action="lookup", isbn="0-14-143951-3", title="My title")
        fetch.assert_called_once_with("9780141439518")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'value="My title"', result.data)
        self.assertIn(b'value="Remote author"', result.data)
        self.assertFalse(self.store.read()["copies"])
        with patch("bookeeper.lookup_isbn", side_effect=LookupUnavailable):
            unavailable = self.post("/copies/new", action="lookup", isbn="9780141439518")
        self.assertIn(b"enter the details manually", unavailable.data)
        self.assertEqual(
            self.post("/copies/new", isbn="9780141439518", title="Manual title").status_code, 302
        )


class SetupTests(unittest.TestCase):
    def test_passwords_outside_previous_length_limits_work(self):
        for password in ("x", "x" * 257):
            with self.subTest(length=len(password)), TemporaryDirectory() as directory:
                app = create_app({"TESTING": True, "DATA_DIR": directory})
                result = app.test_cli_runner().invoke(
                    args=["create-first-user"], input=f"Owner\n{password}\n{password}\n"
                )
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
        self.assertEqual(self.submit(self.owner, f"/wishlist/{key}/bought").status_code, 404)
        self.assertEqual(self.submit(self.member, f"/wishlist/{key}/bought").status_code, 302)
        self.assertEqual(self.submit(self.member, f"/wishlist/{key}/bought").status_code, 404)
        self.assertIn(b"Secret book", self.owner.get("/").data)
        copy_id = next(iter(self.store.read()["copies"]))
        self.submit(self.member, f"/copies/{copy_id}/note", note="Member-only note")
        self.assertNotIn(b"Member-only note", self.owner.get(f"/copies/{copy_id}/edit").data)
        self.assertIn(b"Member-only note", self.member.get(f"/copies/{copy_id}/edit").data)
        self.submit(self.member, "/wishlist", title="Secret book")
        wishlist = self.member.get("/wishlist")
        self.assertIn(b"Marking it bought adds another copy.", wishlist.data)
        self.assertIn("no-store", wishlist.headers["Cache-Control"])

    def test_shared_reading_rereads_loans_and_filters(self):
        copy_id = self.add_copy()
        today = date.today().isoformat()
        path = f"/copies/{copy_id}/reading"
        self.assertEqual(self.submit(self.owner, path, status="finished").status_code, 400)
        for client in (self.member, self.owner):
            self.assertEqual(self.submit(client, path, status="finished", finished=today).status_code, 302)
        self.assertIn(b'class="metric">2</p>', self.member.get("/stats").data)
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
        self.assertIn(b"A borrowed book", self.owner.get("/loans").data)

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
        self.assertEqual(set(json.loads(snapshot)), {"version", "copies", "locations", "reading", "loans"})
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


class DataTests(unittest.TestCase):
    def test_concurrent_updates_and_rollback(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)

            def add_room(index):
                key = str(index)
                with Store(path).edit() as library:
                    library["locations"][key] = {"id": key, "kind": "room", "name": key, "parent_id": ""}

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(add_room, range(20)))
            store = Store(path)
            self.assertEqual(len(store.read()["locations"]), 20)
            with self.assertRaises(ValueError), store.edit() as library:
                library["locations"].clear()
                raise ValueError("Cancel the edit")
            self.assertEqual(len(store.read()["locations"]), 20)

    def test_open_library_query_and_response(self):
        key = "ISBN:9780141439518"
        payload = {key: {"title": "Pride and Prejudice", "authors": [{"name": "Jane Austen"}]}}
        with patch("bookeeper.catalog.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as fetch:
            self.assertEqual(lookup_isbn("9780141439518"), ("Pride and Prejudice", "Jane Austen"))
        url = urlsplit(fetch.call_args.args[0].full_url)
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "openlibrary.org", "/api/books"))
        self.assertEqual(parse_qs(url.query), {"bibkeys": [key], "format": ["json"], "jscmd": ["data"]})
        with patch("bookeeper.catalog.urlopen", return_value=io.BytesIO(b"{}")):
            self.assertIsNone(lookup_isbn("9780141439518"))
        self.assertEqual(normalize_isbn("080442957X"), "9780804429573")

    def test_open_library_reads_a_work_summary(self):
        key = "ISBN:9780141439518"
        payloads = [
            {key: {"title": "Pride and Prejudice", "url": "https://openlibrary.org/books/OL1M/Pride_and_Prejudice"}},
            {"works": [{"key": "/works/OL2W"}]},
            {"description": {"value": "A work summary"}},
        ]
        responses = [io.BytesIO(json.dumps(payload).encode()) for payload in payloads]
        with patch("bookeeper.catalog.urlopen", side_effect=responses) as fetch:
            self.assertEqual(lookup_isbn("9780141439518"), ("Pride and Prejudice", "", "A work summary"))
        self.assertTrue(fetch.call_args.args[0].full_url.endswith("/works/OL2W.json"))


if __name__ == "__main__":
    unittest.main()

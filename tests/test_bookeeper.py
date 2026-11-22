import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from bookeeper import create_app
from bookeeper.catalog import LookupUnavailable, lookup_isbn, normalize_isbn
from bookeeper.storage import Store


PASSWORD = "test-only password"


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


if __name__ == "__main__":
    unittest.main()

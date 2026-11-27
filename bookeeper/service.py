import os
import shutil
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from filelock import FileLock, Timeout
from waitress import wasyncore
from waitress.server import create_server

from . import create_app
from .backup import MAX_BACKUP

HOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"
PORT = 5000
URL = f"http://{HOST}:{PORT}"
WAIT_SECONDS = 10
MAX_BODY = MAX_BACKUP + 64 * 1024
PROJECT = Path(__file__).resolve().parent.parent
SCANNER_ASSETS = (
    Path("node_modules/zxing-wasm/dist/iife/reader/index.js"),
    Path("node_modules/zxing-wasm/dist/reader/zxing_reader.wasm"),
    Path("node_modules/tesseract.js/dist/tesseract.min.js"),
    Path("node_modules/tesseract.js-core/tesseract-core-lstm.wasm.js"),
    Path("node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz"),
)


def npm_installed(root: Path = PROJECT) -> bool:
    # npm ci writes node_modules/.package-lock.json; an older one means package-lock.json changed since.
    installed = root / "node_modules" / ".package-lock.json"
    lock = root / "package-lock.json"
    if not installed.is_file() or not all((root / asset).is_file() for asset in SCANNER_ASSETS):
        return False
    return not lock.is_file() or installed.stat().st_mtime >= lock.stat().st_mtime


def ensure_npm_packages(root: Path = PROJECT) -> None:
    if npm_installed(root):
        return
    npm = shutil.which("npm")
    if not npm:
        print("Bookeeper: npm was not found, so the camera scanner will not load. Install Node.js and run npm ci.",
              file=sys.stderr)
        return
    print("Bookeeper: installing scanner packages with npm ci...")
    if subprocess.run([npm, "ci"], cwd=root, stdin=subprocess.DEVNULL).returncode:
        print("Bookeeper: npm ci failed, so the camera scanner will not load.", file=sys.stderr)


class Service:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.commands = FileLock(directory / "commands.lock", timeout=WAIT_SECONDS)
        self.worker = FileLock(directory / "server.lock")
        self.request = directory / "run-request"
        self.listen_host = directory / "listen-host"
        self.stop_signal = directory / "stop-request"
        self.ready = directory / "ready"
        self.log = directory / "server.log"

    def active(self) -> bool:
        try:
            with self.worker.acquire(timeout=0):
                return False
        except Timeout:
            return True

    def status(self) -> str:
        if not self.active():
            return "Stopped"
        if self.stop_signal.is_file():
            return "Stopping"
        if not self.ready.is_file():
            return "Starting"
        if self.listen_host.exists() and self.listen_host.read_text(encoding="ascii") == ALL_INTERFACES:
            return f"Running on all interfaces at port {PORT}"
        return f"Running at {URL}"

    def serve(self, token: str, host: str = HOST) -> None:
        with self.worker.acquire(timeout=WAIT_SECONDS):
            if self.stop_signal.is_file() or self.request.read_text(encoding="ascii") != token:
                return
            connections: dict = {}
            server = create_server(
                create_app(), map=connections, host=host, port=PORT, max_request_body_size=MAX_BODY,
            )
            try:
                if not self.stop_signal.is_file():
                    self.ready.touch()
                while not self.stop_signal.is_file():
                    wasyncore.loop(timeout=0.25, map=connections, count=1)
            finally:
                self.ready.unlink(missing_ok=True)
                server.accepting = False
                # Let in-flight requests finish, then close idle keep-alive connections.
                deadline = monotonic() + WAIT_SECONDS
                while server.active_channels and monotonic() < deadline:
                    for channel in list(server.active_channels.values()):
                        if not channel.requests:
                            channel.close_when_flushed = True
                    wasyncore.loop(timeout=0.25, map=connections, count=1)
                server.close()
                server.task_dispatcher.shutdown(timeout=WAIT_SECONDS)

    def start(self, host: str = HOST) -> int:
        if host not in {HOST, ALL_INTERFACES}:
            raise ValueError("Unsupported listen address.")
        if self.active():
            if self.ready.is_file() and not self.stop_signal.is_file():
                print(f"Bookeeper is already {self.status().lower()}.")
                return 0
            print(f"Bookeeper: {self.status()}. Try again shortly.", file=sys.stderr)
            return 1

        ensure_npm_packages()
        self.ready.unlink(missing_ok=True)
        self.listen_host.write_text(host, encoding="ascii")
        token = uuid4().hex
        self.request.write_text(token, encoding="ascii")
        self.stop_signal.unlink(missing_ok=True)
        started = False
        try:
            with self.log.open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "bookeeper.service", "--serve", token, host],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
            start_deadline = monotonic() + WAIT_SECONDS
            while process.poll() is None and monotonic() < start_deadline:
                if self.ready.is_file() and self.active():
                    started = True
                    if host == ALL_INTERFACES:
                        print(f"Bookeeper started on all interfaces at port {PORT}.")
                    else:
                        print(f"Bookeeper started at {URL}.")
                    return 0
                sleep(0.1)
            print(f"Bookeeper did not start. See {self.log} for details.", file=sys.stderr)
            return 1
        finally:
            if not started:
                self.stop_signal.touch()

    def stop(self) -> int:
        self.stop_signal.touch()
        try:
            with self.worker.acquire(timeout=WAIT_SECONDS):
                self.ready.unlink(missing_ok=True)
        except Timeout:
            print("Bookeeper is still finishing requests. Check its status again shortly.", file=sys.stderr)
            return 1
        print("Bookeeper is stopped.")
        return 0

    def restart(self) -> int:
        try:
            host = self.listen_host.read_text(encoding="ascii")
        except FileNotFoundError:
            host = HOST
        if host not in {HOST, ALL_INTERFACES}:
            host = HOST
        return self.stop() or self.start(host)


def main() -> int:
    args = sys.argv[1:]
    if args and (len(args) not in {2, 3} or args[0] != "--serve"):
        print("Usage: bash service.sh", file=sys.stderr)
        return 2

    try:
        data_dir = Path(os.environ.get("BOOKEEPER_DATA_DIR") or "instance")
        service = Service(data_dir / "service")
        if args:
            host = args[2] if len(args) == 3 else HOST
            if host not in {HOST, ALL_INTERFACES}:
                print("Unsupported listen address.", file=sys.stderr)
                return 2
            service.serve(args[1], host)
            return 0

        with service.commands:
            print(f"Bookeeper status: {service.status()}")
        print("1. Start service (localhost only)\n2. Stop service\n3. Restart service")
        print("4. Start service on all interfaces (LAN accessible)\n0. Exit")
        choice = input("What would you like to do? ").strip()
        if choice not in {"0", "1", "2", "3", "4"}:
            print("Choose 1 to start locally, 2 to stop, 3 to restart, 4 for all interfaces, or 0 to exit.", file=sys.stderr)
            return 2
        if choice == "0":
            return 0
        with service.commands:
            if choice == "3":
                return service.restart()
            if choice == "1":
                return service.start()
            if choice == "4":
                return service.start(ALL_INTERFACES)
            return service.stop()
    except (EOFError, KeyboardInterrupt):
        return 130
    except (OSError, Timeout) as exc:
        print(f"Bookeeper: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

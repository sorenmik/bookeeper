import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from filelock import FileLock, Timeout
from werkzeug.serving import ThreadedWSGIServer

from . import create_app

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}"
WAIT_SECONDS = 10


class Service:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.commands = FileLock(directory / "commands.lock", timeout=WAIT_SECONDS)
        self.worker = FileLock(directory / "server.lock")
        self.request = directory / "run-request"
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
        return f"Running at {URL}" if self.ready.is_file() else "Starting"

    def serve(self, token: str) -> None:
        with self.worker.acquire(timeout=WAIT_SECONDS):
            if self.stop_signal.is_file() or self.request.read_text(encoding="ascii") != token:
                return
            try:
                with ThreadedWSGIServer(HOST, PORT, create_app()) as server:
                    server.daemon_threads = False  # Let in-flight requests finish on stop.
                    server.timeout = 0.25
                    if not self.stop_signal.is_file():
                        self.ready.touch()
                    while not self.stop_signal.is_file():
                        server.handle_request()
            finally:
                self.ready.unlink(missing_ok=True)

    def start(self) -> int:
        if self.active():
            if self.ready.is_file() and not self.stop_signal.is_file():
                print(f"Bookeeper is already running at {URL}.")
                return 0
            print(f"Bookeeper: {self.status()}. Try again shortly.", file=sys.stderr)
            return 1

        self.ready.unlink(missing_ok=True)
        token = uuid4().hex
        self.request.write_text(token, encoding="ascii")
        self.stop_signal.unlink(missing_ok=True)
        started = False
        try:
            with self.log.open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "bookeeper.service", "--serve", token],
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
        return self.stop() or self.start()


def main() -> int:
    args = sys.argv[1:]
    if args and (len(args) != 2 or args[0] != "--serve"):
        print("Usage: bash service.sh", file=sys.stderr)
        return 2

    try:
        data_dir = Path(os.environ.get("BOOKEEPER_DATA_DIR") or "instance")
        service = Service(data_dir / "service")
        if args:
            service.serve(args[1])
            return 0

        with service.commands:
            print(f"Bookeeper status: {service.status()}")
        print("1. Start service\n2. Stop service\n3. Restart service\n0. Exit")
        choice = input("What would you like to do? ").strip()
        if choice not in {"0", "1", "2", "3"}:
            print("Choose 1 to start, 2 to stop, 3 to restart, or 0 to exit.", file=sys.stderr)
            return 2
        if choice == "0":
            return 0
        with service.commands:
            if choice == "3":
                return service.restart()
            return service.start() if choice == "1" else service.stop()
    except (EOFError, KeyboardInterrupt):
        return 130
    except (OSError, Timeout) as exc:
        print(f"Bookeeper: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""A browser front end, for people who do not live in a terminal.

Three properties this is built around, in order:

**It stays on this machine.** The socket binds to the loopback interface, so
nothing outside the computer can reach it — same guarantee as the CLI, which is
the point. Unpublished data is what this tool holds (QA-3), and moving it onto a
server to get a nicer interface would be a bad trade made silently.

**It runs the CLI rather than reimplementing it.** Every action spawns
`python -m mra ...` and streams the output back. The cost brakes, the citation
checks, the refusal handling and the error messages are then the same ones the
terminal gets, because they are literally the same code. A second
implementation would drift, and the half that drifts is always the half nobody
is watching.

**The browser cannot ask for anything the code does not name.** Requests carry a
command and a payload of named fields, never an argv; `build_argv` maps those
onto flags this module declares. A page on some other site cannot drive it
either: every request needs the token printed at startup, and a cross-origin
one is refused outright.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Config

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# How long a finished job's output stays available for a reconnecting page.
JOB_RETENTION_SECONDS = 3600


# ----------------------------------------------------------------- the surface


@dataclass(frozen=True)
class Option:
    """One named field the browser may set, and the flag it becomes.

    Long flags only, and emitted as `--flag=value`. A short flag cannot take
    `-o=x`, and a bare `--notes` followed by a value that begins with `-` gets
    read as another flag — `--notes=-5%` cannot be misread.
    """

    flag: str
    kind: str = "str"  # str | int | float | bool


@dataclass(frozen=True)
class Spec:
    prefix: tuple[str, ...] = ()
    positional: str = ""
    repeated: bool = False
    options: dict[str, Option] = field(default_factory=dict)


_NOTES = Option("--notes")
_JOURNAL = Option("--journal")
_OUTPUT = Option("--output")

COMMANDS: dict[str, Spec] = {
    "status": Spec(),
    "doctor": Spec(),
    "usage": Spec(),
    "guide": Spec(),
    "hypotheses": Spec(),
    "library": Spec(positional="id"),
    "demo": Spec(options={"to": Option("--to")}),
    "memory": Spec(options={"refresh": Option("--refresh", "bool")}),
    "import": Spec(
        positional="files",
        repeated=True,
        options={
            "topic": Option("--topic"),
            "no_metadata": Option("--no-metadata", "bool"),
        },
    ),
    "search": Spec(
        positional="topic",
        options={"max": Option("--max", "int"), "query": Option("--query")},
    ),
    "digest": Spec(
        options={
            "limit": Option("--limit", "int"),
            "max_cost": Option("--max-cost", "float"),
            "yes": Option("--yes", "bool"),
        }
    ),
    "chat": Spec(positional="message", options={"reset": Option("--reset", "bool")}),
    "hypothesis": Spec(options={"note": Option("--note")}),
    "proposal": Spec(
        options={
            "version": Option("--version", "int"),
            "references": Option("--references", "bool"),
            "output": _OUTPUT,
        }
    ),
    "assess": Spec(
        positional="data",
        repeated=True,
        options={"journal": _JOURNAL, "notes": _NOTES, "output": _OUTPUT},
    ),
    "figures": Spec(
        positional="data",
        repeated=True,
        options={"journal": _JOURNAL, "notes": _NOTES, "output": _OUTPUT},
    ),
    "review": Spec(
        positional="topic",
        options={
            "journal": _JOURNAL,
            "outline_only": Option("--outline-only", "bool"),
            "references": Option("--references", "bool"),
            "yes": Option("--yes", "bool"),
            "output": _OUTPUT,
        },
    ),
    "draft": Spec(
        positional="section",
        options={
            "journal": _JOURNAL,
            "data": Option("--data"),
            "notes": _NOTES,
            "output": _OUTPUT,
        },
    ),
    "finalize": Spec(positional="file", options={"journal": _JOURNAL}),
    "refs": Spec(positional="file", options={"list": Option("--list", "bool")}),
    "lint": Spec(positional="file"),
    "fingerprint": Spec(positional="directory"),
    "journal_add": Spec(
        prefix=("journal", "add"),
        positional="name",
        options={
            "samples": Option("--samples"),
            "pubmed": Option("--pubmed", "int"),
            "years": Option("--years", "int"),
        },
    ),
    "journal_list": Spec(prefix=("journal", "list")),
    "journal_show": Spec(prefix=("journal", "show"), positional="name"),
}

# Commands that spend money. The page marks them, so nobody discovers the
# billing model by clicking around.
COSTLY = {
    "digest", "chat", "assess", "figures", "review", "draft", "proposal",
    "finalize", "hypothesis", "journal_add", "fingerprint", "search", "import",
}


def build_argv(command: str, payload: dict[str, Any]) -> list[str]:
    """Turn a named payload into an argv the CLI will accept.

    Rejects anything not declared above rather than passing it through. The
    browser is not a trusted caller: it is whatever is loaded in a tab.
    """
    spec = COMMANDS.get(command)
    if spec is None:
        raise ValueError(f"未知的命令：{command!r}")

    argv = list(spec.prefix) if spec.prefix else [command]

    if spec.positional:
        value = payload.get(spec.positional)
        if spec.repeated:
            items = value if isinstance(value, list) else ([value] if value else [])
            if not items:
                raise ValueError(f"{command} 需要至少一个 {spec.positional}")
            argv.extend(_positional(item) for item in items)
        elif value not in (None, ""):
            argv.append(_positional(value))

    for key, value in payload.items():
        if key == spec.positional or value in (None, "", False):
            continue
        option = spec.options.get(key)
        if option is None:
            raise ValueError(f"{command} 不接受参数 {key!r}")
        if option.kind == "bool":
            argv.append(option.flag)
        else:
            argv.append(f"{option.flag}={_scalar(value, option.kind)}")

    return argv


def _positional(value: Any) -> str:
    text = _scalar(value, "str")
    if text.startswith("-"):
        # argparse would read it as a flag, and the researcher would get a
        # confusing usage error instead of "that file does not exist".
        raise ValueError(f"参数不能以 - 开头：{text!r}")
    return text


def _scalar(value: Any, kind: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{value!r} 不是一个可用的值")
    if kind == "int":
        return str(int(value))
    if kind == "float":
        return str(float(value))
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"{value!r} 不是一个可用的值")
    text = str(value)
    if "\x00" in text:
        raise ValueError("参数含有空字符")
    return text


# --------------------------------------------------------------------- running


class Job:
    """One CLI invocation, with its output kept for whoever asks."""

    def __init__(self, job_id: str, argv: list[str], cwd: Path, workspace: Path):
        self.id = job_id
        self.argv = argv
        self.lines: list[str] = []
        self.exit_code: int | None = None
        self.finished_at: float | None = None
        self._queues: list[Queue] = []
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._cwd = cwd
        self._workspace = workspace

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        command = [
            sys.executable, "-m", "mra",
            f"--workspace={self._workspace}", *self.argv,
        ]
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(self._cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._emit(f"启动失败：{exc}")
            self._finish(1)
            return

        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._emit(line.rstrip("\n"))
        self._finish(self._process.wait())

    def _emit(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)
            for queue in self._queues:
                queue.put(("line", line))

    def _finish(self, code: int) -> None:
        with self._lock:
            self.exit_code = code
            self.finished_at = time.time()
            for queue in self._queues:
                queue.put(("done", code))

    def subscribe(self) -> tuple[Queue, list[str], int | None]:
        """Attach a listener, and hand back what it already missed.

        Done under the lock so a line emitted mid-subscribe cannot be both
        absent from the backlog and absent from the queue.
        """
        queue: Queue = Queue()
        with self._lock:
            backlog = list(self.lines)
            code = self.exit_code
            if code is None:
                self._queues.append(queue)
        return queue, backlog, code

    def unsubscribe(self, queue: Queue) -> None:
        with self._lock:
            if queue in self._queues:
                self._queues.remove(queue)

    def cancel(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, argv: list[str], cwd: Path, workspace: Path) -> Job:
        job = Job(secrets.token_urlsafe(9), argv, cwd, workspace)
        with self._lock:
            self._sweep()
            self._jobs[job.id] = job
        job.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _sweep(self) -> None:
        cutoff = time.time() - JOB_RETENTION_SECONDS
        stale = [
            key for key, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for key in stale:
            del self._jobs[key]


# --------------------------------------------------------------------- serving


def read_index() -> bytes:
    return (resources.files("mra") / "assets" / "index.html").read_bytes()


def listing(path: Path) -> dict[str, Any]:
    """Directory contents for the file picker.

    The browser cannot hand a real path to the server — `<input type=file>`
    deliberately hides it — so picking a file has to happen on this side.
    """
    directory = path.expanduser()
    if not directory.is_dir():
        directory = directory.parent if directory.parent.is_dir() else Path.home()
    directory = directory.resolve()

    entries = []
    try:
        for item in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name.startswith("."):
                continue
            try:
                is_dir = item.is_dir()
            except OSError:
                continue
            entries.append({"name": item.name, "dir": is_dir, "path": str(item)})
    except PermissionError:
        return {"path": str(directory), "parent": str(directory.parent),
                "entries": [], "error": "没有权限读取这个文件夹"}

    parent = str(directory.parent) if directory.parent != directory else ""
    return {"path": str(directory), "parent": parent, "entries": entries}


class Handler(BaseHTTPRequestHandler):
    server_version = "mra"
    protocol_version = "HTTP/1.1"

    # Set by serve()
    token: str = ""
    jobs: Jobs
    cfg: Config

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # The researcher gets the tool's output, not an access log.

    # ---------------------------------------------------------------- plumbing

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc not in {
            f"{HOST}:{self.server.server_address[1]}",
            f"localhost:{self.server.server_address[1]}",
        }:
            return False
        supplied = self.headers.get("X-MRA-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, self.token)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/":
            self._send(200, read_index(), "text/html; charset=utf-8")
            return

        if url.path == "/favicon.ico":
            # Browsers request this on their own. Answering 403 puts a red line
            # in the console that reads like a real failure.
            self._send(204, b"", "image/x-icon")
            return

        if not self._authorised(query):
            self._json({"error": "token 不对。请用启动时打印的那个网址打开。"}, 403)
            return

        if url.path == "/api/state":
            self._json(self._state())
        elif url.path == "/api/browse":
            start = (query.get("path") or [str(Path.home())])[0]
            self._json(listing(Path(start)))
        elif url.path == "/api/stream":
            self._stream((query.get("job") or [""])[0])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._authorised(query):
            self._json({"error": "token 不对。请用启动时打印的那个网址打开。"}, 403)
            return

        try:
            payload = self._body()
        except (ValueError, UnicodeDecodeError):
            self._json({"error": "请求格式不对"}, 400)
            return

        if url.path == "/api/run":
            self._start(payload)
        elif url.path == "/api/cancel":
            job = self.jobs.get(str(payload.get("job", "")))
            if job is not None:
                job.cancel()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ pieces

    def _state(self) -> dict[str, Any]:
        from . import __version__

        return {
            "version": __version__,
            "workspace": str(self.cfg.workspace),
            "provider": self.cfg.provider or "anthropic",
            "model": self.cfg.model,
            # Where the file picker opens. The project folder holds the data
            # and the drafts; home is a longer walk from anything relevant.
            "start_dir": str(self.cfg.workspace.parent),
            "home": str(Path.home()),
            "costly": sorted(COSTLY),
        }

    def _start(self, payload: dict[str, Any]) -> None:
        command = str(payload.get("command", ""))
        fields = payload.get("args")
        if not isinstance(fields, dict):
            fields = {}
        try:
            argv = build_argv(command, fields)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return

        job = self.jobs.create(argv, self.cfg.workspace.parent, self.cfg.workspace)
        self._json({"job": job.id, "argv": argv})

    def _stream(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            self._json({"error": "这个任务已经过期了"}, 404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        queue, backlog, code = job.subscribe()
        try:
            for line in backlog:
                self._event("line", {"text": line})
            if code is not None:
                self._event("done", {"code": code})
                return
            while True:
                try:
                    kind, value = queue.get(timeout=15)
                except Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if kind == "line":
                    self._event("line", {"text": value})
                else:
                    self._event("done", {"code": value})
                    return
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            job.unsubscribe(queue)

    def _event(self, name: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()


def serve(cfg: Config, port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    token = secrets.token_urlsafe(24)

    class Bound(Handler):
        pass

    Bound.token = token
    Bound.jobs = Jobs()
    Bound.cfg = cfg

    try:
        httpd = ThreadingHTTPServer((HOST, port), Bound)
    except OSError as exc:
        print(f"端口 {port} 打不开：{exc}", file=sys.stderr)
        print("换一个端口再试，例如：mra web --port 8790", file=sys.stderr)
        return 1

    url = f"http://{HOST}:{httpd.server_address[1]}/?t={token}"
    print("网页界面已启动。用下面这个网址打开（带 token，别人打不开）：\n")
    print(f"  {url}\n")
    print("数据仍然只在这台电脑上 —— 服务只监听 127.0.0.1，局域网里也访问不到。")
    print("关掉这个窗口，或按 Ctrl-C，就停止。\n")

    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0

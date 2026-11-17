""""Find usages" through a language server (textDocument/references), as an editor asks it.

Usage: python3 -I lsp_refs.py <settings.json> <repository root> refs <candidates.json> <need> <out.json>
       python3 -I lsp_refs.py <settings.json> <repository root> time <questions.json> <out.json>

<settings.json>: {"command": [...], "env": {...}, "init": {...}, "config": {section: value}, "wait": ...,
                 "language_id": "python" (optional: open the file of the question with didOpen before asking)}
  command  the server, started with the repository root as working directory (pyright-langserver --stdio,
           rust-analyzer);
  init     initializationOptions; config answers workspace/configuration requests by section;
  wait     "quiescent": wait for rust-analyzer's experimental/serverStatus with quiescent true before the first
           query (its index is ready); "log:<regex>": wait for a window/logMessage that matches (pyright logs
           "Found N source files" once it knows the files of the workspace); "none": ask at once.

refs: one references request (includeDeclaration false) on the name of each candidate, in the given order,
      until <need> candidates have a reference in another file.
time: seconds from the start of the server to its first answer (load) and the time of each query.
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import quote, unquote, urlparse


class Server:
    def __init__(self, settings: dict, root: str) -> None:
        env = dict(os.environ, **settings.get("env", {}))
        self.process = subprocess.Popen(settings["command"], cwd=root, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.settings, self.root = settings, root
        self.next_id = 0
        self.lock = threading.Condition()
        self.responses: dict[int, dict] = {}
        self.quiescent = False
        self.opened: set[str] = set()
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, message: dict) -> None:
        body = json.dumps(message).encode()
        self.process.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.process.stdin.flush()

    def _read(self) -> None:
        stream = self.process.stdout
        while True:
            length = 0
            while True:
                line = stream.readline()
                if not line:
                    with self.lock:
                        self.lock.notify_all()
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
            message = json.loads(stream.read(length))
            if "method" in message and "id" in message:
                self._answer(message)
            elif "method" in message:
                wait = self.settings.get("wait", "none")
                ready = (wait == "quiescent" and message["method"] == "experimental/serverStatus"
                         and message["params"].get("quiescent"))
                ready = ready or (wait.startswith("log:") and message["method"] == "window/logMessage"
                                  and re.search(wait[4:], message["params"].get("message", "")) is not None)
                if ready:
                    with self.lock:
                        self.quiescent = True
                        self.lock.notify_all()
            else:
                with self.lock:
                    self.responses[message["id"]] = message
                    self.lock.notify_all()

    def _answer(self, request: dict) -> None:
        result = None
        if request["method"] == "workspace/configuration":
            config = self.settings.get("config", {})
            result = [config.get(item.get("section", ""), None) for item in request["params"]["items"]]
        self._send({"jsonrpc": "2.0", "id": request["id"], "result": result})

    def request(self, method: str, params: dict, timeout: float = 3600) -> dict:
        with self.lock:
            self.next_id += 1
            number = self.next_id
        self._send({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
        with self.lock:
            if not self.lock.wait_for(lambda: number in self.responses or self.process.poll() is not None, timeout):
                raise TimeoutError(method)
            if number not in self.responses:
                raise RuntimeError("the language server stopped")
            response = self.responses.pop(number)
        if "error" in response:
            raise RuntimeError(f"{method}: {response['error']}")
        return response.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def start(self) -> None:
        uri = uri_of(self.root)
        self.request("initialize", {
            "processId": os.getpid(), "rootUri": uri, "rootPath": self.root,
            "workspaceFolders": [{"uri": uri, "name": os.path.basename(self.root)}],
            "initializationOptions": self.settings.get("init", {}),
            "capabilities": {
                "workspace": {"configuration": True, "workspaceFolders": True},
                "textDocument": {"references": {}, "synchronization": {}},
                "window": {"workDoneProgress": True},
                "experimental": {"serverStatusNotification": True},
                "general": {"positionEncodings": ["utf-16"]},
            },
        })
        self.notify("initialized", {})
        if self.settings.get("wait", "none") != "none":
            with self.lock:
                if not self.lock.wait_for(lambda: self.quiescent or self.process.poll() is not None, 7200):
                    raise TimeoutError("server never quiescent")

    def stop(self) -> None:
        try:
            self.request("shutdown", {}, timeout=60)
            self.notify("exit", {})
        except (RuntimeError, TimeoutError, BrokenPipeError):
            pass
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()


def uri_of(path: str) -> str:
    return "file://" + quote(os.path.abspath(path))


def utf16_column(path: str, line: int, column: int) -> int:
    """Our symbol columns count code points; LSP counts UTF-16 units."""
    text = open(path, encoding="utf-8", errors="replace").read().split("\n")[line - 1]
    return len(text[:column].encode("utf-16-le")) // 2


def references(server: Server, root: str, question: dict) -> dict:
    path = os.path.join(root, question["file"])
    started = time.perf_counter()
    if server.settings.get("language_id") and path not in server.opened:
        # As an editor does: the file the question is asked in is open.
        server.opened.add(path)
        server.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri_of(path), "languageId": server.settings["language_id"], "version": 1,
            "text": open(path, encoding="utf-8", errors="replace").read()}})
    found = server.request("textDocument/references", {
        "textDocument": {"uri": uri_of(path)},
        "position": {"line": question["line"] - 1, "character": utf16_column(path, question["line"], question["col"])},
        "context": {"includeDeclaration": False},
    }) or []
    seconds = time.perf_counter() - started
    files = set()
    count = 0
    for location in found:
        rel = os.path.relpath(unquote(urlparse(location["uri"]).path), os.path.realpath(root)).replace(os.sep, "/")
        if rel.startswith(".."):
            rel = os.path.relpath(unquote(urlparse(location["uri"]).path), root).replace(os.sep, "/")
        if rel.startswith(".."):
            continue
        count += 1
        if rel != question["file"]:
            files.add(rel)
    return {"files": sorted(files), "references": count, "seconds": seconds}


def main() -> None:
    settings = json.load(open(sys.argv[1]))
    root = os.path.abspath(sys.argv[2])
    mode = sys.argv[3]
    started = time.perf_counter()
    server = Server(settings, root)
    server.start()
    try:
        if mode == "refs":
            candidates, need = json.load(open(sys.argv[4])), int(sys.argv[5])
            tried, used = [], 0
            for candidate in candidates:
                if used >= need:
                    break
                result = references(server, root, candidate)
                tried.append({**candidate, "files": result["files"], "references": result["references"]})
                used += bool(result["files"])
                print(len(tried), used, candidate["file"], candidate["name"], len(result["files"]), f"{result['seconds']:.1f}s", flush=True)
            json.dump({"tool": settings.get("tool", ""), "tried": tried}, open(sys.argv[6], "w"), indent=1)
        elif mode == "time":
            questions = json.load(open(sys.argv[4]))
            ready = time.perf_counter() - started
            rows = [{"id": q["id"], "seconds": references(server, root, q)["seconds"]} for q in questions]
            json.dump({"load_seconds": ready, "queries": rows}, open(sys.argv[5], "w"), indent=1)
            print(f"ready after {ready:.1f} s, {len(rows)} queries")
        else:
            raise SystemExit("mode: refs or time")
    finally:
        server.stop()


if __name__ == "__main__":
    main()

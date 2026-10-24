"""Notification hooks: a command run without a shell, and a webhook."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hive.notify import build_argv


def test_argv_substitution_has_no_shell():
    fields = {"message": 'evil"; rm -rf ~; echo "', "run": "r1", "status": "done", "kind": "finished", "goal": "g"}
    argv = build_argv('notify-send Hive "{message}" --run={run}', fields)
    assert argv == ["notify-send", "Hive", 'evil"; rm -rf ~; echo "', "--run=r1"]


async def test_command_and_webhook_fire_on_finish(make_app, tmp_path):
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = tmp_path / "notified.txt"
    py = sys.executable.replace("\\", "/")
    script = "import sys; open(sys.argv[1], 'a').write(sys.argv[2] + chr(10))"
    import yaml

    command = f'"{py}" -c "{script}" "{out.as_posix()}" "{{status}}: {{message}}"'
    hive_yaml = yaml.safe_dump({"notify": {"events": ["finished"], "command": command, "url": f"http://127.0.0.1:{srv.server_address[1]}/hook"}})
    app = await make_app(hive_yaml=hive_yaml)
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="solo")
    assert await run.wait(10) == "done"
    await app.notifier.drain()
    srv.shutdown()
    line = out.read_text().strip()
    assert line.startswith("done: Hive run done: write a.py")
    assert got and got[0]["kind"] == "finished" and got[0]["run"] == run.id

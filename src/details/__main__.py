"""python -m details: run the server on 127.0.0.1:<server.port>."""
from aiohttp import web

from .app import BIND_HOST, create_app
from .db import DatabaseError, connect
from .settings import load_settings, scrub_secret_env


def main():
    s = load_settings()
    scrub_secret_env()      # the Hugging Face token stays in settings; no child process inherits it
    s.paths.data_dir.mkdir(parents=True, exist_ok=True)
    reachable = "no (DATABASE_URL not set)"
    if s.db.url:
        try:
            connect(s.db.url, s.db.connect_timeout).close()
            reachable = "yes"
        except DatabaseError as e:
            reachable = f"no ({e})"
    print(f"open http://localhost:{s.server.port}/library   data dir: {s.paths.data_dir}   db reachable: {reachable}")
    web.run_app(create_app(s), host=BIND_HOST, port=s.server.port, print=None)


if __name__ == "__main__":
    main()

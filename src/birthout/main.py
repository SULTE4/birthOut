from __future__ import annotations

import webbrowser
from threading import Timer

import uvicorn


def main() -> None:
    Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run("birthout.app:create_app", host="127.0.0.1", port=8765, factory=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    environment_file = Path(
        os.getenv(
            "CONTROL_PLANE_ENV_FILE",
            "~/.config/control-plane/control-plane.env",
        )
    ).expanduser()
    load_dotenv(environment_file, override=False)
    host = os.getenv("CONTROL_PLANE_API_HOST", "127.0.0.1")
    port_text = os.getenv("CONTROL_PLANE_API_PORT", "8000")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("CONTROL_PLANE_API_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("CONTROL_PLANE_API_PORT must be between 1 and 65535")
    uvicorn.run("controlplane.api.server:app", host=host, port=port)


if __name__ == "__main__":
    main()

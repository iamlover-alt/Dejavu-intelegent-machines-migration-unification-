"""Write openapi/midplane-v0.yaml from the running app schema.

The checked-in file is the contract. Run this after an intentional API change
and commit the result. CI fails if the served schema and the file differ.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from midplane.gateway.app import create_app


def schema_dict(database_url: str = "sqlite:///midplane_openapi.sqlite") -> dict:
    app = create_app(database_url=database_url)
    try:
        return app.openapi()
    finally:
        app.state.gateway.close()
        closer = getattr(app.state.episodes, "close", None)
        if closer is not None:
            closer()


def main() -> int:
    destination = Path("openapi/midplane-v0.yaml")
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = schema_dict()
    destination.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

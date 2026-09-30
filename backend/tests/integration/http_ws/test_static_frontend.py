from pathlib import Path

from fastapi.testclient import TestClient

from werewolf_dm.interfaces.http_ws.app import create_app


def test_serves_built_frontend_with_spa_fallback(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('ok')", encoding="utf-8")

    client = TestClient(create_app(static_dir=static_dir))

    root = client.get("/")
    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in root.text

    join = client.get("/join/ABCDEF")
    assert join.status_code == 200
    assert join.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in join.text

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('ok')"

    unknown_api = client.get("/rooms/ABCDEF/not-a-route")
    assert unknown_api.status_code == 404
    assert "text/html" not in unknown_api.headers["content-type"]

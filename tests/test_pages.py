import asyncio

from conftest import client_for
from details.app import create_app


def stub_web(tmp_path):
    w = tmp_path / "web"
    for x in ("record", "library", "analyse"):
        (w / x).mkdir(parents=True)
        (w / x / "index.html").write_text(f"<h1>{x} stub</h1>")
        (w / x / "app.js").write_text(f"// {x}")
    (w / "shared").mkdir()
    return w


def test_pages_served_from_stub_dir(mk_settings, tmp_path):
    w = stub_web(tmp_path)
    async def go():
        async with client_for(create_app(mk_settings(), web_dir=w)) as c:
            for x in ("record", "library", "analyse"):
                r = await c.get(f"/{x}" + ("?rec=abc" if x == "analyse" else ""))
                assert r.status == 200 and await r.text() == f"<h1>{x} stub</h1>"
                assert (await (await c.get(f"/{x}/app.js")).text()) == f"// {x}"
            assert (await c.get("/record.html")).status == 404
            assert (await c.get("/record/index.html")).status == 200  # asset dir, not a page URL
    asyncio.run(go())


def test_missing_page_is_404(mk_settings, tmp_path):
    async def go():
        async with client_for(create_app(mk_settings(), web_dir=tmp_path / "empty")) as c:
            assert (await c.get("/record")).status == 404
    asyncio.run(go())

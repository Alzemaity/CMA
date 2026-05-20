import pathlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cma.database import init_db

BASE_DIR = pathlib.Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR = BASE_DIR / "web" / "static"

app = FastAPI(title="CMA - تحليل السوق المقارن", version="0.1.0")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
async def startup():
    await init_db()


def register_routes():
    from cma.api.routes import router as api_router
    from cma.web.views import router as web_router

    app.include_router(api_router, prefix="/api")
    app.include_router(web_router)


register_routes()

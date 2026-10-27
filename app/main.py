"""Attestation report service: a standalone page at /attestation.

A teacher logs in with the RITM portal account: the portal checks the password, the
service keeps the portal's session in memory and forgets the password at once. On the
page the teacher picks the period, faculty, department and its directions of groups (or only
some groups of them), and the service builds the reports from the portal's data.

A finished report can be shared: /attestation/share#<token> shows it without a login until the
link expires or is revoked; a link can be permanent (see shares.py).
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, Optional

import anyio
from fastapi import FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .jobs import JobRejected, JobRunner, downloads
from .logging_config import configure_logging
from .portal import Period, ReportRequest, RitmPortal
from .presets import Preset, Presets
from .security import LoginLimiter, Session, SessionStore
from .shares import LIFETIMES_DAYS, Share, ShareRejected, ShareStore

configure_logging("python-attestation-engine")
logger = logging.getLogger(__name__)

BASE = "/attestation"
COOKIE = "attestation_session"
SHARE_HEADER = "x-share-token"
SHARE_GONE = "Ссылка недействительна: срок её действия истёк или её отозвали."
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "charts.js": "text/javascript; charset=utf-8",
    "report.js": "text/javascript; charset=utf-8",
    "share.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
LOGIN_ERRORS = {
    "wrong": "Портал РИТМ не принял логин или пароль.",
    "limit": "Слишком много неудачных попыток. Подождите 15 минут.",
    "portal": "Портал РИТМ недоступен с сервера. Попробуйте позже.",
    "expired": "Сеанс завершён. Войдите снова.",
    # The portal accepted the login, but the browser came back without the session cookie
    "cookie": (
        "Портал РИТМ принял вход, но браузер не сохранил cookie сеанса. Откройте сайт по HTTPS: "
        "по обычному HTTP защищённую cookie браузеры не сохраняют."
    ),
}
NETWORK_ERRORS = {"ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "SSLError"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@dataclass(frozen=True)
class Settings:
    cookie_secure: bool
    data_dir: Path
    files_ttl: int
    session_idle: int
    session_absolute: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cookie_secure=os.getenv("ATTESTATION_COOKIE_SECURE", "1") != "0",
            data_dir=Path(os.getenv("ATTESTATION_DATA_DIR", "/app/data")),
            files_ttl=int(os.getenv("ATTESTATION_FILES_TTL", "1800")),
            session_idle=int(os.getenv("ATTESTATION_SESSION_IDLE", "1800")),
            session_absolute=int(os.getenv("ATTESTATION_SESSION_ABSOLUTE", "28800")),
        )


class JobRequest(BaseModel):
    year: int = Field(..., ge=2008, le=2100)
    odd_semester: bool
    attestation: int = Field(..., ge=1, le=3)
    faculty: str = Field(..., pattern=r"^\d{1,10}$")
    department: str = Field(..., min_length=1, max_length=60)
    codes: list[str] = Field(..., min_length=1, max_length=60)
    # empty: every group of the directions; otherwise only these groups of them
    groups: list[str] = Field(default_factory=list, max_length=300)
    faculty_title: str = Field(..., min_length=1, max_length=200)
    head_name: str = Field("", max_length=100)


class ShareRequest(BaseModel):
    days: Optional[int]  # None: a permanent link


def current_defaults(today: Optional[datetime] = None) -> tuple[int, bool]:
    """Study year and semester by date: September-January odd, February-August even."""
    today = today or datetime.now()
    if today.month >= 9:
        return today.year, True
    if today.month == 1:
        return today.year - 1, True
    return today.year - 1, False


def client_address(request: Request) -> str:
    # The last address is the one added by our own proxy; earlier ones come from the client
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings, portal: Any = None) -> FastAPI:
    portal = portal if portal is not None else RitmPortal()
    sessions = SessionStore(settings.session_idle, settings.session_absolute)
    limiter = LoginLimiter(per_client=5, total=30, window_seconds=15 * 60)
    # a token is 256 random bits, so this is not what stops guessing; it keeps junk requests cheap
    share_limiter = LoginLimiter(per_client=30, total=1000, window_seconds=15 * 60)
    presets = Presets(settings.data_dir)
    runner = JobRunner(settings.files_ttl)
    shares = ShareStore(settings.data_dir / "shares")

    def clean_up() -> None:
        removed_jobs, removed_sessions, removed_shares = runner.cleanup(), sessions.sweep(), shares.cleanup()
        if removed_jobs or removed_sessions or removed_shares:
            logger.info(
                "Removed %d expired report jobs, %d sessions and %d shared links",
                removed_jobs, removed_sessions, removed_shares,
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async def cleaner() -> None:
            while True:
                await anyio.sleep(60)
                await anyio.to_thread.run_sync(clean_up)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(cleaner)
            yield
            tasks.cancel_scope.cancel()

    app = FastAPI(
        title="Attestation Report Service",
        description="Отчёты об аттестации групп кафедры по данным портала РИТМ",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.sessions, app.state.limiter, app.state.presets, app.state.runner = sessions, limiter, presets, runner
    app.state.shares = shares

    @app.middleware("http")
    async def protect(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        logger.info(
            "%s %s -> %s in %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            time.monotonic() - started,
        )
        return response

    def current_session(request: Request) -> Optional[Session]:
        return sessions.get(request.cookies.get(COOKIE))

    def require_session(request: Request, check_csrf: bool = False) -> Session:
        session = current_session(request)
        if session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сеанс завершён. Войдите снова.")
        # bytes: compare_digest refuses non-ASCII strings with TypeError, which would be a 500
        if check_csrf and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode("utf-8"), session.csrf.encode("utf-8")
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Запрос отклонён: устаревшая страница. Обновите её.")
        return session

    def ask_portal(session: Session, call: Callable[[], Any]) -> Any:
        """A portal call on behalf of the page, with portal failures as plain answers."""
        try:
            return call()
        except Exception as error:  # noqa: BLE001 — portal failures must reach the page as text
            name = type(error).__name__
            if name == "PortalSessionExpired":
                sessions.drop(session.token)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сеанс портала РИТМ истёк. Войдите снова.") from None
            if name == "RitmError":
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None
            if name in NETWORK_ERRORS:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, LOGIN_ERRORS["portal"]) from None
            logger.exception("Portal request failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Портал ответил ошибкой ({name}).") from None

    def page(name: str, error: str = "") -> HTMLResponse:
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        message = f'<p class="alert" role="alert">{escape(error)}</p>' if error else ""
        return HTMLResponse(html.replace("<!--error-->", message))

    @app.get("/health")
    async def health():
        return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

    @app.get(BASE)
    async def index(request: Request, error: str = ""):
        if current_session(request) is not None:
            return page("index.html")
        if error == "none":
            logger.warning("Browser came back from login without the session cookie")
            error = "cookie"
        return page("login.html", LOGIN_ERRORS.get(error, ""))

    @app.get(BASE + "/static/{name}")
    async def static(name: str):
        if name not in STATIC_FILES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого файла")
        return FileResponse(STATIC_DIR / name, media_type=STATIC_FILES[name])

    @app.post(BASE + "/login")
    def login(request: Request, username: str = Form("", max_length=200), password: str = Form("", max_length=200)):
        client = client_address(request)
        username = username.strip()
        if not limiter.allowed(client):
            logger.warning("Login refused: too many failures")
            return RedirectResponse(BASE + "?error=limit", status.HTTP_303_SEE_OTHER)
        if not username or not password:
            return RedirectResponse(BASE + "?error=wrong", status.HTTP_303_SEE_OTHER)
        try:
            portal_session = portal.login(username, password)
        except Exception as error:  # noqa: BLE001
            name = type(error).__name__
            if name in NETWORK_ERRORS:
                logger.warning("Login failed: portal unreachable (%s)", name)
                return RedirectResponse(BASE + "?error=portal", status.HTTP_303_SEE_OTHER)
            limiter.failed(client)
            logger.warning("Login refused by the portal for %s: %s", username, name)
            return RedirectResponse(BASE + "?error=wrong", status.HTTP_303_SEE_OTHER)

        limiter.succeeded(client)
        session = sessions.create(username, portal_session)
        logger.info("Login succeeded for %s", username)
        # «?error=none» is only seen when the cookie did not come back: the page then says why
        response = RedirectResponse(BASE + "?error=none", status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            COOKIE,
            session.token,
            max_age=settings.session_absolute,
            path=BASE,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post(BASE + "/logout")
    async def logout(request: Request):
        session = require_session(request, check_csrf=True)
        sessions.drop(session.token)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(COOKIE, path=BASE, secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.get(BASE + "/api/me")
    async def me(request: Request):
        session = require_session(request)
        year, odd = current_defaults()
        return {"csrf": session.csrf, "login": session.login, "year": year, "odd_semester": odd}

    def period_query(year: int, odd_semester: bool, attestation: int) -> Period:
        return Period(year, odd_semester, attestation)

    @app.get(BASE + "/api/faculties")
    def faculties(
        request: Request,
        year: int = Query(..., ge=2008, le=2100),
        odd_semester: bool = Query(...),
        attestation: int = Query(..., ge=1, le=3),
    ):
        session = require_session(request)
        period = period_query(year, odd_semester, attestation)
        return ask_portal(session, lambda: portal.faculties(session.portal, period))

    @app.get(BASE + "/api/departments")
    def departments(
        request: Request,
        year: int = Query(..., ge=2008, le=2100),
        odd_semester: bool = Query(...),
        attestation: int = Query(..., ge=1, le=3),
    ):
        session = require_session(request)
        period = period_query(year, odd_semester, attestation)
        return ask_portal(session, lambda: portal.departments(session.portal, period))

    @app.get(BASE + "/api/groups")
    def groups(
        request: Request,
        year: int = Query(..., ge=2008, le=2100),
        odd_semester: bool = Query(...),
        attestation: int = Query(..., ge=1, le=3),
        faculty: str = Query(..., pattern=r"^\d{1,10}$"),
    ):
        session = require_session(request)
        period = period_query(year, odd_semester, attestation)
        return ask_portal(session, lambda: portal.groups(session.portal, period, faculty))

    @app.get(BASE + "/api/preset")
    async def preset(request: Request, department: str = Query(..., min_length=1, max_length=60)):
        require_session(request)
        found = presets.get(department)
        return None if found is None else found.__dict__

    @app.post(BASE + "/api/jobs", status_code=status.HTTP_202_ACCEPTED)
    def start_job(request: Request, body: JobRequest):
        session = require_session(request, check_csrf=True)
        codes = frozenset(code.strip() for code in body.codes if code.strip() and len(code) <= 40)
        if not codes:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Отметьте хотя бы одно направление групп.")
        groups = frozenset(name.strip() for name in body.groups if name.strip() and len(name) <= 40)
        report = ReportRequest(
            period=Period(body.year, body.odd_semester, body.attestation),
            faculty=body.faculty,
            department=body.department.strip(),
            codes=codes,
            faculty_title=body.faculty_title.strip(),
            head_name=body.head_name.strip(),
            groups=groups,
        )
        semester = "нечётный" if body.odd_semester else "чётный"
        named = ("группа " if len(groups) == 1 else "группы ") + ", ".join(sorted(groups))
        scope = report.department + (f", {named}" if groups else "")
        title = f"{scope}: аттестация №{body.attestation}, {semester} семестр {body.year}-{body.year + 1}"

        def work(workdir: Path, log: Callable[[str], None]) -> Path:
            result = portal.build(session.portal, report, workdir, log)
            if groups:
                # a report on a few groups says nothing about which directions the department has
                return result
            # Remembered only after a successful report, so a mistaken choice is not kept
            presets.save(
                report.department,
                Preset(
                    faculty=report.faculty,
                    codes=sorted(report.codes),
                    faculty_title=report.faculty_title,
                    head_name=report.head_name,
                ),
            )
            return result

        try:
            job = runner.start(session.token, title, work)
        except JobRejected as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
        logger.info("Report requested by %s: %s", session.login, title)
        return job.public()

    @app.get(BASE + "/api/jobs/{job_id}")
    async def job_status(request: Request, job_id: str):
        session = require_session(request)
        job = runner.get(job_id, session.token)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена или её файлы уже удалены.")
        return job.public()

    @app.get(BASE + "/api/jobs/{job_id}/files/{key}")
    async def job_file(request: Request, job_id: str, key: str):
        session = require_session(request)
        job = runner.get(job_id, session.token)
        if job is None or key not in job.files or not job.files[key].exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден или уже удалён.")
        path = job.files[key]
        return FileResponse(path, filename=path.name)

    @app.post(BASE + "/api/jobs/{job_id}/share", status_code=status.HTTP_201_CREATED)
    def share_job(request: Request, job_id: str, body: ShareRequest):
        session = require_session(request, check_csrf=True)
        job = runner.get(job_id, session.token)
        if job is None or job.status != "done":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена или её файлы уже удалены.")
        try:
            token, share = shares.create(session.login, job.title, job.markdown or "", job.data, job.files, body.days)
        except ShareRejected as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from None
        lifetime = "permanently" if body.days is None else f"for {body.days} days"
        logger.info("Share %s created by %s %s: %s", share.id[:8], session.login, lifetime, job.title)
        # the token is shown once: the server keeps only its hash
        return {"link": f"{BASE}/share#{token}", **share.public()}

    @app.get(BASE + "/api/shares")
    def my_shares(request: Request):
        session = require_session(request)
        return {"lifetimes": LIFETIMES_DAYS, "shares": [share.public() for share in shares.owned_by(session.login)]}

    @app.post(BASE + "/api/shares/{share_id}/permanent")
    def make_share_permanent(request: Request, share_id: str):
        session = require_session(request, check_csrf=True)
        share = shares.make_permanent(session.login, share_id)
        if share is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена: возможно, она уже истекла.")
        logger.info("Share %s made permanent by %s", share_id[:8], session.login)
        return share.public()

    @app.delete(BASE + "/api/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_share(request: Request, share_id: str):
        session = require_session(request, check_csrf=True)
        if not shares.revoke(session.login, share_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена: возможно, она уже истекла.")
        logger.info("Share %s revoked by %s", share_id[:8], session.login)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(BASE + "/share")
    async def shared_page():
        return page("share.html")

    def shared(request: Request) -> Share:
        client = client_address(request)
        if not share_limiter.allowed(client):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много неверных ссылок. Подождите 15 минут.")
        share = shares.get(request.headers.get(SHARE_HEADER, ""))
        if share is None:
            share_limiter.failed(client)
            raise HTTPException(status.HTTP_404_NOT_FOUND, SHARE_GONE)
        return share

    @app.get(BASE + "/api/shared")
    def shared_report(request: Request):
        share = shared(request)
        return {
            "title": share.title,
            "created": share.created,
            "expires": share.expires,
            "markdown": share.markdown(),
            "data": share.data(),
            "downloads": downloads(share.sizes, lambda key: f"{BASE}/api/shared/files/{key}"),
        }

    @app.get(BASE + "/api/shared/files/{key}")
    def shared_file(request: Request, key: str):
        share = shared(request)
        path = share.file(key)
        if path is None or not path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден.")
        return FileResponse(path, filename=path.name)

    @app.exception_handler(HTTPException)
    async def json_error(request: Request, error: HTTPException):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    return app


app = create_app(Settings.from_env())

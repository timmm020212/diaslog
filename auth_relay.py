"""HTTP-relay для входа по КОДУ через локального воркера.

Зачем: бот крутится на хостинге (IP дата-центра), а Telegram не доставляет код
входа, запрошенный с такого IP. Поэтому реальные Telethon-операции (send_code /
sign_in) выполняет воркер на домашнем ПК пользователя (обычный IP — код приходит),
а сюда только отдаёт готовую сессию. Бот остаётся единственным интерфейсом.

Поток (воркер сам опрашивает сервер — дружелюбно к NAT):
  пользователь вводит номер  -> job.status = phone_submitted
  воркер GET /jobs -> send_code_request -> POST .../status {code_sent}
  бот просит код, пользователь вводит -> job.status = code_submitted
  воркер -> sign_in -> POST .../session {строка сессии}  (или {need_password})
  бот сохраняет сессию, стартует слежение.

Защита — общий секрет AUTH_RELAY_TOKEN (заголовок Authorization: Bearer ...).
"""
import logging
import secrets

from aiohttp import web

log = logging.getLogger("diaslog.relay")

# статусы, по которым должен действовать воркер
ACTIONABLE = {"phone_submitted", "code_submitted", "password_submitted"}


class Job:
    def __init__(self, job_id, user_id, api_id, api_hash):
        self.id = job_id
        self.user_id = user_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = None
        self.code = None
        self.password = None
        self.status = "need_phone"
        self.dispatched_status = None  # какой статус уже отдан воркеру (чтобы не дублировать)
        self.error = None


class AuthRelay:
    """Очередь задач + HTTP-сервер. manager обрабатывает отчёты воркера."""

    def __init__(self, token, manager, bot):
        self.token = token
        self.manager = manager
        self.bot = bot
        self.jobs = {}     # job_id -> Job
        self._seq = 0

    def create_job(self, user_id, api_id, api_hash):
        # один активный job на пользователя — старый выкидываем
        for jid, job in list(self.jobs.items()):
            if job.user_id == user_id:
                self.jobs.pop(jid, None)
        self._seq += 1
        job = Job(f"j{self._seq}", user_id, api_id, api_hash)
        self.jobs[job.id] = job
        return job

    def _authorized(self, request):
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.query.get("token", "")
        return bool(self.token) and secrets.compare_digest(token, self.token)

    # ---------- HTTP-обработчики ----------
    async def handle_jobs(self, request):
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        out = []
        for job in self.jobs.values():
            if job.status in ACTIONABLE and job.status != job.dispatched_status:
                job.dispatched_status = job.status
                out.append({
                    "id": job.id, "status": job.status,
                    "api_id": job.api_id, "api_hash": job.api_hash,
                    "phone": job.phone, "code": job.code, "password": job.password,
                })
        return web.json_response({"jobs": out})

    async def handle_status(self, request):
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        job = self.jobs.get(request.match_info["id"])
        if job is None:
            return web.json_response({"error": "no job"}, status=404)
        data = await request.json()
        status = data.get("status", "")
        job.status = status
        job.dispatched_status = status  # отчёт-статус воркеру не отдаём
        job.error = data.get("error")
        try:
            await self.manager.on_relay_status(job, status, job.error)
        except Exception as e:  # noqa: BLE001
            log.warning("on_relay_status: %s", e)
        return web.json_response({"ok": True})

    async def handle_session(self, request):
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        job = self.jobs.get(request.match_info["id"])
        if job is None:
            return web.json_response({"error": "no job"}, status=404)
        data = await request.json()
        try:
            await self.manager.on_relay_session(
                job, int(data.get("me_id")), data.get("me_name") or "",
                data.get("session") or "")
        except Exception as e:  # noqa: BLE001
            log.warning("on_relay_session: %s", e)
            return web.json_response({"error": str(e)}, status=500)
        finally:
            self.jobs.pop(job.id, None)
        return web.json_response({"ok": True})

    def build_app(self):
        app = web.Application()
        app.router.add_get("/jobs", self.handle_jobs)
        app.router.add_post("/jobs/{id}/status", self.handle_status)
        app.router.add_post("/jobs/{id}/session", self.handle_session)
        app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
        return app

    async def start(self, host="0.0.0.0", port=8080):
        runner = web.AppRunner(self.build_app())
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        log.info("Auth-relay слушает %s:%d (вход по коду через воркера включён).",
                 host, port)
        return runner

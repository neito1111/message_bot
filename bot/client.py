import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from telethon import TelegramClient
from telethon.sessions import StringSession

from bot.config import API_HASH, API_ID, TG_SYNC_INTERVAL, TG_WORKER_RESTART_DELAY
from bot.db import TGAccount, async_session_maker
from bot.listener import setup_message_listener

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TGAccountSnapshot:
    id: int
    user_id: int
    phone: str | None
    session_string: str
    api_id: int | None
    api_hash: str | None
    is_active: bool

    @classmethod
    def from_model(cls, account: TGAccount) -> "TGAccountSnapshot":
        return cls(
            id=account.id,
            user_id=account.user_id,
            phone=account.phone,
            session_string=account.session_string,
            api_id=account.api_id,
            api_hash=account.api_hash,
            is_active=bool(account.is_active),
        )

    @property
    def resolved_api_id(self) -> int:
        return self.api_id or API_ID or 2040

    @property
    def resolved_api_hash(self) -> str:
        return self.api_hash or API_HASH or "b18441a1ff607e28a9a1686bc0540751"


class TGAccountWorker:
    def __init__(self, manager: "TGClientManager", account: TGAccountSnapshot):
        self.manager = manager
        self.account = account
        self.task: asyncio.Task | None = None
        self.client: TelegramClient | None = None
        self.stop_event = asyncio.Event()

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.stop_event.clear()
        self.task = asyncio.create_task(self._run(), name=f"tg-worker-{self.account.user_id}")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                logger.exception("Error disconnecting worker for user %s", self.account.user_id)
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.client = None

    async def update(self, account: TGAccountSnapshot) -> None:
        should_restart = (
            self.account.session_string != account.session_string
            or self.account.resolved_api_id != account.resolved_api_id
            or self.account.resolved_api_hash != account.resolved_api_hash
            or self.account.is_active != account.is_active
        )
        self.account = account
        if should_restart:
            logger.info("Restarting TG worker for user %s after account update", account.user_id)
            await self.stop()
            if account.is_active:
                await self.start()

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                client = TelegramClient(
                    StringSession(self.account.session_string),
                    self.account.resolved_api_id,
                    self.account.resolved_api_hash,
                    auto_reconnect=True,
                    connection_retries=None,
                    request_retries=5,
                    retry_delay=5,
                    flood_sleep_threshold=60,
                )
                await client.connect()

                if not await client.is_user_authorized():
                    logger.warning("TG client not authorized for user %s", self.account.user_id)
                    await client.disconnect()
                    return

                self.client = client
                self.manager.clients[self.account.user_id] = client
                setup_message_listener(client, self.account)
                logger.info("TG worker started for user %s", self.account.user_id)

                await client.run_until_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TG worker crashed for user %s", self.account.user_id)
            finally:
                self.manager.clients.pop(self.account.user_id, None)
                if self.client:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        logger.exception("Error during cleanup for user %s", self.account.user_id)
                self.client = None

            if not self.stop_event.is_set():
                logger.warning(
                    "TG worker for user %s will restart in %s seconds",
                    self.account.user_id,
                    TG_WORKER_RESTART_DELAY,
                )
                await asyncio.sleep(TG_WORKER_RESTART_DELAY)


class TGClientManager:
    def __init__(self):
        self.clients: dict[int, TelegramClient] = {}
        self.workers: dict[int, TGAccountWorker] = {}
        self.sync_task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()

    async def start(self) -> None:
        self.stop_event.clear()
        await self.sync_accounts()
        if not self.sync_task or self.sync_task.done():
            self.sync_task = asyncio.create_task(self._sync_loop(), name="tg-workers-sync")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass
            self.sync_task = None

        for user_id in list(self.workers.keys()):
            await self.remove_worker(user_id)

    async def _sync_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.sync_accounts()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TG worker sync loop failed")
            await asyncio.sleep(TG_SYNC_INTERVAL)

    async def sync_accounts(self) -> None:
        async with async_session_maker() as session:
            result = await session.execute(select(TGAccount).where(TGAccount.is_active == 1))
            accounts = [TGAccountSnapshot.from_model(account) for account in result.scalars().all()]

        active_user_ids = {account.user_id for account in accounts}
        for account in accounts:
            worker = self.workers.get(account.user_id)
            if worker:
                await worker.update(account)
            else:
                worker = TGAccountWorker(self, account)
                self.workers[account.user_id] = worker
                await worker.start()

        for user_id in list(self.workers.keys()):
            if user_id not in active_user_ids:
                await self.remove_worker(user_id)

    async def refresh_account(self, user_id: int) -> None:
        async with async_session_maker() as session:
            result = await session.execute(select(TGAccount).where(TGAccount.user_id == user_id))
            account = result.scalar_one_or_none()

        if not account or not account.is_active:
            await self.remove_worker(user_id)
            return

        snapshot = TGAccountSnapshot.from_model(account)
        worker = self.workers.get(user_id)
        if worker:
            await worker.update(snapshot)
        else:
            worker = TGAccountWorker(self, snapshot)
            self.workers[user_id] = worker
            await worker.start()

    async def remove_worker(self, user_id: int) -> None:
        worker = self.workers.pop(user_id, None)
        if worker:
            await worker.stop()
        self.clients.pop(user_id, None)

    def get_client(self, user_id: int) -> TelegramClient | None:
        return self.clients.get(user_id)


tg_manager = TGClientManager()

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, Boolean, func, text
from sqlalchemy.dialects.sqlite import JSON
import enum
from datetime import datetime
from bot.config import DB_URL, DB_POOL_SIZE, DB_MAX_OVERFLOW

engine_kwargs = {
    "echo": False,
    "pool_pre_ping": True,
}
if not DB_URL.startswith("sqlite"):
    engine_kwargs["pool_size"] = DB_POOL_SIZE
    engine_kwargs["max_overflow"] = DB_MAX_OVERFLOW

engine = create_async_engine(DB_URL, **engine_kwargs)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class UserStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class RequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class EventType(str, enum.Enum):
    REGA = "REGA"
    DEP = "DEP"
    DO_DEP = "DO_DEP"

class ChatStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEP_DONE = "DEP_DONE"
    DO_DEP_DONE = "DO_DEP_DONE"

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tg_account: Mapped["TGAccount | None"] = relationship(back_populates="user", uselist=False)
    dm_profile: Mapped["DMProfile | None"] = relationship(back_populates="user", uselist=False)
    access_request: Mapped["AccessRequest | None"] = relationship(back_populates="user", uselist=False, foreign_keys="AccessRequest.user_id")

    @property
    def is_admin(self) -> bool:
        """Проверяет, является ли пользователь админом"""
        from bot.config import ADMIN_IDS
        return self.tg_id in ADMIN_IDS

class TGAccount(Base):
    __tablename__ = "tg_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    api_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_string: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="tg_account")
    chats: Mapped[list["Chat"]] = relationship(back_populates="tg_account")

class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus), default=RequestStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    user: Mapped["User"] = relationship(back_populates="access_request", foreign_keys=[user_id])
    processed_by: Mapped["User | None"] = relationship(foreign_keys=[processed_by_id])

class DMProfile(Base):
    __tablename__ = "dm_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    tag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Старые поля (для совместимости)
    message1: Mapped[str | None] = mapped_column(Text, nullable=True)
    message2: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Новые поля для трекинга
    greeting_message1: Mapped[str | None] = mapped_column(Text, nullable=True)  # Сообщение после приветствия 1
    greeting_message2: Mapped[str | None] = mapped_column(Text, nullable=True)  # Сообщение после приветствия 2
    dodep_keyword: Mapped[str | None] = mapped_column(Text, nullable=True)     # Ключевая фраза для додепа

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="dm_profile")

class EventSequence(Base):
    """Отслеживает последовательность событий REGA -> DEP -> DO_DEP для каждого чата"""
    __tablename__ = "event_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_account_id: Mapped[int] = mapped_column(ForeignKey("tg_accounts.id"), nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Статусы прохождения этапов
    rega_done: Mapped[bool] = mapped_column(Integer, default=0)
    dep_done: Mapped[bool] = mapped_column(Integer, default=0)
    dodep_done: Mapped[bool] = mapped_column(Integer, default=0)

    # Данные для отслеживания
    rega_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # ID первого сообщения (рега)
    dep_messages_count: Mapped[int] = mapped_column(Integer, default=0)  # Кол-во сообщений от юзера после деп
    user_messages_after_rega: Mapped[int] = mapped_column(Integer, default=0)  # Кол-во сообщений от юзера после реги

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # No back_populates - просто foreign key

class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_account_id: Mapped[int] = mapped_column(ForeignKey("tg_accounts.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user_first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    first_message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ChatStatus] = mapped_column(Enum(ChatStatus), default=ChatStatus.ACTIVE, index=True)
    dep_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tg_account: Mapped["TGAccount"] = relationship(back_populates="chats")
    events: Mapped[list["Event"]] = relationship(back_populates="chat")

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), index=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), index=True)
    event_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chat: Mapped["Chat"] = relationship(back_populates="events")

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_safe_migrations(conn)


async def _run_safe_migrations(conn: AsyncSession) -> None:
    dialect = conn.dialect.name

    async def column_exists(table: str, column: str) -> bool:
        if dialect == "sqlite":
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            return any(row[1] == column for row in result.fetchall())

        result = await conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                """
            ),
            {"table_name": table, "column_name": column},
        )
        return result.first() is not None

    if not await column_exists("tg_accounts", "api_id"):
        await conn.execute(text("ALTER TABLE tg_accounts ADD COLUMN api_id INTEGER"))
    if not await column_exists("tg_accounts", "api_hash"):
        await conn.execute(text("ALTER TABLE tg_accounts ADD COLUMN api_hash VARCHAR(128)"))

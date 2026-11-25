"""ORM-модели. Схема соответствует ТЗ: проекты + группы поиска + находки.

Модель связей (по ТЗ):
- projects — что проверяем (название + ключевые фразы);
- search_sources — где проверяем (площадка + URL группы), глобальный справочник;
- project_sources — любой активный проект ищется во всех группах поиска,
  связь подвязывается автоматически при создании/импорте проекта или группы.
"""
import enum
import hashlib
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Platform(str, enum.Enum):
    VK = "vk"
    TELEGRAM = "telegram"
    OK = "ok"
    MYMIR = "mymir"
    RUTUBE = "rutube"


class SourceType(str, enum.Enum):
    GROUP = "group"        # группа/сообщество
    CHANNEL = "channel"    # телеграм-канал
    ALBUM = "album"        # альбом/медиа
    USER = "user"


class FindingStatus(str, enum.Enum):
    NEW = "new"                # найдено, ждёт ручной проверки
    CONFIRMED = "confirmed"    # подтверждено: пиратство
    FALSE_POSITIVE = "false_positive"
    IGNORED = "ignored"        # белый список / не нарушает


class ProjectKeyword(Base):
    """Ключевая фраза проекта — дополнительно к названию используется при поиске."""

    __tablename__ = "project_keywords"
    __table_args__ = (
        UniqueConstraint("project_id", "phrase_hash", name="uq_project_keyword"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    phrase: Mapped[str] = mapped_column(String(512), nullable=False)
    phrase_hash: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, default=""
    )  # md5 от нормализованной фразы — по ней уникальность
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="keywords")

    def __repr__(self) -> str:
        # читаемый вывод (в т.ч. в Jinja-шаблонах, если объект попадает в template as-is)
        return self.phrase

    @staticmethod
    def normalize_phrase(phrase: str) -> str:
        return " ".join((phrase or "").split()).casefold()

    def compute_phrase_hash(self) -> None:
        self.phrase_hash = hashlib.md5(
            self.normalize_phrase(self.phrase).encode()
        ).hexdigest()


class Project(Base):
    """Проверяемый проект: название + ключевые фразы.

    Группы, где ищем, не привязываются к проекту вручную: любой активный
    проект автоматически ищется во всех группах из search_sources
    (связи — в таблице project_sources).
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128))   # внешний id проекта
    description: Mapped[str | None] = mapped_column(Text)          # описание / примечания
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    keywords: Mapped[list["ProjectKeyword"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    source_links: Mapped[list["ProjectSource"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def search_terms(self) -> list[str]:
        """Строки для поиска: название + все ключевые фразы."""
        return [self.title] + [k.phrase for k in self.keywords]


class SearchSource(Base):
    """Группа поиска (глобальный справочник): площадка + URL.

    Привязывается ко всем активным проектам автоматически — см. ProjectSource.
    """

    __tablename__ = "search_sources"
    __table_args__ = (
        UniqueConstraint("platform", "url_hash", name="uq_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, values_callable=lambda e: [m.value for m in e])
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)  # ссылка на группу/канал
    url_hash: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, default=""
    )  # md5 от нормализованного URL — по нему уникальность (полный url в индекс не влезает: utf8mb4 лимит 3072 байта)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, values_callable=lambda e: [m.value for m in e]),
        default=SourceType.GROUP,
    )
    title: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project_links: Mapped[list["ProjectSource"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    @staticmethod
    def normalize_url(url: str) -> str:
        return (url or "").strip().rstrip("/").lower()

    def compute_url_hash(self) -> None:
        self.url_hash = hashlib.md5(self.normalize_url(self.url).encode()).hexdigest()


class ProjectSource(Base):
    """Связь «проект ↔ группа поиска». Подвязывается автоматически:
    каждый активный проект ищется во всех группах."""

    __tablename__ = "project_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "source_id", name="uq_project_source"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("search_sources.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="source_links")
    source: Mapped[SearchSource] = relationship(back_populates="project_links")


class Finding(Base):
    """Находка: потенциальный пиратский материал, найденный при проверке."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("project_id", "platform", "url_hash", name="uq_finding_dedupe"),
        Index("ix_findings_status_first_seen", "status", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[Platform] = mapped_column(Enum(Platform, values_callable=lambda e: [m.value for m in e]))
    url: Mapped[str] = mapped_column(String(2048))
    url_hash: Mapped[str] = mapped_column(String(32), index=True)  # md5 от нормализованного URL
    post_id: Mapped[str | None] = mapped_column(String(256))
    author: Mapped[str | None] = mapped_column(String(512))
    snippet: Mapped[str | None] = mapped_column(Text)
    matched_title: Mapped[str | None] = mapped_column(String(512))
    match_score: Mapped[float | None] = mapped_column(Float)
    duration_sec: Mapped[int | None] = mapped_column(Integer)   # для видеофильтра
    external_links: Mapped[str | None] = mapped_column(Text)    # JSON-список ссылок наружу
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, values_callable=lambda e: [m.value for m in e]),
        default=FindingStatus.NEW,
        nullable=False,
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kw):
        super().__init__(**kw)
        # url_hash обязателен для уникальности/дедупликации — считаем сами
        if not kw.get("url_hash") and self.url:
            self.url_hash = hashlib.md5(
                self.url.strip().rstrip("/").lower().encode()
            ).hexdigest()

    project: Mapped[Project] = relationship(back_populates="findings")


class CheckRun(Base):
    """История запусков проверок (по расписанию или вручную)."""

    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    triggered_by: Mapped[str] = mapped_column(String(32), default="scheduler")
    projects_checked: Mapped[int] = mapped_column(Integer, default=0)
    new_findings: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

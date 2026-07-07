"""비동기 DB 연결 관리.

개발/데모: sqlite+aiosqlite (파일 하나, 설치 불필요)
운영:      postgresql+asyncpg (LAARK_DATABASE_URL 만 교체)

SQLAlchemy 2.0 async 인터페이스만 사용하므로 두 환경에서 동일 코드로 동작한다.
"""

import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db.models import Base

logger = logging.getLogger(__name__)


class Database:
    """엔진/세션 팩토리 수명주기 관리. 앱 기동 시 init(), 종료 시 dispose()."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def is_initialized(self) -> bool:
        return self._engine is not None

    async def init(self, url: str) -> None:
        """엔진 생성 + 테이블 생성(없을 때만).

        운영 전환 시 create_all 대신 Alembic 마이그레이션으로 교체한다.
        """
        self._engine = create_async_engine(url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("DB 초기화 완료: %s", url.split("://")[0])

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    def session(self) -> AsyncSession:
        """새 세션 반환. `async with database.session() as s:` 로 사용."""
        if self._session_factory is None:
            raise RuntimeError("Database 가 초기화되지 않았습니다. init() 을 먼저 호출하세요.")
        return self._session_factory()


# 앱 전역 공유 싱글턴
database = Database()

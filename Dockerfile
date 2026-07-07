# ── LaarkBlocks AI Engine (백엔드) ─────────────────────────────
# 컨테이너 환경에서는 PostgreSQL 을 사용하므로 asyncpg 를 추가 설치한다.
# (requirements.txt 는 로컬 개발 기본값인 SQLite 구성을 유지)

FROM python:3.12-slim

# 보안: 루트가 아닌 전용 사용자로 실행
RUN useradd --create-home --shell /bin/false laark

WORKDIR /app

# 의존성 레이어 분리 — 소스 변경 시 pip install 캐시 재사용
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt asyncpg

# 애플리케이션 소스 (테스트/프론트/문서는 .dockerignore 로 제외)
COPY src/ src/
COPY configs/ configs/

USER laark
EXPOSE 8000

# 오케스트레이터용 헬스체크 — slim 이미지에 curl 이 없으므로 표준 라이브러리 사용
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

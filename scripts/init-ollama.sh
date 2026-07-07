#!/bin/sh
# ── Ollama 모델 자동 초기화 ─────────────────────────────────────
# docker compose 의 ollama-init 서비스가 실행한다.
# 1) ollama 서버가 응답할 때까지 대기
# 2) 지정 모델이 없으면 pull (이미 있으면 즉시 종료 — 재기동 시 무비용)
#
# OLLAMA_HOST  : 대상 서버 (compose 가 http://ollama:11434 로 주입)
# OLLAMA_MODEL : 받을 모델 (기본 qwen3:8b — 저사양이면 qwen3:4b 권장)

set -eu

MODEL="${OLLAMA_MODEL:-qwen3:8b}"
HOST="${OLLAMA_HOST:-http://ollama:11434}"

echo "[init-ollama] 대상: ${HOST}, 모델: ${MODEL}"

# 1) 서버 기동 대기 (최대 60회 × 2초 = 2분)
i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "[init-ollama] ERROR: ollama 서버가 응답하지 않습니다 (${HOST})" >&2
    exit 1
  fi
  echo "[init-ollama] ollama 서버 대기 중... (${i}/60)"
  sleep 2
done

# 2) 모델 존재 확인 후 pull — 볼륨에 캐시되므로 최초 1회만 다운로드된다
if ollama list | awk '{print $1}' | grep -qx "${MODEL}"; then
  echo "[init-ollama] 모델이 이미 존재합니다: ${MODEL} — 건너뜀"
else
  echo "[init-ollama] 모델 다운로드 시작: ${MODEL} (수 GB — 최초 1회만)"
  ollama pull "${MODEL}"
  echo "[init-ollama] 다운로드 완료: ${MODEL}"
fi

echo "[init-ollama] 준비 완료 — 플레이그라운드에서 바로 테스트하세요."

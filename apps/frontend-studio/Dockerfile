# ── LaarkBlocks Studio (프론트엔드) — 멀티스테이지 빌드 ────────
# 1단계: Vite 프로덕션 빌드 / 2단계: nginx 정적 서빙 + API 프록시
#
# 개발 환경(vite dev)의 /api 프록시는 localhost:8000 을 향하지만,
# 도커 네트워크 안에서는 서비스 이름(ai-engine)으로 통신해야 한다.
# 이 역할을 nginx.conf 의 proxy_pass 가 담당한다.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

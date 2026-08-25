FROM oven/bun:1.3-alpine AS runner
WORKDIR /app

COPY package.json ./
RUN bun install --production || true

COPY server.ts ./
COPY src ./src

EXPOSE 3737
ENV PORT=3737

CMD ["bun", "run", "server.ts"]

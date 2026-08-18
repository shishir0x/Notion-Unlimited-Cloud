FROM node:20-alpine

WORKDIR /app

COPY notion-drive-app/package*.json ./
RUN npm ci

COPY notion-drive-app/ ./
RUN npm run build

ENV PORT=3000
EXPOSE 3000

CMD ["npm", "start"]

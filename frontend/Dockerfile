FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM dependencies AS development
COPY . .
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "80", "--strictPort"]

FROM dependencies AS build
COPY . .
RUN npm run build

FROM nginx:1.28-alpine AS production
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

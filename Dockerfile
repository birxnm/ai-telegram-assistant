FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для psycopg2-binary не нужны (бинарные колёса), оставляем образ лёгким.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

# По умолчанию (облако): индексация + бот. docker-compose переопределяет команды по сервисам.
CMD ["sh", "start.sh"]

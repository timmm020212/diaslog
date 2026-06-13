FROM python:3.12-slim

WORKDIR /app

# Зависимости ставим отдельным слоем (кэшируется)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код (данные и .env монтируются как volume во время запуска)
COPY *.py ./

EXPOSE 8000

# Слушать на всех интерфейсах внутри контейнера; наружу порт пробрасывается
# только на localhost хоста (см. docker-compose.yml).
ENV DIASLOG_HOST=0.0.0.0

CMD ["python", "app.py"]

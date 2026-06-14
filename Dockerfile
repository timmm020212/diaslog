FROM python:3.12-slim

WORKDIR /app

# Зависимости ставим отдельным слоем (кэшируется)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код (данные и .env монтируются как volume во время запуска)
COPY *.py ./

# Боты работают в фоне и шлют перехваты в Telegram — веб-порт не нужен.
CMD ["python", "run.py"]

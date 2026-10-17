FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY healer ./healer
EXPOSE 8090
CMD ["uvicorn", "healer.main:app", "--host", "0.0.0.0", "--port", "8090"]

FROM python:3.12-slim
WORKDIR /app
COPY notification-service/requirements.txt .
RUN pip install -r requirements.txt
COPY notification-service/ .
COPY common/ /app/common/
CMD ["python", "app.py"]

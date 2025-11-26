FROM python:3.12-slim
WORKDIR /app
COPY order-service/requirements.txt .
RUN pip install -r requirements.txt
COPY order-service/ .
COPY common/ /app/common/
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]

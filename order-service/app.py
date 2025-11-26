import os
import sys
from fastapi import FastAPI, Depends, HTTPException
import requests
import pika
from pika.credentials import PlainCredentials
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base

# Добавляем путь к common модулю
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.logging_config import setup_logging
from common.middleware import LoggingMiddleware, setup_metrics_endpoint

app = FastAPI(title="Order Service")

# Настройка логирования
logger = setup_logging("order-service")

# Добавляем middleware для логирования и метрик
app.add_middleware(LoggingMiddleware, service_name="order-service", logger=logger)
setup_metrics_endpoint(app, "order-service")

logger.info("Order Service started")

RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
USER_SERVICE_URL = os.getenv('USER_SERVICE_URL', 'http://localhost:8000')
DATABASE_URL = os.getenv('DATABASE_URL', "postgresql://user:password@db:5432/userdb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer)
    items = Column(String)
    address = Column(String)
    status = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def send_notification(message: str):  # Функция для отправки в RabbitMQ
    credentials = PlainCredentials('guest', 'guest123')
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        credentials=credentials
    ))
    channel = connection.channel()
    channel.queue_declare(queue='notifications', durable=True)
    channel.basic_publish(exchange='', routing_key='notifications', body=message,
                          properties=pika.BasicProperties(delivery_mode=2))
    connection.close()

@app.post("/create_order")
def create_order(user_id: int, items: str, db: Session = Depends(get_db)):
    logger.info(f"Creating order for user: {user_id}")
    # Вложенный вызов к User Service
    try:
        user_response = requests.get(f"{USER_SERVICE_URL}/user/{user_id}")
        user_response.raise_for_status()
        user_data = user_response.json()
        logger.info(f"User data fetched for user: {user_id}")
    except Exception as e:
        logger.error(f"Failed to fetch user data: {e}")
        raise HTTPException(status_code=404, detail="User not found or service unavailable")
    
    order = Order(user_id=user_id, items=items, address=user_data["address"], status="created")
    db.add(order)
    db.commit()
    db.refresh(order)
    send_notification(f"Order created for user {user_id}")  # Асинхронное уведомление
    logger.info(f"Order created successfully: {order.id}")
    return {"message": "Order created", "order": {"id": order.id, "user_id": order.user_id, "items": order.items, "address": order.address, "status": order.status}}

@app.get("/orders/{user_id}")
def get_orders(user_id: int, db: Session = Depends(get_db)):
    logger.info(f"Fetching orders for user: {user_id}")
    orders = db.query(Order).filter(Order.user_id == user_id).all()
    logger.info(f"Found {len(orders)} orders for user {user_id}")
    return {"orders": [{"id": o.id, "items": o.items, "status": o.status} for o in orders]}

@app.put("/update_order/{order_id}")
def update_order(order_id: int, status: str, db: Session = Depends(get_db)):
    logger.info(f"Updating order {order_id} to status: {status}")
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        logger.warning(f"Order not found: {order_id}")
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = status
    db.commit()
    logger.info(f"Order {order_id} updated successfully to {status}")
    return {"message": f"Order {order_id} updated to {status}"}

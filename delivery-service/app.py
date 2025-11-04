from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os
import requests
import pika
from pika.credentials import PlainCredentials

app = FastAPI(title="Delivery Service")

DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8001")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Delivery(Base):
    __tablename__ = "deliveries"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer)
    courier_id = Column(Integer)
    status = Column(String, default="assigned")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/assign/{order_id}")
def assign_delivery(order_id: int, courier_id: int, db: Session = Depends(get_db)):
    # Проверяем заказ
    try:
        order_resp = requests.get(f"{ORDER_SERVICE_URL}/orders/{order_id}")
        order_resp.raise_for_status()
    except:
        raise HTTPException(404, "Order not found")

    delivery = Delivery(order_id=order_id, courier_id=courier_id, status="in_transit")
    db.add(delivery)
    db.commit()

    # Уведомляем
    credentials = PlainCredentials('guest', 'guest123')
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials))
    channel = connection.channel()
    channel.queue_declare(queue='notifications', durable=True)
    channel.basic_publish(exchange='', routing_key='notifications', body=f"Delivery assigned: order {order_id}")
    connection.close()

    return {"status": "assigned", "courier_id": courier_id}

@app.get("/deliveries/order/{order_id}")
def get_delivery(order_id: int, db: Session = Depends(get_db)):
    delivery = db.query(Delivery).filter(Delivery.order_id == order_id).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return {"delivery_id": delivery.id, "courier_id": delivery.courier_id, "status": delivery.status}

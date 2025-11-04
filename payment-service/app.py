from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os
import pika
from pika.credentials import PlainCredentials

app = FastAPI(title="Payment Service")

DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer)
    amount = Column(Float)
    status = Column(String, default="pending")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def publish_event(event: str, data: dict):
    credentials = PlainCredentials('guest', 'guest123')
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials))
    channel = connection.channel()
    channel.exchange_declare(exchange='payment_events', exchange_type='fanout')
    channel.basic_publish(exchange='payment_events', routing_key='', body=f"{event}:{data}")
    connection.close()

@app.post("/pay/{order_id}")
def pay_order(order_id: int, amount: float, db: Session = Depends(get_db)):
    payment = Payment(order_id=order_id, amount=amount, status="completed")
    db.add(payment)
    db.commit()
    publish_event("PaymentCompleted", {"order_id": order_id, "amount": amount})
    return {"status": "paid", "payment_id": payment.id}

@app.get("/payments/order/{order_id}")
def get_payment_by_order(order_id: int, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"payment_id": payment.id, "amount": payment.amount, "status": payment.status}

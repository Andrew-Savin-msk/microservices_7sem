from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os
import pika
from pika.credentials import PlainCredentials

app = FastAPI(title="Catalog Service")

DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Dish(Base):
    __tablename__ = "dishes"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(String)
    price = Column(Float)
    restaurant_id = Column(Integer)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def send_event(event: str, data: dict):
    credentials = PlainCredentials('guest', 'guest123')
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials))
    channel = connection.channel()
    channel.exchange_declare(exchange='catalog_events', exchange_type='fanout')
    channel.basic_publish(exchange='catalog_events', routing_key='', body=f"{event}:{data}")
    connection.close()

@app.post("/dishes/")
def create_dish(name: str, description: str, price: float, restaurant_id: int, db: Session = Depends(get_db)):
    dish = Dish(name=name, description=description, price=price, restaurant_id=restaurant_id)
    db.add(dish)
    db.commit()
    db.refresh(dish)
    send_event("DishCreated", {"id": dish.id, "name": name})
    return {"id": dish.id, "name": name}

@app.get("/dishes/restaurant/{restaurant_id}")
def get_dishes(restaurant_id: int, db: Session = Depends(get_db)):
    dishes = db.query(Dish).filter(Dish.restaurant_id == restaurant_id).all()
    return [{"id": d.id, "name": d.name, "price": d.price} for d in dishes]

@app.get("/dishes/{dish_id}")
def get_dish(dish_id: int, db: Session = Depends(get_db)):
    dish = db.query(Dish).filter(Dish.id == dish_id).first()
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    return {"id": dish.id, "name": dish.name, "price": dish.price, "description": dish.description}

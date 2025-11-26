from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os
import sys
import pika
from pika.credentials import PlainCredentials

# Добавляем путь к common модулю
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.logging_config import setup_logging
from common.middleware import LoggingMiddleware, setup_metrics_endpoint

app = FastAPI(title="Catalog Service")

# Настройка логирования
logger = setup_logging("catalog-service")

# Добавляем middleware для логирования и метрик
app.add_middleware(LoggingMiddleware, service_name="catalog-service", logger=logger)
setup_metrics_endpoint(app, "catalog-service")

logger.info("Catalog Service started")

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
    logger.info(f"Creating dish: {name} for restaurant {restaurant_id}")
    dish = Dish(name=name, description=description, price=price, restaurant_id=restaurant_id)
    db.add(dish)
    db.commit()
    db.refresh(dish)
    send_event("DishCreated", {"id": dish.id, "name": name})
    logger.info(f"Dish created successfully: {dish.id}")
    return {"id": dish.id, "name": name}

@app.get("/dishes/restaurant/{restaurant_id}")
def get_dishes(restaurant_id: int, db: Session = Depends(get_db)):
    logger.info(f"Fetching dishes for restaurant: {restaurant_id}")
    dishes = db.query(Dish).filter(Dish.restaurant_id == restaurant_id).all()
    logger.info(f"Found {len(dishes)} dishes for restaurant {restaurant_id}")
    return [{"id": d.id, "name": d.name, "price": d.price} for d in dishes]

@app.get("/dishes/{dish_id}")
def get_dish(dish_id: int, db: Session = Depends(get_db)):
    logger.info(f"Fetching dish: {dish_id}")
    dish = db.query(Dish).filter(Dish.id == dish_id).first()
    if not dish:
        logger.warning(f"Dish not found: {dish_id}")
        raise HTTPException(status_code=404, detail="Dish not found")
    logger.info(f"Dish fetched successfully: {dish_id}")
    return {"id": dish.id, "name": dish.name, "price": dish.price, "description": dish.description}

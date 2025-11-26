from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os
import sys

# Добавляем путь к common модулю
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.logging_config import setup_logging
from common.middleware import LoggingMiddleware, setup_metrics_endpoint

app = FastAPI(title = "User Service")

# Настройка логирования
logger = setup_logging("user-service")

# Добавляем middleware для логирования и метрик
app.add_middleware(LoggingMiddleware, service_name="user-service", logger=logger)
setup_metrics_endpoint(app, "user-service")

logger.info("User Service started")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/userdb") 
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    address = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def validate_user_data(username: str, password: str):  # Вложенная функция для валидации
    if len(password) < 6:
        raise ValueError("Password too short")
    return True

@app.post("/register")
def register(username: str, password: str, address: str, db: Session = Depends(get_db)):
    logger.info(f"Registering user: {username}")
    validate_user_data(username, password)  # Вызов вложенной функции
    user = User(username=username, password=password, address=address)
    db.add(user)
    db.commit()
    logger.info(f"User registered successfully: {username}")
    return {"message": "User registered"}

@app.post("/login")
def login(username: str, password: str, db: Session = Depends(get_db)):
    logger.info(f"Login attempt for user: {username}")
    user = db.query(User).filter(User.username == username).first()
    if not user or user.password != password:
        logger.warning(f"Invalid login attempt for user: {username}")
        raise HTTPException(status_code=400, detail="Invalid credentials")
    logger.info(f"User logged in successfully: {username}")
    return {"message": "Logged in"}

@app.put("/update_profile/{user_id}")
def update_profile(user_id: int, address: str, db: Session = Depends(get_db)):
    logger.info(f"Updating profile for user_id: {user_id}")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        logger.warning(f"User not found: {user_id}")
        raise HTTPException(status_code=404, detail="User not found")
    user.address = address
    db.commit()
    logger.info(f"Profile updated for user_id: {user_id}")
    return {"message": "Profile updated"}

@app.get("/user/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    logger.info(f"Fetching user: {user_id}")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        logger.warning(f"User not found: {user_id}")
        raise HTTPException(status_code=404, detail="User not found")
    logger.info(f"User fetched successfully: {user_id}")
    return {"username": user.username, "address": user.address}

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os

app = FastAPI(title = "User Service")

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
    validate_user_data(username, password)  # Вызов вложенной функции
    user = User(username=username, password=password, address=address)
    db.add(user)
    db.commit()
    return {"message": "User registered"}

@app.post("/login")
def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or user.password != password:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    return {"message": "Logged in"}

@app.put("/update_profile/{user_id}")
def update_profile(user_id: int, address: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.address = address
    db.commit()
    return {"message": "Profile updated"}

@app.get("/user/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": user.username, "address": user.address}

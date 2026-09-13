from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(50), unique=True, nullable=False)
    username = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    daily_new_limit = Column(Integer, default=20)
    created_at = Column(DateTime, default=datetime.utcnow)
    streak = Column(Integer, default=0)
    last_review_date = Column(DateTime, nullable=True)
    freeze_tokens = Column(Integer, default=0)


class UserSettings(Base):
    __tablename__ = "user_settings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    
    smart_mix_enabled = Column(Boolean, default=True)
    elaborative_enabled = Column(Boolean, default=True)
    interleaving_enabled = Column(Boolean, default=False)
    confidence_check_enabled = Column(Boolean, default=False)
    zen_mode_default = Column(Boolean, default=False)
    pomodoro_enabled = Column(Boolean, default=False)
    streak_protection_enabled = Column(Boolean, default=True)
    
    daily_target = Column(Integer, default=20)
    default_mode = Column(String, default="SM2")


class Deck(Base):
    __tablename__ = "decks"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Card(Base):
    __tablename__ = "cards"
    id = Column(Integer, primary_key=True)
    question = Column(String(1000), nullable=False)
    answer = Column(String(2000), nullable=False)
    explanation = Column(String(3000), default="")
    deck_id = Column(Integer, ForeignKey("decks.id"), nullable=False)
    
    interval = Column(Float, default=0.0)
    ease_factor = Column(Float, default=2.5)
    repetitions = Column(Integer, default=0)
    next_review_date = Column(DateTime, nullable=True)
    
    tags = Column(String(500), default="")
    related_cards = Column(String(500), default="")
    
    forgot_count = Column(Integer, default=0)
    total_reviews = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class ReviewLog(Base):
    __tablename__ = "review_logs"
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quality = Column(Integer, nullable=False)
    
    interval_before = Column(Float, default=0.0)
    interval_after = Column(Float, default=0.0)
    ease_before = Column(Float, default=2.5)
    ease_after = Column(Float, default=2.5)
    
    confidence_score = Column(Integer, nullable=True)
    user_explanation = Column(String(2000), nullable=True)
    mode = Column(String, default="SM2")
    
    reviewed_at = Column(DateTime, default=datetime.utcnow)
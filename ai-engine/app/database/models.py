from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Text, DateTime
from app.database.session import Base, engine

class PaperTrade(Base):
    """Paper trading decisions with T+2/T+5 P&L tracking"""
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True, nullable=False)
    action = Column(String, nullable=False)  # BUY, SELL, HOLD
    price = Column(Float, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)  # Decision date
    confidence = Column(Float, default=0.0)
    thesis = Column(Text, nullable=True)
    pnl = Column(Float, nullable=True)  # T+2, T+5 actual P&L
    status = Column(String, default="OPEN")  # OPEN, CLOSED
    resolve_price = Column(Float, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class SessionLog(Base):
    """Session run logs for audit trail"""
    __tablename__ = "run_logs"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True, nullable=False)
    intent = Column(String, nullable=False)  # CHAT, RESEARCH, SIGNAL
    pipeline = Column(String, nullable=False)  # simple, graph
    model_used = Column(String, nullable=False)  # gemini, groq, openrouter
    duration_ms = Column(Integer, nullable=True)
    status = Column(String, default="running")  # running, completed, failed
    ticker = Column(String, index=True, nullable=True)
    result = Column(Text, nullable=True)  # JSON string of final decision
    created_at = Column(DateTime, default=datetime.utcnow)

# Initialize database
def init_db():
    Base.metadata.create_all(bind=engine)

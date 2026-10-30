import enum
import os
from datetime import datetime, timezone
from typing import Generator
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Date as SQLDate,
    ForeignKey,
    Enum as SQLEnum,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

# Load .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Database connection setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./analytics.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Enum for Subject Categorization
class ArticleSubject(str, enum.Enum):
    MOLECULE = "MOLECULE"
    BASLA = "BASLA"
    LILLI = "LILLI"
    GENERAL = "GENERAL"


# Helper for UTC timestamp
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 1. Articles Table
class Article(Base):
    __tablename__ = "articles"

    url = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    subject = Column(SQLEnum(ArticleSubject), default=ArticleSubject.BASLA, nullable=False)
    publish_date = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, default=utc_now, onupdate=utc_now)

    # Relationships
    daily_metrics = relationship("DailyMetric", back_populates="article", cascade="all, delete-orphan")
    changes = relationship("ArticleChange", back_populates="article", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Article(url='{self.url}', title='{self.title}', subject='{self.subject}')>"


# 2. Daily Metrics Table
class DailyMetric(Base):
    __tablename__ = "daily_metrics"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    date = Column(SQLDate, nullable=False, index=True)
    article_url = Column(String, ForeignKey("articles.url"), nullable=False, index=True)
    views = Column(Integer, default=0, nullable=False)
    unique_readers = Column(Integer, default=0, nullable=False)
    engagement_time = Column(Float, default=0.0, nullable=False)  # in seconds
    bounce_rate = Column(Float, default=0.0, nullable=False)

    __table_args__ = (
        UniqueConstraint("date", "article_url", name="uq_date_article_url"),
    )

    # Relationship
    article = relationship("Article", back_populates="daily_metrics")

    def __repr__(self) -> str:
        return f"<DailyMetric(date='{self.date}', article_url='{self.article_url}', views={self.views})>"


# 3. Realtime Snapshot Table
class RealtimeSnapshot(Base):
    __tablename__ = "realtime_snapshot"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)
    active_users = Column(Integer, default=0, nullable=False)
    page_title = Column(String, nullable=True)
    views = Column(Integer, default=0, nullable=False)
    country = Column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<RealtimeSnapshot(timestamp='{self.timestamp}', active_users={self.active_users}, country='{self.country}')>"


# 4. Article Changes Table
class ArticleChange(Base):
    __tablename__ = "article_changes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    article_url = Column(String, ForeignKey("articles.url"), nullable=False, index=True)
    change_date = Column(DateTime, default=utc_now, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    impact_status = Column(String, default="PENDING", nullable=True)  # e.g., POSITIVE, NEGATIVE, NEUTRAL, PENDING

    # Relationship
    article = relationship("Article", back_populates="changes")

    def __repr__(self) -> str:
        return f"<ArticleChange(article_url='{self.article_url}', change_date='{self.change_date}', impact_status='{self.impact_status}')>"


# 5. Keyword Insights Table
class KeywordInsight(Base):
    __tablename__ = "keyword_insights"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    keyword = Column(String, unique=True, index=True, nullable=False)
    site_impressions = Column(Integer, default=0, nullable=False)
    site_clicks = Column(Integer, default=0, nullable=False)
    industry_search_volume = Column(Integer, default=0, nullable=False)
    competition_level = Column(String, nullable=True)  # e.g., LOW, MEDIUM, HIGH
    suggested_subject = Column(SQLEnum(ArticleSubject), default=ArticleSubject.GENERAL, nullable=True)

    def __repr__(self) -> str:
        return f"<KeywordInsight(keyword='{self.keyword}', volume={self.industry_search_volume}, subject='{self.suggested_subject}')>"


# Dependency for FastAPI or worker scripts
def get_db() -> Generator[Session, None, None]:
    """Yield a database session context."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Database initialization function
def init_db() -> None:
    """Create all database tables."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Database tables initialized successfully.")

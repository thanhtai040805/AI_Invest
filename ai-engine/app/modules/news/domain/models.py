from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import datetime

class NewsArticle(BaseModel):
    news_id: str = Field(..., alias="newsId")
    symbol: str = "GENERAL"
    title: str
    url: str
    content: str
    publish_date: str = Field(..., alias="publishDate")
    friendly_keyword: str = Field(..., alias="friendlyKeyword")
    sentiment_label: Optional[str] = Field(None, alias="sentimentLabel")
    sentiment_score: Optional[float] = Field(None, alias="sentimentScore")

    class Config:
        populate_by_name = True

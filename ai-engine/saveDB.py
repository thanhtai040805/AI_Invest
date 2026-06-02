from IPython.core import magic_arguments
from app.services.news_ingestion import NewsIngestionService
import json
import asyncio
from app.services.news_rag import news_rag_svc

final_output_data = json.load(open("cafef_data_result.json"))

async def save_to_backend(final_output_data):
    news_ingestion_service = NewsIngestionService()
    await news_ingestion_service._send_to_backend(final_output_data)

# news_rag_svc.add_articles(final_output_data)

if __name__ == "__main__":
    asyncio.run(save_to_backend(final_output_data))
    pass
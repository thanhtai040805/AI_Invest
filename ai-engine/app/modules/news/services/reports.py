import logging
from ..domain.ports import INewsNotifier
from app.services.market_data_service import market_data_svc
from app.services.ai_service import ai_svc

logger = logging.getLogger(__name__)

class NewsReports:
    def __init__(self, notifier: INewsNotifier):
        self.notifier = notifier

    async def generate_premarket(self):
        indices_data = await market_data_svc.get_indices()
        indices_text = "\n".join([f"- {idx.get('name')}: {idx.get('value')} ({idx.get('changePercent'):+.2f}%)" for idx in indices_data.get("indices", [])])
        prompt = f"Tạo bản tin 'Trước Giờ Mở Cửa' cho VN dựa trên:\n{indices_text}"
        analysis = ai_svc._generate_analysis(prompt, {"indices": []})
        await self.notifier.post_report(analysis, "BẢN TIN TRƯỚC GIỜ MỞ CỬA")

    async def generate_midday(self):
        indices_data = await market_data_svc.get_indices()
        indices_text = "\n".join([f"- {idx.get('name')}: {idx.get('value')} ({idx.get('changePercent'):+.2f}%)" for idx in indices_data.get("indices", [])])
        prompt = f"Tạo 'Bản Tin Giữa Phiên' cho VN dựa trên:\n{indices_text}"
        analysis = ai_svc._generate_analysis(prompt, {"indices": []})
        await self.notifier.post_report(analysis, "BẢN TIN GIỮA PHIÊN")

    async def generate_eod(self):
        indices_data = await market_data_svc.get_indices()
        indices_text = "\n".join([f"- {idx.get('name')}: {idx.get('value')} ({idx.get('changePercent'):+.2f}%)" for idx in indices_data.get("indices", [])])
        prompt = f"Tạo 'Tổng Kết Phiên & Nhận Định Mai' cho VN dựa trên:\n{indices_text}"
        analysis = ai_svc._generate_analysis(prompt, {"indices": []})
        await self.notifier.post_report(analysis, "TỔNG KẾT PHIÊN GIAO DỊCH")

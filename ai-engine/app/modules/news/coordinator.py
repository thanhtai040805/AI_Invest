import asyncio
import logging
from datetime import datetime, timezone, timedelta
from .domain.ports import INewsCrawler, INewsRepository, INewsNotifier
from .services.processor import NewsProcessor
from .services.reports import NewsReports

logger = logging.getLogger(__name__)

CAFEF_CATEGORIES = {
    "thi_truong_chung_khoan": "https://cafef.vn/thi-truong-chung-khoan.chn",
    "bat_dong_san":           "https://cafef.vn/bat-dong-san.chn",
    "doanh_nghiep":           "https://cafef.vn/doanh-nghiep.chn",
    "tai_chinh_ngan_hang":    "https://cafef.vn/tai-chinh-ngan-hang.chn",
    "tai_chinh_quoc_te":      "https://cafef.vn/tai-chinh-quoc-te.chn",
    "vi_mo_dau_tu":           "https://cafef.vn/vi-mo-dau-tu.chn",
    "thi_truong":             "https://cafef.vn/thi-truong.chn",
}

class NewsCoordinator:
    def __init__(self, crawler: INewsCrawler, repository: INewsRepository, notifier: INewsNotifier):
        self.crawler = crawler
        self.processor = NewsProcessor(repository, notifier)
        self.reports = NewsReports(notifier)
        self._running = False
        self._task = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._main_loop())
            logger.info("News Coordinator started.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("News Coordinator stopped.")

    async def _main_loop(self):
        while self._running:
            try:
                await self._check_reports()
                await self._run_scan()
            except Exception as e:
                logger.error(f"Error in NewsCoordinator loop: {e}")
            await asyncio.sleep(900) # 15 minutes

    async def _run_scan(self):
        logger.info("Starting news scan (httpx)...")
        for cat_name, url in CAFEF_CATEGORIES.items():
            try:
                links = await self.crawler.fetch_latest_links(url)
                for link in links:
                    article = await self.crawler.crawl_article(link, cat_name)
                    if article:
                        await self.processor.process_article(article)
                        await asyncio.sleep(1.0) # Rate limit
            except Exception as e:
                logger.error(f"Failed scan for category {cat_name}: {e}")

    async def _check_reports(self):
        from app.services.job_state_service import is_job_completed_today, set_running, set_completed, set_failed
        
        vn_tz = timezone(timedelta(hours=7))
        now_vn = datetime.now(timezone.utc).astimezone(vn_tz)

        jobs = [
            ("report_premarket", 8, 30, self.reports.generate_premarket),
            ("report_midday", 11, 0, self.reports.generate_midday),
            ("report_eod", 15, 15, self.reports.generate_eod),
        ]

        for job_name, hour, minute, func in jobs:
            if now_vn.hour == hour and now_vn.minute >= minute:
                if not is_job_completed_today(job_name):
                    try:
                        set_running(job_name, {"triggered_at": now_vn.isoformat()})
                        await func()
                        set_completed(job_name)
                        logger.info(f"Scheduled report {job_name} sent.")
                    except Exception as e:
                        set_failed(job_name, str(e))
                        logger.error(f"Failed scheduled report {job_name}: {e}")

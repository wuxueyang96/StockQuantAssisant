import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.config import Config
from app.services.stock_service import collect_and_store
from app.services.workflow_service import workflow_service

logger = logging.getLogger(__name__)


class JobScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.jobs = {}

    def add_workflow_job(self, workflow_id: str, workflow_data: dict):
        if workflow_id in self.jobs:
            return
        if not Config.AUTO_COLLECT_ENABLED:
            logger.info(f"自动采集已关闭，跳过定时任务: {workflow_id}")
            return

        market = workflow_data['market']
        stock_code = workflow_data['stock_code']
        interval = workflow_data['interval']
        minutes = Config.INTERVAL_MINUTES[interval]

        def job_func():
            try:
                rows = collect_and_store(market, stock_code, interval)
                if rows > 0:
                    logger.info(f"[{workflow_id}] 写入 {rows} 条新数据")
            except Exception as e:
                logger.error(f"[{workflow_id}] 执行失败: {e}")

        job = self.scheduler.add_job(
            job_func,
            trigger=IntervalTrigger(minutes=minutes),
            id=workflow_id,
            name=workflow_id,
            replace_existing=True
        )
        self.jobs[workflow_id] = job
        logger.info(f"添加定时任务: {workflow_id} (每 {minutes} 分钟)")

    def remove_workflow_job(self, workflow_id: str):
        if workflow_id in self.jobs:
            self.scheduler.remove_job(workflow_id)
            del self.jobs[workflow_id]
            logger.info(f"移除定时任务: {workflow_id}")

    def load_all_workflows(self):
        for wf_id, wf_data in workflow_service.workflows.items():
            if wf_data.get('active', True):
                self.add_workflow_job(wf_id, wf_data)

    def start(self):
        self.load_all_workflows()
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("调度器已启动")

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("调度器已停止")


job_scheduler = JobScheduler()

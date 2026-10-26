import os
from redis import Redis
from rq import Queue


def get_ocr_queue() -> Queue:
    connection = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    return Queue(os.getenv("OCR_QUEUE_NAME", "ocr"), connection=connection)

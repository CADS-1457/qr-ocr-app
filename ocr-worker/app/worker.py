import os
from redis import Redis
from rq import Worker

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE = os.getenv("OCR_QUEUE_NAME", "ocr")

if __name__ == "__main__":
    connection = Redis.from_url(REDIS_URL)
    Worker([QUEUE], connection=connection, name="qr-ocr-worker").work(with_scheduler=True)

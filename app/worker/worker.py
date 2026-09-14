import os
import json
import time
import logging
from datetime import datetime
import redis
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Worker] %(message)s")
logger = logging.getLogger("worker")

REDIS_HOST = os.getenv("REDIS_HOST", "redis-service")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_QUEUE_NAME = os.getenv("REDIS_QUEUE_NAME", "task_queue")

DB_HOST = os.getenv("DB_HOST", "postgres-service")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "taskdb")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres123")

WORKER_ID = os.getenv("HOSTNAME", "worker-default")

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5
    )

def main():
    logger.info(f"Worker {WORKER_ID} starting up. Connecting to Redis ({REDIS_HOST}:{REDIS_PORT}) and Postgres ({DB_HOST}:{DB_PORT})...")

    # Connect to Redis
    while True:
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            r.ping()
            logger.info("Connected to Redis message queue successfully.")
            break
        except Exception as e:
            logger.warning(f"Waiting for Redis ({e})... retrying in 3s")
            time.sleep(3)

    # Main processing loop
    while True:
        try:
            # Blocking pop from Redis (timeout 5s to allow graceful handling)
            item = r.brpop(REDIS_QUEUE_NAME, timeout=5)
            if not item:
                continue

            queue_name, raw_data = item
            task = json.loads(raw_data)
            task_id = task.get("id")
            title = task.get("title")

            logger.info(f"Picked up task {task_id}: '{title}'. Processing...")

            # Simulate work (e.g. CPU task, image optimization, batch transformation)
            time.sleep(2)

            # Update PostgreSQL database with completion status
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE tasks
                    SET status = %s, processed_at = %s, processed_by = %s
                    WHERE id = %s;
                """, ("COMPLETED", datetime.utcnow(), WORKER_ID, task_id))
                conn.commit()
            conn.close()

            logger.info(f"Task {task_id} successfully marked COMPLETED by {WORKER_ID}.")

        except redis.ConnectionError as re:
            logger.error(f"Redis connection lost: {re}. Retrying in 5s...")
            time.sleep(5)
        except psycopg2.Error as pe:
            logger.error(f"Database error during task execution: {pe}. Retrying in 5s...")
            time.sleep(5)
        except Exception as ex:
            logger.error(f"Unexpected worker exception: {ex}")
            time.sleep(2)

if __name__ == "__main__":
    main()

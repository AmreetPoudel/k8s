import os
import json
import uuid
import time
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import redis
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backend-api")

app = FastAPI(
    title="Task Microservice API",
    description="Production-grade asynchronous task submission and querying service",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis-service")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_QUEUE_NAME = os.getenv("REDIS_QUEUE_NAME", "task_queue")

DB_HOST = os.getenv("DB_HOST", "postgres-service")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "taskdb")
DB_USER = os.getenv("DB_USER", "postgres")

def load_secret(env_var: str, file_env: str, default_path: str) -> str:
    """
    Secure secret loader prioritizing in-memory volume mounts (/etc/secrets)
    over environment variables, and strictly failing fast without insecure hardcoded fallbacks.
    """
    secret_path = os.getenv(file_env, default_path)
    if os.path.exists(secret_path):
        try:
            with open(secret_path, "r") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            logger.error(f"Error reading secret file {secret_path}: {e}")

    val = os.getenv(env_var)
    if val:
        return val

    raise RuntimeError(
        f"CRITICAL SECURITY CONFIGURATION ERROR: Secret '{env_var}' is missing! "
        f"It must be provided via Kubernetes Secret (env var '{env_var}' or file '{secret_path}'). "
        "Refusing to start with insecure default credentials."
    )

try:
    DB_PASSWORD = load_secret("DB_PASSWORD", "DB_PASSWORD_FILE", "/etc/secrets/db-password")
except RuntimeError as err:
    logger.error(str(err))
    # We allow the app module to load for docs/linting, but DB connections will fail gracefully
    DB_PASSWORD = None


# Redis connection
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

def get_db_connection():
    """Establish and return a PostgreSQL connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5
    )

def init_db():
    """Ensure required database schema exists."""
    for attempt in range(10):
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id VARCHAR(64) PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        payload TEXT,
                        status VARCHAR(50) NOT NULL,
                        created_at TIMESTAMP NOT NULL,
                        processed_at TIMESTAMP,
                        processed_by VARCHAR(100)
                    );
                """)
                conn.commit()
            conn.close()
            logger.info("PostgreSQL task table schema verified.")
            break
        except Exception as e:
            logger.warning(f"Database init retry {attempt + 1}/10: {e}")
            time.sleep(2)

@app.on_event("startup")
def startup_event():
    init_db()

class TaskCreateRequest(BaseModel):
    title: str
    payload: Optional[str] = ""

class TaskResponse(BaseModel):
    id: str
    title: str
    payload: Optional[str]
    status: str
    created_at: str
    processed_at: Optional[str] = None
    processed_by: Optional[str] = None

@app.get("/healthz")
def healthz():
    """Kubernetes liveness and readiness probe endpoint."""
    redis_ok = False
    db_ok = False
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        pass

    try:
        conn = get_db_connection()
        conn.close()
        db_ok = True
    except Exception:
        pass

    overall_status = "healthy" if (redis_ok and db_ok) else "degraded"
    return {
        "status": overall_status,
        "redis": "connected" if redis_ok else "disconnected",
        "postgres": "connected" if db_ok else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.post("/api/tasks", status_code=status.HTTP_202_ACCEPTED)
def create_task(req: TaskCreateRequest):
    """Submits a new task into the Redis processing queue and records initial state."""
    task_id = str(uuid.uuid4())
    now_str = datetime.utcnow().isoformat()

    task_data = {
        "id": task_id,
        "title": req.title,
        "payload": req.payload,
        "status": "QUEUED",
        "created_at": now_str
    }

    try:
        # Record initial task in Postgres
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tasks (id, title, payload, status, created_at)
                VALUES (%s, %s, %s, %s, %s);
            """, (task_id, req.title, req.payload, "QUEUED", datetime.utcnow()))
            conn.commit()
        conn.close()

        # Push task ID / payload into Redis Queue
        redis_client.lpush(REDIS_QUEUE_NAME, json.dumps(task_data))
        logger.info(f"Task {task_id} successfully queued into Redis.")

        return {
            "message": "Task queued for asynchronous processing",
            "task_id": task_id,
            "status": "QUEUED",
            "created_at": now_str
        }
    except Exception as e:
        logger.error(f"Failed to queue task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tasks", response_model=List[TaskResponse])
def list_tasks():
    """Retrieve the latest 50 tasks from PostgreSQL."""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, payload, status, created_at, processed_at, processed_by
                FROM tasks
                ORDER BY created_at DESC
                LIMIT 50;
            """)
            rows = cur.fetchall()
        conn.close()

        result = []
        for r in rows:
            result.append(TaskResponse(
                id=r["id"],
                title=r["title"],
                payload=r["payload"],
                status=r["status"],
                created_at=r["created_at"].isoformat() if r["created_at"] else "",
                processed_at=r["processed_at"].isoformat() if r["processed_at"] else None,
                processed_by=r["processed_by"]
            ))
        return result
    except Exception as e:
        logger.error(f"Failed to query tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
def get_stats():
    """Retrieve real-time queue depth and processed metrics."""
    try:
        queue_len = redis_client.llen(REDIS_QUEUE_NAME)
    except Exception:
        queue_len = -1

    completed_count = 0
    queued_count = 0
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM tasks GROUP BY status;")
            counts = cur.fetchall()
            for st, cnt in counts:
                if st == "COMPLETED":
                    completed_count = cnt
                elif st == "QUEUED":
                    queued_count = cnt
        conn.close()
    except Exception:
        pass

    return {
        "redis_queue_depth": queue_len,
        "db_completed_tasks": completed_count,
        "db_queued_tasks": queued_count,
        "pod_name": os.getenv("HOSTNAME", "backend-local"),
        "timestamp": datetime.utcnow().isoformat()
    }

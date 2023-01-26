import hashlib
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy, inspect

import redis

load_dotenv("auth.env")


class Config:
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = f"postgresql+psycopg2://{os.getenv('DB_USERNAME')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)
    ACCESS_EXPIRES = timedelta(hours=1)
    SECRET_KEY = hashlib.md5(b"super_secret").hexdigest()
    SWAGGER_TEMPLATE = {
        "securityDefinitions": {
            "APIKeyHeader": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "Type in the *'Value'* input box below: **'Bearer &lt;JWT&gt;'**, where JWT is the token",
            }
        }
    }
    MIGRATIONS_PATH = os.getenv("MIGRATIONS_PATH")
    YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
    YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")
    YANDEX_AUTHORIZE_URL = os.getenv("YANDEX_AUTHORIZE_URL")
    YANDEX_ACCESS_TOKEN_URL = os.getenv("YANDEX_ACCESS_TOKEN_URL")
    YANDEX_API_BASE_URL = os.getenv("YANDEX_API_BASE_URL")


db = SQLAlchemy(session_options={"autoflush": False})
migrate_obj = Migrate()
engine = db.create_engine(Config.SQLALCHEMY_DATABASE_URI, {})
insp = inspect(engine)

jwt_redis = redis.Redis(
    host=str(os.getenv("REDIS_AUTH_HOST")),
    port=int(os.getenv("REDIS_AUTH_PORT", 6379)),
    password=os.getenv("REDIS_AUTH_PASSWORD"),
    db=0,
    decode_responses=True,
)
jwt = JWTManager()

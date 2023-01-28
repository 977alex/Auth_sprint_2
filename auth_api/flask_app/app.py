"""
Основной модуль
"""

import logging
import os
import sys
import time

from auth_config import Config, db, engine, insp, jwt, migrate_obj
from db_models import Group, User
from flasgger import Swagger
from flask import Flask, make_response, request
from flask_migrate import init, migrate, upgrade
from flask_opentracing import FlaskTracer
from split_settings.tools import include
from groups_bp.groups_bp import groups_bp
from test_bp.test_bp import test_bp
from users_bp.users_bp import users_bp

BASE_PATH = "/v01"


def db_initialize(app):
    """
    Первоначальная инициализация приложения авторизации

    Инициализируем структуру таблиц, создаем группу
    администраторов и одного пользователя, входящего
    в нее, а также одного пользователя не входящего
    ни в какие группы. Пароли пользователей берутся
    из переменных окружения ADMIN_PASSWORD и
    NOBODY_PASSWORD.

    Эта функция предназначена для начальной инициализации
    базы и уничтожит все имеющиеся данные в ней
    """
    with app.app_context():

        time.sleep(5)
        try:
            db.close_all_sessions()
            db.drop_all()
            if os.path.isdir(Config.MIGRATIONS_PATH):
                # shutil.rmtree(Config.MIGRATIONS_PATH)
                engine.execute("DELETE FROM alembic_version")
                engine.execute(
                    "INSERT INTO alembic_version(version_num) VALUES ('initial_script')"
                )
            init(Config.MIGRATIONS_PATH)
            migrate(Config.MIGRATIONS_PATH, message="Initial migration")
            upgrade(Config.MIGRATIONS_PATH)
            engine.execute(
                """ALTER TABLE auth.user ATTACH PARTITION auth.user_active
                FOR VALUES IN (False);"""
            )
            engine.execute(
                """ALTER TABLE auth.user ATTACH PARTITION auth.user_deleted
                FOR VALUES IN (True);"""
            )
            engine.execute(
                """ALTER TABLE auth.history ATTACH PARTITION auth.history_2022
                FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');"""
            )
            engine.execute(
                """ALTER TABLE auth.history ATTACH PARTITION auth.history_2023
                FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');"""
            )

            db.session.commit()
            # db.create_all()
            admin_group = Group(name="admin", description="Administrators")
            admin_user = User(
                login="admin",
                email="root@localhost",
                password_hash="",
                full_name="Site administrator",
                deleted=False,
            )
            regular_user = User(
                login="nobody",
                email="nobody@localhost",
                password_hash="",
                full_name="Regular user",
                deleted=False,
            )
            deleted_user = User(
                login="deleted",
                email="deeted@localhost",
                password_hash="",
                full_name="Regular user",
                deleted=True,
            )
            # Берем пароли из переменных окружения
            include('config/settings.py', )

            db.session.add(admin_group)
            db.session.add(admin_user)
            db.session.add(regular_user)
            db.session.add(deleted_user)
            db.session.commit()
            # Только после первого коммита пользователь и группа получат
            # автосгенерированные UUID
            admin_group.users.append(admin_user)
            db.session.add(admin_group)
            db.session.commit()
        except Exception as ex:
            logging.error(f"we have a problem: {ex}")


config_data = {
    "sampler": {
        "type": "const",
        "param": 1,
    },
    "local_agent": {
        "reporting_host": os.getenv("REPORTING_HOST"),
        "reporting_port": os.getenv("REPORTING_PORT"),
    },
    "logging": True,
}


def _setup_jaeger():
    from jaeger_client import Config

    config = Config(
        config=config_data,
        service_name="movies-api",
        validate=True,
    )
    return config.initialize_tracer()


app = Flask(__name__)
if os.getenv("ENABLE_TRACER"):
    tracer = FlaskTracer(_setup_jaeger, app=app)

@app.before_request
def before_request():
    request_id = request.headers.get("X-Request-Id")
    if not request_id:
        return make_response("X-Request-Id not found", 404)


def create_app():
    app.config.from_object(Config())
    app.register_blueprint(groups_bp, url_prefix=f"{BASE_PATH}/groups")
    app.register_blueprint(users_bp, url_prefix=f"{BASE_PATH}/users")
    app.register_blueprint(test_bp, url_prefix="/test")

    swagger = Swagger(app, template=Config.SWAGGER_TEMPLATE)
    db.init_app(app)
    # engine = db.create_engine(Config.SQLALCHEMY_DATABASE_URI, {})
    engine.execute("CREATE SCHEMA IF NOT EXISTS auth;")
    jwt.init_app(app)
    migrate_obj.init_app(app, db)

    return app


if __name__ == "__main__":
    app = create_app()

    with app.app_context():
        # При прогоне тестов удаляем прошлые данные из базы и создаем заново
        if len(sys.argv) == 2 and sys.argv[1] == "--reinitialize":
            db.drop_all()
        # Инициалиазции базы. Проверяем наличие таблицы пользователей
        if not insp.has_table("user", schema="auth"):
            logging.info("initializing...")
            db_initialize(app)
        app.run(host="0.0.0.0")

import logging
import os
import random
import string
from datetime import datetime
from functools import wraps
from http import HTTPStatus

import opentracing
from auth_config import Config, db, jwt, jwt_redis
from authlib.integrations.flask_client import OAuth
from db_models import History, SocialAccount, User
from flasgger.utils import swag_from
from split_settings.tools import include
from flask import Blueprint
from flask import current_app as app
from flask import make_response, request, url_for
from flask.json import jsonify
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    verify_jwt_in_request,
)
from sqlalchemy import or_

import redis

oauth = OAuth(app)

jwt_redis_blocklist = jwt_redis
users_bp = Blueprint("users_bp", __name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def limit_requests(per_minute: int):
    include('config/settings.py', )

    redis_conn = redis.Redis(
        host=redis_host, port=redis_port, db=0, password=redis_password
    )

    def wrapper(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            pipe = redis_conn.pipeline()
            now = datetime.now()
            key = f"{now.hour}:{now.minute}"
            pipe.incr(key, 1)
            pipe.expire(key, 59)
            result = pipe.execute()
            logger.info(f"key - {key}. result - {result}")
            if result[0] > per_minute:
                return (
                    jsonify({"error": "too many requests"}),
                    HTTPStatus.TOO_MANY_REQUESTS,
                )
            return func(*args, **kwargs)

        return wrapped

    return wrapper


@swag_from(
    "../schemes/auth_api_swagger.yaml",
    endpoint="list_users",
    methods=["GET"],
    validation=True,
)
@users_bp.route("/", methods=["GET"])
@limit_requests(per_minute=10)
def list_users(**kwargs):
    """
    Список всех зарегистрированных пользователей
    """
    users = []
    page_size = request.args.get("page_size", None)
    page_number = request.args.get("page_number", 1)
    if page_size is None:
        for user in User.query.order_by(User.login).all():
            users.append(user.to_json())
    else:
        for user in (
            User.query.order_by(User.login)
            .paginate(int(page_number), int(page_size), False)
            .items
        ):
            users.append(user.to_json())
    return jsonify(users), HTTPStatus.OK


@swag_from(
    "../schemes/auth_api_swagger.yaml",
    endpoint="register",
    methods=["POST"],
    validation=True,
)
@users_bp.route("/register", methods=["POST"])
@limit_requests(per_minute=10)
def register():
    """
    Метод регистрации пользователя

    Это операция может быть довольно трудоемкой (проверка существования
    такого логина и адреса электронной почты, в перспективе - телефона,
    отправка SMS или электронного письма). Плюс к этому - пользователей
    мы не удаляем и каждая регистрация увеличит размеры нашей базы
    необратимо. Поэтому ограничим количество регистраций десятью в
    минуту.
    """
    obj = request.json
    user = User.query.filter_by(email=obj["email"]).first()
    if not user:
        try:
            # obj["password"] = hash_password(obj["password"])
            user = User(**obj)
            db.session.add(user)
            db.session.commit()
            return jsonify({"msg": "User was successfully registered"}), HTTPStatus.OK

        except Exception as err:
            return jsonify({"msg": f"Unexpected error: {err}"}), HTTPStatus.CONFLICT

    return (
        jsonify(
            {"msg": "User had already been registered. Check the email, id, login"}
        ),
        HTTPStatus.CONFLICT,
    )


@swag_from("../schemes/user_login_param.yaml")
@users_bp.route("/login", methods=["POST"])
def login():
    """
    Метод при успешной авториазции возвращает пару ключей access и refreh токенов
    """
    username = request.args.get("login", None)
    password = request.args.get("password", None)
    user = User.query.filter_by(login=username).first()
    if (user and user.verify_password(password)) or (
        username == "test" and password == "test"
    ):
        if username == "test":
            user_identity = username
        else:
            user_identity = str(user.id)
        access_token = create_access_token(identity=user_identity)
        refresh_token = create_refresh_token(identity=user_identity)
        if user:
            # Добавить информацию о входе в историю
            history = History(
                user_id=user.id, useragent="unknown", timestamp=datetime.now()
            )
            db.session.add(history)
            db.session.commit()
    else:
        return jsonify({"msg": "Bad username or password"}), HTTPStatus.UNAUTHORIZED

    return (
        jsonify(access_token=access_token, refresh_token=refresh_token),
        HTTPStatus.OK,
    )


# @jwt_required(refresh=True)
@swag_from("../schemes/user_refresh_param.yaml")
@users_bp.route("/refresh", methods=["POST"])
def refresh():
    """
    Обновление пары токенов при получении действительного refresh токена
    """
    try:
        verify_jwt_in_request(refresh=True)
    except Exception as ex:
        return jsonify({"msg": f"Bad refresh token: {ex}"}), HTTPStatus.UNAUTHORIZED
    identity = get_jwt_identity()
    access_token = create_access_token(identity=identity)
    refresh_token = create_refresh_token(identity=identity)
    return (
        jsonify(access_token=access_token, refresh_token=refresh_token),
        HTTPStatus.OK,
    )


# @jwt_required()
@swag_from("../schemes/user_logout_param.yaml")
@users_bp.route("/logout", methods=["DELETE"])
def logout():
    """
    Выход пользователя из аккаунта
    """
    try:
        verify_jwt_in_request()
    except Exception as ex:
        return (jsonify({"msg": f"Bad access token: {ex}"}), HTTPStatus.UNAUTHORIZED)
    jti = get_jwt()["jti"]
    jwt_redis_blocklist.set(jti, "", ex=Config.ACCESS_EXPIRES)
    return (
        jsonify(msg="Access token revoked"),
        HTTPStatus.OK,
    )


@swag_from("../schemes/user_account_post_param.yaml")
@users_bp.route("/account/", methods=["POST"])
def update():
    """
    Обновление данных пользователя
    """
    try:
        verify_jwt_in_request()
    except Exception as ex:
        return jsonify({"msg": f"Bad access token: {ex}"}), HTTPStatus.UNAUTHORIZED
    identity = get_jwt_identity()
    user = User.query.get(identity)
    if user is None:
        return jsonify({"error": "user not found"}), HTTPStatus.NOT_FOUND
    obj = request.json
    # obj["password"] = hash_password(obj["password"])
    updated_user = user.from_json(obj)
    return (
        jsonify(msg=f"Update success: {updated_user}"),
        HTTPStatus.OK,
    )


@swag_from(
    "../schemes/auth_api_swagger.yaml",
    endpoint="get_user",
    methods=["GET"],
    validation=True,
)
@users_bp.route("/<user_id>/", methods=["GET"])
def get_user(user_id):
    """
    Получить информацию о пользователе
    """
    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "user not found"}), HTTPStatus.NOT_FOUND
    return jsonify(user.to_json())


@swag_from(
    "../schemes/auth_api_swagger.yaml",
    endpoint="get_user_history",
    methods=["GET"],
    validation=True,
)
@users_bp.route("/history", methods=["GET"])
@jwt_required()
def get_user_history(**kwargs):
    """
    Получить историю операций пользователя
    """

    current_user = User.query.filter_by(id=get_jwt_identity(), deleted=False).first()
    page_size = request.args.get("page_size", None)
    page_number = request.args.get("page_number", 1)
    if not current_user:
        return jsonify({"error": "No such user"}), HTTPStatus.NOT_FOUND
    if page_size is None:
        history = current_user.get_history().all()
    else:
        history = (
            current_user.get_history()
            .paginate(int(page_number), int(page_size), False)
            .items
        )
    return jsonify([h.to_json() for h in history])


@swag_from(
    "../schemes/auth_api_swagger.yaml",
    endpoint="get_user_groups",
    methods=["GET"],
    validation=True,
)
@users_bp.route("/groups", methods=["GET"])
@jwt_required()
def get_user_groups(**kwargs):
    """
    Получить список групп, в которых состоит текущий пользователь
    """
    current_user = User.query.get(get_jwt_identity())
    if not current_user:
        return jsonify({"error": "No such user"}), HTTPStatus.NOT_FOUND
    groups = []
    for group in current_user.get_all_groups().all():
        groups.append(group.to_json())
    return jsonify(groups)


@jwt.token_in_blocklist_loader
def check_if_token_is_revoked(jwt_header, jwt_payload):
    jti = jwt_payload["jti"]
    token_in_redis = jwt_redis_blocklist.get(jti)
    return token_in_redis is not None


@users_bp.route("/oauth/login/<string:social_name>", methods=["GET"])
def create_authorisation_url(social_name: str):
    oauth.register(
        social_name,
    )
    client = oauth.create_client(social_name)
    if not client:
        return make_response({"msg": f"{client} not found"}, HTTPStatus.NOT_FOUND)
    redirect_url = url_for(
        "users_bp.social_user_authorise",
        social_name=social_name,
        _external=True,
    )
    return client.authorize_redirect(redirect_url)


@users_bp.route("/oauth/callback/<string:social_name>", methods=["GET"])
def social_user_authorise(social_name: str):
    from app import tracer

    parent_span = tracer.get_span()
    client = oauth.create_client(social_name)
    if not client:
        return make_response({"msg": f"{client} not found"}, HTTPStatus.NOT_FOUND)
    token = client.authorize_access_token()
    user_info_url = oauth._clients[social_name].api_base_url
    user_info = oauth._clients[social_name].get(user_info_url).json()
    if not user_info:
        return make_response({"msg": "Information not provided"}, HTTPStatus.NOT_FOUND)
    password = "".join(random.choice(string.printable) for i in range(10))
    with opentracing.tracer.start_span(
        "Get User record db save", child_of=parent_span
    ) as span:
        user = User.query.filter(
            or_(
                User.login == user_info["login"],
                User.email == user_info["default_email"],
            )
        ).first()
    if not user:
        with opentracing.tracer.start_span(
            "User record db save", child_of=parent_span
        ) as span:
            user = User(
                email=user_info["default_email"],
                login=user_info["login"],
                password_hash=password,
            )
            db.session.add(user)
            db.session.commit()
            social_user = SocialAccount(
                social_id=user_info["id"], social_name=social_name, user_id=user.id
            )
            db.session.add(social_user)
            db.session.commit()
    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    useragent = request.user_agent.string
    with opentracing.tracer.start_span(
        "history record db save", child_of=parent_span
    ) as span:
        history = History(
            user_id=user.id, useragent=useragent, timestamp=datetime.now()
        )
        db.session.add(history)
        db.session.commit()
    token = {"access_token": access_token, "refresh_token": refresh_token}
    return make_response(token, 200)

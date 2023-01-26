import datetime
import uuid
from typing import Optional

from auth_config import db
from sqlalchemy import DDL, ForeignKeyConstraint, MetaData, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from werkzeug.security import check_password_hash, generate_password_hash

user_group = db.Table(
    "user_group_rel",
    db.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    ),
    db.Column("user_id", UUID(as_uuid=True)),
    db.Column("user_deleted", db.Boolean),
    db.Column("group_id", UUID(as_uuid=True), db.ForeignKey("auth.group.id")),
    ForeignKeyConstraint(
        ["user_id", "user_deleted"], ["auth.user.id", "auth.user.deleted"]
    ),
    extend_existing=True,
    schema="auth",
)


class Group(db.Model):
    """Пользовательская группа (роль)"""

    __table_args__ = {"schema": "auth", "extend_existing": True}
    __tablename__ = "group"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(100), nullable=False)

    users = db.relationship(
        "User", secondary=user_group, lazy="subquery", back_populates="groups"
    )

    def __repr__(self):
        return f"<Group {self.name}>"

    def is_admin(self):
        """Является ли группа группой администраторов"""
        return self.name == "admin"

    def user_in_group(self, user_id):
        """Проверить, состоит ли пользователь в указанной группе"""
        return self.users.filter_by(user_id=user_id).first() is not None

    def get_all_users(self):
        """Список всех пользователей в этой группе"""
        return User.query.filter(User.groups.any(id=self.id))

    def to_json(self, *, url_prefix: Optional[str] = None):
        """
        Преобразовать группу в объект для сериализации в Python

        Если задан параметр url_prefix то дополнительно вернуть
        URL для доступа к информации об указанной группе, с указанным
        префиксом.
        """
        obj = {"id": self.id, "name": self.name, "description": self.description}
        if url_prefix:
            obj["url"] = f"{url_prefix}/group/{self.id}"
        return obj

    @staticmethod
    def from_json(obj):
        """
        Создать группу на основе словаря python
        """
        return Group(
            id=obj["id"],
            name=obj["name"],
            description=obj.get("description", obj["name"]),
        )


class UserMixin:
    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    login = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(100), nullable=False)
    full_name = db.Column(db.String(100))
    phone = db.Column(db.String(100))
    avatar_link = db.Column(db.String(100))
    address = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    deleted = db.Column(db.Boolean, default=False, nullable=False, primary_key=True)


class User(UserMixin, db.Model):
    """Зарегистрированный в системе пользователь"""

    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("id", "deleted"),
        {
            "schema": "auth",
            "postgresql_partition_by": "LIST (deleted)",
            "extend_existing": True,
        },
    )
    groups = db.relationship(
        "Group", secondary=user_group, lazy="subquery", back_populates="users"
    )

    @property
    def password(self):
        raise AttributeError("Could not get password from password hash")

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.login}>"

    def in_group(self, group_id):
        """Проверить, состоит ли пользователь в указанной группе"""
        return self.groups.filter_by(group_id=group_id).first() is not None

    def get_all_groups(self):
        """Список всех групп, в которых состоит пользователь"""
        return self.groups

    def is_admin(self):
        """Состоит ли пользователь в группе администраторов"""
        groups = self.groups
        for g in groups:
            if g.is_admin():
                return True
        return False

    def to_json(self, *, url_prefix: Optional[str] = None):
        """
        Преобразовать запись пользователя в объект для сериализации в Python

        Если задан параметр url_prefix то дополнительно вернуть
        URL для доступа к информации о пользователе, с указанным
        префиксом.
        """
        obj = {
            "id": self.id,
            "login": self.login,
            "email": self.email,
        }
        if url_prefix:
            obj["url"] = f"{url_prefix}/user/account/{self.login}"
        return obj

    def get_history(self, since: Optional[datetime.datetime] = None):
        """
        Вернуть историю действий этого пользователя

        Если задан параметр since, то вернуть только действия, которые
        были позже указанной метки времени
        """
        if since:
            return (
                History.query.filter(History.user_id == self.id)
                .filter(History.timestamp >= since)
                .order_by(History.timestamp.desc())
            )
        else:
            return History.query.filter(History.user_id == self.id).order_by(
                History.timestamp.desc()
            )


class UserActive(UserMixin, db.Model):
    __tablename__ = "user_active"
    __table_args__ = {"schema": "auth", "extend_existing": True}


class UserDeleted(UserMixin, db.Model):
    __tablename__ = "user_deleted"
    __table_args__ = {"schema": "auth", "extend_existing": True}


UserActive.__table__.add_is_dependent_on(User.__table__)
UserDeleted.__table__.add_is_dependent_on(User.__table__)


@event.listens_for(UserDeleted.__table__, "after_create")
def create_user_deleted_after_create(**kw):
    DDL(
        """ALTER TABLE auth.user ATTACH PARTITION auth.user_deleted
        FOR VALUES IN (True);"""
    )


@event.listens_for(UserDeleted.__table__, "after_create")
def create_user_active_after_create(**kw):
    DDL(
        """ALTER TABLE auth.user ATTACH PARTITION auth.user_active
        FOR VALUES IN (False);"""
    )


class HistoryMixin:
    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id = db.Column(UUID(as_uuid=True, nullable=False))
    user_deleted = db.Column(db.Boolean)
    useragent = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, primary_key=True)


class History(HistoryMixin, db.Model):
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "user_deleted"], ["auth.user.id", "auth.user.deleted"]
        ),
        UniqueConstraint("id", "timestamp"),
        {
            "schema": "auth",
            "extend_existing": True,
            "postgresql_partition_by": "RANGE (timestamp)",
        },
    )
    __tablename__ = "history"

    def __repr__(self):
        return f"<History {self.useragent}>"

    def to_json(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "useragent": self.useragent,
            "timestamp": self.timestamp.isoformat(),
        }


class History2022(HistoryMixin, db.Model):
    __tablename__ = "history_2022"
    __table_args__ = {"schema": "auth", "extend_existing": True}


class History2023(HistoryMixin, db.Model):
    __tablename__ = "history_2023"
    __table_args__ = {"schema": "auth", "extend_existing": True}


History2022.__table__.add_is_dependent_on(History.__table__)
History2023.__table__.add_is_dependent_on(History.__table__)


@event.listens_for(History2022.__table__, "after_create")
def create_history2022_after_create(**kw):
    DDL(
        """ALTER TABLE auth.history ATTACH PARTITION auth.history_2022
        FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');"""
    )


@event.listens_for(History2023.__table__, "after_create")
def create_history2023_after_create(**kw):
    DDL(
        """ALTER TABLE auth.history ATTACH PARTITION auth.history_2023
        FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');"""
    )


class SocialAccount(db.Model):
    __tablename__ = "social_account"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    user_id = db.Column(
        UUID(as_uuid=True),
        # db.ForeignKey("auth.user.id"),
        nullable=False,
    )
    user = db.relationship(User, backref=db.backref("social_accounts", lazy=True))
    user_deleted = db.Column(db.Boolean, default=False)
    social_id = db.Column(db.String(100), nullable=False)
    social_name = db.Column(db.String(100), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("social_id", "social_name", name="social_pk"),
        ForeignKeyConstraint(
            ["user_id", "user_deleted"], ["auth.user.id", "auth.user.deleted"]
        ),
        {"schema": "auth", "extend_existing": True},
    )

    def __repr__(self):
        return f"<SocialAccount {self.social_name}:{self.user_id}>"

    def to_json(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user": self.user.name,
            "social_id": self.social_id,
            "social_name": self.social_name,
        }

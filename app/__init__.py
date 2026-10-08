import os
from datetime import timedelta

from flask import Flask

from . import db, sessions
from .routes import bp


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.path.join(app.instance_path, "mids.sqlite"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=1),
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    sessions.init_app(app)
    app.register_blueprint(bp)
    return app

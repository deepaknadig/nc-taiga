from src.models import db, Config
from flask import current_app

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        # Initialize an empty config if it doesn't exist
        if not Config.query.first():
            db.session.add(Config())
            db.session.commit()

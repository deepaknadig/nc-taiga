from models import db, GlobalConfig
from flask import current_app

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        if not GlobalConfig.query.first():
            db.session.add(GlobalConfig())
            db.session.commit()

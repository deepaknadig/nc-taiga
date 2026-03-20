from models import db, GlobalConfig
from flask import current_app
import os

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        config = GlobalConfig.query.first()
        if not config:
            config = GlobalConfig()
            db.session.add(config)

        # Override with environment variables if provided (useful for Docker Compose)
        if os.environ.get('NEXTCLOUD_URL'):
            config.nextcloud_url = os.environ.get('NEXTCLOUD_URL')
        if os.environ.get('NEXTCLOUD_USERNAME'):
            config.nextcloud_username = os.environ.get('NEXTCLOUD_USERNAME')
        if os.environ.get('NEXTCLOUD_APP_PASSWORD'):
            config.nextcloud_app_password = os.environ.get('NEXTCLOUD_APP_PASSWORD')

        if os.environ.get('TAIGA_URL'):
            config.taiga_url = os.environ.get('TAIGA_URL')
        if os.environ.get('TAIGA_USERNAME'):
            config.taiga_username = os.environ.get('TAIGA_USERNAME')
        if os.environ.get('TAIGA_PASSWORD'):
            config.taiga_password = os.environ.get('TAIGA_PASSWORD')

        db.session.commit()

import os
from dotenv import load_dotenv

# 載入環境變數，優先讀取當前目錄的 .env
load_dotenv()
# 如果當前目錄沒有 .env，嘗試讀取上層目錄的 .env
if not os.getenv('host'):
    load_dotenv('../.env')

class Config:
    # 基本配置
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev')
    DEBUG = False
    TESTING = False

    # 資料庫配置
    DB_CONFIG = {
        'host': os.getenv('host'),
        'user': os.getenv('user'),
        'password': os.getenv('password'),
        'database': os.getenv('database'),
        'port': os.getenv('port'),
        'use_pure': True
    }

    # Firebase 配置
    FIREBASE_CONFIG = {
        "type": "service_account",
        "project_id": os.getenv('FIREBASE_PROJECT_ID'),
        "private_key_id": os.getenv('FIREBASE_PRIVATE_KEY_ID'),
        "private_key": os.getenv('FIREBASE_PRIVATE_KEY'),
        "client_email": os.getenv('FIREBASE_CLIENT_EMAIL'),
        "client_id": os.getenv('FIREBASE_CLIENT_ID'),
        "auth_uri": os.getenv('FIREBASE_AUTH_URI'),
        "token_uri": os.getenv('FIREBASE_TOKEN_URI'),
        "auth_provider_x509_cert_url": os.getenv('FIREBASE_AUTH_PROVIDER_CERT_URL'),
        "client_x509_cert_url": os.getenv('FIREBASE_CLIENT_CERT_URL'),
        "universe_domain": os.getenv('FIREBASE_UNIVERSE_DOMAIN')
    }

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

class TestingConfig(Config):
    TESTING = True
    DEBUG = True

def get_config():
    env = os.getenv('FLASK_ENV', 'development')
    config_map = {
        'development': DevelopmentConfig,
        'production': ProductionConfig,
        'testing': TestingConfig
    }
    return config_map.get(env, DevelopmentConfig) 
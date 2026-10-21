"""Safe defaults for manage.py test; production settings and .env remain unchanged."""
from .settings import *  # noqa: F403

AI_PROVIDER='mock'
GEMINI_API_KEY=''
TELEGRAM_BOT_TOKEN=''
TELEGRAM_WEBHOOK_SECRET=''
TELEGRAM_WEBHOOK_ENABLED=False
TELEGRAM_AUTO_REGISTER=False
EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
EMAIL_NOTIFICATIONS_ENABLED=False
EMAIL_HOST_PASSWORD=''
INCIDENT_NOTIFICATION_EMAIL=''
DEFAULT_FROM_EMAIL='no-reply@example.test'
APP_BASE_URL='http://localhost:8000'
REQUEST_DOCUMENT_VISION_ENABLED=False
DATABASES['default'].setdefault('TEST',{})['NAME']='test_incidentes_gymplanet_db'  # noqa: F405
TEST_RUNNER='config.test_runner.GymPlanetTestRunner'

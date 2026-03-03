from django.apps import AppConfig

class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        # This imports and runs your firebase_config.py exactly once on startup
        import api.firebase_config
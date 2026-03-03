import os
import firebase_admin
from firebase_admin import credentials

# 1. Get the exact directory where this firebase_config.py file lives
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Build the full path to the json file
key_path = os.path.join(CURRENT_DIR, "serviceAccountKey.json")

# 3. Initialize Firebase using that exact path
cred = credentials.Certificate(key_path)
firebase_admin.initialize_app(cred)
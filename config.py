import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))

TEMP_DIR = os.path.join(ROOT_DIR, "temp")

os.makedirs(TEMP_DIR, exist_ok=True)
WAITING_TIME = 600
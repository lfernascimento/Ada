import os
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
DB_PATH = Path(os.getenv('ADA_DB_PATH', ROOT / 'ada.db'))
MODEL_BASE_URL = os.getenv('MODEL_BASE_URL', 'http://localhost:11434/v1').rstrip('/')
MODEL_NAME = os.getenv('MODEL_NAME', '')
LIGHT_MODEL = os.getenv('ADA_LIGHT_MODEL', '').strip()
MODEL_API_KEY = os.getenv('MODEL_API_KEY', '')

ANTHROPIC_WORKSPACE_ID = os.getenv('ANTHROPIC_WORKSPACE_ID', '')

MODEL_PROVIDER = os.getenv('ADA_MODEL_PROVIDER', 'claude_agent_sdk' if 'anthropic.com' in MODEL_BASE_URL else 'openai_compatible')
SDK_MAX_BUDGET_USD = float(os.getenv('ADA_SDK_MAX_BUDGET_USD', '0.25'))
SDK_TIMEOUT_SECONDS = int(os.getenv('ADA_SDK_TIMEOUT_SECONDS', '120'))

TASK_BUDGET_USD = float(os.getenv('ADA_TASK_BUDGET_USD', '0.50'))

MAX_SUBAGENTS = int(os.getenv('ADA_MAX_SUBAGENTS', '4'))

MEMORY_AUTOMATIC = os.getenv('ADA_MEMORY_AUTOMATIC','true').lower()=='true'

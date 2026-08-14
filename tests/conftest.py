import os

# Гарантирует, что config.py и agent.py импортируются без реального
# GEMINI_API_KEY в тестовом окружении (тесты мокают вызовы API).
os.environ.setdefault("GEMINI_API_KEY", "test-key-for-unit-tests")

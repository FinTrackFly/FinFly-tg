from pathlib import Path

from environs import Env


def to_bool(value: str) -> bool:
    """Переводит строки в bool значение."""
    return value.lower() in {"", "true", "yes", "on", "t", "y", "1"}


# Путь к рабочей директории проекта
BASE_DIR = Path(__file__).resolve().parent.parent

# Получение данных из переменного окружения
env = Env()
env.read_env(BASE_DIR.parent / ".env")

BOT_TOKEN = env.str("BOT_TOKEN")  # API ключ телеграмм бота
DEBUG = to_bool(env.str("DEBUG", default="true"))

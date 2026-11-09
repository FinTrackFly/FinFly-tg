import os
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


def pop_env_proxy() -> str | None:
    """Убирает системный прокси из окружения процесса и возвращает его.

    GigaChat (httpx) иначе подхватит прокси из переменных окружения,
    а сервера GigaChat недоступны через зарубежный VPN.
    """
    proxy = None
    for name in ("all_proxy", "https_proxy", "http_proxy"):
        for key in (name, name.upper()):
            proxy = proxy or os.environ.pop(key, None)
            os.environ.pop(key, None)
    if proxy and proxy.startswith("socks://"):
        proxy = proxy.replace("socks://", "socks5://", 1)
    return proxy


# Прокси только для Telegram (GigaChat всегда ходит напрямую).
# Если не задан — используется системный прокси (ALL_PROXY/HTTPS_PROXY)
TG_PROXY = env.str("TG_PROXY", default="") or pop_env_proxy()
pop_env_proxy()

# База данных (по умолчанию — SQLite файл в корне проекта)
DB_URL = env.str(
    "DB_URL",
    default=f"sqlite+aiosqlite:///{BASE_DIR.parent.parent / 'finfly.db'}",
)

# GigaChat API (https://developers.sber.ru/studio)
GIGACHAT_CREDENTIALS = env.str("GIGACHAT_CREDENTIALS")  # ключ авторизации
GIGACHAT_SCOPE = env.str("GIGACHAT_SCOPE", default="GIGACHAT_API_PERS")
GIGACHAT_MODEL = env.str("GIGACHAT_MODEL", default="GigaChat-2-Max")
GIGACHAT_VERIFY_SSL = to_bool(env.str("GIGACHAT_VERIFY_SSL", default="true"))
# Сколько запросов к GigaChat выполнять одновременно:
# 1 — бесплатный тариф (лимит 1 поток), >1 — платный тариф
GIGACHAT_MAX_CONCURRENCY = max(env.int("GIGACHAT_MAX_CONCURRENCY", 1), 1)
# Сколько секунд ждать ответа GigaChat (распознавание фото бывает долгим)
GIGACHAT_TIMEOUT = env.float("GIGACHAT_TIMEOUT", 120)

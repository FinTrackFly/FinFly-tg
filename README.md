<h1 align="center">FinFly Backend</h1>

<div align="center">
  
  [![Status](https://img.shields.io/badge/status-active-success.svg)](https://img.shields.io/badge/status-active-success.svg)
  [![License](https://img.shields.io/badge/license-MIT-blue.svg)](/LICENSE)
  ![Stack](https://img.shields.io/badge/Made%20with-aiogram-1f425f.svg)
  [![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
  [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
  
</div>

---

## Быстрый старт

Требования к инструментам:

- [python](https://www.python.org/downloads/) (`>=3.13`)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [make](https://www.gnu.org/software/make/)

### Подготовка

```bash
# Склонируйте репозиторий
git clone https://github.com/FinTrackFly/FinFly-tg.git
cd FinFly-tg

# Виртуальное окружение
uv sync --dev
```

```bash
### Конфигурация проекта

# Хранение api ключа от бота в открытом доступе опасно.
# Поэтому необходимо скрывать его в переменном окружении (файле .env)
# Все переменные из окружения находятся в ../core/config.py

### Создайте .env файл из заготовки
cp template.env .env

# После создания .env файла заполните переменную TG_API_KEY.
# Создайте токен бота через BotFather и вставьте его.
# Этот токен будет использоваться для ваших локальных тестов.

###
### ВАЖНО! 
### Ваш токен будет использован ТОЛЬКО для локальных тестов
### (так как несколько компьютеров не могут подключиться к одному токену)
###
```

```bash
# Запуск
uv run -m finfly
```

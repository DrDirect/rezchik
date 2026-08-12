# Telegram Ringtone Cutter Bot

Бот принимает аудиотрек, спрашивает диапазон вырезки и возвращает рингтон с 3-секундным нарастанием громкости в начале и 3-секундным затуханием в конце.

Из-за fade in/out аудио нельзя просто скопировать без перекодирования: `ffmpeg` должен применить аудиофильтр. Бот сохраняет исходный кодек, битрейт, частоту дискретизации и число каналов там, где эти параметры доступны и поддерживаются выходным форматом.

## Требования

- Python 3.10+
- ffmpeg и ffprobe

Установка ffmpeg на Ubuntu:

```bash
sudo apt update
sudo apt install ffmpeg
```

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Откройте `.env` и укажите токен:

```env
BOT_TOKEN=your_bot_token
```

Запустите бота:

```bash
python bot.py
```

## Docker

```bash
docker build -t rezchik-bot .
docker run -d --name rezchik-bot --restart unless-stopped --env-file .env rezchik-bot
```

Проверка логов:

```bash
docker logs -f rezchik-bot
```

Остановка:

```bash
docker stop rezchik-bot
```

## Использование

1. Отправьте боту аудио или аудиофайл.
2. Напишите диапазон вырезки, например:
   - `0:12-0:42`
   - `с 1:05 до 1:35`
   - `12 42`
3. Бот вернёт готовый файл.

Команда `/cancel` отменяет текущую обработку.

## Безопасность токена

Не храните токен в коде и не публикуйте его. Если токен уже был отправлен в чат или попал в историю, лучше перевыпустить его через BotFather.

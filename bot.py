import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
FADE_SECONDS = 3.0
MAX_OUTPUT_SIZE_MB = int(os.getenv("MAX_OUTPUT_SIZE_MB", "49"))

router = Router()
logger = logging.getLogger(__name__)


class CutStates(StatesGroup):
    waiting_range = State()


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Не найден {name}. Установите ffmpeg: sudo apt install ffmpeg")


def parse_time(value: str) -> float:
    value = value.strip().replace(",", ".")

    if ":" not in value:
        seconds = float(value)
        if seconds < 0:
            raise ValueError
        return seconds

    parts = value.split(":")
    if len(parts) > 3:
        raise ValueError

    numbers = [float(part) for part in parts]
    if any(number < 0 for number in numbers):
        raise ValueError

    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            raise ValueError
        return minutes * 60 + seconds

    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        raise ValueError
    return hours * 3600 + minutes * 60 + seconds


def parse_range(text: str) -> tuple[float, float]:
    matches = re.findall(r"\d+(?::\d{1,2}){0,2}(?:[,.]\d+)?", text)
    if len(matches) < 2:
        raise ValueError("range_required")

    start = parse_time(matches[0])
    end = parse_time(matches[1])
    if end <= start:
        raise ValueError("end_before_start")

    return start, end


def format_seconds(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    suffix = f"{secs:02d}"
    if hours:
        return f"{hours}:{minutes:02d}:{suffix}"
    return f"{minutes}:{suffix}"


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ._ -]+", "_", name).strip(" .")
    return cleaned or "track"


def extension_for(file_name: str | None, mime_type: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix.lower()
        if suffix:
            return suffix

    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/opus": ".ogg",
        "audio/wav": ".wav",
        "audio/flac": ".flac",
    }
    return mapping.get(mime_type or "", ".mp3")


def ffprobe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def first_audio_stream(probe: dict[str, Any]) -> dict[str, Any]:
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    raise RuntimeError("В файле не найден аудиопоток.")


def attached_picture_stream_indexes(probe: dict[str, Any]) -> list[int]:
    indexes = []
    for stream in probe.get("streams", []):
        disposition = stream.get("disposition") or {}
        if stream.get("codec_type") == "video" and disposition.get("attached_pic") == 1:
            indexes.append(int(stream["index"]))
    return indexes


def codec_args(stream: dict[str, Any], extension: str) -> list[str]:
    codec = stream.get("codec_name")
    bit_rate = stream.get("bit_rate")

    codec_by_name = {
        "mp3": "libmp3lame",
        "aac": "aac",
        "opus": "libopus",
        "vorbis": "libvorbis",
        "flac": "flac",
        "pcm_s16le": "pcm_s16le",
    }

    extension_fallback = {
        ".mp3": "libmp3lame",
        ".m4a": "aac",
        ".mp4": "aac",
        ".aac": "aac",
        ".ogg": "libopus" if codec == "opus" else "libvorbis",
        ".opus": "libopus",
        ".wav": "pcm_s16le",
        ".flac": "flac",
    }

    encoder = codec_by_name.get(codec) or extension_fallback.get(extension.lower(), "libmp3lame")
    args = ["-c:a", encoder]

    # Fade filters force re-encoding. Reuse the original bitrate when the input exposes it.
    if bit_rate and encoder not in {"flac", "pcm_s16le"}:
        args.extend(["-b:a", str(bit_rate)])

    sample_rate = stream.get("sample_rate")
    channels = stream.get("channels")
    if sample_rate:
        args.extend(["-ar", str(sample_rate)])
    if channels:
        args.extend(["-ac", str(channels)])

    return args


def cut_audio(input_path: Path, output_path: Path, start: float, end: float) -> None:
    duration = end - start
    probe = ffprobe(input_path)
    stream = first_audio_stream(probe)
    attached_pictures = attached_picture_stream_indexes(probe)
    fade_out_start = max(duration - FADE_SECONDS, 0)
    audio_filter = f"afade=t=in:st=0:d={FADE_SECONDS},afade=t=out:st={fade_out_start}:d={FADE_SECONDS}"

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-i",
        str(input_path),
        "-t",
        str(duration),
        "-map",
        "0:a:0",
    ]
    for stream_index in attached_pictures:
        command.extend(["-map", f"0:{stream_index}"])

    command.extend([
        "-map_metadata",
        "0",
        "-af",
        audio_filter,
        *codec_args(stream, output_path.suffix),
    ])
    if attached_pictures:
        command.extend(["-c:v", "copy", "-disposition:v", "attached_pic"])

    command.append(str(output_path))
    subprocess.run(command, check=True, capture_output=True, text=True)


def output_is_too_large(path: Path) -> bool:
    max_bytes = MAX_OUTPUT_SIZE_MB * 1024 * 1024
    return path.stat().st_size > max_bytes


async def send_intro(message: Message) -> None:
    await message.answer(
        "Пришлите аудиотрек файлом или аудио.\n\n"
        "После загрузки я спрошу диапазон. Форматы времени: `0:30-0:45`, "
        "`с 1:05 до 1:35`, `30 45`.",
        parse_mode="Markdown",
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_intro(message)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил текущую обработку. Пришлите новый трек.")


@router.message(F.audio | F.document | F.voice)
async def handle_track(message: Message, state: FSMContext) -> None:
    file_info = None
    file_name = None
    mime_type = None

    if message.audio:
        file_info = message.audio
        file_name = message.audio.file_name or f"{message.audio.title or 'track'}.mp3"
        mime_type = message.audio.mime_type
    elif message.document:
        file_info = message.document
        file_name = message.document.file_name
        mime_type = message.document.mime_type
        if mime_type and not mime_type.startswith("audio/"):
            await message.answer("Похоже, это не аудиофайл. Пришлите трек в аудиоформате.")
            return
    elif message.voice:
        file_info = message.voice
        file_name = "voice.ogg"
        mime_type = message.voice.mime_type

    if not file_info:
        await message.answer("Не смог прочитать аудиофайл. Попробуйте отправить его как файл.")
        return

    await state.set_state(CutStates.waiting_range)
    await state.update_data(
        file_id=file_info.file_id,
        file_name=file_name or "track",
        mime_type=mime_type,
    )

    await message.answer(
        "Трек получил. Напишите, с какого момента и до какого вырезать рингтон.\n"
        "Например: `0:12-0:42` или `с 12 до 42`.",
        parse_mode="Markdown",
    )


@router.message(CutStates.waiting_range)
async def handle_range(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.text:
        await message.answer("Пришлите диапазон текстом, например: `0:12-0:42`.", parse_mode="Markdown")
        return

    try:
        start, end = parse_range(message.text)
    except ValueError:
        await message.answer(
            "Не понял диапазон. Напишите два времени: `0:12-0:42`, `с 1:05 до 1:35` или `12 42`.",
            parse_mode="Markdown",
        )
        return

    data = await state.get_data()
    if not data.get("file_id"):
        await state.clear()
        await message.answer("Не нашёл исходный трек. Пришлите аудиофайл ещё раз.")
        return

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    extension = extension_for(data.get("file_name"), data.get("mime_type"))
    base_name = safe_filename(Path(data.get("file_name") or "track").stem)

    await message.answer(
        f"Режу фрагмент {format_seconds(start)}-{format_seconds(end)} и добавляю плавное начало/конец..."
    )

    with tempfile.TemporaryDirectory(prefix="ringtone_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        input_path = temp_dir / f"source{extension}"
        output_path = temp_dir / f"{base_name}_ringtone{extension}"

        try:
            file = await bot.get_file(data["file_id"])
            await bot.download_file(file.file_path, destination=input_path)
            await asyncio.to_thread(cut_audio, input_path, output_path, start, end)

            if output_is_too_large(output_path):
                await message.answer(
                    f"Готовый файл больше {MAX_OUTPUT_SIZE_MB} МБ, Telegram может не принять его. "
                    "Попробуйте выбрать более короткий фрагмент."
                )
                return

            await message.answer_document(
                document=FSInputFile(output_path),
                caption="Готово: рингтон с 3-секундным нарастанием и затуханием.",
            )
            await state.clear()
        except subprocess.CalledProcessError as error:
            logger.exception("ffmpeg failed: %s", error.stderr)
            await message.answer(
                "Не получилось обработать файл через ffmpeg. Проверьте, что файл не повреждён, "
                "или попробуйте другой аудиоформат."
            )
        except Exception:
            logger.exception("processing failed")
            await message.answer("Произошла ошибка при обработке. Попробуйте отправить трек ещё раз.")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Пришлите аудиотрек или используйте /start для подсказки.")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Укажите токен в переменной окружения BOT_TOKEN или файле .env")

    require_binary("ffmpeg")
    require_binary("ffprobe")

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

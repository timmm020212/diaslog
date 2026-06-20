"""Параллельное скачивание медиа (приём FastTelethon, painor, MIT-идея).

Тяжёлое видео (особенно одноразовое) обычный download_media тянет одним
соединением — медленно. Здесь файл качается НЕСКОЛЬКИМИ соединениями к DC
одновременно, кусками по 512 КБ, и собирается по порядку.

Используется только для документов (видео/файлы). При ЛЮБОЙ ошибке вызывающий
код (capturer._download) обязан откатиться на client.download_media — поэтому
тут можно падать, не ломая доставку.
"""
import os
import math
import asyncio
import logging

from telethon import utils
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import (ExportAuthorizationRequest,
                                        ImportAuthorizationRequest)
from telethon.tl.functions.upload import GetFileRequest
from telethon.tl.types import Document, InputDocumentFileLocation

log = logging.getLogger("diaslog.fastdl")

PART = 512 * 1024   # 512 КБ — кратно 4096 и ≤ 1 МБ (требование GetFile)
MAX_CONN = 8        # максимум параллельных соединений


def _location(document):
    """Document -> (dc_id, InputDocumentFileLocation, size) или (None, None, 0)."""
    if not isinstance(document, Document):
        return None, None, 0
    loc = InputDocumentFileLocation(
        id=document.id, access_hash=document.access_hash,
        file_reference=document.file_reference, thumb_size="")
    return document.dc_id, loc, int(document.size or 0)


class _Part:
    """Одно соединение качает свою серию кусков с шагом stride."""

    def __init__(self, client, sender, location, offset, count):
        self.client = client
        self.sender = sender
        self.request = GetFileRequest(location, offset=offset, limit=PART)
        self.stride = None     # задаётся снаружи (conns * PART)
        self.remaining = count

    async def next(self):
        if self.remaining <= 0:
            return None
        result = await self.client._call(self.sender, self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return result.bytes    # FileCdnRedirect не имеет .bytes -> ошибка -> откат

    async def close(self):
        try:
            await self.sender.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _make_sender(client, dc_id, auth_key_box):
    dc = await client._get_dc(dc_id)
    sender = MTProtoSender(auth_key_box[0], loggers=client._log)
    await sender.connect(client._connection(
        dc.ip_address, dc.port, dc.id, loggers=client._log, proxy=client._proxy))
    if auth_key_box[0] is None:   # чужой DC — экспортируем авторизацию один раз
        auth = await client(ExportAuthorizationRequest(dc_id))
        client._init_request.query = ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes)
        await sender.send(InvokeWithLayerRequest(LAYER, client._init_request))
        auth_key_box[0] = sender.auth_key
    return sender


async def download(client, document, out_dir):
    """Скачать document несколькими соединениями. Вернуть путь или None
    (None = не наш случай, делайте обычный download_media). Бросает исключение
    при сбое — вызывающий ловит и откатывается."""
    dc_id, location, size = _location(document)
    if location is None or size <= 0:
        return None

    part_count = math.ceil(size / PART)
    conns = max(1, min(MAX_CONN, part_count))
    minimum, remainder = divmod(part_count, conns)

    # общий auth_key: если DC совпадает с сессией — берём готовый, иначе экспорт
    same_dc = client.session.dc_id == dc_id
    auth_key_box = [client.session.auth_key if same_dc else None]

    parts = []
    for i in range(conns):
        count = minimum + (1 if i < remainder else 0)
        if count == 0:
            continue
        sender = await _make_sender(client, dc_id, auth_key_box)
        p = _Part(client, sender, location, i * PART, count)
        p.stride = conns * PART
        parts.append(p)

    ext = utils.get_extension(document) or ".bin"
    path = os.path.join(out_dir, f"{document.id}{ext}")
    written = 0
    try:
        with open(path, "wb") as f:
            while True:
                chunks = await asyncio.gather(*[p.next() for p in parts])
                got = False
                for data in chunks:          # порядок parts = порядок кусков в файле
                    if data:
                        f.write(data)
                        written += len(data)
                        got = True
                if not got:
                    break
    finally:
        await asyncio.gather(*[p.close() for p in parts], return_exceptions=True)

    if written < size:
        try:
            os.remove(path)
        except OSError:
            pass
        raise IOError(f"скачано {written} из {size} байт")
    return path

"""Explicit bounded image attachments; never fetch remote images or write files."""
import base64
import binascii

MAX_IMAGE_BYTES = 256 * 1024
MAX_IMAGES_BYTES = 512 * 1024


def validate_images(attachments):
    images, size = [], 0
    for item in attachments:
        if 'content' in item:
            continue
        if set(item) != {'name', 'mime_type', 'data_base64'}:
            raise ValueError('Image attachments require name, mime_type and data_base64')
        encoded, mime = item['data_base64'], item['mime_type']
        if not isinstance(encoded, str) or len(encoded) > 4 * (MAX_IMAGE_BYTES + 2) // 3 + 4:
            raise ValueError('Each image attachment must fit within 256 KiB')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Image attachment must contain valid base64') from None
        matches = {'image/png': data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 33,
                   'image/jpeg': data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'),
                   'image/webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP'}
        if not matches.get(mime) or not 1 <= len(data) <= MAX_IMAGE_BYTES:
            raise ValueError('Image type and file signature must match PNG, JPEG or WebP')
        size += len(data)
        images.append(dict(item))
    if len(images) > 4 or size > MAX_IMAGES_BYTES:
        raise ValueError('Attach up to four images totaling at most 512 KiB')
    return images


def content(prompt, images):
    return [{'type': 'text', 'text': prompt}, *[
        {'type': 'image_url', 'image_url': {'url': 'data:' + image['mime_type'] + ';base64,' + image['data_base64']}}
        for image in images]]

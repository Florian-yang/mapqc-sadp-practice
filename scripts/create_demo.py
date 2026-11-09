"""Create an isolated local demo with synthetic accounts, photo and position."""
import argparse
import base64
import io
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from check_collect.auth import Store
from check_collect.collection import Collection
from check_collect.roles import grant_role


PASSWORD = 'Demo-Only-2026!'


def destination(value):
    requested = Path(value)
    if '..' in requested.parts:
        raise ValueError('路径不能包含上级目录')
    target = requested if requested.is_absolute() else REPO / requested
    data = REPO / 'data'
    if not target.is_relative_to(data) or target.parent == data or target.suffix != '.sqlite3':
        raise ValueError('请使用项目data独立子目录中的.sqlite3文件')
    for component in (target, *target.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError('路径包含链接或目录联接，未写入')
        if component == REPO:
            break
    resolved = target.resolve()
    if not resolved.is_relative_to(data.resolve()) or resolved.parent == data.resolve():
        raise ValueError('目标不在项目data真实子目录')
    if target.exists():
        raise ValueError('目标数据库已存在，未覆盖')
    if target.parent.exists() and any(target.parent.iterdir()):
        raise ValueError('演示目录须为空，以免混用已有照片或数据库')
    return target


def create(value):
    target = destination(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    destination(value)
    with target.open('xb'):
        pass
    store = Store(str(target))
    collection = Collection(store)
    actors = {}
    for name in ('demo_collector', 'demo_reviewer', 'demo_staff'):
        uid, token = store.local_account(name, PASSWORD, True)
        actors[name] = uid
        store.logout(token)
    collection.enroll(actors['demo_collector'])
    grant_role(store, actors['demo_reviewer'], 'reviewer')
    grant_role(store, actors['demo_staff'], 'third_party')
    image = Image.new('RGB', (640, 220), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), 'DEMO SHOP', fill='black', font=ImageFont.load_default(size=62))
    draw.text((70, 150), 'SYNTHETIC DEMO', fill='black', font=ImageFont.load_default(size=26))
    photo = io.BytesIO()
    image.save(photo, 'JPEG', quality=88)
    now = time.time() * 1000
    poi = collection.create(actors['demo_collector'], {
        'name': 'DEMO SHOP 合成招牌', 'category': 'other',
        'description': '照片、WGS84位置及精度为本机合成演示数据，不代表设备采集。',
        'capture_id': 'synthetic-demo-capture-0001',
        'latitude': 30.546, 'longitude': 114.370, 'accuracy': 10.0,
        'captured_ms': now, 'located_ms': now,
        'photo_base64': base64.b64encode(photo.getvalue()).decode('ascii'),
    })
    return target, poi


def main():
    parser = argparse.ArgumentParser(description='创建独立本机合成演示数据，不覆盖已有数据库')
    parser.add_argument('--database', default='data/demo/demo.sqlite3')
    args = parser.parse_args()
    try:
        target, poi = create(args.database)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print('演示数据库：' + target.relative_to(REPO).as_posix())
    print('账号：demo_collector / demo_reviewer / demo_staff')
    print('演示专用口令：' + PASSWORD)
    print('合成POI：' + poi)
    print('照片和位置为合成样例；未启动服务或调用外部平台。')


if __name__ == '__main__':
    main()

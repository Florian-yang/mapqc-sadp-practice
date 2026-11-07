"""Small mobile-friendly HTML views for authorized chat rooms."""
import html
import secrets


escape = html.escape


def listing(chat, uid):
    rooms = chat.list_rooms(uid)
    content = '<p><a href="/">账号</a> · <a href="/my-pois">我的采集</a> · <a href="/review">核验工作台</a></p><h2>工作交流</h2>'
    content += '<p>角色群用于交流工作经验。针对错误标识的私聊，请从对应POI的处理记录进入。</p>'
    for item in rooms:
        content += f'<p><a href="/chat/{escape(item["id"], quote=True)}">{escape(item["title"])}</a></p>'
    return content


def room(chat, uid, room_id):
    item = chat.room(uid, room_id)
    messages = chat.messages(uid, room_id)
    content = f'<p><a href="/chats">交流列表</a></p><h2>{escape(item["title"])}</h2>'
    if item['kind'] == 'private':
        content += f'<p>错误说明：{escape(item["description"])}</p><p><a href="/poi/{escape(item["poi_id"], quote=True)}">查看POI处理记录</a></p>'
    content += '<p>显示最近100条消息。</p>'
    if not messages:
        content += '<p>暂无消息</p>'
    for message in messages:
        sender = '我' if message['sender'] == uid else ('对方' if item['kind'] == 'private' else message['sender'])
        content += f'<article><p><strong>{escape(sender)}</strong></p><p style="white-space:pre-wrap">{escape(message["text"])}</p></article>'
    content += f'''<form action="/chat/{escape(room_id, quote=True)}/send" method="post">
      <input type="hidden" name="request_id" value="{secrets.token_hex(16)}">
      <p><label>消息 <textarea name="text" required maxlength="1000"></textarea></label></p>
      <button>发送</button></form>'''
    return content

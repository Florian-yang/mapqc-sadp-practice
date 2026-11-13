import html


def page(content, path, username=None, unread=0, roles=()):
    escape = html.escape
    map_view = path == '/map'
    links = [('/map', 'POI地图'), ('/my-pois', '我的采集'), ('/chats', '工作讨论')]
    if 'reviewer' in roles:
        links.append(('/review', '核验工作台'))
    if 'third_party' in roles:
        links.append(('/disputes', '异议处理'))
    nav = ''.join(f'<a href="{url}" class="{"active" if path == url else ""}">{label}</a>' for url, label in links)
    bell = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M10 21h4"/></svg>'
    account = ''
    if username is not None:
        badge = str(min(unread, 99)) + ('+' if unread > 99 else '')
        account = (f'<div class="header-actions"><a id="notification-bell" class="notification-bell" href="/notifications" '
                   f'aria-label="我的通知，{unread}条未读">{bell}<span id="notification-badge" class="notification-badge" '
                   f'{"" if unread else "hidden"}>{badge}</span></a>'
                   f'<a class="account-chip" href="/"><span class="avatar">{escape(username[:1].upper())}</span>'
                   f'<span>{escape(username)}</span></a></div>')
    header = (f'<header class="topbar"><a class="brand" href="/map"><span class="brand-mark">c</span>check@collect</a>'
              f'<nav aria-label="主导航">{nav if username is not None else ""}</nav>{account}</header>')
    css = '<link rel="stylesheet" href="/static/layout.css">'
    if map_view:
        main = f'<main class="map-main">{content}</main>'
    else:
        main = f'<main class="page-content {"login-page" if username is None else ""}">{content}</main>'
    script = '<script src="/static/notifications.js" defer></script>' if username is not None else ''
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>check@collect</title>'
            f'{css}</head><body>{header}{main}{script}</body></html>')

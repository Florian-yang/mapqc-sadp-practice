import html
import re
from datetime import datetime, timezone, timedelta

LABELS={'poi.ready_for_review':'有POI数据待核验','poi.error_reported':'采集数据被标识错误，请复核',
        'poi.resubmitted':'采集者已修改数据，请重新核验','poi.rejected':'采集者驳回错误标识，请查看理由',
        'system.notice':'系统通知','chat.message':'有新的讨论消息',
        'dispute.assigned':'请前往现场重采并核验异议','dispute.finalized':'第三方核验结果已确定'}


def page(rows):
    unread=sum(row['read_ms'] is None for row in rows)
    content=(f'<div class="notice-heading"><div><p class="eyebrow">消息中心</p><h2>我的通知</h2></div>'
             f'<span class="count-pill">当前列表 · {unread} 条未读</span></div>')
    if not rows: return content+'<div class="empty-state"><h3>暂时没有通知</h3><p>核验反馈、任务和讨论消息会显示在这里。</p></div>'
    for row in rows:
        payload=row['payload']
        is_unread=row['read_ms'] is None
        stamp=datetime.fromtimestamp(row['received_ms']/1000,timezone(timedelta(hours=8))).strftime('%m月%d日 %H:%M')
        category='核验反馈' if row['event_type'].startswith('poi.') else '讨论消息' if row['event_type']=='chat.message' else '异议任务' if row['event_type'].startswith('dispute.') else '系统通知'
        content+=f'<article class="notice-card {"unread" if is_unread else ""}"><div class="notice-icon" aria-hidden="true">{category[0]}</div><div class="notice-body"><div class="notice-meta"><span>{category}</span><time>{stamp}</time>'
        content+=('<span class="unread-label">未读</span>' if is_unread else '<span>已读</span>')+'</div><h3>'+html.escape(LABELS.get(row['event_type'],'系统消息'))+'</h3>'
        if payload.get('text'): content+='<p>'+html.escape(payload['text'])+'</p>'
        poi_id=payload.get('poi_id','')
        if isinstance(poi_id,str) and re.fullmatch('[a-f0-9]{32}',poi_id): content+=f'<p><a href="/poi/{poi_id}">查看POI与处理记录</a></p>'
        room_id=payload.get('room_id','')
        if isinstance(room_id,str) and (room_id in ('collectors','reviewers') or re.fullmatch('[a-f0-9]{32}',room_id)):
            content+=f'<p><a href="/chat/{room_id}">查看讨论</a></p>'
        case_id=payload.get('dispute_id','')
        if isinstance(case_id,str) and re.fullmatch('[a-f0-9]{32}',case_id):content+=f'<p><a href="/dispute/{case_id}">查看异议任务与结果</a></p>'
        content+='</div>'
        if is_unread: content+=f'<form action="/notifications/{row["event_id"]}/read" method="post"><button>标为已读</button></form>'
        content+='</article>'
    return content

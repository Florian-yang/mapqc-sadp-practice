import html
import re

LABELS={'poi.ready_for_review':'有POI数据待核验','poi.error_reported':'采集数据被标识错误，请复核',
        'poi.resubmitted':'采集者已修改数据，请重新核验','poi.rejected':'采集者驳回错误标识，请查看理由',
        'system.notice':'系统通知','chat.message':'有新的讨论消息',
        'dispute.assigned':'请前往现场重采并核验异议','dispute.finalized':'第三方核验结果已确定'}


def page(rows):
    content='<p><a href="/">账号</a></p><h2>我的通知</h2>'
    if not rows: return content+'<p>暂无已送达通知</p>'
    for row in rows:
        payload=row['payload']
        content+='<article><h3>'+html.escape(LABELS.get(row['event_type'],'系统消息'))+'</h3>'
        if payload.get('text'): content+='<p>'+html.escape(payload['text'])+'</p>'
        poi_id=payload.get('poi_id','')
        if isinstance(poi_id,str) and re.fullmatch('[a-f0-9]{32}',poi_id): content+=f'<p><a href="/poi/{poi_id}">查看POI与处理记录</a></p>'
        room_id=payload.get('room_id','')
        if isinstance(room_id,str) and (room_id in ('collectors','reviewers') or re.fullmatch('[a-f0-9]{32}',room_id)):
            content+=f'<p><a href="/chat/{room_id}">查看讨论</a></p>'
        case_id=payload.get('dispute_id','')
        if isinstance(case_id,str) and re.fullmatch('[a-f0-9]{32}',case_id):content+=f'<p><a href="/dispute/{case_id}">查看异议任务与结果</a></p>'
        if row['read_ms'] is None: content+=f'<form action="/notifications/{row["event_id"]}/read" method="post"><button>标为已读</button></form>'
        else: content+='<p>已读</p>'
        content+='</article><hr>'
    return content

import html
from urllib.parse import urlencode
from .collection import CATEGORIES
from .review import STATUS

escape=html.escape


def options(selected=None):
    return ''.join(f'<option value="{k}"'+(' selected' if k==selected else '')+f'>{v}</option>' for k,v in CATEGORIES.items())


def fields(row):
    return f'<p><label>名称 <input name="name" required maxlength="120" value="{escape(row["name"],quote=True)}"></label></p><p><label>类别 <select name="category">{options(row["category"])}</select></label></p><p><label>描述 <textarea name="description" required maxlength="1000">{escape(row["description"])}</textarea></label></p>'


def hidden(row,issue=None):
    content=f'<input type="hidden" name="poi_id" value="{row["id"]}"><input type="hidden" name="revision" value="{row["revision"]}">'
    if issue: content+=f'<input type="hidden" name="issue_id" value="{issue["id"]}">'
    return content


def listing(review,uid,reviewer=False):
    rows=review.queue(uid) if reviewer else review.collection.list_owned(uid)
    content='<p><a href="/">账号</a> · <a href="/collect">采集</a> · <a href="/my-pois">我的采集</a></p>'
    content+='<h2>'+('核验工作台' if reviewer else '我的采集')+'</h2>'
    if not rows: return content+'<p>暂无记录</p>'
    for row in rows:
        detail=review.detail(uid,row['id'])
        content+=f'<article><h3>{escape(row["name"])}</h3><p>{CATEGORIES[row["category"]]} · {STATUS[row["status"]]} · 版本{row["revision"]}</p><img alt="POI照片" style="max-width:100%" src="/api/pois/{row["id"]}/photo"><p>{escape(row["description"])}</p><p>自动位置：{row["latitude"]:.6f}, {row["longitude"]:.6f}；精度约{row["accuracy"]:.0f}米</p><p><a href="/poi/{row["id"]}">查看版本与处理记录</a></p>'
        issue=next((x for x in reversed(detail['issues']) if x['status']=='open'),None)
        if issue: content+=f'<p>错误说明：{escape(issue["description"])}</p>'
        if reviewer and row['status']=='pending':
            content+='<form action="/review/report" method="post">'+hidden(row)+'<p><label>错误说明 <textarea name="description" required maxlength="1000"></textarea></label></p><button>标识错误并保存</button></form>'
        elif not reviewer and issue:
            query=urlencode({'poi_id':row['id'],'issue_id':issue['id'],'revision':row['revision']})
            content+=f'<p><a href="/collect?{query}">重新拍照采集</a></p>'
            content+='<form action="/review/respond" method="post">'+hidden(row,issue)+fields(row)+'<p><label>处理说明 <textarea name="reason" required maxlength="1000"></textarea></label></p><button name="action" value="update">更新信息并重送核验</button> <button name="action" value="reject">驳回错误标识</button></form>'
        elif row['status']=='disputed':
            content+='<p>驳回已记录，双方讨论与第三方处理入口按后续计划开发。</p>'
        content+='</article><hr>'
    return content


def history(detail):
    row=detail['poi']
    content=f'<p><a href="/">账号</a> · <a href="/my-pois">我的采集</a> · <a href="/review">核验工作台</a></p><h2>{escape(row["name"])}</h2><p>当前：{STATUS[row["status"]]}</p><h3>处理记录</h3>'
    if not detail['issues']: content+='<p>尚无错误记录</p>'
    labels={'open':'待处理','update':'已更新','recollect':'已重新采集','reject':'已驳回'}
    for issue in detail['issues']:
        content+=f'<p>针对版本{issue["based_revision"]}：{escape(issue["description"])}（{labels[issue["status"]]}）</p>'
        if issue['response']: content+='<p>采集者说明：'+escape(issue['response'])+'</p>'
    content+='<h3>采集版本</h3>'
    for version in detail['versions']:
        content+=f'<h4>版本{version["revision"]}：{escape(version["name"])}</h4><p>{CATEGORIES[version["category"]]} · {escape(version["description"])}</p><p>位置：{version["latitude"]:.6f}, {version["longitude"]:.6f}</p><img alt="历史版本照片" style="max-width:100%" src="/api/pois/{row["id"]}/photo?revision={version["revision"]}">'
    return content

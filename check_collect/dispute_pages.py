import html
from .disputes import DECISIONS


def listing(disputes,uid):
    rows=disputes.list_owned(uid)
    content='<p><a href="/">账号</a></p><h2>异议处理</h2>'
    if not rows:return content+'<p>暂无相关任务</p>'
    for row in rows:
        status='已裁决' if row['status']=='finalized' else '待第三方重采核验'
        content+=f'<p><a href="/dispute/{row["id"]}">{html.escape(row["reason"])}</a> · {status}</p>'
    return content


def detail(disputes,uid,case_id):
    data=disputes.detail(uid,case_id)
    row=data['case'];poi=data['poi'];issue=data['issue']
    content=f'<p><a href="/disputes">异议列表</a> · <a href="/poi/{poi["id"]}">POI版本</a></p><h2>{html.escape(poi["name"])}</h2><p>错误说明：{html.escape(issue["description"])}</p><p>采集者复核：{html.escape(issue["response"] or "")}</p><p>异议说明：{html.escape(row["reason"])}</p><img alt="POI照片" style="max-width:100%" src="/api/pois/{poi["id"]}/photo">'
    if row['status']=='finalized':
        content+=f'<h3>最终结果：{DECISIONS[row["decision"]]}</h3><p>{html.escape(row["resolution"])}</p><p>第三方重采版本：{row["final_revision"]}</p>'
    elif uid==row['assignee']:
        content+=f'<p><a href="/collect?dispute_id={row["id"]}">重新拍照定位并提交核验结论</a></p>'
    else:content+='<p>已分派独立第三方工作人员，等待重采核验。</p>'
    return content

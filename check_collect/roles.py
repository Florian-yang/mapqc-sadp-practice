"""Trusted local operator command; no public privilege-granting endpoint."""
import argparse
from .auth import Settings, Store, AuthError
from .collection import Collection


def grant_role(store,uid,role):
    if role not in ('reviewer','third_party'):raise AuthError('角色无效')
    Collection(store)
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
            raise AuthError('账号不存在，请先注册并确认用户ID')
        db.execute('INSERT OR IGNORE INTO user_roles VALUES(?,?)',(uid,role))


def grant_reviewer(store,uid):
    grant_role(store,uid,'reviewer')


def main():
    parser=argparse.ArgumentParser(description='由本机维护者授予核验角色')
    parser.add_argument('user_id')
    parser.add_argument('role',nargs='?',default='reviewer',choices=['reviewer','third_party'])
    args=parser.parse_args()
    try:
        grant_role(Store(Settings.from_env().database),args.user_id,args.role)
    except AuthError as exc:
        parser.exit(1,str(exc)+'\n')
    print('工作角色已授予')


if __name__=='__main__': main()

"""Trusted local operator command; no public privilege-granting endpoint."""
import argparse
from .auth import Settings, Store, AuthError
from .collection import Collection


def grant_reviewer(store,uid):
    Collection(store)
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
            raise AuthError('账号不存在，请先注册并确认用户ID')
        db.execute("INSERT OR IGNORE INTO user_roles VALUES(?,'reviewer')",(uid,))


def main():
    parser=argparse.ArgumentParser(description='由本机维护者授予核验角色')
    parser.add_argument('user_id')
    args=parser.parse_args()
    try:
        grant_reviewer(Store(Settings.from_env().database),args.user_id)
    except AuthError as exc:
        parser.exit(1,str(exc)+'\n')
    print('核验角色已授予')


if __name__=='__main__': main()

import json
import math
import sqlite3
import time
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup


url = 'https://github.com/orgs/google/repositories'
db_file = './last.sqlite'


def get_page(page):
    # 429の場合は、待ち時間を延ばしながら最大5回再試行する
    for attempt in range(6):
        time.sleep(1)
        res = requests.get(url, params={'page': page}, timeout=30)

        if res.status_code != 429 or attempt == 5:
            res.raise_for_status()
            return res

        wait_seconds = 60 * (2 ** attempt)
        retry_after = res.headers.get('Retry-After')
        if retry_after:
            try:
                if retry_after.strip().isdigit():
                    requested_wait = int(retry_after)
                else:
                    retry_at = parsedate_to_datetime(retry_after)
                    requested_wait = math.ceil(retry_at.timestamp() - time.time())
                wait_seconds = max(wait_seconds, requested_wait)
            except (ValueError, TypeError, OverflowError):
                pass

        res.close()
        print(f'アクセス制限中です。{wait_seconds}秒待って{page}ページ目を再試行します', flush=True)
        time.sleep(wait_seconds)


con = sqlite3.connect(db_file)
page = 1

try:
    with con:
        cur = con.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS repositories (
                name TEXT,
                language TEXT,
                stars INTEGER
            )
        ''')
        # 停止した場合に、次に取得するページを記録しておく
        cur.execute('''
            CREATE TABLE IF NOT EXISTS scrape_progress (
                url TEXT PRIMARY KEY,
                next_page INTEGER NOT NULL
            )
        ''')

    cur.execute('SELECT next_page FROM scrape_progress WHERE url = ?', (url,))
    progress = cur.fetchone()
    if progress:
        page = progress[0]
        print(f'{page}ページ目から再開します', flush=True)

    while True:
        res = get_page(page)
        html_soup = BeautifulSoup(res.content, 'html.parser')
        data_tag = html_soup.find(
            'script', attrs={'data-target': 'react-app.embeddedData'}
        )
        if data_tag is None:
            raise ValueError('HTML内にリポジトリ一覧のデータが見つかりません')

        data = json.loads(data_tag.string)
        page_data = data['payload']['orgReposPageRoute']
        if page_data.get('errorMessage'):
            raise ValueError(page_data['errorMessage'])
        page_repositories = page_data['repositories']
        if not page_repositories:
            raise ValueError(f'{page}ページ目でリポジトリを取得できませんでした')

        last_page = page >= page_data['pageCount']

        # 1ページ分のデータと再開位置を、まとめて保存する
        with con:
            for repository in page_repositories:
                name = repository['name']
                primary_language = repository['primaryLanguage']
                language = primary_language['name'] if primary_language else None
                stars = repository['starsCount']

                cur.execute('DELETE FROM repositories WHERE name = ?', (name,))
                cur.execute('''
                    INSERT INTO repositories (name, language, stars)
                    VALUES (?, ?, ?)
                ''', (name, language, stars))

            if last_page:
                cur.execute('DELETE FROM scrape_progress WHERE url = ?', (url,))
            else:
                cur.execute('''
                    INSERT OR REPLACE INTO scrape_progress (url, next_page)
                    VALUES (?, ?)
                ''', (url, page + 1))

        print(f'{page}ページ目をDBに保存しました（{len(page_repositories)}件）', flush=True)
        if last_page:
            break

        page += 1
        # 次のリクエスト前の1秒と合わせて、通常は5秒以上空ける
        time.sleep(4)

    print('全ページの取得が終わりました')
    cur.execute('SELECT name, language, stars FROM repositories ORDER BY name')
    print('リポジトリ名\t主要な言語\tスター数')
    for name, language, stars in cur.fetchall():
        print(f'{name}\t{language}\t{stars}')
finally:
    con.close()
